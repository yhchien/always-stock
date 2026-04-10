from datetime import date

from app.models import Base, DailyPrice, StockMaster
from migrate_sqlite_to_postgres import build_engine, sqlite_url_from_path
from validate_migrated_data import (
    build_table_report,
    count_missing_ohlc_rows,
    count_rows_for_date,
    has_mismatch,
)


def test_build_table_report_matches_identical_sqlite_tables(tmp_path):
    source_engine = build_engine(sqlite_url_from_path(tmp_path / "source.db"))
    target_engine = build_engine(sqlite_url_from_path(tmp_path / "target.db"))
    Base.metadata.create_all(bind=source_engine)
    Base.metadata.create_all(bind=target_engine)

    row = {
        "stock_id": "2330",
        "stock_name": "TSMC",
        "industry_name": "Semiconductor",
        "chain": "upstream",
        "sub_industry": "Foundry",
        "is_active": True,
    }
    price_row = {
        "trade_date": date(2026, 4, 9),
        "stock_id": "2330",
        "open_price": 900.0,
        "high_price": 910.0,
        "low_price": 895.0,
        "close_price": 905.0,
        "volume": 1000.0,
        "turnover": 905000.0,
        "avg_price": 905.0,
    }

    for engine in (source_engine, target_engine):
        with engine.begin() as conn:
            conn.execute(StockMaster.__table__.insert(), [row])
            conn.execute(DailyPrice.__table__.insert(), [price_row])

    report = build_table_report(
        source_engine, target_engine, type("Spec", (), {"model": DailyPrice, "name": "daily_price"})()
    )
    assert report["count_match"] is True
    assert report["date_range_match"] is True


def test_count_missing_ohlc_rows_and_count_rows_for_date(tmp_path):
    engine = build_engine(sqlite_url_from_path(tmp_path / "db.db"))
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            DailyPrice.__table__.insert(),
            [
                {
                    "trade_date": date(2024, 7, 11),
                    "stock_id": "2330",
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "close_price": 100.0,
                    "volume": 10.0,
                    "turnover": 1000.0,
                    "avg_price": 100.0,
                }
            ],
        )

    assert count_rows_for_date(engine, DailyPrice, "2024-07-11") == 1
    assert count_missing_ohlc_rows(engine, "2024-07-11") == 1


def test_has_mismatch_detects_any_false_flag():
    assert has_mismatch({"daily_price": {"count_match": True, "date_range_match": True}}) is False
    assert has_mismatch({"daily_price": {"count_match": False, "date_range_match": True}}) is True
