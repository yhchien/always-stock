"""
FinMind 遷移 Phase 1：新增表格與欄位

執行方式：
    python migrate_finmind_phase1.py

功能：
    1. 為現有表新增 source, ingested_at, market 等欄位
    2. 建立新表：daily_valuation, monthly_revenue, financial_statement
    3. 建立新表：broker_trade_raw, broker_trade_agg, industry_mapping
    4. 建立資料驗證檢查點
"""

import logging
import sys
from datetime import datetime
from sqlalchemy import text, inspect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_db():
    """取得資料庫連線"""
    from app.database import SessionLocal

    return SessionLocal()


def column_exists(db, table_name: str, column_name: str) -> bool:
    """檢查欄位是否存在"""
    inspector = inspect(db.bind)
    columns = inspector.get_columns(table_name)
    return any(col["name"] == column_name for col in columns)


def table_exists(db, table_name: str) -> bool:
    """檢查表是否存在"""
    inspector = inspect(db.bind)
    return table_name in inspector.get_table_names()


def add_column_if_not_exists(db, table_name: str, column_def: str) -> bool:
    """
    新增欄位（如果不存在）

    Args:
        db: 資料庫連線
        table_name: 表名稱
        column_def: 欄位定義 (e.g., "ALTER TABLE stocks_master ADD COLUMN market VARCHAR DEFAULT 'twse'")

    Returns:
        True 代表成功新增或已存在，False 代表失敗
    """
    try:
        db.execute(text(column_def))
        db.commit()
        logger.info(f"✓ Added column: {column_def.split('ADD COLUMN')[1].split()[0]}")
        return True
    except Exception as e:
        logger.warning(f"✗ Could not add column: {e}")
        db.rollback()
        return False


def migrate_stocks_master(db):
    """新增 stocks_master 欄位"""
    logger.info("\n--- Migrating stocks_master ---")

    # market 欄位
    if not column_exists(db, "stocks_master", "market"):
        add_column_if_not_exists(
            db,
            "stocks_master",
            "ALTER TABLE stocks_master ADD COLUMN market VARCHAR DEFAULT 'twse'"
        )

    # source 欄位
    if not column_exists(db, "stocks_master", "source"):
        add_column_if_not_exists(
            db,
            "stocks_master",
            "ALTER TABLE stocks_master ADD COLUMN source VARCHAR DEFAULT 'fugle'"
        )

    # source_version 欄位
    if not column_exists(db, "stocks_master", "source_version"):
        add_column_if_not_exists(
            db,
            "stocks_master",
            "ALTER TABLE stocks_master ADD COLUMN source_version VARCHAR"
        )


