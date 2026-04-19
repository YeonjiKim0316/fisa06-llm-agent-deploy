# LLM Agent Chatbot — FastAPI + LangGraph + AWS

FastAPI와 LangGraph Supervisor 패턴 기반의 멀티 에이전트 챗봇입니다.
Elasticsearch RAG, MCP 툴(날씨/주식/검색/위키), JWT 인증, AWS RDS MySQL + S3를 사용합니다.

---

## 아키텍처

```
사용자 브라우저
    │  (SSE 스트리밍)
    ▼
FastAPI (app/main.py)
    │  JWT 쿠키 인증
    │  thread_id 관리
    ▼
LangGraph Supervisor Graph
    ├── supervisor_node  ← 질의 분석 후 워커 라우팅
    ├── mcp_worker       ← 날씨/주식/Tavily/위키피디아 (MCP)
    ├── clarify_worker   ← 모호한 질문 재질문 요청
    └── dummy_worker     ← "짱구" 키워드 처리
    │
    ▼ checkpointer
[로컬] AsyncSqliteSaver      (data/checkpoints.sqlite)
[프로덕션] AIOMySQLSaver     (AWS RDS MySQL)

DB (사용자/스레드 관리)
[로컬] SQLite                (data/app.db)
[프로덕션] AWS RDS MySQL

정적 파일
[로컬] /static 경로 직접 서빙
[프로덕션] AWS S3 버킷
```

### Supervisor 라우팅 규칙

| 워커 | 조건 |
|------|------|
| `mcp_worker` | 날씨·주식 등 실시간 데이터, Tavily 검색, 위키피디아 검색 |
| `clarify_worker` | 질문이 너무 짧거나 모호한 경우 |
| `dummy_worker` | "짱구" 단어 포함 시 |

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 웹 프레임워크 | FastAPI, Uvicorn |
| AI 오케스트레이션 | LangGraph (Supervisor), LangChain |
| LLM | OpenAI GPT-4o-mini |
| RAG | Elasticsearch + OpenAI Embeddings |
| 외부 툴 | MCP (날씨/주식 HF Space, Tavily, Wikipedia) |
| 인증 | JWT HTTP-only 쿠키, bcrypt 비밀번호 해싱 |
| 대화 영속성 | LangGraph Checkpointer (SQLite / MySQL) |
| DB ORM | SQLAlchemy + Alembic |
| 정적 파일 | AWS S3 |
| 클라우드 DB | AWS RDS MySQL |
| 컨테이너 | Docker, Docker Compose |
| 테스트 | pytest, pytest-asyncio, httpx |

---

## 프로젝트 구조

```
fisa06-fastapi-aws/
├── app/
│   └── main.py              # FastAPI 앱, 라우트, SSE 스트리밍, JWT 인증
├── agent/
│   └── supervisor_graph.py  # LangGraph Supervisor 멀티 에이전트 그래프
├── rag/
│   └── retriever_tool.py    # Elasticsearch 벡터 검색 툴
├── db/
│   ├── connection.py        # SQLAlchemy 엔진, DB_BACKEND 스위칭
│   ├── models.py            # User, UserThread ORM 모델
│   └── user_repo.py         # 사용자 등록/인증, 스레드 ID 관리
├── auth/
│   └── jwt_handler.py       # JWT 토큰 생성/검증, 쿠키 인증
├── templates/
│   ├── index.html           # 챗봇 메인 화면
│   └── login.html           # 로그인/회원가입 화면
├── static/
│   └── chat.js              # 프론트엔드 SSE 클라이언트
├── alembic/
│   └── versions/            # DB 마이그레이션 파일
├── scripts/
│   └── upload_static_s3.py  # S3 정적 파일 업로드 스크립트
├── tests/                   # pytest 테스트 (35개)
├── docker-compose.yaml
├── Dockerfile
├── requirements.txt
├── pytest.ini
└── .env                     # 환경변수 (git 제외)
```

---

## 환경변수

`.env` 파일 또는 컨테이너 환경변수로 설정합니다.

### 공통 필수

```env
OPENAI_API_KEY=sk-...
ELASTICSEARCH_URL=http://localhost:9200
```

### 선택 (기능 활성화)

```env
LANGSMITH_API_KEY=...            # LangSmith 추적 (없으면 비활성화)
TAVILY_API_KEY=...               # Tavily 웹 검색 MCP 툴
```

### JWT 인증

```env
JWT_SECRET_KEY=your-secret-key   # 프로덕션에서 반드시 변경
JWT_ALGORITHM=HS256              # 기본값
JWT_EXPIRE_MINUTES=1440          # 기본값 (24시간)
```

