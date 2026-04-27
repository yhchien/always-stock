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
from app.routers import industries as industries_router
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

    industries_router._industries_cache.clear()
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    industries_router._industries_cache.clear()
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
        streak=1 if total_net > 0 else -1 if total_net < 0 else 0,
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
    def test_reuses_cache_for_same_resolved_date(self, api, monkeypatch):
        client, db = api
        seed_industry(db, "晶圓代工", 1000, 800, 100, 100)
        db.commit()

        industries_router._industries_cache.clear()

        original_load = industries_router.load_industry_flow_rows
        calls = {"count": 0}

        def wrapped_load(*args, **kwargs):
            calls["count"] += 1
            return original_load(*args, **kwargs)

        monkeypatch.setattr(industries_router, "load_industry_flow_rows", wrapped_load)

        resp1 = client.get(f"/api/industries?date={TRADE_DATE}")
        resp2 = client.get(f"/api/industries?date={TRADE_DATE}")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert calls["count"] == 1

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

    def test_resolves_to_previous_trade_date_when_requested_date_has_no_rows(self, api):
        client, db = api
        db.add(IndustryDailyFlow(
            trade_date=date(2025, 3, 31),
            industry_name="半導體",
            total_net_amount=1000,
            total_buy_amount=2000,
            total_sell_amount=1000,
            foreign_net_amount=700,
            trust_net_amount=200,
            dealer_net_amount=100,
        ))
        db.commit()

        resp = client.get("/api/industries?date=2025-04-01")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["industry_name"] == "半導體"

    def test_falls_back_to_inst_flow_when_agg_table_is_missing(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體")
        seed_flow(db, "2330", "foreign", 500, 500000)
        seed_flow(db, "2330", "trust", 100, 100000)
        seed_flow(db, "2330", "dealer", -50, -50000)
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["industry_name"] == "半導體"
        assert data[0]["total_net_amount"] == 550000

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

    def test_uses_persisted_streak_without_history_scan(self, api):
        client, db = api
        db.add(IndustryDailyFlow(
            trade_date=TRADE_DATE,
            industry_name="半導體",
            total_net_amount=1000,
            total_buy_amount=2000,
            total_sell_amount=1000,
            foreign_net_amount=700,
            trust_net_amount=200,
            dealer_net_amount=100,
            streak=7,
        ))
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        assert resp.json()[0]["streak"] == 7

    def test_canonicalizes_legacy_industry_names_in_l0_response(self, api):
        client, db = api
        db.add(IndustryDailyFlow(
            trade_date=TRADE_DATE,
            industry_name="塑膠工業",
            total_net_amount=40,
            total_buy_amount=80,
            total_sell_amount=40,
            foreign_net_amount=30,
            trust_net_amount=5,
            dealer_net_amount=5,
            streak=2,
        ))
        db.add(IndustryDailyFlow(
            trade_date=TRADE_DATE,
            industry_name="石化及塑橡膠",
            total_net_amount=60,
            total_buy_amount=120,
            total_sell_amount=60,
            foreign_net_amount=40,
            trust_net_amount=10,
            dealer_net_amount=10,
            streak=2,
        ))
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["industry_name"] == "石化及塑橡膠"
        assert data[0]["total_net_amount"] == 100

    def test_streak_positive_consecutive_buy(self, api):
        client, db = api
        for streak, d in [(3, date(2025, 4, 1)), (2, date(2025, 3, 31)), (1, date(2025, 3, 28))]:
            db.add(IndustryDailyFlow(
                trade_date=d, industry_name="半導體",
                total_net_amount=1000 + streak, total_buy_amount=2000, total_sell_amount=1000,
                foreign_net_amount=800, trust_net_amount=100, dealer_net_amount=100,
                streak=streak,
            ))
        db.commit()

        resp = client.get(f"/api/industries?date={TRADE_DATE}")
        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["streak"] == 3

    def test_streak_negative_consecutive_sell(self, api):
        client, db = api
        for streak, d in [(-2, date(2025, 4, 1)), (-1, date(2025, 3, 31))]:
            db.add(IndustryDailyFlow(
                trade_date=d, industry_name="半導體",
                total_net_amount=-500, total_buy_amount=1000, total_sell_amount=1500,
                foreign_net_amount=-400, trust_net_amount=-50, dealer_net_amount=-50,
                streak=streak,
            ))
        db.add(IndustryDailyFlow(
            trade_date=date(2025, 3, 28), industry_name="半導體",
            total_net_amount=100, total_buy_amount=1100, total_sell_amount=1000,
            foreign_net_amount=80, trust_net_amount=10, dealer_net_amount=10,
            streak=1,
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

    def test_stocks_resolve_to_previous_trade_date_when_requested_date_has_no_rows(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓代工")
        db.add(DailyPrice(
            trade_date=date(2025, 3, 31),
            stock_id="2330",
            close_price=990.0,
            volume=1000000,
            turnover=990000000,
            avg_price=990.0,
        ))
        db.add(InstStockFlow(
            trade_date=date(2025, 3, 31),
            stock_id="2330",
            inst_type="foreign",
            buy_shares=500,
            sell_shares=0,
            net_shares=500,
            buy_amount_est=495000,
            sell_amount_est=0,
            net_amount_est=495000,
        ))
        db.commit()

        resp = client.get("/api/industries/半導體/stocks?date=2025-04-01")
        assert resp.status_code == 200
        assert resp.json()[0]["stock_id"] == "2330"

    def test_legacy_industry_alias_can_drill_down_to_canonical_stocks(self, api):
        client, db = api
        seed_stock(db, "1301", "台塑", "石化及塑橡膠", sub_industry="塑膠製品")
        seed_price(db, "1301", 100.0)
        seed_flow(db, "1301", "foreign", 100, 100000)
        db.commit()

        resp = client.get(f"/api/industries/塑膠工業/stocks?date={TRADE_DATE}")
        assert resp.status_code == 200
        assert resp.json()[0]["stock_id"] == "1301"


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

    def test_summary_includes_streak(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓製造")
        seed_flow(db, "2330", "foreign", 500, 500000)
        db.commit()

        resp = client.get(f"/api/industries/半導體/summary?date={TRADE_DATE}")
        item = resp.json()[0]
        assert "streak" in item
        assert "total_net_amount" in item
        assert "foreign_net_amount" in item

    def test_summary_404_for_unknown_industry(self, api):
        client, db = api
        resp = client.get(f"/api/industries/不存在的產業/summary?date={TRADE_DATE}")
        assert resp.status_code == 404

    def test_summary_resolves_to_previous_trade_date_when_requested_date_has_no_rows(self, api):
        client, db = api
        seed_stock(db, "2330", "台積電", "半導體", sub_industry="晶圓製造")
        db.add(InstStockFlow(
            trade_date=date(2025, 3, 31),
            stock_id="2330",
            inst_type="foreign",
            buy_shares=500,
            sell_shares=0,
            net_shares=500,
            buy_amount_est=500000,
            sell_amount_est=0,
            net_amount_est=500000,
        ))
        db.commit()

        resp = client.get("/api/industries/半導體/summary?date=2025-04-01")
        assert resp.status_code == 200
        assert resp.json()[0]["sub_industry"] == "晶圓製造"
