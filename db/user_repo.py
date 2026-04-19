from uuid import uuid4
import bcrypt
from db.connection import get_session
from db.models import User, UserThread

## [클라우드 마이그레이션] JWT 인증 강화 — bcrypt로 비밀번호 해시/검증
# bcrypt 패키지 직접 사용 (passlib 4.x 호환성 이슈 회피)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


## [클라우드 마이그레이션] 회원가입: username + bcrypt 해시 비밀번호를 RDS MySQL에 저장
def register_user(username: str, password: str) -> bool:
    """신규 가입. username 중복이면 False 반환."""
    with get_session() as session:
        if session.query(User).filter_by(username=username).first():
            return False
        session.add(User(username=username, password_hash=hash_password(password)))
    return True


## [클라우드 마이그레이션] 로그인 검증: DB에서 bcrypt 해시와 비교
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
