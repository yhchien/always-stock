"""迴歸測試：`GET /api/signals/shadow-portfolio` 必須依 `strategy_version` query
param 回傳該策略自己的資金/曝險規則，不能不管傳入哪個版本都套用 v1 的門檻值——這是
2026-09 加入 UI 版本切換時發現並修好的既有 bug（改動前 `max_stocks`／
`max_units_per_stock`／`max_total_units`／`initial_capital` 全部硬編碼
`V1_STRATEGY_PARAMS`）。
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    ShadowCompletedTrade,
    ShadowPortfolioDailySnapshot,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
)
from app.signals import shadow_portfolio as sp


@pytest.fixture
def api():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_shadow_portfolio_endpoint_returns_v1_caps_for_v1_frozen(api):
    client, db = api
    db.add(ShadowVirtualPortfolio(strategy_version="v1_frozen", cash=600000.0))
    db.commit()

    res = client.get("/api/signals/shadow-portfolio", params={"strategy_version": "v1_frozen"})
    assert res.status_code == 200
    body = res.json()
    # 2026-09-09 第二輪重寫：拿掉全域 5 檔上限（PART 14），9 是兩個資金桶各自
    # 容量上限的理論最大值（6×5萬 Starter + 3×10萬 Pullback）；max_total_units
    # 也不再是固定值（改由兩個桶各自的容量限制，不是單一 unit 數上限）。
    assert body["max_stocks"] == 9
    assert body["max_units_per_stock"] == 2
    assert body["max_total_units"] is None
    assert body["max_position_exposure_pct"] is None
    assert body["cycle_length_trading_days"] == 35


def test_shadow_portfolio_endpoint_returns_forward_v1_caps_not_v1_hardcode(api):
    client, db = api
    db.add(ShadowVirtualPortfolio(strategy_version="FORWARD_V1_202609", cash=600000.0))
    db.commit()

    res = client.get("/api/signals/shadow-portfolio", params={"strategy_version": "FORWARD_V1_202609"})
    assert res.status_code == 200
    body = res.json()
    assert body["max_stocks"] == 5
    # 迴歸重點：這兩個過去會被誤填成 v1 的 2 / 6，FORWARD_V1_202609 沒有這兩個上限
    assert body["max_units_per_stock"] is None
    assert body["max_total_units"] is None
    assert body["max_position_exposure_pct"] == pytest.approx(0.50)
    assert body["cycle_length_trading_days"] is None  # 無強制循環重置


def test_shadow_portfolio_endpoint_unknown_strategy_version_falls_back_to_v1(api):
    client, _db = api
    res = client.get("/api/signals/shadow-portfolio", params={"strategy_version": "TYPO_VERSION"})
    assert res.status_code == 200
    body = res.json()
    # strategy_version 欄位本身照原樣回傳（不偷改使用者傳入的值），但參數 fallback 回 v1
    assert body["strategy_version"] == "TYPO_VERSION"
    assert body["max_stocks"] == 9
    assert body["max_units_per_stock"] == 2


def test_shadow_history_endpoint_groups_daily_performance_and_transactions(api):
    client, db = api
    db.add_all(
        [
            ShadowPortfolioDailySnapshot(
                strategy_version="v1_frozen",
                trade_date=date(2026, 8, 3),
                cash=500000.0,
                invested_cost=100000.0,
                market_value=101000.0,
                total_equity=601000.0,
                total_return_pct=0.1666667,
                realized_pnl=0.0,
                unrealized_pnl=1000.0,
                position_count=1,
                total_units=1,
            ),
            ShadowPortfolioDailySnapshot(
                strategy_version="v1_frozen",
                trade_date=date(2026, 8, 4),
                cash=500000.0,
                invested_cost=100000.0,
                market_value=102000.0,
                total_equity=602000.0,
                total_return_pct=0.3333333,
                realized_pnl=0.0,
                unrealized_pnl=2000.0,
                position_count=1,
                total_units=1,
            ),
            ShadowStrategyOrder(
                strategy_version="v1_frozen",
                stock_id="2330",
                stock_name="台積電",
                action="BUY",
                signal_date=date(2026, 8, 3),
                scheduled_execution_date=date(2026, 8, 4),
                status="EXECUTED",
                units=1,
                planned_amount=100000.0,
                execution_price=1000.0,
            ),
            ShadowCompletedTrade(
                strategy_version="v1_frozen",
                cycle_number=1,
                stock_id="2330",
                stock_name="台積電",
                entry_type="EARLY_HEALTHY_PULLBACK",
                entry_signal_date=date(2026, 8, 3),
                entry_execution_date=date(2026, 8, 4),
                entry_price=1000.0,
                exit_reason="P4_STOP",
                exit_signal_date=date(2026, 8, 4),
                exit_execution_date=date(2026, 8, 4),
                exit_price=1020.0,
                shares=100.0,
                allocation=100000.0,
                realized_pnl=2000.0,
                realized_return_pct=2.0,
                holding_days=1,
                followed_by_rotation=False,
            ),
        ]
    )
    db.commit()

    res = client.get(
        "/api/signals/shadow-portfolio/history",
        params={"strategy_version": "v1_frozen", "start_date": "2026-08-01", "end_date": "2026-09-07"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["trading_day_count"] == 2
    assert body["period_return_pct"] == pytest.approx(0.3333333)
    assert body["trading_days"][1]["trade_date"] == "2026-08-04"
    assert len(body["trading_days"][1]["executed_orders"]) == 1
    assert len(body["trading_days"][1]["completed_trades"]) == 1
