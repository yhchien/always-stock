"""
tests for backend/app/models.py
"""
import datetime
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import StockMaster, DailyPrice, InstStockFlow, IndustryDailyFlow


class TestStockMaster:
    def test_insert(self, db):
        db.add(StockMaster(stock_id="2330", stock_name="台積電", industry_name="半導體業"))
        db.commit()
        row = db.get(StockMaster, "2330")
        assert row.stock_name == "台積電"
        assert row.is_active is True  # default

    def test_primary_key_unique(self, db):
        db.add(StockMaster(stock_id="2330", stock_name="台積電", industry_name="半導體業"))
        db.commit()
        db.add(StockMaster(stock_id="2330", stock_name="重複", industry_name="X"))
        with pytest.raises(IntegrityError):
            db.commit()


class TestDailyPrice:
    def test_insert(self, db):
        db.add(DailyPrice(
            trade_date=datetime.date(2024, 1, 2),
            stock_id="2330",
            close_price=600.0,
            volume=10000,
            turnover=6000000,
            avg_price=600.0,
        ))
        db.commit()
        row = db.query(DailyPrice).first()
        assert row.close_price == 600.0

    def test_unique_constraint_trade_date_stock(self, db):
        row1 = DailyPrice(trade_date=datetime.date(2024, 1, 2), stock_id="2330", close_price=600.0)
        row2 = DailyPrice(trade_date=datetime.date(2024, 1, 2), stock_id="2330", close_price=610.0)
        db.add(row1)
        db.commit()
        db.add(row2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_different_stocks_same_date_allowed(self, db):
        db.add(DailyPrice(trade_date=datetime.date(2024, 1, 2), stock_id="2330", close_price=600.0))
        db.add(DailyPrice(trade_date=datetime.date(2024, 1, 2), stock_id="2454", close_price=100.0))
        db.commit()
        assert db.query(DailyPrice).count() == 2


class TestInstStockFlow:
    def test_insert(self, db):
        db.add(InstStockFlow(
            trade_date=datetime.date(2024, 1, 2),
            stock_id="2330",
            inst_type="foreign",
            buy_shares=1000,
            sell_shares=500,
            net_shares=500,
        ))
        db.commit()
        row = db.query(InstStockFlow).first()
        assert row.inst_type == "foreign"
        assert row.buy_shares == 1000

    def test_unique_constraint_date_stock_inst(self, db):
        row1 = InstStockFlow(trade_date=datetime.date(2024, 1, 2), stock_id="2330", inst_type="foreign")
        row2 = InstStockFlow(trade_date=datetime.date(2024, 1, 2), stock_id="2330", inst_type="foreign")
        db.add(row1)
        db.commit()
        db.add(row2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_different_inst_types_same_stock_allowed(self, db):
        for inst in ("foreign", "trust", "dealer"):
            db.add(InstStockFlow(
                trade_date=datetime.date(2024, 1, 2),
                stock_id="2330",
                inst_type=inst,
            ))
        db.commit()
        assert db.query(InstStockFlow).count() == 3


class TestIndustryDailyFlow:
    def test_insert(self, db):
        db.add(IndustryDailyFlow(
            trade_date=datetime.date(2024, 1, 2),
            industry_name="半導體業",
            total_net_amount=1_000_000,
            foreign_net_amount=800_000,
            trust_net_amount=100_000,
            dealer_net_amount=100_000,
        ))
        db.commit()
        row = db.query(IndustryDailyFlow).first()
        assert row.industry_name == "半導體業"

    def test_unique_constraint_date_industry(self, db):
        row1 = IndustryDailyFlow(trade_date=datetime.date(2024, 1, 2), industry_name="半導體業")
        row2 = IndustryDailyFlow(trade_date=datetime.date(2024, 1, 2), industry_name="半導體業")
        db.add(row1)
        db.commit()
        db.add(row2)
        with pytest.raises(IntegrityError):
            db.commit()
