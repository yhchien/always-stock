"""
tests for backend/app/database.py
"""
import os
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

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


def test_get_db_closes_on_exit():
    """驗證 get_db 的 finally block 一定會關閉 session。"""
    gen = get_db()
    session = next(gen)
    assert session.is_active
    try:
        next(gen)
    except StopIteration:
        pass
    # 關閉後 session 不應再 active
    assert not session.is_active
