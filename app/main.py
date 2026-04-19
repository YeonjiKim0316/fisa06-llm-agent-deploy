import os
import json
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
## [클라우드 마이그레이션] PyMySQLSaver(동기) → AIOMySQLSaver(비동기) 교체
# PyMySQLSaver 사용 시 astream_events에서 aget_tuple NotImplementedError 발생
from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

from agent.supervisor_graph import create_supervisor_app as create_agent_app
## [클라우드 마이그레이션] DB_BACKEND 스위칭 모듈 import
from db.connection import DB_BACKEND, get_db_url, engine
from db.models import Base
## [클라우드 마이그레이션] 비밀번호 기반 인증 함수 추가 (register_user, authenticate_user)
from db.user_repo import get_or_create_thread_id, reset_thread_id, register_user, authenticate_user
## [클라우드 마이그레이션] SessionMiddleware 제거 후 JWT 쿠키 인증으로 교체
from auth.jwt_handler import create_access_token, get_current_user, COOKIE_NAME

load_dotenv()

# LangSmith 설정 (선택)
langsmith_api_key = os.getenv("LANGSMITH_API_KEY")
if langsmith_api_key and langsmith_api_key.strip():
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "LLM Agent with LangGraph")
else:
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

## [클라우드 마이그레이션] S3 정적 파일 분기
# STATIC_BASE_URL=/static      → 로컬: /static 경로에서 직접 서빙
# STATIC_BASE_URL=https://...  → 프로덕션: S3 URL 사용, 로컬 마운트 불필요
STATIC_BASE_URL = os.getenv("STATIC_BASE_URL", "/static")

app = FastAPI(title="LLM Agent API")
templates = Jinja2Templates(directory="templates")

if not STATIC_BASE_URL.startswith("http"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def _pymysql_uri() -> str:
    """SQLAlchemy mysql+pymysql:// → AIOMySQLSaver용 mysql:// 변환."""
    return get_db_url().replace("mysql+pymysql://", "mysql://").split("?")[0]


## [클라우드 마이그레이션] DB_BACKEND에 따라 checkpointer를 분기하는 lifespan
# mysql: AIOMySQLSaver로 RDS MySQL에 대화 기록 영구 저장
# sqlite: 기존 AsyncSqliteSaver 유지 (로컬/테스트용)
@asynccontextmanager
async def lifespan(app: FastAPI):
    if DB_BACKEND == "mysql":
        # ── 프로덕션: MySQL checkpointer (async) ──────────────────
        async with AIOMySQLSaver.from_conn_string(_pymysql_uri()) as checkpointer:
            await checkpointer.setup()
            app.state.checkpointer = checkpointer
            app.state.agent_app = await create_agent_app(checkpointer=checkpointer)
            yield
    else:
        # ── 로컬/테스트: 기존 SQLite 코드 그대로 ─────────────────
        os.makedirs("data", exist_ok=True)
        Base.metadata.create_all(engine)  # users, user_threads 테이블 자동 생성
        sqlite_checkpointer_cm = AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite")
        sqlite_checkpointer = await sqlite_checkpointer_cm.__aenter__()
        await sqlite_checkpointer.setup()
        app.state.checkpointer = sqlite_checkpointer
        app.state.agent_app = await create_agent_app(checkpointer=sqlite_checkpointer)
        try:
            yield
        finally:
            await sqlite_checkpointer_cm.__aexit__(None, None, None)


app.router.lifespan_context = lifespan


def get_thread_id(request: Request) -> str | None:
    username = get_current_user(request)
    if not username:
        return None
    return get_or_create_thread_id(username)


async def ensure_agent_app(request: Request):
    agent_app = getattr(request.app.state, "agent_app", None)
    if agent_app is not None:
        return agent_app

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="Agent is not initialized")

    agent_app = await create_agent_app(checkpointer=checkpointer)
    request.app.state.agent_app = agent_app
    return agent_app


