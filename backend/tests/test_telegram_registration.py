"""registration.py 測試：密碼驗證、重複註冊、密碼未設定。"""
import pytest

from app.models import TelegramChat
from app.telegram import registration


@pytest.fixture
def with_password(monkeypatch):
    monkeypatch.setenv("SITE_GATE_PASSWORD", "correct-pass")
    yield


@pytest.fixture
def without_password(monkeypatch):
    monkeypatch.delenv("SITE_GATE_PASSWORD", raising=False)
    yield


def test_register_chat_success(db, with_password):
    result = registration.register_chat(
        db, chat_id=123, password="correct-pass", chat_label="alice",
    )
    assert result.success is True
    assert result.already_registered is False
    assert "註冊成功" in result.message
    # 確認 chat_id 出現在成功訊息（給管理員設 ADMIN_TELEGRAM_CHAT_IDS 用）
    assert "123" in result.message
    assert "ADMIN_TELEGRAM_CHAT_IDS" in result.message

    row = db.get(TelegramChat, 123)
    assert row is not None
    assert row.chat_label == "alice"


def test_register_chat_wrong_password(db, with_password):
    result = registration.register_chat(
        db, chat_id=123, password="wrong-pass", chat_label="alice",
    )
    assert result.success is False
    assert "密碼錯誤" in result.message
    assert db.get(TelegramChat, 123) is None


def test_register_chat_strips_whitespace(db, with_password):
    result = registration.register_chat(
        db, chat_id=123, password="  correct-pass  ", chat_label="alice",
    )
    assert result.success is True


def test_register_chat_already_registered(db, with_password):
    registration.register_chat(db, chat_id=123, password="correct-pass", chat_label="alice")
    result = registration.register_chat(
        db, chat_id=123, password="correct-pass", chat_label="alice2",
    )
    assert result.success is True
    assert result.already_registered is True
    assert "已註冊" in result.message


def test_register_chat_password_not_configured(db, without_password):
    result = registration.register_chat(
        db, chat_id=123, password="anything", chat_label="alice",
    )
    assert result.success is False
    assert "尚未設定" in result.message


def test_is_registered(db, with_password):
    assert registration.is_registered(db, 123) is False
    registration.register_chat(db, chat_id=123, password="correct-pass", chat_label=None)
    assert registration.is_registered(db, 123) is True


def test_touch_last_seen_updates_existing(db, with_password):
    registration.register_chat(db, chat_id=123, password="correct-pass", chat_label="alice")
    chat = db.get(TelegramChat, 123)
    original_last_seen = chat.last_seen_at

    # 強制差距
    import time
    time.sleep(0.01)

    registration.touch_last_seen(db, 123)
    chat_after = db.get(TelegramChat, 123)
    assert chat_after.last_seen_at >= original_last_seen


def test_touch_last_seen_safe_when_not_registered(db, with_password):
    # 不應 raise
    registration.touch_last_seen(db, 999)
