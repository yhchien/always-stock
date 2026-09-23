"""Start an anchored whole-strategy repair experiment.

Example:
    python start_shadow_repair_run.py \
      --run-key incident-2026-09-21-6933 \
      --title "6933 decision-day repair" \
      --anchor-date 2026-09-21 \
      --baseline-strategy v1_frozen \
      --candidate-strategy FORWARD_V1_202609 \
      --reason "Record the strategy change from the incident decision date"

The anchor date is required on purpose.  This command never defaults it to
today, because a repair experiment must reproduce the state at the decision
that exposed the problem.
"""
from __future__ import annotations

import argparse
from datetime import date

from app.database import SessionLocal, engine
from app.signals.shadow_portfolio import STRATEGY_PARAMS_BY_VERSION, ensure_shadow_portfolio_tables
from app.signals.shadow_repair_lab import add_repair_revision, create_repair_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an anchored live strategy-repair run")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--anchor-date", required=True, type=date.fromisoformat)
    parser.add_argument("--baseline-strategy", required=True)
    parser.add_argument("--candidate-strategy", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--trigger-stock-id")
    parser.add_argument("--trigger-stock-name")
    parser.add_argument("--cycle-number", type=int)
    parser.add_argument("--initial-capital", type=float, default=1000000.0)
    parser.add_argument("--notes")
    parser.add_argument("--before-label", default="原策略")
    parser.add_argument("--after-label", default="修正版")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baseline_strategy not in STRATEGY_PARAMS_BY_VERSION:
        raise SystemExit(f"unknown baseline strategy: {args.baseline_strategy}")
    if args.candidate_strategy not in STRATEGY_PARAMS_BY_VERSION:
        raise SystemExit(f"unknown candidate strategy: {args.candidate_strategy}")

    ensure_shadow_portfolio_tables(engine)
    with SessionLocal() as db:
        run = create_repair_run(
            db,
            run_key=args.run_key,
            title=args.title,
            baseline_strategy_version=args.baseline_strategy,
            candidate_strategy_version=args.candidate_strategy,
            anchor_trade_date=args.anchor_date,
            initial_capital=args.initial_capital,
            cycle_number=args.cycle_number,
            notes=args.notes,
        )
        revision = add_repair_revision(
            db,
            run_id=run.id,
            effective_trade_date=args.anchor_date,
            title=args.title,
            reason=args.reason,
            before_strategy_label=args.before_label,
            after_strategy_label=args.after_label,
            trigger_stock_id=args.trigger_stock_id,
            trigger_stock_name=args.trigger_stock_name,
            before_config=STRATEGY_PARAMS_BY_VERSION[args.baseline_strategy],
            after_config=STRATEGY_PARAMS_BY_VERSION[args.candidate_strategy],
        )
        db.commit()
        print(f"created run={run.run_key} id={run.id} revision={revision.revision_no} anchor={run.anchor_trade_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
