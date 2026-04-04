"""
tests for backend/init_db.py
驗證 Base.metadata.create_all 能正確建立所有資料表。
"""
from sqlalchemy import create_engine, inspect

from app.models import Base


def test_all_tables_created():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "stocks_master" in tables
    assert "daily_price" in tables
    assert "inst_stock_flow" in tables
    assert "industry_daily_flow" in tables


def test_stocks_master_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("stocks_master")}
    assert {"stock_id", "stock_name", "industry_name", "is_active"} <= cols


def test_daily_price_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("daily_price")}
    assert {"id", "trade_date", "stock_id", "close_price", "volume", "turnover", "avg_price"} <= cols


def test_idempotent_create_all():
    """重複呼叫 create_all 不應拋出錯誤（checkfirst=True 預設行為）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)  # 第二次呼叫應無事
