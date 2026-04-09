"""
One-time migration: create broker_trade table.

Idempotent — skips if table already exists.

Usage:
    cd backend
    python migrate_add_broker_trade.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from logging_config import setup_logging
from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)


def migrate():
    setup_logging()
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE TABLE broker_trade (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    stock_id TEXT NOT NULL,
                    broker_id TEXT NOT NULL,
                    broker_name TEXT NOT NULL,
                    buy_shares REAL DEFAULT 0,
                    sell_shares REAL DEFAULT 0,
                    net_shares REAL DEFAULT 0,
                    UNIQUE(trade_date, stock_id, broker_id)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_broker_trade_stock_date "
                "ON broker_trade(stock_id, trade_date)"
            ))
            conn.commit()
            logger.info("Created table: broker_trade")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info("Table broker_trade already exists (skipped)")
            else:
                raise
    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
