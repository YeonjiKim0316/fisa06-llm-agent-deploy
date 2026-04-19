# AWS Migration (RDS MySQL + S3 + JWT) Implementation Plan

**Goal:** 현재 SQLite + 세션 기반 인증을 AWS RDS MySQL(checkpointer + user 데이터) + JWT 쿠키 인증 + S3 정적 파일로 마이그레이션.

**Architecture:**
- LangGraph Checkpointer: `AIOMySQLSaver` (`langgraph-checkpoint-mysql` + `aiomysql`) → RDS MySQL (비동기)
- User/Thread 데이터: 동일 RDS MySQL에 SQLAlchemy ORM 모델로 정의, Alembic으로 마이그레이션 관리
- 인증: Starlette SessionMiddleware 제거 → `python-jose` JWT + `passlib[bcrypt]` 비밀번호 해시, HTTP-only 쿠키
- 정적 파일: `static/chat.js` 생성 → S3 업로드 → 템플릿 URL 환경변수로 분기

**Tech Stack:** FastAPI, LangGraph, `langgraph-checkpoint-mysql`, `aiomysql`, SQLAlchemy, Alembic, pymysql, python-jose[cryptography], passlib[bcrypt], boto3, AWS RDS MySQL 8.0, AWS S3

---

## 사전 확인 사항

### 참조 코드 (동작 확인된 MySQL Checkpointer 패턴)

```python
# 패키지: langgraph-checkpoint-mysql + aiomysql
from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

DB_URI = "mysql://user:pass@host:3306/schema"

async with AIOMySQLSaver.from_conn_string(DB_URI) as memory:
    await memory.setup()  # checkpoints 테이블 자동 생성
    graph = builder.compile(checkpointer=memory)
    result = await graph.ainvoke({"messages": [...]}, config={"configurable": {"thread_id": "t-1"}})
```

> **주의:** `AIOMySQLSaver`는 비동기(async) 방식으로, `astream_events` 등 LangGraph 비동기 API와 함께 사용해야 한다.  
> `PyMySQLSaver`(동기)를 사용하면 `aget_tuple NotImplementedError` 발생.

---

## Task 1: 의존성 추가 및 환경변수 정의

**Files:**
- Modify: `requirements.txt`
- Modify: `.env` (또는 `.env.example` 생성)

**Step 1: requirements.txt에 패키지 추가**

```
# 기존 유지
fastapi
uvicorn
jinja2
python-multipart
langchain-openai>=0.1.0
langchain-elasticsearch
langchain>=0.1.0
langgraph
langsmith
python-dotenv
langchain-community
langchain-text-splitters
openai>=1.0.0
langchain_mcp_adapters
pytest
wikipedia-mcp

# 추가: MySQL checkpointer (SQLite 대체)
langgraph-checkpoint-mysql[pymysql]
pymysql
aiomysql                      # AIOMySQLSaver 비동기 드라이버

# 추가: SQLAlchemy ORM + Alembic 마이그레이션
sqlalchemy
alembic
greenlet

# 추가: JWT 인증 (SessionMiddleware 대체)
python-jose[cryptography]

# 추가: 비밀번호 해시
passlib[bcrypt]

# 추가: S3 업로드
boto3
```

> `langgraph-checkpoint-sqlite`는 **유지** — 로컬/테스트 환경에서 SQLite 백엔드로 계속 사용.  
> `itsdangerous`는 SessionMiddleware 제거로 불필요 → 제거.  
> `greenlet`은 SQLAlchemy 동기 드라이버 실행에 필요.

**Step 2: 환경별 .env 파일 구성**

`.env` (로컬/테스트 — 기존 파일 기반으로 추가):
```env
# 기존 유지
OPENAI_API_KEY=...
LANGSMITH_API_KEY=...
ELASTICSEARCH_URL=http://host.docker.internal:9200
SESSION_SECRET_KEY=...   # Task 5 완료 후 제거 가능

# 추가: 백엔드 선택 (sqlite | mysql)
DB_BACKEND=sqlite        # 로컬/테스트: sqlite, 프로덕션: mysql

# 추가: JWT
JWT_SECRET_KEY=dev-secret-change-in-prod
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# 추가: S3 (로컬에서는 STATIC_BASE_URL을 /static으로 두면 S3 불필요)
STATIC_BASE_URL=/static
```

`.env.production` (AWS 배포용):
```env
DB_BACKEND=mysql

# RDS MySQL
MYSQL_USER=fisaai6
MYSQL_PASSWORD=...
MYSQL_HOST=<RDS-endpoint>.rds.amazonaws.com
MYSQL_PORT=3306
MYSQL_SCHEMA=yeonji

# JWT
JWT_SECRET_KEY=<랜덤 256비트 문자열>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=<버킷명>
STATIC_BASE_URL=https://<버킷명>.s3.ap-northeast-2.amazonaws.com
```

