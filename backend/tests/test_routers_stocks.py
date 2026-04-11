"""
tests for app/routers/stocks.py
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models import Base, DailyPrice, InstStockFlow, StockMaster
from app.database import get_db

BASE_DATE = date(2025, 4, 1)


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


# ── seed helpers ──────────────────────────────────────────────────────────────

def seed_stock(db, stock_id="2344", name="華邦電", industry="半導體業", sub_industry="記憶體IC"):
    db.add(StockMaster(
        stock_id=stock_id, stock_name=name,
        industry_name=industry, sub_industry=sub_industry,
    ))


def seed_price(db, stock_id, trade_date, close, open_p=None, high=None, low=None):
    db.add(DailyPrice(
        trade_date=trade_date, stock_id=stock_id,
        open_price=(close - 1) if open_p is None else open_p,
        high_price=(close + 1) if high is None else high,
        low_price=(close - 2) if low is None else low,
        close_price=close, volume=1000000,
        turnover=close * 1000000, avg_price=close,
    ))


def seed_flow(db, stock_id, trade_date, inst_type, net_shares):
    db.add(InstStockFlow(
        trade_date=trade_date, stock_id=stock_id, inst_type=inst_type,
        buy_shares=max(net_shares, 0), sell_shares=max(-net_shares, 0),
        net_shares=net_shares,
        buy_amount_est=0, sell_amount_est=0, net_amount_est=0,
    ))


# ── tests ─────────────────────────────────────────────────────────────────────

class TestGetStockHistory:
    def test_returns_history(self, api):
        client, db = api
        seed_stock(db)
        seed_price(db, "2344", BASE_DATE - timedelta(days=1), 90.0)
        seed_price(db, "2344", BASE_DATE, 91.0)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "foreign", 1000)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "trust",    200)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "dealer",   -50)
        seed_flow(db, "2344", BASE_DATE, "foreign", -500)
        seed_flow(db, "2344", BASE_DATE, "trust",    100)
        seed_flow(db, "2344", BASE_DATE, "dealer",     0)
        db.commit()

        resp = client.get(f"/api/stocks/2344/history?days=5&end_date={BASE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock_id"] == "2344"
        assert data["stock_name"] == "華邦電"
        assert data["sub_industry"] == "記憶體IC"
        assert len(data["history"]) == 2
        # Verify OHLC fields are returned
        item = data["history"][1]
        assert item["open_price"] == 90.0   # close - 1
        assert item["high_price"] == 92.0   # close + 1
        assert item["low_price"] == 89.0    # close - 2
        assert item["close_price"] == 91.0

    def test_cumulative_net_shares(self, api):
        client, db = api
        seed_stock(db)
        seed_price(db, "2344", BASE_DATE - timedelta(days=1), 90.0)
        seed_price(db, "2344", BASE_DATE, 91.0)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "foreign", 1000)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "trust",   0)
        seed_flow(db, "2344", BASE_DATE - timedelta(days=1), "dealer",  0)
        seed_flow(db, "2344", BASE_DATE, "foreign", -300)
        seed_flow(db, "2344", BASE_DATE, "trust",   0)
        seed_flow(db, "2344", BASE_DATE, "dealer",  0)
        db.commit()

        resp = client.get(f"/api/stocks/2344/history?days=5&end_date={BASE_DATE}")
        history = resp.json()["history"]
        assert history[0]["foreign_cumulative"] == 1000.0
        assert history[1]["foreign_cumulative"] == 700.0

    def test_404_for_unknown_stock(self, api):
        client, db = api
        resp = client.get(f"/api/stocks/9999/history?end_date={BASE_DATE}")
        assert resp.status_code == 404

    def test_404_when_no_price_data(self, api):
        client, db = api
        seed_stock(db)
        db.commit()
        resp = client.get(f"/api/stocks/2344/history?end_date={BASE_DATE}")
        assert resp.status_code == 404

    def test_days_with_no_flow_default_zero(self, api):
        client, db = api
        seed_stock(db)
        seed_price(db, "2344", BASE_DATE, 91.0)
        db.commit()

        resp = client.get(f"/api/stocks/2344/history?days=3&end_date={BASE_DATE}")
        history = resp.json()["history"]
        assert history[0]["foreign_net_shares"] == 0.0
        assert history[0]["foreign_cumulative"] == 0.0
