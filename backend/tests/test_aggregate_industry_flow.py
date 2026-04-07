"""
tests for backend/etl/aggregate_industry_flow.py
"""
from datetime import date

import pytest

from app.models import IndustryDailyFlow, InstStockFlow, StockMaster
from etl.aggregate_industry_flow import aggregate_industry_flow

TRADE_DATE = date(2025, 4, 1)


def add_stock(db, stock_id, industry_name, sub_industry=None):
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=f"股票{stock_id}",
        industry_name=industry_name,
        sub_industry=sub_industry,
    ))


def add_flow(db, stock_id, inst_type, buy_amt, sell_amt, net_amt):
    db.add(InstStockFlow(
        trade_date=TRADE_DATE,
        stock_id=stock_id,
        inst_type=inst_type,
        buy_shares=0,
        sell_shares=0,
        net_shares=0,
        buy_amount_est=buy_amt,
        sell_amount_est=sell_amt,
        net_amount_est=net_amt,
    ))


class TestAggregateIndustryFlow:
    def test_basic_aggregation(self, db):
        add_stock(db, "2330", "半導體", sub_industry="晶圓代工")
        add_flow(db, "2330", "foreign", 1000, 200, 800)
        add_flow(db, "2330", "trust",   100,  50,  50)
        add_flow(db, "2330", "dealer",  300, 100, 200)
        db.commit()

        count = aggregate_industry_flow(db, TRADE_DATE)

        assert count == 1
        row = db.query(IndustryDailyFlow).filter_by(industry_name="半導體").first()
        assert row is not None
        assert row.foreign_net_amount == 800.0
        assert row.trust_net_amount   == 50.0
        assert row.dealer_net_amount  == 200.0
        assert row.total_net_amount   == 1050.0
        assert row.total_buy_amount   == 1400.0
        assert row.total_sell_amount  == 350.0

    def test_aggregates_by_industry_name_not_sub_industry(self, db):
        """Aggregation groups by industry_name (broad category), not sub_industry."""
        add_stock(db, "2330", "半導體", sub_industry="晶圓代工")
        add_flow(db, "2330", "foreign", 0, 0, 100)
        add_flow(db, "2330", "trust",   0, 0, 0)
        add_flow(db, "2330", "dealer",  0, 0, 0)
        db.commit()

        aggregate_industry_flow(db, TRADE_DATE)

        assert db.query(IndustryDailyFlow).filter_by(industry_name="半導體").first() is not None
        assert db.query(IndustryDailyFlow).filter_by(industry_name="晶圓代工").first() is None

    def test_fallback_industry_name_for_unmapped_stocks(self, db):
        add_stock(db, "2454", "IC設計業", sub_industry=None)
        add_flow(db, "2454", "foreign", 0, 0, 500)
        add_flow(db, "2454", "trust",   0, 0, 0)
        add_flow(db, "2454", "dealer",  0, 0, 0)
        db.commit()

        aggregate_industry_flow(db, TRADE_DATE)

        assert db.query(IndustryDailyFlow).filter_by(industry_name="IC設計業").first() is not None

    def test_multiple_stocks_same_industry_summed(self, db):
        add_stock(db, "2330", "半導體", sub_industry="晶圓代工")
        add_stock(db, "5347", "半導體", sub_industry="晶圓代工")
        add_flow(db, "2330", "foreign", 0, 0, 300)
        add_flow(db, "2330", "trust",   0, 0, 0)
        add_flow(db, "2330", "dealer",  0, 0, 0)
        add_flow(db, "5347", "foreign", 0, 0, 200)
        add_flow(db, "5347", "trust",   0, 0, 0)
        add_flow(db, "5347", "dealer",  0, 0, 0)
        db.commit()

        aggregate_industry_flow(db, TRADE_DATE)

        row = db.query(IndustryDailyFlow).filter_by(industry_name="半導體").first()
        assert row is not None
        assert row.foreign_net_amount == 500.0

    def test_skips_stocks_not_in_master(self, db):
        # Only flow, no stock master → skip
        add_flow(db, "9999", "foreign", 0, 0, 999)
        add_flow(db, "9999", "trust",   0, 0, 0)
        add_flow(db, "9999", "dealer",  0, 0, 0)
        db.commit()

        count = aggregate_industry_flow(db, TRADE_DATE)
        assert count == 0

    def test_returns_zero_when_no_flows(self, db):
        count = aggregate_industry_flow(db, TRADE_DATE)
        assert count == 0

    def test_upsert_updates_existing(self, db):
        add_stock(db, "2330", "半導體", sub_industry="晶圓代工")
        db.add(IndustryDailyFlow(
            trade_date=TRADE_DATE, industry_name="半導體",
            total_buy_amount=0, total_sell_amount=0, total_net_amount=0,
            foreign_net_amount=0, trust_net_amount=0, dealer_net_amount=0,
        ))
        add_flow(db, "2330", "foreign", 500, 100, 400)
        add_flow(db, "2330", "trust",   0, 0, 0)
        add_flow(db, "2330", "dealer",  0, 0, 0)
        db.commit()

        aggregate_industry_flow(db, TRADE_DATE)

        rows = db.query(IndustryDailyFlow).filter_by(industry_name="半導體").all()
        assert len(rows) == 1
        assert rows[0].foreign_net_amount == 400.0
