"""
공유 pytest fixtures.

DB_BACKEND=sqlite (기본값)로 in-memory SQLite를 사용하여
MySQL 없이 로컬에서 모든 테스트를 실행할 수 있다.
"""
import os
import pytest

# 테스트 전에 환경변수 설정 (다른 모듈 임포트 전에 반드시 먼저)
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("STATIC_BASE_URL", "/static")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base
import db.connection as db_conn


@pytest.fixture(scope="function")
def db_engine(tmp_path):
    """테스트 함수마다 격리된 in-memory SQLite 엔진."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine, monkeypatch):
    """db.connection.engine을 테스트용 in-memory 엔진으로 교체."""
    TestSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_conn, "engine", db_engine)
    monkeypatch.setattr(db_conn, "SessionLocal", TestSession)
    yield TestSession