> `DB_BACKEND=sqlite` 일 때 MYSQL_* 환경변수는 불필요 → 로컬에서 MySQL 설치 없이 바로 실행 가능.

**Step 3: 커밋**

```bash
git add requirements.txt .env.example
git commit -m "chore: add mysql, jwt, s3 dependencies and env vars"
```

---

## Task 2: SQLAlchemy 모델 정의 및 Alembic 마이그레이션 설정

> LangGraph checkpointer 테이블(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`)은  
> `PyMySQLSaver.setup()`이 자동 생성하므로 여기서는 **user 도메인 모델만** 관리.

**Files:**
- Create: `db/__init__.py`
- Create: `db/models.py`
- Create: `db/connection.py`
- Create: `alembic.ini` (alembic 명령으로 자동 생성)
- Create: `alembic/env.py` (수정 필요)
- Create: `alembic/versions/<hash>_create_users_and_user_threads.py` (자동 생성)

---

### Step 1: db/__init__.py 생성

```python
# db/__init__.py
```

---

### Step 2: db/connection.py 작성 (DB_BACKEND 스위칭)

```python
# db/connection.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()  # .env 파일 자동 로드 (alembic CLI 등 직접 실행 시 필수)

# 환경변수 하나로 백엔드 전환: "sqlite" (기본) | "mysql"
DB_BACKEND: str = os.getenv("DB_BACKEND", "sqlite")

def get_db_url() -> str:
    """SQLAlchemy 엔진용 URL. DB_BACKEND에 따라 자동 분기."""
    if DB_BACKEND == "mysql":
        user   = os.environ["MYSQL_USER"]
        pwd    = os.environ["MYSQL_PASSWORD"]
        host   = os.environ["MYSQL_HOST"]
        port   = os.environ.get("MYSQL_PORT", "3306")
        schema = os.environ["MYSQL_SCHEMA"]
        return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{schema}?charset=utf8mb4"
    # sqlite (로컬/테스트 기본값)
    os.makedirs("data", exist_ok=True)
    return "sqlite:///data/app.db"

def _make_engine():
    url = get_db_url()
    if DB_BACKEND == "mysql":
        return create_engine(url, pool_pre_ping=True, pool_recycle=1800)
    # SQLite: 멀티스레드 허용 (FastAPI 동기 함수 지원)
    return create_engine(url, connect_args={"check_same_thread": False})

engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

@contextmanager
def get_session() -> Session:
    """with get_session() as session: 패턴으로 사용. 양쪽 백엔드 동일."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

> `DB_BACKEND=sqlite` → `data/app.db` 자동 생성, MySQL 환경변수 불필요.  
> `DB_BACKEND=mysql` → RDS 연결, pool 설정 적용.

---

### Step 3: db/models.py 작성 (SQLAlchemy ORM 모델)

```python
# db/models.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)   # bcrypt 해시
    created_at    = Column(DateTime, nullable=False, server_default=func.now())

    thread = relationship(
        "UserThread", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserThread(Base):
    __tablename__ = "user_threads"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    username   = Column(String(255), ForeignKey("users.username", ondelete="CASCADE"), unique=True, nullable=False)
    thread_id  = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="thread")
```

---

### Step 4: Alembic 초기화

```bash
alembic init alembic
```

Expected: 프로젝트 루트에 `alembic/`, `alembic.ini` 생성

---

### Step 5: alembic.ini DB URL 설정

`alembic.ini` 파일에서 아래 줄을 찾아 수정:

```ini
# 기존
sqlalchemy.url = driver://user:pass@localhost/dbname

# 변경: 실제 값은 env.py에서 주입하므로 placeholder 유지
sqlalchemy.url =
```

---

### Step 6: alembic/env.py 수정 (모델 자동감지 + 환경변수 URL 주입)

`alembic/env.py`에서 상단 import 블록과 `run_migrations_offline` / `run_migrations_online` 함수 수정:

```python
# alembic/env.py 전체 교체
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# 모델 import (autogenerate가 테이블을 감지하려면 반드시 필요)
from db.models import Base
from db.connection import get_db_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 대상 metadata
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_db_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

---

### Step 7: 최초 마이그레이션 파일 자동 생성

```bash
alembic revision --autogenerate -m "create users and user_threads"
```

Expected: `alembic/versions/<hash>_create_users_and_user_threads.py` 생성

생성된 파일에서 `upgrade()` 함수가 아래를 포함하는지 확인:
```python
op.create_table('users', ...)
op.create_table('user_threads', ...)
```

---

### Step 8: 백엔드별 마이그레이션 실행

**로컬(SQLite) — 개발/테스트:**
```bash
DB_BACKEND=sqlite alembic upgrade head
```

Expected:
```
INFO  [alembic.runtime.migration] Running upgrade  -> <hash>, create users and user_threads
```

`data/app.db` 생성 확인:
```bash
python -c "
from db.connection import engine
from sqlalchemy import inspect
print(inspect(engine).get_table_names())
# ['alembic_version', 'user_threads', 'users']
"
```

**프로덕션(MySQL) — 배포 전:**
```bash
DB_BACKEND=mysql alembic upgrade head
```

MySQL에서 테이블 생성 확인:
```sql
SHOW TABLES;
-- users, user_threads, alembic_version 확인
```

---

### Step 9: 커밋

```bash
git add db/ alembic/ alembic.ini
git commit -m "feat: add SQLAlchemy models and Alembic migration for users/user_threads"
```

---

> **향후 스키마 변경 방법:**
> 1. `db/models.py` 모델 수정
> 2. `alembic revision --autogenerate -m "설명"` 으로 마이그레이션 파일 생성
> 3. `alembic upgrade head` 로 DB 반영
> 4. 코드 커밋

---

## Task 3: AIOMySQLSaver로 Checkpointer 교체

> **실제 적용 과정에서 발견된 이슈:** `PyMySQLSaver`(동기)를 사용하면 `astream_events` 호출 시
> `langgraph.checkpoint.base.__init__.aget_tuple: NotImplementedError` 발생.
> 반드시 `AIOMySQLSaver`(비동기, `aiomysql` 드라이버)를 사용해야 한다.

**Files:**
- Modify: `app/main.py` (lifespan 함수만 수정)

**Step 1: main.py lifespan 변경**

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.mysql.aio import AIOMySQLSaver  # pymysql 아닌 aio
from db.connection import DB_BACKEND, get_db_url

def _pymysql_uri() -> str:
    """SQLAlchemy mysql+pymysql:// → AIOMySQLSaver용 mysql:// 변환."""
    return get_db_url().replace("mysql+pymysql://", "mysql://").split("?")[0]

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
        # ── 로컬/테스트: SQLite (기존 코드 유지) ──────────────────
        os.makedirs("data", exist_ok=True)
        sqlite_checkpointer_cm = AsyncSqliteSaver.from_conn_string("data/checkpoints.sqlite")
        sqlite_checkpointer = await sqlite_checkpointer_cm.__aenter__()
        await sqlite_checkpointer.setup()
        app.state.sqlite_checkpointer_cm = sqlite_checkpointer_cm
        app.state.sqlite_checkpointer = sqlite_checkpointer
        app.state.checkpointer = sqlite_checkpointer
        app.state.agent_app = await create_agent_app(checkpointer=sqlite_checkpointer)
        try:
            yield
        finally:
            await sqlite_checkpointer_cm.__aexit__(None, None, None)
```

**Step 2: SQLite 모드로 로컬 기동 확인 (기존 동작 회귀 테스트)**

```bash
# DB_BACKEND가 설정 안 되어 있거나 sqlite면 기존과 동일하게 동작
DB_BACKEND=sqlite uvicorn app.main:app --reload --port 8000
```

Expected: 기존과 동일하게 `data/checkpoints.sqlite` 생성, 채팅 정상 동작

**Step 3: MySQL 모드 전환 확인**

```bash
DB_BACKEND=mysql uvicorn app.main:app --reload --port 8000
```

Expected: MySQL에 `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` 테이블 생성 + 채팅 동작

```sql
SELECT thread_id, created_at FROM checkpoints LIMIT 5;
```

**Step 4: 커밋**

```bash
git add app/main.py
git commit -m "feat: add DB_BACKEND switch for SQLite(local) / MySQL(prod) checkpointer"
```

---

## Task 4: User/Thread 데이터를 MySQL로 이전

**Files:**
- Create: `db/user_repo.py`
- Modify: `app/main.py` (get_thread_id, clear_chat 로직)

**Step 1: db/user_repo.py 작성 (SQLite/MySQL 공통 표준 ORM)**

```python
# db/user_repo.py
from uuid import uuid4
from passlib.context import CryptContext
from db.connection import get_session
from db.models import User, UserThread

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def register_user(username: str, password: str) -> bool:
    """신규 가입. username 중복이면 False 반환."""
    with get_session() as session:
        if session.query(User).filter_by(username=username).first():
            return False
        session.add(User(username=username, password_hash=hash_password(password)))
    return True

def authenticate_user(username: str, password: str) -> bool:
    """username + password 검증. 성공 시 True."""
    with get_session() as session:
        user = session.query(User).filter_by(username=username).first()
        if not user:
            return False
        return verify_password(password, user.password_hash)

def get_or_create_thread_id(username: str) -> str:
    """username의 thread_id 반환. UserThread가 없으면 생성. SQLite/MySQL 공통."""
    with get_session() as session:
        user_thread = session.query(UserThread).filter_by(username=username).first()
        if user_thread:
            return user_thread.thread_id
        thread_id = f"{username}:{uuid4().hex}"
        session.add(UserThread(username=username, thread_id=thread_id))
        return thread_id

def reset_thread_id(username: str) -> str:
    """새 thread_id 발급 후 DB 갱신. SQLite/MySQL 공통."""
    new_thread_id = f"{username}:{uuid4().hex}"
    with get_session() as session:
        user_thread = session.query(UserThread).filter_by(username=username).first()
        if user_thread:
            user_thread.thread_id = new_thread_id
        else:
            session.add(UserThread(username=username, thread_id=new_thread_id))
    return new_thread_id
```

> dialect 전용 문법(`mysql_insert`) 없이 표준 SQLAlchemy ORM만 사용 → SQLite/MySQL 모두 동일하게 동작.

**Step 2: main.py의 get_thread_id 함수 교체**

기존 (`app/main.py:54-67`):
```python
def get_thread_id(request: Request) -> str | None:
    username = request.session.get("username")
    if not username:
        return None
    user_threads: Dict[str, str] = request.session.get("user_threads", {})
    thread_id = user_threads.get(username)
    if not thread_id:
        thread_id = f"{username}:{uuid4().hex}"
        user_threads[username] = thread_id
        request.session["user_threads"] = user_threads
    request.session["thread_id"] = thread_id
    return thread_id
```

변경 후:
```python
from db.user_repo import get_or_create_thread_id, reset_thread_id

def get_thread_id(request: Request) -> str | None:
    username = get_current_user(request)  # Task 5에서 구현 (JWT 기반)
    if not username:
        return None
    return get_or_create_thread_id(username)
```

**Step 3: clear_chat 엔드포인트 교체**

기존 (`app/main.py:248-260`):
```python
@app.post("/clear-chat")
async def clear_chat(request: Request):
    username = request.session.get("username")
    ...
    new_thread_id = f"{username}:{uuid4().hex}"
    user_threads: Dict[str, str] = request.session.get("user_threads", {})
    user_threads[username] = new_thread_id
    request.session["user_threads"] = user_threads
    request.session["thread_id"] = new_thread_id
    return JSONResponse({"ok": True})
```

변경 후:
```python
@app.post("/clear-chat")
async def clear_chat(request: Request):
    username = get_current_user(request)
    if not username:
        return JSONResponse({"ok": False, "error": "로그인이 필요합니다."}, status_code=401)
    reset_thread_id(username)
    return JSONResponse({"ok": True})
```

**Step 4: 커밋**

```bash
git add db/user_repo.py app/main.py
git commit -m "feat: migrate user/thread data to MySQL, remove session-based thread tracking"
```

---

## Task 5: JWT 인증으로 교체

**Files:**
- Create: `auth/jwt_handler.py`
- Modify: `app/main.py` (login/logout/인증 미들웨어)

**Step 1: auth/__init__.py 생성**

```python
# auth/__init__.py
```

**Step 2: auth/jwt_handler.py 작성**

```python
# auth/jwt_handler.py
import os
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import Request

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-me")
ALGORITHM  = os.environ.get("JWT_ALGORITHM", "HS256")
EXPIRE_MIN = int(os.environ.get("JWT_EXPIRE_MINUTES", 1440))
COOKIE_NAME = "access_token"

def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=EXPIRE_MIN)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> str | None:
    """유효하면 username 반환, 아니면 None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(request: Request) -> str | None:
    """HTTP-only 쿠키에서 JWT를 읽어 username 반환."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return decode_token(token)
