"""M23 後續 — expectation_price service + routes 測試。

涵蓋：
  - build_expectation_context（context builder 抽資料正確性 + 失敗 case）
  - generate_for_stock（LLM 成功 / 失敗 路徑 + UPSERT 覆蓋）
  - update_hit_targets（首次觸發保守 / 夢想標旗）
  - API endpoints（list / get / quota / regenerate + 限額）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

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
    InstStockFlow,
    MarginTrade,
    SignalExpectationPrice,
    SignalSnapshot,
    SignalWatchHit,
    StockMaster,
)
from app.routers import signals as signals_router
from app.signals import expectation_price as svc


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def api(monkeypatch):
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

    # 攔截 background task：直接同步呼叫一個 stub，不真的開 SessionLocal
    bg_calls: list = []

    def fake_bg(stock_id: str):
        bg_calls.append(stock_id)

    monkeypatch.setattr(signals_router, "_run_expectation_safely", fake_bg)

    client = TestClient(app)
    yield client, session, bg_calls
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _register_login(client: TestClient, email: str = "alice@example.com") -> None:
    """Auth disabled 模式下任何呼叫都會落到 demo user，但保險起見呼叫 /register。"""
    client.post("/api/auth/register", json={"email": email, "password": "passw0rd!"})


def _seed_minimal_stock(db, stock_id="2330"):
    db.add(
        StockMaster(
            stock_id=stock_id,
            stock_name=f"{stock_id}名",
            industry_name="半導體",
            sub_industry="IC設計",
        )
    )
    # 21 個交易日 OHLC，逐日 +1 元
    base = date(2026, 4, 1)
    for i in range(21):
        d = base + timedelta(days=i)
        close = 100.0 + i
        db.add(
            DailyPrice(
                trade_date=d,
                stock_id=stock_id,
                open_price=close - 1,
                high_price=close + 0.5,
                low_price=close - 1.5,
                close_price=close,
                volume=1000.0 + i * 10,
            )
        )
    # 三大法人近 5 日，foreign 每天淨買 200 張
    for i in range(5):
        d = base + timedelta(days=16 + i)
        db.add(
            InstStockFlow(
                trade_date=d,
                stock_id=stock_id,
                inst_type="foreign",
                buy_shares=200_000,
                sell_shares=0,
                net_shares=200_000,
            )
        )
    # 第一次 signal hit
    db.add(
        SignalWatchHit(
            snapshot_date=base + timedelta(days=18),
            stock_id=stock_id,
            stock_name=f"{stock_id}名",
            signal_type="LEADER",
            industry_name="半導體",
            sub_industry="IC設計",
            business_summary="晶圓代工",
            reason="籌碼 / 題材 / 量價",
            theme={"main_theme": "AI 伺服器", "theme_duration": "2Q_plus", "theme_score": 3},
            group_info={},
            leader_check={},
            signals={
                "capital_flow": "strong_inflow",
                "chip_trend": "concentrating",
                "margin_short_signal": "neutral",
                "technical_status": "breakout",
            },
            max_positive_return_pct=12.5,
            max_negative_return_pct=-1.2,
        )
    )
    # SignalSnapshot 對應該天，watchlist 含本檔（給 theme_fit）
    db.add(
        SignalSnapshot(
            snapshot_date=base + timedelta(days=18),
            market_context={"market_state": "STRONG_BULL"},
            watchlist=[
                {
                    "stock": stock_id,
                    "stock_id": stock_id,
                    "industry": "半導體",
                    "theme_fit": "HIGH",
                }
            ],
            removed=[],
            summary={},
            candidate_pool_size=50,
            final_watchlist_size=1,
            llm_model="gpt-test",
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# context builder
# ---------------------------------------------------------------------------


def test_build_expectation_context_happy_path(db_session):
    _seed_minimal_stock(db_session)
    ctx = svc.build_expectation_context(db_session, "2330")
    assert ctx.payload["stock"]["code"] == "2330"
    assert ctx.payload["stock"]["name"] == "2330名"
    assert ctx.payload["stock"]["detected_type"] == "LEADER"
    assert ctx.payload["stock"]["hit_count"] == 1
    # theme_score deterministic：LEADER + HIGH → 3（context_builder 內若拿到 payload 的 3 也會優先用）
    assert ctx.payload["theme_context"]["theme_score"] == 3
    assert ctx.payload["theme_context"]["theme_fit"] == "HIGH"
    # 近 21 日 high/low/MA 都應該被填
    assert ctx.payload["price_data"]["high_5d"] is not None
    assert ctx.payload["price_data"]["ma5"] is not None
    # institution_flow 累計 5 日 foreign 應 = 200*5 = 1000 張
    assert ctx.payload["institution_flow"]["foreign_flow_5d"] == 1000
    # meta 帶 detected_day_high / detected_day_close
    assert ctx.meta["detected_day_high"] is not None
    assert ctx.meta["detected_day_close"] is not None


def test_build_expectation_context_unknown_stock_raises(db_session):
    with pytest.raises(ValueError):
        svc.build_expectation_context(db_session, "9999")


def test_build_expectation_context_no_signal_hit_raises(db_session):
    db_session.add(
        StockMaster(stock_id="1111", stock_name="名", industry_name="工")
    )
    db_session.commit()
    with pytest.raises(ValueError):
        svc.build_expectation_context(db_session, "1111")


# ---------------------------------------------------------------------------
# generate_for_stock 成功 / 失敗
# ---------------------------------------------------------------------------


def test_generate_for_stock_persists_ok(db_session, monkeypatch):
    _seed_minimal_stock(db_session)

    fake_payload = {
        "date": "2026-04-19",
        "stock": "2330",
        "name": "2330名",
        "expectation_result": {
            "conservative_price": 130.0,
            "dream_price": 150.0,
            "price_base": "detected_day_high",
            "valuation_mode": "MOMENTUM_MARKUP",
            "valuation_basis": "momentum_markup",
            "current_price_position": "optimistic",
            "chase_risk": "medium",
            "confidence": "medium",
        },
        "valuation_detail": {"pe_reason": "EPS 改善"},
        "scorecard": {"total_score": 78},
        "classification": {"role": "LEADER"},
        "reason_50_words": "籌碼題材皆強",
        "risk_note_30_words": "若法人轉賣需提防",
    }

    monkeypatch.setattr(
        svc,
        "_call_llm",
        lambda payload, *, model: (fake_payload, {"status": "ok", "model": model}),
    )

    row = svc.generate_for_stock(db_session, "2330", source="manual")
    assert row.status == "ok"
    assert row.conservative_price == 130.0
    assert row.dream_price == 150.0
    assert row.valuation_mode == "MOMENTUM_MARKUP"
    assert row.source == "manual"

    # 第二次跑應 UPSERT 同一筆
    monkeypatch.setattr(
        svc,
        "_call_llm",
        lambda payload, *, model: (
            {**fake_payload, "expectation_result": {**fake_payload["expectation_result"], "dream_price": 160.0}},
            {"status": "ok", "model": model},
        ),
    )
    row2 = svc.generate_for_stock(db_session, "2330", source="manual")
    assert row2.id == row.id  # 同筆
    assert row2.dream_price == 160.0


def test_generate_for_stock_writes_failed_when_llm_fails(db_session, monkeypatch):
    _seed_minimal_stock(db_session)
    monkeypatch.setattr(
        svc,
        "_call_llm",
        lambda payload, *, model: (None, {"status": "openai_exception", "message": "boom", "model": model}),
    )
    row = svc.generate_for_stock(db_session, "2330", source="manual")
    assert row.status == "failed"
    assert row.conservative_price is None
    assert row.error_message == "boom"


# ---------------------------------------------------------------------------
# update_hit_targets
# ---------------------------------------------------------------------------


def test_update_hit_targets_marks_first_touch_only(db_session):
    _seed_minimal_stock(db_session)
    # 直接 insert 一筆 expectation row
    db_session.add(
        SignalExpectationPrice(
            stock_id="2330",
            stock_name="2330名",
            first_detected_date=date(2026, 4, 19),
            conservative_price=110.0,
            dream_price=200.0,
            source="cron",
            status="ok",
        )
    )
    db_session.commit()

    # 4/20 收盤 = 100+19 = 119（超過保守 110，但未到夢想 200）
    hits = svc.update_hit_targets(db_session, date(2026, 4, 20))
    assert hits["conservative_hits"] == 1
    assert hits["dream_hits"] == 0

    # 再跑一次同一日 → 不重複標
    hits2 = svc.update_hit_targets(db_session, date(2026, 4, 20))
    assert hits2["conservative_hits"] == 0
    assert hits2["dream_hits"] == 0

    row = (
        db_session.query(SignalExpectationPrice)
        .filter(SignalExpectationPrice.stock_id == "2330")
        .first()
    )
    assert row.hit_conservative_at == date(2026, 4, 20)
    assert row.hit_dream_at is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_list_expectation_prices_returns_items(api):
    client, db, _ = api
    _seed_minimal_stock(db)
    db.add(
        SignalExpectationPrice(
            stock_id="2330",
            stock_name="2330名",
            first_detected_date=date(2026, 4, 19),
            conservative_price=130.0,
            dream_price=150.0,
            valuation_mode="MOMENTUM_MARKUP",
            source="manual",
            status="ok",
        )
    )
    db.commit()
    res = client.get("/api/signals/expectation-prices?snapshot_date=2026-04-19")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot_date"] == "2026-04-19"
    assert len(body["items"]) == 1
    assert body["items"][0]["stock_id"] == "2330"
    assert body["items"][0]["conservative_price"] == 130.0


def test_get_expectation_price_single(api):
    client, db, _ = api
    _seed_minimal_stock(db)
    db.add(
        SignalExpectationPrice(
            stock_id="2330",
            stock_name="2330名",
            first_detected_date=date(2026, 4, 19),
            conservative_price=130.0,
            dream_price=150.0,
            source="manual",
            status="ok",
        )
    )
    db.commit()
    res = client.get("/api/signals/expectation-prices/2330")
    assert res.status_code == 200
    assert res.json()["dream_price"] == 150.0


def test_get_expectation_price_404(api):
    client, db, _ = api
    res = client.get("/api/signals/expectation-prices/9999")
    assert res.status_code == 404


def test_regenerate_expectation_price_404_when_not_in_hits(api):
    client, db, _ = api
    res = client.post(
        "/api/signals/expectation-prices/regenerate",
        json={"stock_id": "9999"},
    )
    assert res.status_code == 404


def test_regenerate_expectation_price_accepts(api):
    client, db, bg_calls = api
    _seed_minimal_stock(db)
    res = client.post(
        "/api/signals/expectation-prices/regenerate",
        json={"stock_id": "2330"},
    )
    assert res.status_code == 202, res.text
    assert res.json()["stock_id"] == "2330"
    assert bg_calls == ["2330"]


def test_expectation_quota_endpoint(api):
    client, db, _ = api
    res = client.get("/api/signals/expectation-prices/quota")
    assert res.status_code == 200
    body = res.json()
    assert body["daily_limit"] == signals_router.USER_DAILY_EXPECTATION_LIMIT
    assert body["disabled"] is False
