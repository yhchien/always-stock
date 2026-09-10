"""Copy completed shadow replay output from the local cache into production.

This is intentionally date-scoped.  It replaces only the requested historical
dates, leaves the current cycle's 9/7+ decisions/orders alone, and never
touches the singleton portfolio or live positions.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, or_
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import (
    ShadowCompletedTrade,
    ShadowMissedCandidate,
    ShadowPortfolioDailySnapshot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowWinnerTracking,
)


STRATEGIES = ("v1_frozen", "FORWARD_V1_202609")


def _row_data(row) -> dict:
    """Copy model data without carrying SQLite primary keys into Postgres."""
    return {
        column.name: getattr(row, column.name)
        for column in inspect(type(row)).columns
        if column.name not in {"id", "created_at", "updated_at"}
    }


def _copy_rows(
    source: Session,
    target: Session,
    model,
    strategy_version: str,
    predicate,
) -> int:
    rows = (
        source.query(model)
        .filter(model.strategy_version == strategy_version, predicate(model))
        .all()
    )
    target.add_all([model(**_row_data(row)) for row in rows])
    return len(rows)


def _sync_strategy(
    source: Session,
    target: Session,
    strategy_version: str,
    strategy_end: date,
    settlement_date: date,
) -> dict[str, int]:
    # Daily decisions / missed candidates / winner tracking are signal-date
    # records.  Keep the new cycle's 9/7 decisions and later records intact.
    daily_models = (
        ShadowStrategyDailyDecision,
        ShadowMissedCandidate,
        ShadowWinnerTracking,
    )
    for model in daily_models:
        target.query(model).filter(
            model.strategy_version == strategy_version,
            model.trade_date <= strategy_end,
        ).delete(synchronize_session=False)

    # Orders are signal records too.  A 9/4 order may be scheduled for 9/7;
    # selecting by signal_date keeps it historical without touching 9/7's new
    # cycle orders.
    target.query(ShadowStrategyOrder).filter(
        ShadowStrategyOrder.strategy_version == strategy_version,
        ShadowStrategyOrder.signal_date <= strategy_end,
    ).delete(synchronize_session=False)

    # Snapshots and completed trades include the administrative settlement day.
    target.query(ShadowPortfolioDailySnapshot).filter(
        ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
        ShadowPortfolioDailySnapshot.trade_date <= settlement_date,
    ).delete(synchronize_session=False)
    target.query(ShadowCompletedTrade).filter(
        ShadowCompletedTrade.strategy_version == strategy_version,
        or_(
            ShadowCompletedTrade.entry_execution_date <= settlement_date,
            ShadowCompletedTrade.exit_execution_date <= settlement_date,
        ),
    ).delete(synchronize_session=False)
    target.flush()

    counts = {
        "decisions": 0,
        "missed": 0,
        "winners": 0,
        "orders": 0,
        "snapshots": 0,
        "trades": 0,
    }
    for model, key in [
        (ShadowStrategyDailyDecision, "decisions"),
        (ShadowMissedCandidate, "missed"),
        (ShadowWinnerTracking, "winners"),
    ]:
        counts[key] = _copy_rows(
            source,
            target,
            model,
            strategy_version,
            lambda m: m.trade_date <= strategy_end,
        )
    counts["orders"] = _copy_rows(
        source,
        target,
        ShadowStrategyOrder,
        strategy_version,
        lambda m: m.signal_date <= strategy_end,
    )
    counts["snapshots"] = _copy_rows(
        source,
        target,
        ShadowPortfolioDailySnapshot,
        strategy_version,
        lambda m: m.trade_date <= settlement_date,
    )
    counts["trades"] = _copy_rows(
        source,
        target,
        ShadowCompletedTrade,
        strategy_version,
        lambda m: or_(
            m.entry_execution_date <= settlement_date,
            m.exit_execution_date <= settlement_date,
        ),
    )
    target.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 8, 3))
    parser.add_argument("--strategy-end", type=date.fromisoformat, default=date(2026, 9, 4))
    parser.add_argument("--settlement-date", type=date.fromisoformat, default=date(2026, 9, 7))
    parser.add_argument("--strategy-version", action="append", choices=STRATEGIES)
    args = parser.parse_args()

    source_url = os.environ.get("SOURCE_DATABASE_URL", "sqlite:///db/dual_engine_backtest_cache.db")
    target_url = os.environ["DATABASE_URL"]
    source_session = sessionmaker(bind=create_engine(source_url))
    target_session = sessionmaker(bind=create_engine(target_url, pool_pre_ping=True))

    strategies = tuple(args.strategy_version or STRATEGIES)
    with source_session() as source, target_session() as target:
        for strategy_version in strategies:
            counts = _sync_strategy(
                source,
                target,
                strategy_version,
                args.strategy_end,
                args.settlement_date,
            )
            print(strategy_version, counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