```

**Step 3: main.py import 및 SessionMiddleware 교체**

기존:
```python
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY", "dev-only-change-me"))
```

변경 후:
```python
# SessionMiddleware 제거 (위 두 줄 삭제)
from auth.jwt_handler import create_access_token, get_current_user, COOKIE_NAME
```

**Step 4: login / register 엔드포인트 구현**

로그인(비밀번호 검증 후 JWT 발급)과 회원가입(bcrypt 해시 저장 후 JWT 발급) 두 라우트를 추가한다.
`templates/login.html`은 `mode` 변수(login/register)로 탭 UI를 분기한다.

**Step 4-1: templates/login.html — 비밀번호 필드 및 로그인/회원가입 탭 추가**

기존 login.html은 아이디 입력 필드만 있었으나, 비밀번호 필드와 탭 UI를 추가한다.

```html
{% extends "base.html" %}

{% block title %}{{ "회원가입" if mode == "register" else "로그인" }} | LLM Agent{% endblock %}

{% block body_class %}bg-light d-flex align-items-center{% endblock %}
{% block body_style %}min-height: 100vh;{% endblock %}

{% block content %}
    <div class="container">
        <div class="row justify-content-center">
            <div class="col-md-5 col-lg-4">
                <div class="card shadow-sm border-0">
                    <div class="card-body p-4">
                        <h4 class="mb-3"><i class="bi bi-person-circle"></i> LLM Agent</h4>

                        <!-- 탭: mode 변수로 로그인/회원가입 분기 -->
                        <ul class="nav nav-tabs mb-4">
                            <li class="nav-item">
                                <a class="nav-link {{ 'active' if mode != 'register' else '' }}" href="/login">로그인</a>
                            </li>
                            <li class="nav-item">
                                <a class="nav-link {{ 'active' if mode == 'register' else '' }}" href="/register">회원가입</a>
                            </li>
                        </ul>

                        {% if error %}
                        <div class="alert alert-danger py-2">{{ error }}</div>
                        {% endif %}

                        <!-- form action: mode에 따라 /login 또는 /register로 전송 -->
                        <form method="post" action="{{ '/register' if mode == 'register' else '/login' }}">
                            <div class="mb-3">
                                <label for="username" class="form-label">아이디</label>
                                <input
                                    type="text"
                                    class="form-control"
                                    id="username"
                                    name="username"
                                    placeholder="아이디 입력"
                                    required
                                    autofocus
                                >
                            </div>
                            <!-- 비밀번호 입력 필드 추가 -->
                            <div class="mb-4">
                                <label for="password" class="form-label">비밀번호</label>
                                <input
                                    type="password"
                                    class="form-control"
                                    id="password"
                                    name="password"
                                    placeholder="비밀번호 입력"
                                    required
                                >
                            </div>
                            <button type="submit" class="btn btn-primary w-100">
                                {% if mode == "register" %}
                                <i class="bi bi-person-plus"></i> 가입하고 시작하기
                                {% else %}
                                <i class="bi bi-box-arrow-in-right"></i> 로그인
                                {% endif %}
                            </button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
{% endblock %}
```

> **핵심 변경점:**
> - `password` 입력 필드 추가 (`type="password"`, `name="password"`)
> - 로그인/회원가입 탭 UI 추가 (`mode` 변수로 active 탭 분기)
> - `form action`을 `mode`에 따라 `/login` 또는 `/register`로 동적 전환
> - 버튼 텍스트도 `mode`에 따라 변경

**Step 4-2: main.py — 비밀번호 포함 엔드포인트 구현**

```python
from db.user_repo import get_or_create_thread_id, reset_thread_id, register_user, authenticate_user

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "", "mode": "login"},
    )

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
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
    response.set_cookie(key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
                        max_age=60 * int(os.getenv("JWT_EXPIRE_MINUTES", 1440)))
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "", "mode": "register"},
    )

