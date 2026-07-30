"""Idempotent P6 Day10 outcome cache backfill.

Usage:
    python run_signal_outcome_backfill.py \
      --start-date 2026-04-01 --end-date 2026-07-29 \
      --outcome-version day10_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.database import SessionLocal, engine
from app.outcome_schema import ensure_outcome_tables
from app.signals.outcome_metrics import (
    OUTCOME_DEFINITION_VERSION,
    refresh_outcome_cache,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill P6 Day10 outcomes")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--outcome-version",
        default=OUTCOME_DEFINITION_VERSION,
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("start-date cannot be later than end-date", file=sys.stderr)
        return 2
    if args.outcome_version != OUTCOME_DEFINITION_VERSION:
        print(
            f"unsupported outcome version: {args.outcome_version}",
            file=sys.stderr,
        )
        return 2
    ensure_outcome_tables(engine)
    with SessionLocal() as db:
        result = refresh_outcome_cache(
            db,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    print(
        json.dumps(
            {
                "outcome_version": OUTCOME_DEFINITION_VERSION,
                "start_date": (
                    args.start_date.isoformat() if args.start_date else None
                ),
                "end_date": (
                    args.end_date.isoformat() if args.end_date else None
                ),
                **result,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
