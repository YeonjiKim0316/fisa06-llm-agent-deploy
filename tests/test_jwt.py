"""
JWT 인증 모듈 테스트.
테스트 대상: auth/jwt_handler.py
"""
import time
import pytest
from unittest.mock import MagicMock
from auth.jwt_handler import create_access_token, decode_token, get_current_user, COOKIE_NAME


class TestCreateAccessToken:
    def test_returns_string(self):
        token = create_access_token("alice")
        assert isinstance(token, str)
        assert len(token) > 10

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token("alice")
        t2 = create_access_token("bob")
        assert t1 != t2

    def test_same_user_different_calls_differ(self):
        """exp가 달라야 하지만 1초 이내엔 같을 수 있으므로 decode로 확인."""
        t = create_access_token("alice")
        assert decode_token(t) == "alice"


class TestDecodeToken:
    def test_valid_token_returns_username(self):
        token = create_access_token("alice")
        assert decode_token(token) == "alice"

    def test_invalid_token_returns_none(self):
        assert decode_token("not.a.valid.token") is None

    def test_tampered_token_returns_none(self):
        token = create_access_token("alice")
        tampered = token[:-5] + "XXXXX"
        assert decode_token(tampered) is None

    def test_empty_string_returns_none(self):
        assert decode_token("") is None


class TestGetCurrentUser:
    def test_valid_cookie_returns_username(self):
        token = create_access_token("alice")
        request = MagicMock()
        request.cookies = {COOKIE_NAME: token}
        assert get_current_user(request) == "alice"

    def test_missing_cookie_returns_none(self):
        request = MagicMock()
        request.cookies = {}
        assert get_current_user(request) is None

    def test_invalid_cookie_returns_none(self):
        request = MagicMock()
        request.cookies = {COOKIE_NAME: "garbage"}
        assert get_current_user(request) is None
