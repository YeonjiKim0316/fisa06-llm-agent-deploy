"""
db/connection.py DB_BACKEND 스위칭 테스트.
"""
import os
import pytest
from unittest.mock import patch


class TestDbBackendSwitching:
    def test_default_backend_is_sqlite(self, monkeypatch):
        monkeypatch.delenv("DB_BACKEND", raising=False)
        import importlib
        import db.connection as conn
        # load_dotenv()가 .env(DB_BACKEND=mysql)를 다시 읽지 않도록 mock
        with patch("dotenv.load_dotenv"):
            importlib.reload(conn)
        assert conn.DB_BACKEND == "sqlite"

    def test_sqlite_url_uses_data_path(self, monkeypatch):
        monkeypatch.setenv("DB_BACKEND", "sqlite")
        import importlib
        import db.connection as conn
        importlib.reload(conn)
        url = conn.get_db_url()
        assert "sqlite" in url
        assert "data/app.db" in url

    def test_mysql_url_requires_env_vars(self, monkeypatch):
        monkeypatch.setenv("DB_BACKEND", "mysql")
        monkeypatch.setenv("MYSQL_USER", "user")
        monkeypatch.setenv("MYSQL_PASSWORD", "pass")
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.setenv("MYSQL_PORT", "3306")
        monkeypatch.setenv("MYSQL_SCHEMA", "testdb")
        import importlib
        import db.connection as conn
        importlib.reload(conn)
        url = conn.get_db_url()
        assert "mysql+pymysql" in url
        assert "user:pass@localhost:3306/testdb" in url
        assert "charset=utf8mb4" in url

    def test_mysql_url_missing_env_raises(self, monkeypatch):
        monkeypatch.setenv("DB_BACKEND", "mysql")
        monkeypatch.delenv("MYSQL_USER", raising=False)
        import importlib
        import db.connection as conn
        # load_dotenv()가 .env에서 MYSQL_USER를 다시 불러오지 않도록 mock
        with patch("dotenv.load_dotenv"):
            with pytest.raises(KeyError):
                importlib.reload(conn)
