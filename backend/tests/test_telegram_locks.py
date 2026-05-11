"""locks.py 測試：互斥、釋放、timeout 過期。"""
from datetime import datetime, timedelta

import pytest

from app.telegram import locks as telegram_locks


@pytest.fixture(autouse=True)
def _reset_locks():
    telegram_locks._reset_all_for_tests()
    yield
    telegram_locks._reset_all_for_tests()


def test_try_acquire_returns_true_for_fresh_chat():
    assert telegram_locks.try_acquire(123) is True


def test_try_acquire_returns_false_when_already_locked():
    assert telegram_locks.try_acquire(123) is True
    assert telegram_locks.try_acquire(123) is False


def test_try_acquire_different_chats_independent():
    assert telegram_locks.try_acquire(111) is True
    assert telegram_locks.try_acquire(222) is True


def test_release_lets_acquire_again():
    assert telegram_locks.try_acquire(123) is True
    telegram_locks.release(123)
    assert telegram_locks.try_acquire(123) is True


def test_release_unknown_chat_is_safe():
    # 重複呼叫不應 raise
    telegram_locks.release(999)
    telegram_locks.release(999)


def test_is_locked_reflects_state():
    assert telegram_locks.is_locked(123) is False
    telegram_locks.try_acquire(123)
    assert telegram_locks.is_locked(123) is True
    telegram_locks.release(123)
    assert telegram_locks.is_locked(123) is False


def test_expired_lock_can_be_reacquired(monkeypatch):
    # 直接把 _locks 內的時間改成超過 timeout
    telegram_locks.try_acquire(123)
    expired_time = datetime.utcnow() - timedelta(seconds=telegram_locks.LOCK_TIMEOUT_SECONDS + 60)
    telegram_locks._locks[123] = expired_time
    assert telegram_locks.is_locked(123) is False  # 視為釋放
    assert telegram_locks.try_acquire(123) is True  # 可重新拿
