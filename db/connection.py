import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from dotenv import load_dotenv

## [클라우드 마이그레이션] alembic CLI 등 직접 실행 시에도 .env를 자동으로 읽도록 추가
load_dotenv()

## [클라우드 마이그레이션] DB_BACKEND 환경변수 하나로 로컬(SQLite) ↔ 프로덕션(RDS MySQL) 전환
# DB_BACKEND=sqlite  → 로컬/테스트: data/app.db 사용, MySQL 환경변수 불필요
# DB_BACKEND=mysql   → 프로덕션: MYSQL_* 환경변수로 RDS 연결
DB_BACKEND: str = os.getenv("DB_BACKEND", "sqlite")


def get_db_url() -> str:
    """SQLAlchemy 엔진용 URL. DB_BACKEND에 따라 자동 분기."""
    if DB_BACKEND == "mysql":
        ## [클라우드 마이그레이션] RDS MySQL 연결 URL 구성
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
        ## [클라우드 마이그레이션] pool_pre_ping: 끊긴 연결 자동 감지 / pool_recycle: 30분마다 재연결
        return create_engine(url, pool_pre_ping=True, pool_recycle=1800)
    # SQLite: 멀티스레드 허용 (FastAPI 동기 함수 지원)
    return create_engine(url, connect_args={"check_same_thread": False})


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Session:
    """with get_session() as session: 패턴으로 사용. SQLite/MySQL 양쪽 동일."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
