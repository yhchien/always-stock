"""
tests for backend/app/database.py
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import database
from app.database import engine, get_db, DATABASE_URL


def test_database_url_is_sqlite():
    assert DATABASE_URL.startswith("sqlite:///")


def test_engine_connects():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_get_db_yields_session():
    gen = get_db()
    session = next(gen)
    assert isinstance(session, Session)
    try:
        next(gen)
    except StopIteration:
        pass  # 正常結束，session 已被 close


def test_get_db_closes_on_exit(monkeypatch):
    """驗證 get_db 的 finally block 會呼叫 session.close()."""
    closed = {"value": False}

    class FakeSession:
        def close(self):
            closed["value"] = True

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeSession())

    gen = database.get_db()
    session = next(gen)
    assert isinstance(session, FakeSession)
    try:
        next(gen)
    except StopIteration:
        pass

    assert closed["value"] is True
