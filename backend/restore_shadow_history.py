"""Restore a historical shadow replay into production for the simulation UI.

This is an explicit, date-bounded migration helper. It copies only historical
derived records for one strategy version; it never touches the current portfolio,
positions, or records after ``--end``.

Example (run from backend/ with production DATABASE_URL loaded)::

    python3 restore_shadow_history.py \
        --source-db=sqlite:///db/dual_engine_backtest_cache.db \
        --strategy-version=v1_frozen \
        --start=2026-08-01 --end=2026-09-07
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _copy_rows(source_db: Session, target_db: Session, model, query, *, label: str) -> int:
    rows = query.all()
    if not rows:
        print(f"  {label}: 0 筆")
        return 0
    columns = [column.name for column in model.__table__.columns if column.name != "id"]
    payload = [{column: getattr(row, column) for column in columns} for row in rows]
    target_db.bulk_insert_mappings(model, payload)
    print(f"  {label}: {len(payload):,} 筆")
    return len(payload)


def restore(
    *,
    source_database_url: str,
    strategy_version: str,
    start: date,
    end: date,
) -> dict[str, int]:
    from app.database import SessionLocal
    from app.models import (
        ShadowCompletedTrade,
        ShadowMissedCandidate,
        ShadowPortfolioDailySnapshot,
        ShadowStrategyDailyDecision,
        ShadowStrategyOrder,
        ShadowWinnerTracking,
    )

    source_engine = create_engine(source_database_url)
    SourceSession = sessionmaker(bind=source_engine)
    counts: dict[str, int] = {}

    with SourceSession() as source_db, SessionLocal() as target_db:
        # Delete only this strategy's historical interval so the migration is
        # idempotent. Current 9/8+ live records remain untouched.
        target_db.query(ShadowPortfolioDailySnapshot).filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
            ShadowPortfolioDailySnapshot.trade_date >= start,
            ShadowPortfolioDailySnapshot.trade_date <= end,
        ).delete(synchronize_session=False)
        target_db.query(ShadowStrategyDailyDecision).filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version,
            ShadowStrategyDailyDecision.trade_date >= start,
            ShadowStrategyDailyDecision.trade_date <= end,
        ).delete(synchronize_session=False)
        target_db.query(ShadowStrategyOrder).filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == "EXECUTED",
            ShadowStrategyOrder.scheduled_execution_date >= start,
            ShadowStrategyOrder.scheduled_execution_date <= end,
        ).delete(synchronize_session=False)
        target_db.query(ShadowCompletedTrade).filter(
            ShadowCompletedTrade.strategy_version == strategy_version,
            ShadowCompletedTrade.entry_execution_date <= end,
            ShadowCompletedTrade.exit_execution_date >= start,
        ).delete(synchronize_session=False)
        target_db.query(ShadowMissedCandidate).filter(
            ShadowMissedCandidate.strategy_version == strategy_version,
            ShadowMissedCandidate.trade_date >= start,
            ShadowMissedCandidate.trade_date <= end,
        ).delete(synchronize_session=False)
        target_db.query(ShadowWinnerTracking).filter(
            ShadowWinnerTracking.strategy_version == strategy_version,
            ShadowWinnerTracking.trade_date >= start,
            ShadowWinnerTracking.trade_date <= end,
        ).delete(synchronize_session=False)
        target_db.commit()

        counts["snapshots"] = _copy_rows(
            source_db,
            target_db,
            ShadowPortfolioDailySnapshot,
            source_db.query(ShadowPortfolioDailySnapshot).filter(
                ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
                ShadowPortfolioDailySnapshot.trade_date >= start,
                ShadowPortfolioDailySnapshot.trade_date <= end,
            ),
            label="shadow_portfolio_daily_snapshots",
        )
        counts["decisions"] = _copy_rows(
            source_db,
            target_db,
            ShadowStrategyDailyDecision,
            source_db.query(ShadowStrategyDailyDecision).filter(
                ShadowStrategyDailyDecision.strategy_version == strategy_version,
                ShadowStrategyDailyDecision.trade_date >= start,
                ShadowStrategyDailyDecision.trade_date <= end,
            ),
            label="shadow_strategy_daily_decisions",
        )
        counts["orders"] = _copy_rows(
            source_db,
            target_db,
            ShadowStrategyOrder,
            source_db.query(ShadowStrategyOrder).filter(
                ShadowStrategyOrder.strategy_version == strategy_version,
                ShadowStrategyOrder.status == "EXECUTED",
                ShadowStrategyOrder.scheduled_execution_date >= start,
                ShadowStrategyOrder.scheduled_execution_date <= end,
            ),
            label="shadow_strategy_orders (EXECUTED)",
        )
        counts["completed_trades"] = _copy_rows(
            source_db,
            target_db,
            ShadowCompletedTrade,
            source_db.query(ShadowCompletedTrade).filter(
                ShadowCompletedTrade.strategy_version == strategy_version,
                ShadowCompletedTrade.entry_execution_date <= end,
                ShadowCompletedTrade.exit_execution_date >= start,
            ),
            label="shadow_completed_trades",
        )
        counts["missed_candidates"] = _copy_rows(
            source_db,
            target_db,
            ShadowMissedCandidate,
            source_db.query(ShadowMissedCandidate).filter(
                ShadowMissedCandidate.strategy_version == strategy_version,
                ShadowMissedCandidate.trade_date >= start,
                ShadowMissedCandidate.trade_date <= end,
            ),
            label="shadow_missed_candidates",
        )
        counts["winner_tracking"] = _copy_rows(
            source_db,
            target_db,
            ShadowWinnerTracking,
            source_db.query(ShadowWinnerTracking).filter(
                ShadowWinnerTracking.strategy_version == strategy_version,
                ShadowWinnerTracking.trade_date >= start,
                ShadowWinnerTracking.trade_date <= end,
            ),
            label="shadow_winner_tracking",
        )
        target_db.commit()

    source_engine.dispose()
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a bounded historical shadow replay")
    parser.add_argument("--source-db", required=True, help="source DB URL, usually sqlite:///db/<replay>.db")
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must be on or after --start")
    counts = restore(
        source_database_url=args.source_db,
        strategy_version=args.strategy_version,
        start=args.start,
        end=args.end,
    )
    print(f"完成：{strategy_version_label(args.strategy_version)} {counts}")
    return 0


def strategy_version_label(value: str) -> str:
    return value


if __name__ == "__main__":
    raise SystemExit(main())
