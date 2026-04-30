"""Tests for `run_signal_archive_returns.py` target-date resolution."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, DailyPrice
import run_signal_archive_returns as runner


def test_parse_target_trade_date_uses_argv_override():
    assert runner._parse_target_trade_date(["run_signal_archive_returns.py", "2026-04-30"]) == date(
        2026,
        4,
        30,
    )


def test_parse_target_trade_date_before_ready_time_uses_previous_trade_day():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                DailyPrice(stock_id="2330", trade_date=date(2026, 4, 29), close_price=100.0),
                DailyPrice(stock_id="2330", trade_date=date(2026, 4, 30), close_price=101.0),
            ]
        )
        db.commit()

        target = runner._resolve_default_trade_date(
            db,
            now=datetime(2026, 5, 1, 0, 7, tzinfo=runner.TAIPEI_TZ),
        )
        assert target == date(2026, 4, 30)


def test_parse_target_trade_date_after_ready_time_uses_same_trading_day():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                DailyPrice(stock_id="2330", trade_date=date(2026, 4, 30), close_price=101.0),
                DailyPrice(stock_id="2330", trade_date=date(2026, 5, 4), close_price=102.0),
            ]
        )
        db.commit()

        target = runner._resolve_default_trade_date(
            db,
            now=datetime(2026, 5, 4, 20, 1, tzinfo=runner.TAIPEI_TZ),
        )
        assert target == date(2026, 5, 4)


def test_parse_target_trade_date_after_ready_time_on_holiday_uses_previous_trade_day():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                DailyPrice(stock_id="2330", trade_date=date(2026, 4, 29), close_price=100.0),
                DailyPrice(stock_id="2330", trade_date=date(2026, 4, 30), close_price=101.0),
            ]
        )
        db.commit()

        target = runner._resolve_default_trade_date(
            db,
            now=datetime(2026, 5, 1, 20, 1, tzinfo=runner.TAIPEI_TZ),
        )
        assert target == date(2026, 4, 30)
