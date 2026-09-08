"""`app.trading_calendar.is_trading_day` —— 單一權威的交易日判斷。"""
from __future__ import annotations

from datetime import date

from app.models import DailyPrice
from app.trading_calendar import is_trading_day


def test_is_trading_day_true_when_daily_price_exists(db):
    db.add(DailyPrice(stock_id="2330", trade_date=date(2026, 8, 7), close_price=100.0))
    db.commit()
    assert is_trading_day(db, date(2026, 8, 7)) is True


def test_is_trading_day_false_when_no_daily_price(db):
    db.add(DailyPrice(stock_id="2330", trade_date=date(2026, 8, 7), close_price=100.0))
    db.commit()
    assert is_trading_day(db, date(2026, 8, 9)) is False  # 週日，無資料


def test_is_trading_day_false_on_completely_empty_table(db):
    assert is_trading_day(db, date(2026, 8, 9)) is False
