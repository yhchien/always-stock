"""
M22 hot-money router tests（L0 market + L1 industry）。
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, DailyPrice, InstStockFlow, StockMaster


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


def _seed_stock(db, stock_id, stock_name, industry_name, sub_industry=None):
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=stock_name,
        industry_name=industry_name,
        sub_industry=sub_industry,
    ))


def _seed_flow(db, trade_date, stock_id, inst_type, net_amount):
    buy = max(net_amount, 0)
    sell = abs(min(net_amount, 0))
    db.add(InstStockFlow(
        trade_date=trade_date,
        stock_id=stock_id,
        inst_type=inst_type,
        buy_shares=0,
        sell_shares=0,
        net_shares=0,
        buy_amount_est=buy,
        sell_amount_est=sell,
        net_amount_est=net_amount,
    ))


def _seed_price(db, trade_date, stock_id, close_price):
    db.add(DailyPrice(
        trade_date=trade_date,
        stock_id=stock_id,
        close_price=close_price,
    ))


class TestMarketHotMoney:
    def test_returns_top_stocks_sorted_by_total_net(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業", sub_industry="IC 製造")
        _seed_stock(db, "2317", "鴻海", "其他電子")
        _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 3e8)
        _seed_flow(db, date(2026, 4, 22), "2330", "trust", 1e8)
        _seed_flow(db, date(2026, 4, 22), "2317", "foreign", 2e7)
        _seed_price(db, date(2026, 4, 19), "2330", 1000.0)
        _seed_price(db, date(2026, 4, 22), "2330", 1050.0)
        db.commit()

        resp = client.get("/api/market/hot-money?date=2026-04-22&days=1&limit=10")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["start_date"] == "2026-04-22"
        assert payload["end_date"] == "2026-04-22"
        assert payload["trade_dates"] == ["2026-04-22"]

        top = payload["items"][0]
        assert top["rank"] == 1
        assert top["stock_id"] == "2330"
        assert top["stock_name"] == "台積電"
        assert top["sub_industry"] == "IC 製造"
        assert top["total_net_amount"] == 4e8
        assert top["end_close_price"] == 1050.0
        assert top["start_close_price"] == 1000.0
        assert top["price_change_pct"] == 5.0

    def test_defaults_to_latest_trade_date_when_no_date_param(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業")
        _seed_flow(db, date(2026, 4, 21), "2330", "foreign", 1e8)
        _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 2e8)
        db.commit()

        resp = client.get("/api/market/hot-money")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["end_date"] == "2026-04-22"

    def test_resolves_to_previous_trade_date_when_requested_date_has_no_data(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業")
        _seed_flow(db, date(2026, 4, 18), "2330", "foreign", 1e8)
        db.commit()

        resp = client.get("/api/market/hot-money?date=2026-04-22&days=1")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["end_date"] == "2026-04-18"
        assert len(payload["items"]) == 1

    def test_returns_empty_when_no_flow_data(self, api):
        client, db = api
        resp = client.get("/api/market/hot-money?date=2026-04-22")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["items"] == []
        assert payload["trade_dates"] == []

    def test_respects_limit(self, api):
        client, db = api
        for idx in range(5):
            sid = f"100{idx}"
            _seed_stock(db, sid, f"S{idx}", "雜項")
            _seed_flow(db, date(2026, 4, 22), sid, "foreign", (5 - idx) * 1e7)
        db.commit()

        resp = client.get("/api/market/hot-money?date=2026-04-22&days=1&limit=3")
        assert resp.status_code == 200
        ids = [item["stock_id"] for item in resp.json()["items"]]
        assert ids == ["1000", "1001", "1002"]

    def test_rejects_invalid_params(self, api):
        client, _ = api
        assert client.get("/api/market/hot-money?days=0").status_code == 422
        assert client.get("/api/market/hot-money?limit=0").status_code == 422
        assert client.get("/api/market/hot-money?limit=9999").status_code == 422


class TestIndustryHotMoney:
    def test_returns_only_industry_stocks(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業", sub_industry="IC 製造")
        _seed_stock(db, "3711", "日月光投控", "半導體業", sub_industry="IC 封測")
        _seed_stock(db, "1101", "台泥", "水泥工業")
        for d in (date(2026, 4, 21), date(2026, 4, 22)):
            _seed_flow(db, d, "2330", "foreign", 1e8)
            _seed_flow(db, d, "3711", "foreign", 5e7)
            _seed_flow(db, d, "1101", "foreign", 9e8)  # 雖然大，但不同產業
        db.commit()

        resp = client.get("/api/industries/半導體業/hot-money?date=2026-04-22&days=2&limit=10")
        assert resp.status_code == 200
        payload = resp.json()
        ids = [item["stock_id"] for item in payload["items"]]
        assert "1101" not in ids
        assert ids == ["2330", "3711"]

    def test_filter_by_sub_industry(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業", sub_industry="IC 製造")
        _seed_stock(db, "3711", "日月光投控", "半導體業", sub_industry="IC 封測")
        _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 1e8)
        _seed_flow(db, date(2026, 4, 22), "3711", "foreign", 5e7)
        db.commit()

        resp = client.get("/api/industries/半導體業/hot-money?date=2026-04-22&days=1&sub_industry=IC%20封測")
        assert resp.status_code == 200
        ids = [item["stock_id"] for item in resp.json()["items"]]
        assert ids == ["3711"]

    def test_returns_404_for_unknown_industry(self, api):
        client, _ = api
        resp = client.get("/api/industries/不存在的產業/hot-money?date=2026-04-22")
        assert resp.status_code == 404

    def test_returns_empty_when_sub_industry_filter_matches_nothing(self, api):
        client, db = api
        _seed_stock(db, "2330", "台積電", "半導體業", sub_industry="IC 製造")
        _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 1e8)
        db.commit()

        resp = client.get("/api/industries/半導體業/hot-money?date=2026-04-22&sub_industry=不存在")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["items"] == []
