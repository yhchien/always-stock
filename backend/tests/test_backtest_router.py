from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest_catalog import DEFAULT_STRATEGY_TEXT
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


def test_backtest_templates(api):
    client, _ = api
    response = client.get("/api/backtest/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["strategy_text"]
