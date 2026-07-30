"""Idempotent additive P6 outcome analytics schema bootstrap.

The repository does not use Alembic.  Existing signal additions are deployed with
targeted ``create_all`` bootstraps, so P6 follows that compatibility policy and
never alters or overwrites source snapshots.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from app.database import Base
from app.models import (
    SignalObservationOutcomeMetric,
    SignalOutcomeMetric,
    SignalOutcomeReviewQueue,
)

logger = logging.getLogger(__name__)


def ensure_outcome_tables(engine: Engine) -> None:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            SignalOutcomeMetric.__table__,
            SignalObservationOutcomeMetric.__table__,
            SignalOutcomeReviewQueue.__table__,
        ],
    )
    logger.info("Ensured P6 outcome analytics tables")
