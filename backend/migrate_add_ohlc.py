"""
One-time migration: add open_price, high_price, low_price columns to daily_price.

SQLAlchemy create_all() does not add columns to existing tables, so we
run ALTER TABLE manually. Each ALTER is idempotent — if the column already
exists, the error is caught and skipped.

Usage:
    cd backend
    python migrate_add_ohlc.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from logging_config import setup_logging
from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)

COLUMNS = [
    ("open_price", "REAL"),
    ("high_price", "REAL"),
    ("low_price", "REAL"),
]


def migrate():
    setup_logging()
    with engine.connect() as conn:
        for col_name, col_type in COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE daily_price ADD COLUMN {col_name} {col_type}"))
                logger.info("Added column: daily_price.%s", col_name)
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    logger.info("Column already exists: daily_price.%s (skipped)", col_name)
                else:
                    raise
    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