### DB 백엔드 스위칭

```env
DB_BACKEND=sqlite   # 로컬/테스트 (기본값)
# DB_BACKEND=mysql  # 프로덕션 (RDS)
```

### RDS MySQL (DB_BACKEND=mysql 일 때만 필요)

```env
MYSQL_USER=admin
MYSQL_PASSWORD=your-password
MYSQL_HOST=your-rds-endpoint.rds.amazonaws.com
MYSQL_PORT=3306
MYSQL_SCHEMA=your_db_name
```

### S3 정적 파일 (프로덕션)

```env
STATIC_BASE_URL=https://your-bucket.s3.ap-northeast-2.amazonaws.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-northeast-2
S3_BUCKET_NAME=your-bucket
```

> `STATIC_BASE_URL`이 `http`로 시작하면 FastAPI `/static` 마운트를 건너뛰고 S3 URL을 직접 사용합니다.

---

## 로컬 실행 (SQLite 모드)

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. .env 설정 (최소)
echo "OPENAI_API_KEY=sk-..." > .env
echo "DB_BACKEND=sqlite" >> .env
echo "ELASTICSEARCH_URL=http://localhost:9200" >> .env

# 3. Elasticsearch 실행 (Docker)
docker compose up -d elasticsearch

# 4. 문서 인덱싱 (선택)
python rag/ingest.py

# 5. 서버 실행
uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000` 접속 후 회원가입 → 로그인하여 사용합니다.

---

## 프로덕션 배포 (MySQL + S3 모드)

### 1. RDS MySQL 스키마 초기화

```bash
# .env에 DB_BACKEND=mysql 및 MySQL 접속 정보 설정 후
alembic upgrade head
```

### 2. S3 정적 파일 업로드

```bash
python scripts/upload_static_s3.py
```

S3 버킷에 퍼블릭 읽기 정책이 적용되어 있어야 합니다.

### 3. Docker Compose로 실행

```bash
# .env에 모든 환경변수 설정 후
docker compose up --build -d api
```

---

## Docker Compose 구성

```yaml
services:
  elasticsearch:   # RAG 벡터 DB (포트 9200)
  kibana:          # ES 모니터링 UI (포트 5601)
  api:             # FastAPI 앱 (포트 8000)
```

### API 컨테이너만 재빌드

```bash
docker compose down api && docker compose up --build -d api
```

---

## 테스트

```bash
pytest tests/ -v
```

총 35개 테스트:

| 파일 | 내용 |
|------|------|
| `test_routes.py` | 로그인·회원가입·로그아웃·채팅 라우트, 중복 가입, 잘못된 비밀번호 등 |
| `test_user_repo.py` | `register_user`, `authenticate_user`, `get_or_create_thread_id`, `reset_thread_id` |
| `test_db_connection.py` | `DB_BACKEND` SQLite/MySQL URL 생성, 환경변수 누락 처리 |
| `test_jwt.py` | 토큰 생성·검증, 만료·변조 토큰 거부 |

---

## 인증 흐름

```
회원가입 (/register POST)
  → bcrypt로 비밀번호 해시 → RDS users 테이블 저장
  → JWT 토큰 생성 → HTTP-only 쿠키 발급 → / 리다이렉트

로그인 (/login POST)
  → DB에서 password_hash 조회 → bcrypt 검증
  → JWT 토큰 생성 → HTTP-only 쿠키 발급 → / 리다이렉트

인증된 요청
  → 쿠키에서 JWT 추출 → 서명 검증 → username 추출
  → username으로 thread_id 조회/생성 → LangGraph 대화 이력 복원
```

---

## 대화 이력 관리

- 사용자별 `thread_id`가 `user_threads` 테이블에 저장됩니다.
- 재로그인 후에도 동일 `thread_id`를 통해 이전 대화가 복원됩니다.
- `/clear-chat` 호출 시 새 `thread_id`가 발급되어 대화가 초기화됩니다.
- LangGraph Checkpointer가 `thread_id` 기준으로 메시지 상태를 영속 저장합니다.

---

## AWS 인프라 요약

| 서비스 | 용도 |
|--------|------|
| EC2 | FastAPI + Docker 컨테이너 실행 |
| RDS MySQL | users/user_threads 테이블, LangGraph 체크포인트 |
| S3 | chat.js 등 정적 파일 호스팅 |
| Elasticsearch (Docker) | RAG 벡터 인덱스 |
