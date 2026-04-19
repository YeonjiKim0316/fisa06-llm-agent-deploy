import os
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import Request

## [클라우드 마이그레이션] Starlette SessionMiddleware 제거 후 JWT HTTP-only 쿠키로 인증 교체
# 기존: request.session["username"] 으로 서버 세션 저장
# 변경: JWT를 access_token 쿠키에 저장 → 서버 세션 불필요, 수평 확장 가능
# python-jose[cryptography] 패키지 필요
SECRET_KEY  = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-me")
ALGORITHM   = os.environ.get("JWT_ALGORITHM", "HS256")
EXPIRE_MIN  = int(os.environ.get("JWT_EXPIRE_MINUTES", 1440))
COOKIE_NAME = "access_token"


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=EXPIRE_MIN)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    """유효한 토큰이면 username 반환, 아니면 None."""
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
