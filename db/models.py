from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

## [클라우드 마이그레이션] SQLAlchemy ORM 모델 — Mapped/mapped_column 미사용, 고전 Column() 스타일
# 스키마 변경은 alembic revision --autogenerate 후 alembic upgrade head 로 반영
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(255), unique=True, nullable=False, index=True)
    ## [클라우드 마이그레이션] JWT 인증 강화: 비밀번호를 bcrypt 해시로 저장 (평문 저장 금지)
    password_hash = Column(String(255), nullable=False)
    ## [클라우드 마이그레이션] nullable=False 필수 — 생략 시 Alembic이 ALTER COLUMN 마이그레이션을 생성해 SQLite에서 오류 발생
    created_at    = Column(DateTime, nullable=False, server_default=func.now())

    thread = relationship(
        "UserThread", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserThread(Base):
    __tablename__ = "user_threads"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    username   = Column(String(255), ForeignKey("users.username", ondelete="CASCADE"), unique=True, nullable=False)
    ## [클라우드 마이그레이션] thread_id 형식: "{username}:{uuid4().hex}" — LangGraph checkpointer 키로 사용
    thread_id  = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="thread")
