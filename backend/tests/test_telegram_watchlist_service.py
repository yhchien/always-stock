"""watchlist_service.py 測試：CRUD、comma 解析、20 檔上限。"""
from datetime import date
from typing import Optional

import pytest

from app.models import DailyPrice, StockMaster, TelegramChat, TelegramWatchlistEntry
from app.telegram import watchlist_service


def _seed_stock(
    db,
    stock_id: str,
    name: str,
    industry: str = "半導體",
    sub: Optional[str] = "積體電路業",
):
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=name,
        industry_name=industry,
        sub_industry=sub,
        is_active=True,
    ))
    db.commit()


def _seed_price(db, stock_id: str, close: float, spread: float = 1.5, d: date = date(2026, 5, 11)):
    db.add(DailyPrice(
        trade_date=d, stock_id=stock_id,
        open_price=close - 1, high_price=close + 1, low_price=close - 2,
        close_price=close, volume=10000, spread=spread,
    ))
    db.commit()


def _seed_chat(db, chat_id: int = 100):
    db.add(TelegramChat(chat_id=chat_id))
    db.commit()


# ── parse_stock_ids ──────────────────────────────────────────────────────────


def test_parse_stock_ids_single():
    assert watchlist_service.parse_stock_ids("2330") == ["2330"]


def test_parse_stock_ids_comma_separated():
    assert watchlist_service.parse_stock_ids("2330,2317") == ["2330", "2317"]


def test_parse_stock_ids_with_spaces():
    assert watchlist_service.parse_stock_ids("2330, 2317 , 2454") == ["2330", "2317", "2454"]


def test_parse_stock_ids_deduplicates():
    assert watchlist_service.parse_stock_ids("2330, 2330, 2317") == ["2330", "2317"]


def test_parse_stock_ids_empty():
    assert watchlist_service.parse_stock_ids("") == []
    assert watchlist_service.parse_stock_ids("   ") == []
    assert watchlist_service.parse_stock_ids(",  ,") == []


# ── add_stocks ───────────────────────────────────────────────────────────────


def test_add_single_stock_success(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", close=1050.0, spread=1.2)

    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])
    assert len(result.added) == 1
    assert result.added[0].stock_id == "2330"
    assert result.added[0].stock_name == "台積電"
    assert result.added[0].close_price == 1050.0
    assert result.added[0].spread_pct == 1.2
    assert result.current_count == 1


def test_add_multiple_stocks(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2317", "鴻海")
    _seed_price(db, "2330", close=1050.0)
    _seed_price(db, "2317", close=200.0)

    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330", "2317"])
    assert len(result.added) == 2
    assert result.current_count == 2


def test_add_not_found_stock(db):
    _seed_chat(db)

    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["9999"])
    assert result.added == []
    assert result.not_found == ["9999"]


def test_add_mixed_found_and_not_found(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", close=1050.0)

    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330", "9999"])
    assert len(result.added) == 1
    assert result.added[0].stock_id == "2330"
    assert result.not_found == ["9999"]


def test_add_duplicate(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", close=1050.0)

    watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])
    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])
    assert result.added == []
    assert result.duplicates == ["2330"]


def test_add_exceeds_limit(db):
    _seed_chat(db)
    for i in range(25):
        sid = f"{1000 + i}"
        _seed_stock(db, sid, f"STK{i}")
        _seed_price(db, sid, close=100.0 + i)

    result = watchlist_service.add_stocks(
        db, chat_id=100, stock_ids=[f"{1000 + i}" for i in range(25)],
    )
    assert len(result.added) == 20
    assert len(result.over_limit) == 5
    assert result.current_count == 20


def test_add_no_stock_price_yet(db):
    """股票存在但 daily_price 無資料時，close_price/spread_pct 為 None"""
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    # 不 seed price

    result = watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])
    assert len(result.added) == 1
    assert result.added[0].close_price is None
    assert result.added[0].spread_pct is None


# ── remove_stocks ────────────────────────────────────────────────────────────


def test_remove_single_stock(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", close=1050.0)
    watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])

    result = watchlist_service.remove_stocks(db, chat_id=100, stock_ids=["2330"])
    assert result.removed == ["2330"]
    assert result.remaining == []
    assert result.current_count == 0


def test_remove_not_in_list(db):
    _seed_chat(db)
    result = watchlist_service.remove_stocks(db, chat_id=100, stock_ids=["2330"])
    assert result.removed == []
    assert result.not_in_list == ["2330"]


def test_remove_mixed(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2317", "鴻海")
    _seed_price(db, "2330", close=1050.0)
    _seed_price(db, "2317", close=200.0)
    watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330", "2317"])

    result = watchlist_service.remove_stocks(db, chat_id=100, stock_ids=["2330", "9999"])
    assert result.removed == ["2330"]
    assert result.not_in_list == ["9999"]
    assert len(result.remaining) == 1
    assert result.remaining[0].stock_id == "2317"


# ── list_watchlist ───────────────────────────────────────────────────────────


def test_list_empty_watchlist(db):
    _seed_chat(db)
    assert watchlist_service.list_watchlist(db, 100) == []


def test_list_with_entries_sorted_by_added_at(db):
    _seed_chat(db)
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2317", "鴻海")
    _seed_price(db, "2330", close=1050.0)
    _seed_price(db, "2317", close=200.0)

    # 分兩次 add 確保 added_at 不同
    watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2330"])
    watchlist_service.add_stocks(db, chat_id=100, stock_ids=["2317"])

    snaps = watchlist_service.list_watchlist(db, 100)
    assert [s.stock_id for s in snaps] == ["2330", "2317"]


def test_list_handles_orphaned_stock(db):
    """edge case: watchlist 有條目但 stocks_master 找不到該股 → 回 fallback snapshot"""
    _seed_chat(db)
    # 直接寫一筆 — 跳過 add_stocks 的 stocks_master 檢查
    db.add(TelegramWatchlistEntry(chat_id=100, stock_id="9999"))
    db.commit()

    snaps = watchlist_service.list_watchlist(db, 100)
    assert len(snaps) == 1
    assert snaps[0].stock_id == "9999"
    assert snaps[0].stock_name == "9999"  # fallback 用 stock_id
    assert snaps[0].close_price is None
