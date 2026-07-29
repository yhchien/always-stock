"""Point-in-time P4 observation lifecycle replay.

The command reconstructs selected observation episodes chronologically from their
original recommendation dates.  It calls the date-bounded tracking prompt but does
not write SignalObservation, SignalObservationReview, SignalSnapshot, or
SignalWatchHit rows.

Examples:
    python run_p4_tracking_replay.py 2026-07-01 2026-07-29
    python run_p4_tracking_replay.py 2026-07-01 2026-07-29 \
        --observation-id 12 --out /tmp/p4-replay.json
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from app.database import SessionLocal
from app.signals.observation_lifecycle import replay_observation_lifecycle


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only P4 point-in-time observation replay."
    )
    parser.add_argument("start_date", type=date.fromisoformat)
    parser.add_argument("end_date", type=date.fromisoformat)
    parser.add_argument(
        "--observation-id",
        dest="observation_ids",
        type=int,
        action="append",
        help="Replay only this observation id; may be repeated.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional JSON output path. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    with SessionLocal() as db:
        payload = replay_observation_lifecycle(
            db,
            start_date=args.start_date,
            end_date=args.end_date,
            observation_ids=args.observation_ids,
        )
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        output_path = Path(args.out).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized + "\n", encoding="utf-8")
        print(
            f"P4 replay wrote {len(payload['rows'])} rows to {output_path}"
        )
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