async def get_history(request: Request) -> List[BaseMessage]:
    agent_app = getattr(request.app.state, "agent_app", None)
    if agent_app is None:
        return []

    thread_id = get_thread_id(request)
    if not thread_id:
        return []

    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = await agent_app.aget_state(config)
        messages = state.values.get("messages", [])
        history = [m for m in messages if m.type in ["human", "ai"]]
        return history
    except Exception:
        return []


## [클라우드 마이그레이션] JWT 기반 로그인/회원가입
# 기존: username만 입력 → 세션 저장
# 변경: username + password 입력 → bcrypt 검증 → JWT 쿠키 발급
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "", "mode": "login"},
    )


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(default=""), password: str = Form(default="")):
    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "error": "아이디와 비밀번호를 입력하세요.", "mode": "login"},
        )
    if not authenticate_user(username, password):
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다.", "mode": "login"},
        )
    token = create_access_token(username)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=60 * int(os.getenv("JWT_EXPIRE_MINUTES", 1440)),
    )
    return response


## [클라우드 마이그레이션] 회원가입 라우트 — 신규 추가
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "", "mode": "register"},
    )


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, username: str = Form(default=""), password: str = Form(default="")):
    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "error": "아이디와 비밀번호를 입력하세요.", "mode": "register"},
        )
    if not register_user(username, password):
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "error": "이미 존재하는 아이디입니다.", "mode": "register"},
        )
    token = create_access_token(username)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=60 * int(os.getenv("JWT_EXPIRE_MINUTES", 1440)),
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    username = get_current_user(request)
    if not username:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "chat_history": await get_history(request),
            "username": username,
            "success": True,
            "error": "",
            "static_base_url": STATIC_BASE_URL,
        },
    )


@app.get("/chat/stream")
async def chat_stream(request: Request, query: str):
    async def event_generator():
        import traceback
        try:
            username = get_current_user(request)
            if not username:
                yield f"data: {json.dumps({'type': 'error', 'content': '로그인이 필요합니다.'})}\n\n"
                return

            agent_app = await ensure_agent_app(request)

            thread_id = get_thread_id(request)
            if not thread_id:
                yield f"data: {json.dumps({'type': 'error', 'content': '세션이 유효하지 않습니다.'})}\n\n"
                return

            config = {"configurable": {"thread_id": thread_id}}
            messages_to_send = [HumanMessage(content=query)]
            emitted_token = False

            async for event in agent_app.astream_events({"messages": messages_to_send}, version="v2", config=config):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    if event.get("metadata", {}).get("langgraph_node") == "supervisor":
                        continue
                    content = event["data"]["chunk"].content
                    if content:
                        emitted_token = True
                        yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    yield f"data: {json.dumps({'type': 'tool_start', 'content': f'도구 사용: {tool_name}'})}\n\n"
                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    tool_output = event.get("data", {}).get("output", "")
                    print(f"[TOOL RESULT] {tool_name}: {str(tool_output)[:500]}", flush=True)
                    yield f"data: {json.dumps({'type': 'tool_end', 'content': f'도구 완료: {tool_name}'})}\n\n"

            if not emitted_token:
                state = await agent_app.aget_state(config)
                messages = state.values.get("messages", [])
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
                        content = getattr(msg, "content", "")
                        if content:
                            yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
                        break

            yield f"data: {json.dumps({'type': 'finish'})}\n\n"

        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/clear-chat")
async def clear_chat(request: Request):
    username = get_current_user(request)
    if not username:
        return JSONResponse({"ok": False, "error": "로그인이 필요합니다."}, status_code=401)
    reset_thread_id(username)
    return JSONResponse({"ok": True})


@app.get("/chat-history")
async def get_chat_history(request: Request):
    if not get_current_user(request):
        return {"chat_history": []}

    history_messages = await get_history(request)
    chat_history = []
    for msg in history_messages:
        if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
            chat_history.append({"type": "user", "content": getattr(msg, "content", "")})
        elif isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
            chat_history.append({"type": "ai", "content": getattr(msg, "content", "")})
    return {"chat_history": chat_history}


@app.get("/api")
async def root():
    return {"message": "LLM Agent API is running"}
