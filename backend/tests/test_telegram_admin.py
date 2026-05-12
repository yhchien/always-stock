"""admin 功能測試：settings helper、watchlist_service admin queries、formatters、handlers。"""
from datetime import datetime, timedelta

import pytest

from app.models import DailyPrice, StockMaster, TelegramChat, TelegramWatchlistEntry
from app.settings import get_admin_telegram_chat_ids
from app.telegram import commands, formatters, watchlist_service


# ── settings.get_admin_telegram_chat_ids ────────────────────────────────────


def test_admin_chat_ids_empty_when_not_set(monkeypatch):
    monkeypatch.delenv("ADMIN_TELEGRAM_CHAT_IDS", raising=False)
    assert get_admin_telegram_chat_ids() == set()


def test_admin_chat_ids_single():
    import os
    os.environ["ADMIN_TELEGRAM_CHAT_IDS"] = "12345"
    try:
        assert get_admin_telegram_chat_ids() == {12345}
    finally:
        os.environ.pop("ADMIN_TELEGRAM_CHAT_IDS", None)


def test_admin_chat_ids_multiple(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_CHAT_IDS", "12345, 67890, -1001234567890")
    assert get_admin_telegram_chat_ids() == {12345, 67890, -1001234567890}


def test_admin_chat_ids_ignore_invalid(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_CHAT_IDS", "12345, notanumber, 99999")
    assert get_admin_telegram_chat_ids() == {12345, 99999}


def test_admin_chat_ids_handles_whitespace(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_CHAT_IDS", "  12345  ,  67890  ")
    assert get_admin_telegram_chat_ids() == {12345, 67890}


# ── watchlist_service admin queries ─────────────────────────────────────────


def _seed_chat(db, chat_id, label=None, last_seen_offset_seconds=0):
    now = datetime.utcnow()
    db.add(TelegramChat(
        chat_id=chat_id,
        chat_label=label,
        last_seen_at=now - timedelta(seconds=last_seen_offset_seconds),
    ))
    db.commit()


def _seed_stock(db, stock_id, name="STK"):
    db.add(StockMaster(
        stock_id=stock_id, stock_name=name,
        industry_name="半導體", sub_industry="積體電路業",
    ))
    db.commit()


def test_all_chats_with_summary_empty(db):
    assert watchlist_service.all_chats_with_summary(db) == []


def test_all_chats_with_summary_returns_all_registered(db):
    _seed_chat(db, 111, "alice", last_seen_offset_seconds=100)
    _seed_chat(db, 222, "bob", last_seen_offset_seconds=10)
    _seed_chat(db, 333, last_seen_offset_seconds=50)

    items = watchlist_service.all_chats_with_summary(db)
    assert len(items) == 3
    # 依 last_seen DESC 排序 — bob (10s ago) 最新，alice (100s ago) 最舊
    assert items[0].chat_id == 222
    assert items[0].chat_label == "bob"
    assert items[1].chat_id == 333
    assert items[2].chat_id == 111


def test_all_chats_with_summary_includes_watchlist_size(db):
    _seed_chat(db, 111)
    _seed_stock(db, "2330")
    _seed_stock(db, "2317")
    db.add(TelegramWatchlistEntry(chat_id=111, stock_id="2330"))
    db.add(TelegramWatchlistEntry(chat_id=111, stock_id="2317"))
    db.commit()

    items = watchlist_service.all_chats_with_summary(db)
    assert items[0].watchlist_size == 2


def test_get_chat_detail_existing(db):
    _seed_chat(db, 111, "alice")
    _seed_stock(db, "2330", "台積電")
    db.add(TelegramWatchlistEntry(chat_id=111, stock_id="2330"))
    db.commit()

    result = watchlist_service.get_chat_detail(db, 111)
    assert result is not None
    chat, snapshots = result
    assert chat.chat_id == 111
    assert chat.chat_label == "alice"
    assert len(snapshots) == 1
    assert snapshots[0].stock_id == "2330"


def test_get_chat_detail_not_found(db):
    result = watchlist_service.get_chat_detail(db, 999)
    assert result is None


# ── formatters ──────────────────────────────────────────────────────────────


def test_format_admin_chats_empty():
    out = formatters.format_admin_chats([])
    assert "沒有任何註冊" in out


def test_format_admin_chats_with_items():
    items = [
        watchlist_service.AdminChatSummary(
            chat_id=12345,
            chat_label="alice",
            registered_at=datetime(2026, 5, 1, 10, 0),
            last_seen_at=datetime(2026, 5, 12, 14, 30),
            watchlist_size=5,
        ),
        watchlist_service.AdminChatSummary(
            chat_id=67890,
            chat_label=None,
            registered_at=datetime(2026, 5, 2, 9, 0),
            last_seen_at=datetime(2026, 5, 10, 15, 0),
            watchlist_size=0,
        ),
    ]
    out = formatters.format_admin_chats(items)
    assert "12345" in out
    assert "alice" in out
    assert "67890" in out
    assert "5/20" in out
    assert "0/20" in out


def test_format_admin_chat_detail_with_entries(db):
    _seed_chat(db, 111, "alice")
    _seed_stock(db, "2330", "台積電")
    db.add(TelegramWatchlistEntry(chat_id=111, stock_id="2330"))
    db.commit()

    result = watchlist_service.get_chat_detail(db, 111)
    assert result is not None
    chat, snapshots = result
    out = formatters.format_admin_chat_detail(chat, snapshots)
    assert "111" in out
    assert "alice" in out
    assert "2330" in out
    assert "台積電" in out
    assert "1/20" in out


def test_format_admin_chat_detail_empty_list(db):
    _seed_chat(db, 111, "alice")
    result = watchlist_service.get_chat_detail(db, 111)
    chat, snapshots = result
    out = formatters.format_admin_chat_detail(chat, snapshots)
    assert "111" in out
    assert "（清單為空）" in out


# ── commands.handle_admin_* ─────────────────────────────────────────────────


def test_handle_admin_chats_no_chats(db):
    out = commands.handle_admin_chats(db)
    assert "沒有任何註冊" in out


def test_handle_admin_show_not_found(db):
    out = commands.handle_admin_show(db, target_chat_id=999)
    assert "不存在" in out


def test_handle_admin_show_with_chat(db):
    _seed_chat(db, 111, "alice")
    out = commands.handle_admin_show(db, target_chat_id=111)
    assert "111" in out
    assert "alice" in out