@app.post("/register", response_class=HTMLResponse)
async def register(request: Request, username: str = Form(...), password: str = Form(...)):
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
    response.set_cookie(key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
                        max_age=60 * int(os.getenv("JWT_EXPIRE_MINUTES", 1440)))
    return response
```

**Step 5: logout 엔드포인트 교체**

기존:
```python
@app.post("/logout")
async def logout(request: Request):
    request.session.pop("username", None)
    request.session.pop("thread_id", None)
    return RedirectResponse(url="/login", status_code=303)
```

변경 후:
```python
@app.post("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=COOKIE_NAME)
    return response
```

**Step 6: 인증 체크 수정 — request.session → get_current_user**

`app/main.py` 전체에서 `request.session.get("username")` → `get_current_user(request)` 로 일괄 교체.

| 기존 | 변경 |
|------|------|
| `request.session.get("username")` | `get_current_user(request)` |
| `request.session.get("username", "")` | `get_current_user(request) or ""` |

수정 대상 위치: `/login` GET, `/`, `/chat` POST, `/chat/stream`, `/clear-chat`, `/chat-history`

예시 (`app/main.py:139-154` 홈 라우트):
```python
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    username = get_current_user(request)
    if not username:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request, "index.html",
        {
            "request": request,
            "chat_history": await get_history(request),
            "username": username,
            "success": True,
            "error": "",
        },
    )
