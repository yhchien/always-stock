"""Idempotent P4 observation table creation for API and cron entry points."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import Base
from app.models import (
    SignalObservation,
    SignalObservationArchive,
    SignalObservationReview,
)

logger = logging.getLogger(__name__)


def ensure_observation_tables(engine: Engine) -> None:
    """Create additive lifecycle tables without altering legacy hit/archive tables."""

    Base.metadata.create_all(
        bind=engine,
        tables=[
            SignalObservation.__table__,
            SignalObservationReview.__table__,
            SignalObservationArchive.__table__,
        ],
    )
    _ensure_stop_confirm_count_column(engine)
    _ensure_pending_stop_columns(engine)
    logger.info("Ensured P4 observation lifecycle tables")


def _ensure_stop_confirm_count_column(engine: Engine) -> None:
    """Backfill ``stop_confirm_count`` for signal_observations tables created
    before the 3-day STOP_OBSERVING confirmation feature existed."""
    inspector = inspect(engine)
    if "signal_observations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("signal_observations")}
    if "stop_confirm_count" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE signal_observations "
                "ADD COLUMN stop_confirm_count INTEGER NOT NULL DEFAULT 0"
            )
        )
    logger.info("Added signal_observations.stop_confirm_count column")


def _ensure_pending_stop_columns(engine: Engine) -> None:
    """Backfill P4 Observation Lifecycle v2 (2026-08-18) composite-risk pending
    columns for signal_observations tables created before this feature existed."""
    inspector = inspect(engine)
    if "signal_observations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("signal_observations")}
    statements = {
        "pending_stop_status": (
            "ALTER TABLE signal_observations ADD COLUMN pending_stop_status VARCHAR(16)"
        ),
        "pending_stop_reason": (
            "ALTER TABLE signal_observations ADD COLUMN pending_stop_reason VARCHAR(64)"
        ),
        "pending_stop_since": (
            "ALTER TABLE signal_observations ADD COLUMN pending_stop_since DATE"
        ),
        "pending_stop_trigger_snapshot": (
            "ALTER TABLE signal_observations ADD COLUMN pending_stop_trigger_snapshot JSON"
        ),
        "pending_stop_review_count": (
            "ALTER TABLE signal_observations ADD COLUMN pending_stop_review_count "
            "INTEGER NOT NULL DEFAULT 0"
        ),
    }
    missing = [name for name in statements if name not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            conn.execute(text(statements[name]))
    logger.info("Added signal_observations pending_stop_* columns: %s", missing)
