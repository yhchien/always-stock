"""
One-time setup: creates all SQLite tables.
Usage: python init_db.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database import engine
from app.models import Base  # noqa: F401 — import triggers all model registrations

logger = logging.getLogger(__name__)


def init_db() -> None:
    """Create all tables (idempotent — existing tables are not dropped or recreated)."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    init_db()