```

**Step 7: 동작 테스트**

```bash
uvicorn app.main:app --reload --port 8000
```

- 브라우저 → `http://localhost:8000/login` → 이름 입력 → 홈 이동 확인
- 개발자 도구 → Application → Cookies → `access_token` HTTP-only 쿠키 확인
- 채팅 메시지 전송 → MySQL `checkpoints` 테이블에 thread_id 저장 확인
- 로그아웃 → 쿠키 삭제 확인

**Step 8: 커밋**

```bash
git add auth/ app/main.py
git commit -m "feat: replace SessionMiddleware with JWT HTTP-only cookie auth"
```

---

## Task 6: S3 정적 파일 설정

**Files:**
- Create: `static/chat.js`
- Modify: `templates/index.html` (인라인 JS 제거, 외부 파일 참조)
- Modify: `app/main.py` (StaticFiles 마운트, static_base_url 템플릿 주입)
- Create: `scripts/upload_static_s3.py`

**Step 1: static/chat.js 생성 — index.html 인라인 JS 이동**

`templates/index.html`의 `{% block scripts %}` 안 `<script>` 태그 내용을 그대로 `static/chat.js`로 이동.

```javascript
// static/chat.js
// (index.html {% block scripts %} 내 <script> 본문 그대로 복사)
function setQuery(query) { ... }
async function clearChat() { ... }
// ... 이하 전체
```