def migrate_daily_price(db):
    """新增 daily_price 欄位"""
    logger.info("\n--- Migrating daily_price ---")

    # spread 欄位
    if not column_exists(db, "daily_price", "spread"):
        add_column_if_not_exists(
            db,
            "daily_price",
            "ALTER TABLE daily_price ADD COLUMN spread FLOAT"
        )

    # source 欄位
    if not column_exists(db, "daily_price", "source"):
        add_column_if_not_exists(
            db,
            "daily_price",
            "ALTER TABLE daily_price ADD COLUMN source VARCHAR DEFAULT 'twse'"
        )

    # ingested_at 欄位
    if not column_exists(db, "daily_price", "ingested_at"):
        add_column_if_not_exists(
            db,
            "daily_price",
            "ALTER TABLE daily_price ADD COLUMN ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )


def migrate_inst_stock_flow(db):
    """新增 inst_stock_flow 欄位"""
    logger.info("\n--- Migrating inst_stock_flow ---")

    # source 欄位
    if not column_exists(db, "inst_stock_flow", "source"):
        add_column_if_not_exists(
            db,
            "inst_stock_flow",
            "ALTER TABLE inst_stock_flow ADD COLUMN source VARCHAR DEFAULT 'twse'"
        )

    # ingested_at 欄位
    if not column_exists(db, "inst_stock_flow", "ingested_at"):
        add_column_if_not_exists(
            db,
            "inst_stock_flow",
            "ALTER TABLE inst_stock_flow ADD COLUMN ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )


def migrate_broker_trade(db):
    """新增 broker_trade 欄位"""
    logger.info("\n--- Migrating broker_trade ---")

    # source 欄位
    if not column_exists(db, "broker_trade", "source"):
        add_column_if_not_exists(
            db,
            "broker_trade",
            "ALTER TABLE broker_trade ADD COLUMN source VARCHAR DEFAULT 'twse'"
        )

    # ingested_at 欄位
    if not column_exists(db, "broker_trade", "ingested_at"):
        add_column_if_not_exists(
            db,
            "broker_trade",
            "ALTER TABLE broker_trade ADD COLUMN ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )


def create_new_tables(db):
    """建立新表"""
    logger.info("\n--- Creating new tables ---")

    from app.models import (
        Base,
        DailyValuation,
        MonthlyRevenue,
        FinancialStatement,
        BrokerTradeRaw,
        BrokerTradeAgg,
        IndustryMapping,
    )

    # 使用 SQLAlchemy 的 create_all 方法建立表
    # 但只建立新表，不影響既有表
    tables_to_create = [
        DailyValuation,
        MonthlyRevenue,
        FinancialStatement,
        BrokerTradeRaw,
        BrokerTradeAgg,
        IndustryMapping,
    ]

    for model in tables_to_create:
        table_name = model.__tablename__

        if not table_exists(db, table_name):
            try:
                model.__table__.create(db.bind, checkfirst=True)
                logger.info(f"✓ Created table: {table_name}")
            except Exception as e:
                logger.error(f"✗ Failed to create table {table_name}: {e}")
                db.rollback()
        else:
            logger.info(f"✓ Table already exists: {table_name}")


def create_checkpoint_file(db):
    """建立遷移檢查點檔案"""
    logger.info("\n--- Creating migration checkpoint ---")

    checkpoint = {
        "migration_date": datetime.utcnow().isoformat(),
        "phase": 1,
        "description": "FinMind Phase 1: Add columns to existing tables and create new tables",
        "tables_modified": [
            "stocks_master",
            "daily_price",
            "inst_stock_flow",
            "broker_trade",
        ],
        "tables_created": [
            "daily_valuation",
            "monthly_revenue",
            "financial_statement",
            "broker_trade_raw",
            "broker_trade_agg",
            "industry_mapping",
        ],
        "status": "completed",
    }

    import json
    import os

    checkpoint_path = "backend/logs/finmind_migration_checkpoint.json"
    os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)

    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)

    logger.info(f"✓ Checkpoint saved to {checkpoint_path}")


def verify_migration(db):
    """驗證遷移結果"""
    logger.info("\n--- Verifying migration ---")

    required_columns = {
        "stocks_master": ["market", "source", "source_version"],
        "daily_price": ["spread", "source", "ingested_at"],
        "inst_stock_flow": ["source", "ingested_at"],
        "broker_trade": ["source", "ingested_at"],
    }

    required_tables = [
        "daily_valuation",
        "monthly_revenue",
        "financial_statement",
        "broker_trade_raw",
        "broker_trade_agg",
        "industry_mapping",
    ]

    all_ok = True

    # 檢查欄位
    for table_name, columns in required_columns.items():
        for column_name in columns:
            if column_exists(db, table_name, column_name):
                logger.info(f"✓ {table_name}.{column_name}")
            else:
                logger.error(f"✗ {table_name}.{column_name} MISSING")
                all_ok = False

    # 檢查新表
    for table_name in required_tables:
        if table_exists(db, table_name):
            logger.info(f"✓ Table {table_name}")
        else:
            logger.error(f"✗ Table {table_name} MISSING")
            all_ok = False

    return all_ok


def main():
    """執行遷移"""
    logger.info("=" * 80)
    logger.info("FinMind Migration Phase 1")
    logger.info("=" * 80)

    db = get_db()

    try:
        # 執行遷移步驟
        migrate_stocks_master(db)
        migrate_daily_price(db)
        migrate_inst_stock_flow(db)
        migrate_broker_trade(db)
        create_new_tables(db)
        create_checkpoint_file(db)

        # 驗證
        if verify_migration(db):
            logger.info("\n" + "=" * 80)
            logger.info("✓ Migration completed successfully!")
            logger.info("=" * 80)
            return 0
        else:
            logger.error("\n" + "=" * 80)
            logger.error("✗ Migration verification FAILED")
            logger.error("=" * 80)
            return 1

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
