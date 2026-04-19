# 테스트 보고서 — AWS Migration (RDS MySQL + S3 + JWT)

## 실행 결과

```
22 passed, 4 warnings in 0.84s
```

모든 테스트는 **MySQL 없이 in-memory SQLite** 로 실행됩니다.

---

## 실행 방법

### 기본 실행 (SQLite 로컬)

```bash
DB_BACKEND=sqlite pytest tests/ -v
```

### 특정 파일만 실행

```bash
pytest tests/test_jwt.py -v          # JWT 인증 테스트
pytest tests/test_user_repo.py -v    # DB 레포지토리 테스트
pytest tests/test_db_connection.py -v # DB 연결 스위칭 테스트
pytest tests/test_routes.py -v       # FastAPI 라우트 통합 테스트 (비동기)
```

### 라우트 테스트 (asyncio 모드 필요)

```bash
pytest tests/test_routes.py -v --asyncio-mode=auto
```

---

## 테스트 파일 구조

```
tests/
├── conftest.py          # 공유 fixtures (in-memory SQLite DB, monkeypatch)
├── test_jwt.py          # JWT 인증 단위 테스트
├── test_user_repo.py    # User/Thread DB 레포지토리 단위 테스트
├── test_db_connection.py # DB_BACKEND 스위칭 단위 테스트
└── test_routes.py       # FastAPI 라우트 통합 테스트 (agent mock)
```

---

## 테스트 커버리지

### `tests/test_jwt.py` — 10개 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| `test_returns_string` | `create_access_token`이 문자열 토큰 반환 |
| `test_different_users_get_different_tokens` | 다른 username → 다른 토큰 |
| `test_same_user_different_calls_differ` | 토큰 디코딩으로 username 복원 확인 |
| `test_valid_token_returns_username` | 정상 토큰 → username 반환 |
| `test_invalid_token_returns_none` | 잘못된 형식 → None |
| `test_tampered_token_returns_none` | 위변조 토큰 → None |
| `test_empty_string_returns_none` | 빈 문자열 → None |
| `test_valid_cookie_returns_username` | 쿠키에서 JWT 읽어 username 반환 |
| `test_missing_cookie_returns_none` | 쿠키 없음 → None |
| `test_invalid_cookie_returns_none` | 잘못된 쿠키 → None |

### `tests/test_user_repo.py` — 8개 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| `test_creates_user_and_thread_on_first_call` | 최초 호출 시 `username:uuid` 형식 thread_id 생성 |
| `test_returns_same_thread_id_on_second_call` | 동일 username 재호출 시 동일 thread_id 반환 |
| `test_different_users_get_different_thread_ids` | 다른 user → 다른 thread_id |
| `test_creates_user_row_in_db` | `users` 테이블에 row 생성 확인 |
| `test_creates_user_thread_row_in_db` | `user_threads` 테이블에 row 생성 확인 |
| `test_returns_new_thread_id` | `reset_thread_id` 호출 후 새 thread_id 반환 |
| `test_subsequent_get_returns_new_thread_id` | reset 후 get 시 새 thread_id 조회 |
| `test_reset_without_prior_create` | 기존 User 없이 reset 호출 정상 동작 |

### `tests/test_db_connection.py` — 4개 테스트

| 테스트 | 검증 내용 |
|--------|----------|
| `test_default_backend_is_sqlite` | `DB_BACKEND` 미설정 시 `"sqlite"` 기본값 |
| `test_sqlite_url_uses_data_path` | SQLite URL이 `data/app.db` 포함 |
| `test_mysql_url_requires_env_vars` | MySQL URL에 host/port/schema 포함 여부 |
| `test_mysql_url_missing_env_raises` | MySQL 환경변수 누락 시 `KeyError` 발생 |

### `tests/test_routes.py` — 9개 테스트 (비동기, agent mock)

| 테스트 | 검증 내용 |
|--------|----------|
| `test_login_redirects_to_home` | POST /login → 303 리다이렉트 |
| `test_login_sets_jwt_cookie` | 로그인 후 `access_token` 쿠키 설정 |
| `test_empty_username_returns_login_page` | 빈 username → 로그인 페이지 재렌더링 |
| `test_logout_deletes_cookie` | 로그아웃 후 쿠키 삭제 |
| `test_home_without_auth_redirects_to_login` | 미인증 GET / → /login 리다이렉트 |
| `test_home_with_auth_returns_200` | JWT 쿠키 있으면 홈 페이지 200 |
| `test_chat_history_without_auth_returns_empty` | 미인증 /chat-history → 빈 배열 |
| `test_clear_chat_without_auth_returns_401` | 미인증 /clear-chat → 401 |
| `test_clear_chat_with_auth_returns_ok` | 인증 후 /clear-chat → `{"ok": true}` |
| `test_api_endpoint_returns_running` | GET /api → `{"message": "...running"}` |

---

## 테스트 설계 원칙

### in-memory SQLite (MySQL 불필요)

- `conftest.py`의 `db_engine` fixture가 매 테스트마다 `:memory:` SQLite DB 생성
- `monkeypatch`로 `db.connection.engine`과 `SessionLocal`을 교체
- `DB_BACKEND=sqlite`로 실행 → MySQL 환경변수 없이 동작

### LangGraph Agent Mock

- `test_routes.py`는 `app.main.create_agent_app`을 `AsyncMock`으로 패치
- 실제 LLM API 호출 없이 라우트 인증/리다이렉트/응답 코드만 검증

### 격리 원칙

- 각 테스트 함수마다 DB가 새로 생성/소멸 (`scope="function"`)
- 테스트 간 데이터 오염 없음

---

## 프로덕션 전환 시 추가 테스트

MySQL RDS에 연결 가능한 환경에서는 아래를 추가로 검증:

```bash
# Alembic 마이그레이션 적용
DB_BACKEND=mysql alembic upgrade head

# MySQL 연결 확인
python -c "
import os
os.environ['DB_BACKEND'] = 'mysql'
import importlib, db.connection as c; importlib.reload(c)
from sqlalchemy import inspect
print(inspect(c.engine).get_table_names())
"

# 통합 테스트 (실제 DB)
DB_BACKEND=mysql pytest tests/ -v
```

---

## 알려진 경고

```
jose/jwt.py:311: DeprecationWarning: datetime.datetime.utcnow() is deprecated
```

`python-jose` 라이브러리 내부에서 발생하는 경고. 테스트 동작에 영향 없음.  
해결: `python-jose` 업데이트 또는 `cryptography` 기반 대안(`PyJWT`) 전환 시 해소.