**Step 2: templates/index.html scripts 블록 교체**

기존:
```html
{% block scripts %}
    <script>
        // 전체 JS 코드 (약 100줄)
    </script>
{% endblock %}
```

변경 후:
```html
{% block scripts %}
    <script src="{{ static_base_url }}/chat.js"></script>
{% endblock %}
```

**Step 3: app/main.py에 StaticFiles 마운트 및 컨텍스트 주입**

```python
from fastapi.staticfiles import StaticFiles

STATIC_BASE_URL = os.getenv("STATIC_BASE_URL", "/static")

# lifespan 이후, 라우트 정의 전에 추가
# 로컬 개발용: /static → ./static 디렉토리 서빙
# 프로덕션: STATIC_BASE_URL을 S3 URL로 설정하면 마운트 불필요하지만 양쪽 모두 지원
if not STATIC_BASE_URL.startswith("http"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
```

모든 `templates.TemplateResponse` 호출에 `"static_base_url": STATIC_BASE_URL` 추가:

```python
# 예: home 라우트
return templates.TemplateResponse(
    request, "index.html",
    {
        "request": request,
        "chat_history": await get_history(request),
        "username": username,
        "success": True,
        "error": "",
        "static_base_url": STATIC_BASE_URL,  # 추가
    },
)
```

**Step 4: scripts/upload_static_s3.py 작성 (배포 스크립트)**

```python
# scripts/upload_static_s3.py
import os, boto3
from pathlib import Path

s3 = boto3.client(
    "s3",
    region_name=os.environ["AWS_REGION"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
)
BUCKET = os.environ["S3_BUCKET_NAME"]
STATIC_DIR = Path(__file__).parent.parent / "static"

CONTENT_TYPES = {".js": "application/javascript", ".css": "text/css"}

for path in STATIC_DIR.iterdir():
    if path.is_file():
        ct = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        s3.upload_file(
            str(path), BUCKET, path.name,
            ExtraArgs={"ContentType": ct, "ACL": "public-read"},
        )
        print(f"Uploaded: {path.name}")
```

**Step 5: S3 버킷 퍼블릭 읽기 권한 확인**

```bash
# 버킷 생성 (이미 있으면 skip)
aws s3 mb s3://$S3_BUCKET_NAME --region $AWS_REGION

# 퍼블릭 액세스 차단 해제 (정적 파일 호스팅용)
aws s3api put-public-access-block \
  --bucket $S3_BUCKET_NAME \
  --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
```

**Step 6: 정적 파일 S3 업로드**

```bash
python scripts/upload_static_s3.py
```

Expected: `Uploaded: chat.js`

**Step 7: 로컬 → S3 전환 확인**

`.env`에서 `STATIC_BASE_URL=https://<버킷>.s3.ap-northeast-2.amazonaws.com` 설정 후 서버 재기동.  
브라우저 개발자 도구 Network 탭에서 `chat.js`가 S3 URL에서 로드되는지 확인.

**Step 8: 커밋**

```bash
git add static/ templates/index.html app/main.py scripts/
git commit -m "feat: extract inline JS to static/chat.js, serve from S3 in production"
```

---

## Task 7: Dockerfile 및 docker-compose 업데이트

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yaml`

**Step 1: docker-compose.yaml 환경변수 추가**

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - LANGSMITH_API_KEY=${LANGSMITH_API_KEY}
      - ELASTICSEARCH_URL=${ELASTICSEARCH_URL}
      - TAVILY_API_KEY=${TAVILY_API_KEY}
      # MySQL (기존 SQLite/data 볼륨 제거)
      - MYSQL_USER=${MYSQL_USER}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
      - MYSQL_HOST=${MYSQL_HOST}
      - MYSQL_PORT=${MYSQL_PORT}
      - MYSQL_SCHEMA=${MYSQL_SCHEMA}
      # JWT
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - JWT_ALGORITHM=${JWT_ALGORITHM}
      - JWT_EXPIRE_MINUTES=${JWT_EXPIRE_MINUTES}
      # S3
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - AWS_REGION=${AWS_REGION}
      - S3_BUCKET_NAME=${S3_BUCKET_NAME}
      - STATIC_BASE_URL=${STATIC_BASE_URL}
    # volumes: data 볼륨 제거 (SQLite 불필요)
```

**Step 2: Dockerfile에서 data 디렉토리 생성 제거**

