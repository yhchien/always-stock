from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest_catalog import DEFAULT_STRATEGY_TEXT
from app.models import DailyPrice
from app.database import get_db
from app.main import app
from app.models import Base

from test_routers_stocks import seed_flow, seed_price, seed_stock

BASE_DATE = date(2024, 1, 1)


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


def seed_backtest_dataset(db, stock_id="2330"):
    seed_stock(db, stock_id=stock_id, name="台積電", industry="半導體業")

    for offset in range(30):
        trade_date = BASE_DATE + timedelta(days=offset)
        close = 100.0 if offset < 20 else 120.0 + offset
        seed_price(db, stock_id, trade_date, close, open_p=close)
        foreign_net = 1000 if 20 <= offset <= 22 else (-500 if offset >= 24 else 0)
        seed_flow(db, stock_id, trade_date, "foreign", foreign_net)
        seed_flow(db, stock_id, trade_date, "trust", 0)
        seed_flow(db, stock_id, trade_date, "dealer", 0)

    db.commit()


def test_interpret_backtest_strategy(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["strategy"]["entry_rules"][0]["indicator"] == "close_above_ma"
    assert payload["strategy"]["entry_rules"][1]["indicator"] == "foreign_consecutive_buy"
    assert payload["strategy"]["exit_rules"][1]["indicator"] == "foreign_net_negative"


def test_run_backtest_returns_metrics_and_trades(api):
    client, db = api
    seed_backtest_dataset(db)

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["metrics"]["trade_count"] >= 1
    assert payload["metrics"]["sharpe_ratio"] >= 0
    assert len(payload["equity_curve"]) == 30
    assert payload["trades"][0]["entry_date"] < payload["trades"][0]["exit_date"]
    assert payload["latest_recommendation"]["action"] in {"wait", "observe_buy", "observe_sell", "hold"}
    assert payload["unsupported_conditions"] == []


def test_backtest_templates(api):
    client, _ = api
    response = client.get("/api/backtest/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["strategy_text"]


def test_backtest_advice_returns_structured_response(api):
    client, _ = api
    response = client.post(
        "/api/backtest/advice",
        json={
            "stock_id": "2330",
            "strategy_text": DEFAULT_STRATEGY_TEXT,
            "normalized_text": "買進：收盤價站上 MA20 且外資連買 3 天；賣出：跌破 MA20 或外資賣超",
            "metrics": {
                "total_return_pct": 10.0,
                "annual_return_pct": 8.0,
                "win_rate_pct": 60.0,
                "max_drawdown_pct": -8.0,
                "sharpe_ratio": 1.1,
                "trade_count": 4,
                "avg_holding_days": 12.0,
            },
            "trades": [],
            "latest_recommendation": {
                "latest_signal_date": "2024-01-30",
                "action": "hold",
                "reason": "目前仍持有部位，且尚未出現新的出場訊號。",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]
    assert payload["strengths"]
    assert payload["rewrite_suggestions"]


def test_interpret_returns_unsupported_conditions(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且突破60日高點就買進；收盤價跌破20日均線就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is False
    assert "突破60日高點" in payload["unsupported_conditions"]
    assert payload["warnings"]


def test_run_backtest_rejects_unsupported_conditions(api):
    client, db = api
    seed_backtest_dataset(db)

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且突破60日高點就買進；收盤價跌破20日均線就賣出",
        },
    )

    assert response.status_code == 422
    assert "Unsupported strategy conditions" in response.json()["detail"]


def test_run_backtest_returns_lookback_and_missing_open_warnings(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    for offset in range(10):
        trade_date = BASE_DATE + timedelta(days=offset)
        db.add(DailyPrice(
            stock_id="2330",
            trade_date=trade_date,
            open_price=None,
            high_price=101.0 + offset,
            low_price=99.0 + offset,
            close_price=100.0 + offset,
            volume=1000000,
            turnover=(100.0 + offset) * 1000000,
            avg_price=100.0 + offset,
        ))
        seed_flow(db, "2330", trade_date, "foreign", 1000 if offset >= 2 else 0)
        seed_flow(db, "2330", trade_date, "trust", 0)
        seed_flow(db, "2330", trade_date, "dealer", 0)
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any("開盤價" in warning for warning in warnings)
    assert any("lookback" in warning for warning in warnings)


def test_run_backtest_rejects_invalid_date_range(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-02-01",
            "end_date": "2024-01-01",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 422
    assert "start_date" in response.json()["detail"]


def test_interpret_rejects_blank_strategy(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "   ",
        },
    )

    assert response.status_code == 422
    assert "blank" in response.json()["detail"]
