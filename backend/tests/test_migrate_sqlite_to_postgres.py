from datetime import date

from sqlalchemy import select

from app.models import Base, DailyPrice, StockMaster
from migrate_sqlite_to_postgres import (
    TABLE_NAME_TO_SPEC,
    build_engine,
    get_import_columns,
    import_table,
    load_checkpoint,
    normalize_database_url,
    resolve_table_specs,
    save_checkpoint,
    sqlite_url_from_path,
)


def test_resolve_table_specs_rejects_unknown_table():
    try:
        resolve_table_specs(["not_a_real_table"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Unknown table" in str(exc)


def test_get_import_columns_excludes_surrogate_id():
    daily_price_spec = TABLE_NAME_TO_SPEC["daily_price"]
    assert "id" not in get_import_columns(daily_price_spec)
    assert "trade_date" in get_import_columns(daily_price_spec)


def test_normalize_database_url_for_render_style_postgres_url():
    assert normalize_database_url("postgresql://user:pass@host/db") == (
        "postgresql+psycopg://user:pass@host/db"
    )


def test_import_table_copies_rows_between_sqlite_databases(tmp_path):
    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"

    source_engine = build_engine(sqlite_url_from_path(source_path))
    target_engine = build_engine(sqlite_url_from_path(target_path))
    Base.metadata.create_all(bind=source_engine)
    Base.metadata.create_all(bind=target_engine)

    with source_engine.begin() as conn:
        conn.execute(
            StockMaster.__table__.insert(),
            [
                {
                    "stock_id": "2330",
                    "stock_name": "TSMC",
                    "industry_name": "Semiconductor",
                    "chain": "upstream",
                    "sub_industry": "Foundry",
                    "is_active": True,
                }
            ],
        )
        conn.execute(
            DailyPrice.__table__.insert(),
            [
                {
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
            ],
        )

    import_table(source_engine, target_engine, TABLE_NAME_TO_SPEC["stocks_master"], batch_size=100)
    import_table(source_engine, target_engine, TABLE_NAME_TO_SPEC["daily_price"], batch_size=100)

    with target_engine.connect() as conn:
        stocks = conn.execute(select(StockMaster.__table__)).mappings().all()
        prices = conn.execute(select(DailyPrice.__table__)).mappings().all()

    assert len(stocks) == 1
    assert stocks[0]["stock_id"] == "2330"
    assert len(prices) == 1
    assert prices[0]["stock_id"] == "2330"
    assert prices[0]["close_price"] == 905.0


def test_checkpoint_round_trip(tmp_path):
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = {
        "completed_tables": ["stocks_master"],
        "table_reports": {"stocks_master": {"processed_rows": 1}},
    }

    save_checkpoint(checkpoint_path, checkpoint)
    loaded = load_checkpoint(checkpoint_path)

    assert loaded == checkpoint


def test_load_checkpoint_returns_default_structure_when_missing(tmp_path):
    checkpoint_path = tmp_path / "missing.json"
    loaded = load_checkpoint(checkpoint_path)

    assert loaded == {"completed_tables": [], "table_reports": {}}
