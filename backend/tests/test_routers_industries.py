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
        assert "streak" in item

    def test_streak_positive_consecutive_buy(self, api):
        """Streak should be positive when multiple consecutive days have positive net amount."""
        client, db = api
        # Seed 3 consecutive positive days
        for i, d in enumerate([date(2025, 4, 1), date(2025, 3, 31), date(2025, 3, 28)]):
            db.add(IndustryDailyFlow(
                trade_date=d, industry_name="半導體",
                total_net_amount=1000 + i, total_buy_amount=2000, total_sell_amount=1000,
                foreign_net_amount=800, trust_net_amount=100, dealer_net_amount=100,
            ))
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["streak"] == 3

    def test_streak_negative_consecutive_sell(self, api):
        """Streak should be negative when consecutive days have negative net amount."""
        client, db = api
        for d in [date(2025, 4, 1), date(2025, 3, 31)]:
            db.add(IndustryDailyFlow(
                trade_date=d, industry_name="半導體",
                total_net_amount=-500, total_buy_amount=1000, total_sell_amount=1500,
                foreign_net_amount=-400, trust_net_amount=-50, dealer_net_amount=-50,
            ))
        # Day before that was positive — breaks the streak
        db.add(IndustryDailyFlow(
            trade_date=date(2025, 3, 28), industry_name="半導體",
            total_net_amount=100, total_buy_amount=1100, total_sell_amount=1000,
            foreign_net_amount=80, trust_net_amount=10, dealer_net_amount=10,
        ))
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        item = resp.json()[0]
        assert item["streak"] == -2


class TestGetIndustryStocks:
    def test_returns_stocks_for_industry(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓代工", chain="中游")
        seed_stock(db, "5347", "世界先進", "半導體", sub_industry="晶圓代工", chain="中游")
        seed_price(db, "2330", 1000.0)
        seed_price(db, "5347", 200.0)
        seed_flow(db, "2330", "foreign", 500, 500000)
        seed_flow(db, "2330", "trust",   100, 100000)
        seed_flow(db, "2330", "dealer",  50,   50000)
        seed_flow(db, "5347", "foreign", 200, 40000)
        seed_flow(db, "5347", "trust",     0, 0)
        seed_flow(db, "5347", "dealer",    0, 0)
        db.commit()

        resp = client.get(f"/api/industries/半導體/stocks?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["stock_id"] == "2330"
        # Verify price change fields are present
        assert "price_change" in data[0]
        assert "price_change_pct" in data[0]

    def test_404_for_unknown_industry(self, api):
        client, db = api
        resp = client.get(f"/api/industries/不存在的產業/stocks?date={TRADE_DATE}")
        assert resp.status_code == 404

    def test_fallback_from_twse_industry_name(self, api):
        client, db = api
        seed_stock(db, "1101", "台泥", "水泥", sub_industry="水泥", chain="中游")
        seed_price(db, "1101", 42.0)
        seed_flow(db, "1101", "foreign", 100, 4200)
        seed_flow(db, "1101", "trust", 0, 0)
        seed_flow(db, "1101", "dealer", 0, 0)
        db.commit()

        resp = client.get(f"/api/industries/水泥工業/stocks?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_id"] == "1101"
        assert data[0]["industry_name"] == "水泥"

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


class TestGetSubIndustrySummary:
    def test_returns_sub_industry_summary(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓製造", chain="上游")
        seed_stock(db, "3711", "日月光投控", "半導體", sub_industry="IC封裝測試", chain="下游")
        seed_flow(db, "2330", "foreign", 500, 500000)
        seed_flow(db, "2330", "trust", 100, 100000)
        seed_flow(db, "2330", "dealer", 50, 50000)
        seed_flow(db, "3711", "foreign", 200, 30000)
        seed_flow(db, "3711", "trust", 0, 0)
        seed_flow(db, "3711", "dealer", 0, 0)
        db.commit()

        resp = client.get(f"/api/industries/半導體/summary?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        subs = {d["sub_industry"] for d in data}
        assert "晶圓製造" in subs
        assert "IC封裝測試" in subs

    def test_summary_includes_chain_and_streak(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓製造", chain="上游")
        seed_flow(db, "2330", "foreign", 500, 500000)
        db.commit()

        resp = client.get(f"/api/industries/半導體/summary?date={TRADE_DATE}")
        item = resp.json()[0]
        assert "chain" in item
        assert item["chain"] == "上游"
        assert "streak" in item
        assert "total_net_amount" in item
        assert "foreign_net_amount" in item

    def test_summary_404_for_unknown_industry(self, api):
        client, db = api
        resp = client.get(f"/api/industries/不存在的產業/summary?date={TRADE_DATE}")
        assert resp.status_code == 404

    def test_summary_fallback_from_twse_industry_name(self, api):
        client, db = api
        seed_stock(db, "1216", "統一", "食品", sub_industry="食品加工", chain="中游")
        seed_flow(db, "1216", "foreign", 300, 300000)
        seed_flow(db, "1216", "trust", 100, 100000)
        seed_flow(db, "1216", "dealer", 0, 0)
        db.commit()

        resp = client.get(f"/api/industries/食品工業/summary?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sub_industry"] == "食品加工"

