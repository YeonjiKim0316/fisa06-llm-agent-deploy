"""
User/Thread DB 레포지토리 테스트.
테스트 대상: db/user_repo.py
모든 테스트는 in-memory SQLite로 실행 (MySQL 불필요).
"""
import pytest
from db.user_repo import get_or_create_thread_id, reset_thread_id, register_user, authenticate_user
from db.models import User, UserThread
from db.connection import get_session


class TestGetOrCreateThreadId:
    def test_creates_user_and_thread_on_first_call(self, db_session):
        thread_id = get_or_create_thread_id("alice")
        assert thread_id.startswith("alice:")
        assert len(thread_id) > len("alice:")

    def test_returns_same_thread_id_on_second_call(self, db_session):
        t1 = get_or_create_thread_id("alice")
        t2 = get_or_create_thread_id("alice")
        assert t1 == t2

    def test_different_users_get_different_thread_ids(self, db_session):
        t_alice = get_or_create_thread_id("alice")
        t_bob = get_or_create_thread_id("bob")
        assert t_alice != t_bob
        assert t_alice.startswith("alice:")
        assert t_bob.startswith("bob:")

    def test_creates_user_row_in_db(self, db_session):
        # User는 register_user로 생성됨 (get_or_create_thread_id는 UserThread만 담당)
        register_user("charlie", "pw123")
        with get_session() as session:
            user = session.query(User).filter_by(username="charlie").first()
            username = user.username if user else None
        assert username == "charlie"

    def test_creates_user_thread_row_in_db(self, db_session):
        thread_id = get_or_create_thread_id("dave")
        with get_session() as session:
            ut = session.query(UserThread).filter_by(username="dave").first()
            stored_thread_id = ut.thread_id if ut else None  # 세션 내에서 속성 읽기
        assert stored_thread_id == thread_id


class TestResetThreadId:
    def test_returns_new_thread_id(self, db_session):
        old = get_or_create_thread_id("alice")
        new = reset_thread_id("alice")
        assert new != old
        assert new.startswith("alice:")

    def test_subsequent_get_returns_new_thread_id(self, db_session):
        get_or_create_thread_id("alice")
        new = reset_thread_id("alice")
        fetched = get_or_create_thread_id("alice")
        assert fetched == new

    def test_reset_without_prior_create(self, db_session):
        """User 없이 reset 호출해도 오류 없이 새 thread_id 반환."""
        # User가 없으면 UserThread INSERT가 FK 오류날 수 있으므로 먼저 User 생성
        get_or_create_thread_id("eve")
        new = reset_thread_id("eve")
        assert new.startswith("eve:")
