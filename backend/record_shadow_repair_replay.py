"""Persist a completed baseline/candidate replay into Repair Lab."""
from __future__ import annotations

import argparse
from datetime import date

from app.database import SessionLocal, engine
from app.models import ShadowRepairRevision, ShadowRepairRun
from app.signals.shadow_portfolio import ensure_shadow_portfolio_tables
from app.signals.shadow_repair_lab import (
    record_daily_diffs_for_run,
    record_daily_state_from_strategy,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist a completed repair replay")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be on or after --start")

    ensure_shadow_portfolio_tables(engine)
    with SessionLocal() as db:
        run = db.query(ShadowRepairRun).filter(ShadowRepairRun.run_key == args.run_key).first()
        if run is None:
            raise SystemExit(f"repair run not found: {args.run_key}")
        revision = (
            db.query(ShadowRepairRevision)
            .filter(ShadowRepairRevision.run_id == run.id)
            .order_by(ShadowRepairRevision.revision_no.desc())
            .first()
        )
        dates = sorted({
            row[0]
            for row in db.query(ShadowRepairRevision.effective_trade_date)
            .filter(ShadowRepairRevision.run_id == run.id)
            .all()
            if args.start <= row[0] <= args.end
        } | {args.start, args.end})
        # The run is anchored to the incident date.  The replay may start
        # earlier to establish positions, but Repair Lab only exposes the
        # incident window requested by the operator.
        dates = [d for d in dates if args.start <= d <= args.end]
        for trade_date in dates:
            revision_for_date = (
                db.query(ShadowRepairRevision)
                .filter(
                    ShadowRepairRevision.run_id == run.id,
                    ShadowRepairRevision.effective_trade_date <= trade_date,
                )
                .order_by(ShadowRepairRevision.revision_no.desc())
                .first()
            )
            record_daily_state_from_strategy(
                db,
                run_id=run.id,
                trade_date=trade_date,
                track="BASELINE",
                strategy_version=run.baseline_strategy_version,
                revision_id=revision_for_date.id if revision_for_date else None,
            )
            record_daily_state_from_strategy(
                db,
                run_id=run.id,
                trade_date=trade_date,
                track="CANDIDATE",
                strategy_version=run.candidate_strategy_version,
                revision_id=revision_for_date.id if revision_for_date else None,
            )
            record_daily_diffs_for_run(db, run=run, trade_date=trade_date)
        db.commit()
        print(
            f"recorded run={run.run_key} dates={args.start}..{args.end} "
            f"revision={revision.revision_no if revision else 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
