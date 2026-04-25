"""Tests for GET /api/analysis/context (M21)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, DailyPrice, IndustryDailyFlow, StockMaster


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


def _register_and_login(client: TestClient, email: str = "alice@example.com") -> None:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": "passw0rd!"},
    )
    assert res.status_code == 200, res.text


def _seed_stock(db, stock_id: str, stock_name: str = "測試股", industry: str = "AI 伺服器") -> None:
    db.add(
        StockMaster(
            stock_id=stock_id,
            stock_name=stock_name,
            industry_name=industry,
            is_active=True,
        )
    )


def _seed_prices(db, stock_id: str, end_date: date, n_days: int = 25) -> None:
    for i in range(n_days):
        d = date.fromordinal(end_date.toordinal() - (n_days - 1 - i))
        db.add(
            DailyPrice(
                trade_date=d,
                stock_id=stock_id,
                close_price=100.0 + i,
                volume=100.0,
                turnover=100.0,
            )
        )


def _seed_industry_flow(db, d: date, industry: str = "AI 伺服器") -> None:
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


# ── Auth ──────────────────────────────────────────────────────────────────


def test_requires_login(api):
    client, _ = api
    res = client.get("/api/analysis/context", params={"stock_id": "A1"})
    assert res.status_code == 401


# ── Happy path ────────────────────────────────────────────────────────────


def test_returns_schema_for_known_stock(api):
    client, db = api
    _seed_stock(db, "A1", stock_name="光寶科")
    _seed_stock(db, "A2")
    _seed_stock(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, date(2026, 4, 22))
    db.commit()
    _register_and_login(client)

    res = client.get(
        "/api/analysis/context",
        params={"stock_id": "A1", "buy_date": "2026-04-22"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stock_id"] == "A1"
    assert body["buy_date"] == "2026-04-22"
    for key in (
        "industry_summary",
        "chip_summary",
        "peer_rank",
        "fundamental",
        "price_structure",
        "news_input_stub",
        "data_quality_notes",
    ):
        assert key in body


# ── buy_date fallback ────────────────────────────────────────────────────


def test_fallbacks_to_latest_trade_date_when_absent(api):
    client, db = api
    _seed_stock(db, "A1", stock_name="光寶科")
    _seed_stock(db, "A2")
    _seed_stock(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, date(2026, 4, 22))
    # industry_daily_flow 才是 get_latest_industry_trade_date 的來源
    _seed_industry_flow(db, date(2026, 4, 20))
    _seed_industry_flow(db, date(2026, 4, 22))
    db.commit()
    _register_and_login(client)

    res = client.get("/api/analysis/context", params={"stock_id": "A1"})
    assert res.status_code == 200, res.text
    assert res.json()["buy_date"] == "2026-04-22"


def test_returns_404_when_no_trade_dates(api):
    client, _ = api
    _register_and_login(client)

    res = client.get("/api/analysis/context", params={"stock_id": "A1"})
    assert res.status_code == 404


# ── Unknown stock ────────────────────────────────────────────────────────


def test_returns_404_for_unknown_stock(api):
    client, db = api
    _seed_industry_flow(db, date(2026, 4, 22))
    db.commit()
    _register_and_login(client)

    res = client.get(
        "/api/analysis/context",
        params={"stock_id": "NOPE", "buy_date": "2026-04-22"},
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]
