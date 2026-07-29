"""Idempotent P4 observation table creation for API and cron entry points."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from app.database import Base
from app.models import SignalObservation, SignalObservationReview

logger = logging.getLogger(__name__)


def ensure_observation_tables(engine: Engine) -> None:
    """Create additive lifecycle tables without altering legacy hit/archive tables."""

    Base.metadata.create_all(
        bind=engine,
        tables=[
            SignalObservation.__table__,
            SignalObservationReview.__table__,
        ],
    )
    logger.info("Ensured P4 observation lifecycle tables")
