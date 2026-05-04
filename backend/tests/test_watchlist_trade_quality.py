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
    load_latest_ok_snapshot,
    load_snapshot,
    resolve_snapshot_trade_date,
    save_snapshot_failed,
    save_snapshot_ok,
)


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
    save_snapshot_ok(db, **base, response_payload={"rating": "BUY", "rating_label": "推薦"})
    save_snapshot_ok(db, **base, response_payload={"rating": "RUN", "rating_label": "快跑"})

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
                     response_payload={"rating": "BUY", "rating_label": "推薦"})
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 29),
                     response_payload={"rating": "NEUTRAL", "rating_label": "中立"})

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


def test_load_latest_ok_snapshot_skips_failed(api):
    client, db = api
    user_id = _register_and_login(client)
    _seed_stock(db, "2330")

    save_snapshot_ok(
        db, user_id=user_id, stock_id="2330",
        buy_date=date(2026, 4, 1), snapshot_trade_date=date(2026, 4, 28),
        response_payload={"rating": "BUY", "rating_label": "推薦"}, source="cron",
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
        response_payload={"rating": "NEUTRAL", "rating_label": "中立", "summary": "x"},
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
                     response_payload={"rating": "BUY", "rating_label": "推薦", "classification": "B"})
    save_snapshot_ok(db, **common, snapshot_trade_date=date(2026, 4, 30),
                     response_payload={"rating": "RUN", "rating_label": "快跑", "classification": "C"})

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
