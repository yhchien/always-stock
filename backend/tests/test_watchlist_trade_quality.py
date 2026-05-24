"""Tests for M25 watchlist trade quality snapshot cache + endpoints."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    DailyPrice,
    StockMaster,
    UserWatchlist,
    WatchlistTradeQualitySnapshot,
)
from app.trade_quality_cache import (
    ETL_DONE_TIME,
    is_snapshot_complete,
    load_latest_ok_snapshot,
    load_snapshot,
    resolve_snapshot_trade_date,
    save_snapshot_failed,
    save_snapshot_ok,
)


_FULL_KEY_FACTORS = [
    {"category": "industry", "level": "A", "trend": "stable", "note": "n1"},
    {"category": "industry_heat", "level": "A", "trend": "stable", "note": "n2"},
    {"category": "return", "level": "A", "trend": "stable", "note": "n3"},
    {"category": "chip", "level": "A", "trend": "stable", "note": "n4"},
    {"category": "technical", "level": "A", "trend": "stable", "note": "n5"},
    {"category": "fundamental", "level": "A", "trend": "stable", "note": "n6"},
]

# M3：is_snapshot_complete 加 sections_json + action_one_liner 檢查後，所有「完整」fixture 都要帶
_FULL_SECTIONS = {
    "action_one_liner": "目前籌碼穩定，可保留觀察",
    "industry_section": ["產業 bullet 1"],
    "chip_section": ["籌碼 bullet 1"],
    "fundamental_section": ["基本面 bullet 1"],
    "technical_section": ["技術 bullet 1"],
    "peer_section": ["同業 bullet 1"],
    "news_section": ["新聞 bullet 1"],
}


def _complete_payload(**overrides):
    payload = {
        "rating": "BUY",
        "rating_label": "推薦",
        "summary": "x",
        "report_markdown": "r",
        "key_factors": _FULL_KEY_FACTORS,
        **_FULL_SECTIONS,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _register_and_login(client: TestClient, email: str = "alice@example.com") -> int:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": "passw0rd!"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _seed_stock(db, stock_id: str, name: str = "測試股") -> None:
    db.add(StockMaster(
        stock_id=stock_id, stock_name=name,
        industry_name="半導體業", is_active=True,
    ))
    db.commit()


def _seed_price(db, stock_id: str, trade_date: date, close: float) -> None:
    db.add(DailyPrice(trade_date=trade_date, stock_id=stock_id, close_price=close))
    db.commit()


# ── trade_quality_cache helpers ──────────────────────────────────────────────


def test_resolve_snapshot_trade_date_uses_today_after_etl_done_time(api):
    _, db = api
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    result = resolve_snapshot_trade_date(db, now=after_etl)
    assert result == date(2026, 4, 30)


def test_resolve_snapshot_trade_date_uses_yesterday_before_etl_done_time(api):
    _, db = api
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 29), 1000.0)
    _seed_price(db, "2330", date(2026, 4, 30), 1010.0)

    before_etl = datetime(2026, 4, 30, 18, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    result = resolve_snapshot_trade_date(db, now=before_etl)
    # ceiling 為前一天 4-29，DB 該日有資料 → 回 4-29
    assert result == date(2026, 4, 29)


def test_resolve_snapshot_trade_date_falls_back_when_today_holiday(api):
    _, db = api
    _seed_stock(db, "2330")
    # 4-30 假設休市，僅 4-28 有資料
    _seed_price(db, "2330", date(2026, 4, 28), 1000.0)

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    result = resolve_snapshot_trade_date(db, now=after_etl)
    assert result == date(2026, 4, 28)


def test_resolve_snapshot_trade_date_returns_none_when_db_empty(api):
    _, db = api
    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert resolve_snapshot_trade_date(db, now=after_etl) is None


def test_save_snapshot_ok_then_load(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    payload = {
        "rating": "BUY", "rating_label": "推薦",
        "classification": "B", "summary": "x",
        "report_markdown": "y",
        "key_factors": [{"category": "industry", "level": "A", "trend": "stable", "note": "n"}],
    }
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        response_payload=payload, source="manual",
    )
    row = load_snapshot(db, user_id=user_id, stock_id="2330",
                       buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30))
    assert row is not None
    assert row.status == "ok"
    assert row.rating == "BUY"
    assert row.key_factors[0]["category"] == "industry"
    assert row.source == "manual"


def test_save_snapshot_ok_upserts_on_same_key(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    base = dict(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        source="manual",
    )
    save_snapshot_ok(db, **base, response_payload=_complete_payload())
    save_snapshot_ok(db, **base, response_payload=_complete_payload(rating="RUN", rating_label="快跑"))

    rows = db.query(WatchlistTradeQualitySnapshot).all()
    assert len(rows) == 1  # UPSERT 而非新增
    assert rows[0].rating == "RUN"


def test_save_snapshot_failed_clears_payload_fields(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        response_payload={"rating": "BUY", "rating_label": "推薦", "summary": "old"},
        source="manual",
    )
    save_snapshot_failed(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        error_message="OpenAI down", source="cron",
    )
    row = load_snapshot(db, user_id=user_id, stock_id="2330",
                       buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30))
    assert row.status == "failed"
    assert row.rating is None
    assert row.summary is None
    assert row.error_message == "OpenAI down"


def test_load_latest_ok_snapshot_returns_most_recent(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    common = dict(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), source="cron",
    )
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 28),
                     response_payload=_complete_payload())
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 29),
                     response_payload=_complete_payload(rating="NEUTRAL", rating_label="中立"))

    row = load_latest_ok_snapshot(
        db, user_id=user_id, stock_id="2330", buy_date=date(2026, 4, 1)
    )
    assert row.snapshot_trade_date == date(2026, 4, 29)
    assert row.rating == "NEUTRAL"

    # before_or_eq 過濾：要 4-28 之前最新一筆
    row28 = load_latest_ok_snapshot(
        db, user_id=user_id, stock_id="2330", buy_date=date(2026, 4, 1),
        before_or_eq_trade_date=date(2026, 4, 28),
    )
    assert row28.snapshot_trade_date == date(2026, 4, 28)


def test_load_latest_ok_snapshot_skips_incomplete_ok_rows(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    common = dict(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), source="cron",
    )
    save_snapshot_ok(
        db, **common, snapshot_trade_date=date(2026, 4, 29),
        response_payload={
            "rating": "BUY",
            "rating_label": "推薦",
            "summary": "fake ok",
            "report_markdown": "has report but no lights",
            "key_factors": None,
        },
    )
    save_snapshot_ok(
        db, **common, snapshot_trade_date=date(2026, 4, 28),
        response_payload={
            "rating": "NEUTRAL",
            "rating_label": "中立",
            "summary": "complete",
            "report_markdown": "complete row",
            "key_factors": _FULL_KEY_FACTORS,
            # M3：is_snapshot_complete 也要求 sections_json + action_one_liner
            **_FULL_SECTIONS,
        },
    )

    row = load_latest_ok_snapshot(
        db, user_id=user_id, stock_id="2330", buy_date=date(2026, 4, 1)
    )
    assert row is not None
    assert row.snapshot_trade_date == date(2026, 4, 28)


def test_is_snapshot_complete_requires_status_ok_and_six_categories(api):
    """完整 ok 快照 = status='ok' 且 6 個 category 全部齊。

    這是 4/30「假 ok」row 出現後加的防呆判定：避免 LLM 沒給 key_factors 卻寫成
    status='ok' 的 row 被當成有效快照、永遠卡在沒燈號狀態。
    """
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    # Case 1: status='ok' + 6 個 category + M3 sections + action_one_liner → 完整
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 28),
        response_payload={
            "rating": "BUY", "rating_label": "推薦", "summary": "x",
            "key_factors": _FULL_KEY_FACTORS,
            **_FULL_SECTIONS,
        },
        source="manual",
    )
    row_full = load_snapshot(db, user_id=user_id, stock_id="2330",
                             buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 28))
    assert is_snapshot_complete(row_full) is True

    # Case 2: status='ok' 但 key_factors=None（4/30 那種「假 ok」）→ 不完整
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 29),
        response_payload={
            "rating": "BUY", "rating_label": "推薦", "summary": "x",
            "key_factors": None,
        },
        source="manual",
    )
    row_null = load_snapshot(db, user_id=user_id, stock_id="2330",
                             buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 29))
    assert is_snapshot_complete(row_null) is False

    # Case 3: status='ok' 但 key_factors 缺 1 個 category → 不完整
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        response_payload={
            "rating": "BUY", "rating_label": "推薦", "summary": "x",
            "key_factors": _FULL_KEY_FACTORS[:5],  # 只 5 個
        },
        source="manual",
    )
    row_short = load_snapshot(db, user_id=user_id, stock_id="2330",
                              buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30))
    assert is_snapshot_complete(row_short) is False

    # Case 4: status='failed' 即使 key_factors 齊也算不完整
    save_snapshot_failed(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 1),
        error_message="x", source="cron",
    )
    row_failed = load_snapshot(db, user_id=user_id, stock_id="2330",
                               buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 1))
    assert is_snapshot_complete(row_failed) is False

    # Case 5: row=None → False
    assert is_snapshot_complete(None) is False

    # Case 6 (M3)：6 個 category 齊但 sections_json=None（pre-M3 舊快照）→ 不完整，
    # cron 會自動把它當「不完整」 → 一次性 backfill 出新版 6 panel。
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 2),
        response_payload={
            "rating": "BUY", "rating_label": "推薦", "summary": "x",
            "key_factors": _FULL_KEY_FACTORS,
            # 不帶任何 sections / action_one_liner → sections_json 寫成 None
        },
        source="manual",
    )
    row_no_sections = load_snapshot(db, user_id=user_id, stock_id="2330",
                                    buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 2))
    assert is_snapshot_complete(row_no_sections) is False

    # Case 7 (M3)：sections_json 存在但 action_one_liner 空白 → 不完整
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 3),
        response_payload={
            "rating": "BUY", "rating_label": "推薦", "summary": "x",
            "key_factors": _FULL_KEY_FACTORS,
            "action_one_liner": "   ",  # 空白字串
            "industry_section": ["x"],
        },
        source="manual",
    )
    row_blank_one_liner = load_snapshot(db, user_id=user_id, stock_id="2330",
                                        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 3))
    assert is_snapshot_complete(row_blank_one_liner) is False


def test_load_latest_ok_snapshot_skips_failed(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 28),
        response_payload=_complete_payload(), source="cron",
    )
    save_snapshot_failed(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        error_message="x", source="cron",
    )
    row = load_latest_ok_snapshot(
        db, user_id=user_id, stock_id="2330", buy_date=date(2026, 4, 1)
    )
    assert row.snapshot_trade_date == date(2026, 4, 28)  # 跳過 4-30 failed


# ── GET /api/watchlist/trade-quality ─────────────────────────────────────────


def test_list_trade_quality_unauthorized(api):
    client, _ = api
    res = client.get("/api/watchlist/trade-quality")
    assert res.status_code == 401


def test_list_trade_quality_empty_returns_total_zero(api):
    client, _ = api
    _register_and_login(client)
    res = client.get("/api/watchlist/trade-quality")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_trade_quality_returns_today_snapshot_as_latest(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 29), 1000.0)
    _seed_price(db, "2330", date(2026, 4, 30), 1020.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), avg_price=950.0,
    ))
    db.commit()
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
        response_payload={
            "rating": "BUY", "rating_label": "推薦",
            "classification": "A", "summary": "強勢",
            "key_factors": [
                {"category": "industry", "level": "A", "trend": "stable", "note": "AI"},
            ],
        },
        source="cron",
    )

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        # 保留其他屬性
        mock_dt.utcnow = datetime.utcnow
        res = client.get("/api/watchlist/trade-quality")

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["stock_id"] == "2330"
    assert item["latest"] is not None
    assert item["latest"]["rating"] == "BUY"
    assert item["latest"]["is_stale"] is False
    assert item["latest"]["key_factors"][0]["category"] == "industry"
    assert item["previous"] is None
    assert item["change_pct"] == pytest.approx(2.0)  # (1020 - 1000) / 1000 * 100


def test_list_trade_quality_falls_back_to_old_when_no_today_snapshot(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), avg_price=950.0,
    ))
    db.commit()
    # 只在 4-28 有 ok 快照、4-30 沒有
    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 28),
        response_payload=_complete_payload(rating="NEUTRAL", rating_label="中立"),
        source="cron",
    )
    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        res = client.get("/api/watchlist/trade-quality")

    item = res.json()["items"][0]
    assert item["latest"]["snapshot_trade_date"] == "2026-04-28"
    assert item["latest"]["is_stale"] is True
    assert item["previous"] is None  # 沒有更早的 ok


def test_list_trade_quality_does_not_fallback_to_incomplete_ok_row(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), avg_price=950.0,
    ))
    db.commit()

    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 29),
        response_payload={
            "rating": "BUY",
            "rating_label": "推薦",
            "summary": "有報告但沒燈號",
            "report_markdown": "old fake ok row",
            "key_factors": None,
        },
        source="cron",
    )

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        res = client.get("/api/watchlist/trade-quality")

    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["latest"] is None
    assert item["recent_factors"] == []


def test_list_trade_quality_provides_previous_for_delta(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), avg_price=950.0,
    ))
    db.commit()

    common = dict(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), source="cron",
    )
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 29),
                     response_payload=_complete_payload(classification="B"))
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 30),
                     response_payload=_complete_payload(rating="RUN", rating_label="快跑", classification="C"))

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        res = client.get("/api/watchlist/trade-quality")

    item = res.json()["items"][0]
    assert item["latest"]["rating"] == "RUN"
    assert item["latest"]["classification"] == "C"
    assert item["previous"] is not None
    assert item["previous"]["rating"] == "BUY"
    assert item["previous"]["classification"] == "B"


def test_list_trade_quality_returns_failed_latest_when_no_fallback_ok(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 30), avg_price=1000.0,
    ))
    db.commit()
    save_snapshot_failed(
        db,
        user_id=user_id,
        stock_id="2330",
        buy_date=date(2026, 4, 30),
        snapshot_trade_date=date(2026, 4, 30),
        error_message="on-demand refresh failed",
        source="on_demand",
    )

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        res = client.get("/api/watchlist/trade-quality")

    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["latest"] is not None
    assert item["latest"]["status"] == "failed"
    assert item["latest"]["snapshot_trade_date"] == "2026-04-30"
    assert item["latest"]["is_stale"] is False
    assert item["previous"] is None


# ── POST /api/watchlist/trade-quality/refresh ────────────────────────────────


def test_refresh_unauthorized(api):
    client, _ = api
    res = client.post("/api/watchlist/trade-quality/refresh", json={"stock_id": "2330"})
    assert res.status_code == 401


def test_refresh_404_when_stock_not_in_watchlist(api):
    client, _ = api
    _register_and_login(client)
    res = client.post("/api/watchlist/trade-quality/refresh", json={"stock_id": "9999"})
    assert res.status_code == 404


def test_refresh_invokes_runner_and_returns_item(api, monkeypatch):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 30), 1000.0)
    db.add(UserWatchlist(
        user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), avg_price=950.0,
    ))
    db.commit()

    # mock runner：寫入一筆 ok snapshot 後回傳 dummy result
    from app.routers import analysis as analysis_module

    def fake_runner(db, *, user, stock_id, buy_date_input, persist_source, **kwargs):
        save_snapshot_ok(
            db, user_id=user.id, stock_id=stock_id,
            buy_date=buy_date_input, snapshot_trade_date=date(2026, 4, 30),
            response_payload={"rating": "BUY", "rating_label": "推薦"},
            source=persist_source,
        )
        return analysis_module.TradeQualityRunResult(
            response=analysis_module.TradeQualityResponse(
                stock_id=stock_id, stock_name="台積電", buy_date=str(buy_date_input),
                rating="BUY", rating_label="推薦", summary="x", report_markdown="y",
                source="openai",
            )
        )

    monkeypatch.setattr(analysis_module, "run_trade_quality_for_user", fake_runner)

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        res = client.post(
            "/api/watchlist/trade-quality/refresh",
            json={"stock_id": "2330"},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stock_id"] == "2330"
    assert body["latest"]["rating"] == "BUY"
    assert body["latest"]["is_stale"] is False


# ── runner 防呆：incomplete key_factors 自動標 failed ────────────────────────


def _seed_min_runner_context(db, *, stock_id: str = "2330") -> None:
    """run_trade_quality_for_user 跑 OpenAI 需要的最小 DB context。"""
    _seed_stock(db, stock_id, "台積電")
    _seed_price(db, stock_id, date(2026, 4, 30), 1000.0)


def _patch_runner_dependencies(monkeypatch, payloads: list):
    """把 runner 內 OpenAI 與 context-building 換成 in-memory stub。

    payloads: list[dict | None] — 對應每次 _call_openai 回傳；用完丟空 list 後續回 None。
    """
    from app.routers import analysis as analysis_module

    call_log: list[dict] = []

    def fake_call_openai(system_prompt: str, user_msg: str):
        call_log.append({"system": system_prompt, "user_msg": user_msg})
        if not payloads:
            return None
        return payloads.pop(0)

    # 清掉 module-level 5min in-memory cache，避免測試之間互相串到
    analysis_module._trade_quality_cache.clear()

    monkeypatch.setattr(analysis_module, "_call_openai", fake_call_openai)
    monkeypatch.setattr(analysis_module, "_load_system_prompt", lambda: "stub-prompt")

    def fake_collect_context(db, sid, bd):
        stock = db.get(StockMaster, sid)
        ctx = {
            "stock_id": sid,
            "stock_name": stock.stock_name if stock else sid,
            "industry_name": "半導體",
            "sub_industry": None,
            "buy_date": str(bd),
            "latest_close": 1000.0,
            "prices_text": "(stub)",
            "flows_text": "(stub)",
            "revenue_text": "(stub)",
        }
        return stock, ctx, []

    monkeypatch.setattr(analysis_module, "_collect_context", fake_collect_context)
    monkeypatch.setattr(
        analysis_module, "_build_deterministic_context",
        lambda db, sid, bd, warnings: {"m21": True},
    )
    return call_log


def test_runner_writes_failed_when_key_factors_stay_incomplete_after_retry(api, monkeypatch):
    """LLM 兩次都不給齊 6 個 category → runner 寫 status='failed'，不留「假 ok」。"""
    client, db = api
    user_id = _register_and_login(client)
    _seed_min_runner_context(db)

    incomplete = {
        "rating": "BUY", "summary": "缺燈", "report_markdown": "r",
        "key_factors": _FULL_KEY_FACTORS[:3],  # 只給 3 個 category
    }
    call_log = _patch_runner_dependencies(monkeypatch, [incomplete, incomplete])

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        from app.models import User
        from app.routers.analysis import run_trade_quality_for_user
        user = db.get(User, user_id)
        result = run_trade_quality_for_user(
            db,
            user=user,
            stock_id="2330",
            buy_date_input=date(2026, 4, 1),
            persist_source="cron",
        )

    # OpenAI 被打了兩次（第一次 incomplete → factors-retry 補強提醒）
    assert len(call_log) == 2
    # 第二次 user_msg 應含補強提醒
    assert "缺少必備 category" in call_log[1]["user_msg"]

    # DB 應該寫 failed（不是 ok）
    row = load_snapshot(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
    )
    assert row is not None
    assert row.status == "failed"
    assert row.rating is None  # failed 會清 payload 欄位
    assert "未提供完整" in (row.error_message or "")
    # response 仍有回給 caller，加上 warning
    assert any("完整燈號" in w for w in result.response.warnings)


def test_runner_writes_ok_when_first_call_already_complete(api, monkeypatch):
    """第一次就齊 6 category → 不 retry、寫 status='ok'。"""
    client, db = api
    user_id = _register_and_login(client)
    _seed_min_runner_context(db)

    full_payload = {
        "rating": "BUY", "summary": "完整", "report_markdown": "r",
        "key_factors": _FULL_KEY_FACTORS,
    }
    call_log = _patch_runner_dependencies(monkeypatch, [full_payload])

    after_etl = datetime(2026, 4, 30, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    with patch("app.trade_quality_cache.datetime") as mock_dt:
        mock_dt.now.return_value = after_etl
        mock_dt.utcnow = datetime.utcnow
        from app.models import User
        from app.routers.analysis import run_trade_quality_for_user
        user = db.get(User, user_id)
        run_trade_quality_for_user(
            db,
            user=user,
            stock_id="2330",
            buy_date_input=date(2026, 4, 1),
            persist_source="cron",
        )

    # 完整 → 不 retry，OpenAI 只被打一次
    assert len(call_log) == 1

    row = load_snapshot(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
    )
    assert row is not None
    assert row.status == "ok"
    assert isinstance(row.key_factors, list)
    assert len(row.key_factors) == 6


def test_runner_honors_snapshot_trade_date_override(api, monkeypatch):
    """cron `--date 2026-04-30` 應透傳給 runner，把快照寫到指定日（不被 resolve 蓋掉）。"""
    client, db = api
    user_id = _register_and_login(client)
    _seed_min_runner_context(db)
    # 還補一個更近期的價格，讓 resolve 不會剛好等於 4/30
    _seed_price(db, "2330", date(2026, 5, 2), 1010.0)

    full_payload = {
        "rating": "BUY", "summary": "back", "report_markdown": "r",
        "key_factors": _FULL_KEY_FACTORS,
    }
    _patch_runner_dependencies(monkeypatch, [full_payload])

    from app.models import User
    from app.routers.analysis import run_trade_quality_for_user
    user = db.get(User, user_id)
    run_trade_quality_for_user(
        db,
        user=user,
        stock_id="2330",
        buy_date_input=date(2026, 4, 1),
        persist_source="cron",
        use_db_cache=False,
        snapshot_trade_date_override=date(2026, 4, 30),
    )

    # 寫入應該落在 4/30，不是 resolve 出來的 5/2
    row_4_30 = load_snapshot(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 30),
    )
    assert row_4_30 is not None
    assert row_4_30.status == "ok"

    row_5_2 = load_snapshot(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 5, 2),
    )
    assert row_5_2 is None
