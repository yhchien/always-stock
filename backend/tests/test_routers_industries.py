"""
tests for app/routers/industries.py
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models import Base, DailyPrice, IndustryDailyFlow, InstStockFlow, StockMaster
from app.database import get_db

TRADE_DATE = date(2025, 4, 1)


@pytest.fixture
def api(db):
    """
    回傳 (TestClient, session)。
    用 StaticPool 讓 in-memory SQLite 跨 thread 共享同一個連線。
    """
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

def seed_industry(db, industry_name, total_net, foreign_net, trust_net, dealer_net):
    db.add(IndustryDailyFlow(
        trade_date=TRADE_DATE,
        industry_name=industry_name,
        total_net_amount=total_net,
        total_buy_amount=abs(total_net) * 2,
        total_sell_amount=abs(total_net),
        foreign_net_amount=foreign_net,
        trust_net_amount=trust_net,
        dealer_net_amount=dealer_net,
    ))


def seed_stock(db, stock_id, name, industry, sub_industry=None, chain=None):
    db.add(StockMaster(
        stock_id=stock_id, stock_name=name,
        industry_name=industry, sub_industry=sub_industry, chain=chain,
    ))


def seed_price(db, stock_id, close_price):
    db.add(DailyPrice(
        trade_date=TRADE_DATE, stock_id=stock_id,
        close_price=close_price, volume=1000000,
        turnover=close_price * 1000000, avg_price=close_price,
    ))


def seed_flow(db, stock_id, inst_type, net_shares, net_amount):
    db.add(InstStockFlow(
        trade_date=TRADE_DATE, stock_id=stock_id, inst_type=inst_type,
        buy_shares=abs(net_shares), sell_shares=0,
        net_shares=net_shares,
        buy_amount_est=abs(net_amount), sell_amount_est=0,
        net_amount_est=net_amount,
    ))


# ── tests ─────────────────────────────────────────────────────────────────────

class TestGetIndustries:
    def test_returns_sorted_list(self, api):
        client, db = api
        seed_industry(db, "晶圓代工", 1000, 800, 100, 100)
        seed_industry(db, "記憶體IC", 500, 400, 50, 50)
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["industry_name"] == "晶圓代工"
        assert data[1]["industry_name"] == "記憶體IC"

    def test_returns_404_when_no_data(self, api):
        client, db = api
        resp = client.get("/api/industries?date=2000-01-01")
        assert resp.status_code == 404

    def test_returns_correct_fields(self, api):
        client, db = api
        seed_industry(db, "晶圓代工", 1000, 800, 100, 100)
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        item = resp.json()[0]
        assert "industry_name" in item
        assert "total_net_amount" in item
        assert "foreign_net_amount" in item
        assert "trust_net_amount" in item
        assert "dealer_net_amount" in item


class TestGetIndustryStocks:
    def test_returns_stocks_for_sub_industry(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體業", sub_industry="晶圓代工", chain="中游")
        seed_stock(db, "5347", "世界先進", "半導體業", sub_industry="晶圓代工", chain="中游")
        seed_price(db, "2330", 1000.0)
        seed_price(db, "5347", 200.0)
        seed_flow(db, "2330", "foreign", 500, 500000)
        seed_flow(db, "2330", "trust",   100, 100000)
        seed_flow(db, "2330", "dealer",  50,   50000)
        seed_flow(db, "5347", "foreign", 200, 40000)
        seed_flow(db, "5347", "trust",     0, 0)
        seed_flow(db, "5347", "dealer",    0, 0)
        db.commit()

        resp = client.get(f"/api/industries/晶圓代工/stocks?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["stock_id"] == "2330"

    def test_404_for_unknown_industry(self, api):
        client, db = api
        resp = client.get(f"/api/industries/不存在的產業/stocks?date={TRADE_DATE}")
        assert resp.status_code == 404

    def test_fallback_to_industry_name(self, api):
        client, db = api
        seed_stock(db, "2454", "聯發科", "IC設計業", sub_industry=None)
        seed_price(db, "2454", 800.0)
        seed_flow(db, "2454", "foreign", 100, 80000)
        seed_flow(db, "2454", "trust",     0, 0)
        seed_flow(db, "2454", "dealer",    0, 0)
        db.commit()

        resp = client.get(f"/api/industries/IC設計業/stocks?date={TRADE_DATE}")
        assert resp.status_code == 200
        assert resp.json()[0]["stock_id"] == "2454"
