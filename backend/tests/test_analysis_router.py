"""Tests for /api/analysis/trade-quality and supporting endpoints."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

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
    IndustryDailyFlow,
    InstStockFlow,
    MonthlyRevenue,
    StockMaster,
)
from app.routers.analysis import _load_system_prompt


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


def _seed_stock(db, stock_id: str, stock_name: str, industry: str = "半導體") -> None:
    db.add(StockMaster(stock_id=stock_id, stock_name=stock_name, industry_name=industry))


def _seed_price(db, d: date, stock_id: str, close: float) -> None:
    db.add(
        DailyPrice(
            trade_date=d,
            stock_id=stock_id,
            open_price=close - 1,
            high_price=close + 1,
            low_price=close - 2,
            close_price=close,
            volume=1_000_000,
            turnover=close * 1_000_000,
            spread=0.5,
        )
    )


def _seed_flow(db, d: date, stock_id: str, inst_type: str, net_shares: float) -> None:
    db.add(
        InstStockFlow(
            trade_date=d,
            stock_id=stock_id,
            inst_type=inst_type,
            net_shares=net_shares,
            net_amount_est=net_shares * 100,
        )
    )


def _seed_industry_flow(db, d: date, industry: str) -> None:
    db.add(
        IndustryDailyFlow(
            trade_date=d,
            industry_name=industry,
            foreign_net_amount=0,
            trust_net_amount=0,
            dealer_net_amount=0,
            total_net_amount=0,
        )
    )


# ── /api/stocks/search ──────────────────────────────────────────────────────


def test_stocks_search_by_id_prefix(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2337", "旺宏")
    _seed_stock(db, "2317", "鴻海")
    db.commit()

    resp = client.get("/api/stocks/search?q=233")
    assert resp.status_code == 200
    data = resp.json()
    ids = [row["stock_id"] for row in data]
    assert "2330" in ids
    assert "2337" in ids
    assert "2317" not in ids


def test_stocks_search_by_name(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2454", "聯發科")
    db.commit()

    resp = client.get("/api/stocks/search?q=聯發")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["stock_id"] == "2454"


def test_stocks_search_rejects_empty_query(api):
    client, _ = api
    resp = client.get("/api/stocks/search?q=")
    assert resp.status_code == 422  # pydantic min_length=1


# ── /api/market/latest-trade-date ───────────────────────────────────────────


def test_latest_trade_date_returns_max(api):
    client, db = api
    _seed_industry_flow(db, date(2024, 1, 2), "半導體")
    _seed_industry_flow(db, date(2024, 1, 5), "半導體")
    db.commit()

    resp = client.get("/api/market/latest-trade-date")
    assert resp.status_code == 200
    assert resp.json()["trade_date"] == "2024-01-05"


def test_latest_trade_date_null_when_empty(api):
    client, _ = api
    resp = client.get("/api/market/latest-trade-date")
    assert resp.status_code == 200
    assert resp.json()["trade_date"] is None


# ── /api/analysis/trade-quality ─────────────────────────────────────────────


def _seed_full_context(db, stock_id: str = "2330") -> None:
    _seed_stock(db, stock_id, "台積電", "半導體")
    for i in range(10):
        d = date(2024, 1, 2 + i)
        _seed_industry_flow(db, d, "半導體")
        _seed_price(db, d, stock_id, close=600 + i * 2)
        _seed_flow(db, d, stock_id, "foreign", 1000 * (i + 1))
        _seed_flow(db, d, stock_id, "trust", 500)
        _seed_flow(db, d, stock_id, "dealer", -200)
    db.add(
        MonthlyRevenue(
            revenue_month=date(2023, 12, 31),
            stock_id=stock_id,
            revenue=200000,
            yoy_pct=15.0,
            mom_pct=5.0,
        )
    )
    db.commit()


def test_trade_quality_unavailable_when_openai_key_missing(api):
    client, db = api
    _seed_full_context(db)

    with patch("app.routers.analysis.get_openai_api_key", return_value=""):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["stock_id"] == "2330"
    assert payload["stock_name"] == "台積電"
    assert payload["source"] == "unavailable"
    assert payload["rating"] == "WATCH"
    assert payload["rating_label"] == "再看看"
    assert "OpenAI 服務不可用" in payload["warnings"]


def test_trade_quality_parses_openai_json(api):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "stock": "台積電 (2330)",
        "buy_date": "2024-01-11",
        "classification": "A",
        "classification_reason": "外資連買、均線多頭",
        "action": "BUY",
        "core_logic": "AI 需求帶動晶圓代工訂單",
        "risk_level": "MEDIUM",
        "rating": "STRONG_BUY",
        "summary": "近 10 日法人連買、月營收 YoY +15%，屬結構性成長階段。",
        "target_price_low": 650,
        "target_price_high": 720,
        "time_horizon_days": 60,
        "exit_price_low": None,
        "exit_price_high": None,
        "max_holding_days": None,
        "report_markdown": "## 股票：台積電\n\n完整分析段落...",
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    assert data["rating"] == "STRONG_BUY"
    assert data["rating_label"] == "強烈推薦"
    assert data["classification"] == "A"
    assert data["target_price_low"] == 650
    assert data["target_price_high"] == 720
    assert data["time_horizon_days"] == 60
    assert "台積電" in data["report_markdown"]


def test_trade_quality_resolves_buy_date_when_absent(api):
    client, db = api
    _seed_full_context(db, stock_id="2330")

    captured = {}

    def fake_create(*args, **kwargs):
        messages = kwargs["messages"]
        captured["user_msg"] = messages[1]["content"]
        return MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps({
                    "rating": "NEUTRAL",
                    "summary": "中立",
                    "report_markdown": "report",
                })
            ))]
        )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post("/api/analysis/trade-quality", json={"stock_id": "2330"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["buy_date"] == "2024-01-11"  # last seeded industry flow date
    assert '"buy_date": "2024-01-11"' in captured["user_msg"]


def test_trade_quality_falls_back_when_openai_returns_invalid_json(api):
    client, db = api
    _seed_full_context(db)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="not-json"))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "unavailable"
    assert data["rating"] == "WATCH"


def test_trade_quality_retries_once_when_openai_returns_invalid_json(api):
    client, db = api
    _seed_full_context(db)

    valid_payload = {
        "rating": "BUY",
        "summary": "重試後成功",
        "report_markdown": "retry-ok",
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"rating":"BUY","summary":"broken'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(valid_payload)))]),
    ]

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    assert data["rating"] == "BUY"
    assert data["summary"] == "重試後成功"
    assert mock_client.chat.completions.create.call_count == 2


def test_trade_quality_returns_404_for_unknown_stock(api):
    client, db = api
    _seed_industry_flow(db, date(2024, 1, 5), "半導體")
    db.commit()

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "9999", "buy_date": "2024-01-05"},
        )

    assert resp.status_code == 404


def test_trade_quality_prompt_can_be_loaded():
    prompt = _load_system_prompt()
    assert "buy-side research analyst" in prompt
    assert "時空隔離" in prompt
