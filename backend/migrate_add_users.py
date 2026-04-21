"""
One-time migration: M18 使用者認證系統

建立兩張表：
- `users`：使用者帳號（email + bcrypt hash）
- `user_sessions`：server-side session

並 seed admin 帳號（ADMIN_EMAIL / ADMIN_PASSWORD）。

Idempotent：
- 用 SQLAlchemy `create_all()` 建表（已存在會略過）
- admin seeder 檢查 email 是否存在再決定建立

Usage:
    cd backend
    python migrate_add_users.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from logging_config import setup_logging

from app.auth import ensure_admin_user
from app.database import Base, SessionLocal, engine
from app.models import User, UserSession  # noqa: F401 — import 觸發 metadata

logger = logging.getLogger(__name__)


def migrate() -> None:
    setup_logging()

    logger.info("Creating tables: users, user_sessions (if missing)")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, UserSession.__table__],
    )

    logger.info("Seeding admin user")
    db = SessionLocal()
    try:
        admin = ensure_admin_user(db)
        logger.info("Admin user ready: id=%s email=%s", admin.id, admin.email)
    finally:
        db.close()

    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