기존 Dockerfile에 `RUN mkdir -p data` 같은 줄이 있으면 제거.  
`static/` 디렉토리는 이미지에 포함되도록 COPY 확인:

```dockerfile
COPY static/ ./static/
```

**Step 3: docker-compose 빌드 및 기동 테스트**

```bash
docker compose down
docker compose up --build -d
docker compose logs -f app
```

Expected: MySQL 연결 성공 로그, `checkpoints` 테이블 생성 완료

**Step 4: 커밋**

```bash
git add Dockerfile docker-compose.yaml
git commit -m "chore: update docker config for RDS MySQL, JWT, S3 (remove SQLite volume)"
```

---

## 변경 요약

| 항목 | 로컬/테스트 (`DB_BACKEND=sqlite`) | 프로덕션 (`DB_BACKEND=mysql`) |
|------|------|------|
| Checkpointer | `AsyncSqliteSaver` (기존 그대로) | `AIOMySQLSaver` + `aiomysql` (RDS MySQL, 비동기) |
| User/Thread 저장 | SQLAlchemy ORM → `data/app.db` | SQLAlchemy ORM → RDS MySQL |
| 스키마 관리 | `DB_BACKEND=sqlite alembic upgrade head` | `DB_BACKEND=mysql alembic upgrade head` |
| 인증 | JWT HTTP-only 쿠키 + bcrypt 비밀번호 (동일) | JWT HTTP-only 쿠키 + bcrypt 비밀번호 (동일) |
| 정적 파일 | `STATIC_BASE_URL=/static` (로컬 서빙) | `STATIC_BASE_URL=https://...s3...` (S3) |
| MySQL 환경변수 | 불필요 | 필요 |

## 핵심 주의사항

1. **DB_BACKEND 스위칭 원리** — `db/connection.py`의 `DB_BACKEND` 변수 하나로 SQLAlchemy URL + 엔진 옵션을 분기. `user_repo.py`는 표준 ORM만 사용하므로 양쪽 백엔드에서 코드 변경 없이 동작.
2. **AIOMySQLSaver 필수** — `PyMySQLSaver`(동기)는 `astream_events` 호출 시 `aget_tuple: NotImplementedError` 발생. 반드시 `AIOMySQLSaver` + `aiomysql` 패키지 사용.
3. **Alembic과 checkpoint 테이블 분리** — `checkpoint*` 테이블은 `AIOMySQLSaver.setup()`이 자동 생성하므로 Alembic autogenerate 결과에서 해당 drop/create 구문을 반드시 수동 제거해야 한다.
4. **JWT 만료 처리** — 만료 시 `/login` 리다이렉트. refresh token 로직은 필요 시 추가.
5. **S3 CORS 설정** — 브라우저에서 S3 JS 파일 로드 시 CORS 오류 발생하면 S3 버킷 CORS 정책 추가 필요.
6. **MySQL utf8mb4** — checkpointer 관련 컬럼은 JSON/BLOB으로 저장되므로 collation 충돌 없음. `users`/`user_threads`는 `utf8mb4_unicode_ci` 사용.
7. **bcrypt 직접 사용** — `passlib[bcrypt]`는 bcrypt 4.x와 `__about__.__version__` 호환성 이슈 발생. `bcrypt` 패키지를 직접 사용하여 해결.
8. **Form 필드 기본값** — `Form(...)` (required)는 빈 문자열 POST 시 422를 반환. `Form(default="")` 로 선언하고 라우트 내부에서 `if not username` 로 검증.

---

## 최종 구현 보고 (2026-04-12)

### 완료된 작업

| # | 작업 | 상태 |
|---|------|------|
| 1 | 의존성 추가 (`aiomysql`, `passlib→bcrypt`, `pytest-asyncio`) | 완료 |
| 2 | SQLAlchemy 모델 (`users`, `user_threads`) + `password_hash` 컬럼 | 완료 |
| 3 | Alembic 마이그레이션 2개 적용 (`create_users_and_user_threads`, `add_password_hash_to_users`) | 완료 |
| 4 | `db/connection.py` — `load_dotenv()` + DB_BACKEND 스위칭 | 완료 |
| 5 | `AIOMySQLSaver` + `async with` lifespan (PyMySQLSaver 교체) | 완료 |
| 6 | JWT HTTP-only 쿠키 인증 (SessionMiddleware 제거) | 완료 |
| 7 | 비밀번호 가입/로그인 (`/register`, `/login`) + bcrypt 해시 | 완료 |
| 8 | S3 정적 파일 분기 (`STATIC_BASE_URL`) + `scripts/upload_static_s3.py` | 완료 |
| 9 | `event_generator` 전체 try/except 로 SSE 오류 가시성 확보 | 완료 |
| 10 | 테스트 35개 전부 통과 (in-memory SQLite, MySQL 불필요) | 완료 |

