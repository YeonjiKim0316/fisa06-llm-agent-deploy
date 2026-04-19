"""
FastAPI 라우트 통합 테스트.
- LangGraph agent는 mock 처리 (실제 LLM 호출 없음)
- DB는 in-memory SQLite
- JWT 쿠키 흐름 검증
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


# ── 환경변수는 conftest.py에서 설정됨 ──────────────────────────────

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def mock_agent_app():
    """LangGraph agent 전체를 mock 처리."""
    mock = AsyncMock()
    mock.aget_state = AsyncMock(return_value=MagicMock(values={"messages": []}))
    mock.ainvoke = AsyncMock(return_value={"messages": []})
    return mock


@pytest_asyncio.fixture()
async def client(db_session, mock_agent_app):
    """
    테스트용 FastAPI 클라이언트.
    - app.state.agent_app을 mock으로 교체
    - DB는 in-memory SQLite (conftest db_session fixture가 패치)
    """
    with patch("app.main.create_agent_app", new=AsyncMock(return_value=mock_agent_app)):
        from app.main import app
        app.state.agent_app = mock_agent_app
        app.state.checkpointer = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# 테스트용 로그인 헬퍼 (register 후 login)
async def _register_and_login(client, username="testuser", password="pw123"):
    await client.post("/register", data={"username": username, "password": password})
    return await client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


class TestLoginLogout:
    async def test_login_redirects_to_home(self, client):
        await client.post("/register", data={"username": "testuser", "password": "pw123"})
        response = await client.post("/login", data={"username": "testuser", "password": "pw123"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    async def test_login_sets_jwt_cookie(self, client):
        await client.post("/register", data={"username": "testuser", "password": "pw123"})
        response = await client.post("/login", data={"username": "testuser", "password": "pw123"}, follow_redirects=False)
        assert "access_token" in response.cookies

    async def test_empty_username_returns_login_page(self, client):
        response = await client.post("/login", data={"username": "", "password": ""})
        assert response.status_code == 200
        assert "아이디와 비밀번호를 입력하세요" in response.text

    async def test_wrong_password_returns_error(self, client):
        await client.post("/register", data={"username": "testuser", "password": "correct"})
        response = await client.post("/login", data={"username": "testuser", "password": "wrong"})
        assert response.status_code == 200
        assert "올바르지 않습니다" in response.text

    async def test_logout_deletes_cookie(self, client):
        await _register_and_login(client)
        response = await client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        cookie_val = response.cookies.get("access_token", "")
        assert cookie_val == ""


class TestRegister:
    async def test_register_redirects_to_home(self, client):
        response = await client.post("/register", data={"username": "newuser", "password": "pw123"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    async def test_duplicate_register_returns_error(self, client):
        await client.post("/register", data={"username": "dup", "password": "pw123"})
        response = await client.post("/register", data={"username": "dup", "password": "pw123"})
        assert response.status_code == 200
        assert "이미 존재하는 아이디" in response.text


class TestProtectedRoutes:
    async def test_home_without_auth_redirects_to_login(self, client):
        response = await client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert "/login" in response.headers["location"]

    async def test_home_with_auth_returns_200(self, client):
        await _register_and_login(client)
        response = await client.get("/")
        assert response.status_code == 200

    async def test_chat_history_without_auth_returns_empty(self, client):
        response = await client.get("/chat-history")
        assert response.status_code == 200
        assert response.json() == {"chat_history": []}

    async def test_clear_chat_without_auth_returns_401(self, client):
        response = await client.post("/clear-chat")
        assert response.status_code == 401

    async def test_clear_chat_with_auth_returns_ok(self, client):
        await _register_and_login(client)
        response = await client.post("/clear-chat")
        assert response.status_code == 200
        assert response.json()["ok"] is True


class TestApiHealth:
    async def test_api_endpoint_returns_running(self, client):
        response = await client.get("/api")
        assert response.status_code == 200
        assert "running" in response.json()["message"]
