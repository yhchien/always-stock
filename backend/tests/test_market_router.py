from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, InstStockFlow, StockMaster
from app.routers.market import _yf_price


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


def _seed_stock(db, stock_id: str, stock_name: str, industry_name: str) -> None:
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=stock_name,
        industry_name=industry_name,
    ))


def _seed_flow(db, trade_date: date, stock_id: str, inst_type: str, net_amount: float) -> None:
    buy_amount = max(net_amount, 0)
    sell_amount = abs(min(net_amount, 0))
    db.add(InstStockFlow(
        trade_date=trade_date,
        stock_id=stock_id,
        inst_type=inst_type,
        buy_shares=buy_amount / 10 if buy_amount else 0,
        sell_shares=sell_amount / 10 if sell_amount else 0,
        net_shares=(buy_amount - sell_amount) / 10,
        buy_amount_est=buy_amount,
        sell_amount_est=sell_amount,
        net_amount_est=net_amount,
    ))


def test_daily_brief_uses_fallback_when_openai_key_missing(api):
    client, db = api
    trade_date = date(2024, 1, 3)
    _seed_stock(db, "2330", "台積電", "半導體")
    _seed_flow(db, trade_date, "2330", "foreign", 1500000)
    _seed_flow(db, trade_date, "2330", "trust", 500000)
    _seed_flow(db, trade_date, "2330", "dealer", -100000)
    db.commit()

    with patch("app.routers.market.get_openai_api_key", return_value=""), \
         patch("app.routers.market._yf_price", return_value=[]):
        resp = client.get("/api/market/daily-brief?date=2024-01-03")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["trade_date"] == "2024-01-03"
    assert payload["source"] == "unavailable"
    assert "市場因子分析" in payload["content"]
    assert "綜合判斷" in payload["content"]
    assert "推薦關注個股" in payload["content"]


def test_daily_brief_falls_back_when_openai_call_fails(api):
    client, db = api
    trade_date = date(2024, 1, 3)
    _seed_stock(db, "2330", "台積電", "半導體")
    _seed_stock(db, "2317", "鴻海", "電子代工")
    _seed_flow(db, trade_date, "2330", "foreign", 1000000)
    _seed_flow(db, trade_date, "2317", "foreign", 800000)
    db.commit()

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("boom")

    with patch("app.routers.market.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.market.OpenAI", return_value=mock_client), \
         patch("app.routers.market._yf_price", return_value=[]):
        resp = client.get("/api/market/daily-brief?date=2024-01-03")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source"] == "unavailable"
    assert "前1日買超前三細產業" in payload["content"]


def test_yf_price_anchors_to_requested_trade_date():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "chart": {
            "result": [{
                "timestamp": [1703980800, 1704067200, 1704153600, 1704240000],
                "indicators": {
                    "quote": [{
                        "close": [17.0, 18.0, 19.0, 20.0],
                    }]
                },
            }]
        }
    }
    mock_response.raise_for_status.return_value = None

    with patch("app.routers.market.requests.get", return_value=mock_response) as mock_get:
        rows = _yf_price("^VIX", days=3, end_date=date(2024, 1, 2))

    assert [row["date"] for row in rows] == ["2023-12-31", "2024-01-01", "2024-01-02"]
    params = mock_get.call_args.kwargs["params"]
    assert "period1" in params
    assert "period2" in params
    assert "range" not in params