### 트러블슈팅 이력

| 오류 | 원인 | 해결 |
|------|------|------|
| `sqlite3.OperationalError: near "ALTER"` | `Column(DateTime)` nullable 기본값이 True → Alembic이 ALTER COLUMN 생성 | `nullable=False` 명시, 잘못된 마이그레이션 삭제 후 재생성 |
| `aget_tuple: NotImplementedError` | `PyMySQLSaver`(동기)를 `astream_events`(비동기)와 혼용 | `AIOMySQLSaver` + `aiomysql`으로 교체 |
| `KeyError: MYSQL_USER` on startup | `db/connection.py`에 `load_dotenv()` 누락 → `.env` 미로드 | `load_dotenv()` 추가 |
| 스트리밍 연결 오류 (onerror) | `event_generator` 내 try/except가 일부 예외를 미처리 | generator 전체를 try/except로 감싸고 traceback 출력 |
| `ValueError: password cannot be longer than 72 bytes` | `passlib` + `bcrypt 4.x` 호환성 이슈 (`__about__` 제거) | `passlib` 제거, `bcrypt` 직접 사용 |
| 테스트 `422 Unprocessable Entity` | `Form(...)` required 필드에 빈 문자열 POST 시 FastAPI가 거부 | `Form(default="")` 로 변경, 라우트 내부 검증 |
| 테스트 `load_dotenv` mock 실패 | `importlib.reload` 시 `from dotenv import load_dotenv` 재실행으로 mock 덮어씌워짐 | `patch("dotenv.load_dotenv")` 로 원본 패치 |

### 최종 테스트 결과

```
35 passed in 7.56s
```

| 파일 | 테스트 수 | 내용 |
|------|-----------|------|
| `test_jwt.py` | 10 | JWT 생성/검증/쿠키 읽기 |
| `test_user_repo.py` | 8 | 회원가입·로그인·thread_id CRUD |
| `test_db_connection.py` | 4 | DB_BACKEND 스위칭 |
| `test_routes.py` | 13 | 로그인·회원가입·보호 라우트·API (async) |

### 추가 작업 (2026-04-12)

#### 0. 가상환경 설정

```bash
# .venv 생성
python -m venv .venv

# .gitignore에 추가 (이미 반영됨)
# .venv/
# venv/

# 패키지 설치
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux
```

#### 1. supervisor_graph.py — rag_worker 제거

Elasticsearch RAG 워커를 제거하고 MCP 워커 중심으로 단순화했다.

**변경 전:**
- `RouteResponse.next_worker`: `"rag_worker" | "mcp_worker" | "clarify_worker" | "dummy_worker"`
- SUPERVISOR_PROMPT에 `rag_worker` 라우팅 조건 포함
- `rag_node` 함수 및 `rag_worker` 노드 존재

**변경 후:**
- `RouteResponse.next_worker`: `"mcp_worker" | "clarify_worker" | "dummy_worker"`
- SUPERVISOR_PROMPT에서 `rag_worker` 항목 제거
- `rag_node`, `rag_worker` 노드 및 `from rag.retriever_tool import retriever_tool` import 제거
- `route_to_worker` fallback이 기존 `rag_worker` → `mcp_worker`로 변경

```python
# 변경 후 SUPERVISOR_PROMPT
SUPERVISOR_PROMPT = """당신은 스마트 라우터(Supervisor)입니다.
- "mcp_worker": 날씨, 주식 정보 등 실시간 외부 데이터를 요구하거나, 외부 정보에 대한 검색이 필요할 때
- "clarify_worker": 질문이 너무 짧거나 모호해서 어떤 워커로 보낼지 확신이 없을 때
- "dummy_worker": 사용자가 **짱구**라는 단어를 말했을 때
사용자의 질문을 분석하여 다음 세 워커 중 하나에게 질문을 전달하세요:
답변을 직접 생성하지 말고, 반드시 워커 역할 중 하나를 골라 next_worker 필드로 출력하세요.
"""

class RouteResponse(TypedDict):
    next_worker: Literal["mcp_worker", "clarify_worker", "dummy_worker"]
```

---

### S3 정적 파일 전환 방법 (미완료 — 배포 시 수행)

현재 `STATIC_BASE_URL=/static` (로컬 서빙)으로 동작 중. S3로 전환하려면:

```bash
# 1. .env에 추가
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=<버킷명>
STATIC_BASE_URL=https://<버킷명>.s3.ap-northeast-2.amazonaws.com

# 2. S3 버킷 퍼블릭 접근 허용
aws s3api put-public-access-block \
  --bucket <버킷명> \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"

# 3. 파일 업로드 (chat.js → S3)
python scripts/upload_static_s3.py

# 4. 서버 재시작 → STATIC_BASE_URL이 https://로 시작하면 자동으로 S3 URL 사용
```
