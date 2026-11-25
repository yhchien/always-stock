"""Seed the market-calendar prediction table from a walk-forward CSV.

This is intentionally idempotent so the historical fifty-day result can be
loaded after deployment without duplicating rows.

Example:
    python3 seed_market_calendar_predictions.py \
      --input ../docs/reports/market_calendar_rule_analysis_2026-08-03_2026-11-23_walk_forward.csv
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from app.database import Base, SessionLocal, engine
from app.models import MarketCalendarPrediction


def _date(value: str) -> Optional[date]:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def _float(value: str) -> Optional[float]:
    value = (value or "").strip()
    return float(value) if value else None


def _result_status(predicted: str, actual: str, actual_return: Optional[float]) -> str:
    if not actual:
        return "pending"
    if predicted == actual:
        return "correct"
    if actual_return is not None and abs(actual_return) <= 0.30:
        return "tolerated"
    return "incorrect"


def _trim_prediction_history(db, limit: int = 50) -> None:
    stale = (
        db.query(MarketCalendarPrediction)
        .order_by(MarketCalendarPrediction.signal_date.desc())
        .offset(limit)
        .all()
    )
    for row in stale:
        db.delete(row)


def seed_predictions(input_path: Path, limit: int = 50, replace: bool = False) -> int:
    Base.metadata.create_all(bind=engine, tables=[MarketCalendarPrediction.__table__])
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("signal_date")]

    rows = rows[-limit:]
    db = SessionLocal()
    try:
        for row in rows:
            signal_date = _date(row["signal_date"])
            actual_return = _float(row.get("actual_return", ""))
            record = (
                db.query(MarketCalendarPrediction)
                .filter(MarketCalendarPrediction.signal_date == signal_date)
                .one_or_none()
            )
            if record is not None and not replace:
                # The daily job may already have a fresher factor snapshot or
                # actual result.  Historical seeding must never overwrite it.
                continue
            if record is None:
                record = MarketCalendarPrediction(signal_date=signal_date)
                db.add(record)

            record.target_date = _date(row.get("target_date", ""))
            record.predicted_binary = row.get("predicted_binary") or "偏跌"
            record.predicted_direction = row.get("predicted_direction") or None
            record.predicted_return = _float(row.get("predicted_return", ""))
            record.actual_binary = row.get("actual_binary") or None
            record.actual_direction = row.get("actual_direction") or None
            record.actual_broad = row.get("actual_broad") or None
            record.actual_return = actual_return
            record.result_status = _result_status(
                record.predicted_binary,
                record.actual_binary or "",
                actual_return,
            )
            record.factor_snapshot = {"source": "walk_forward_csv"}
            record.explanation = {
                "matched_rule_count": int(float(row["matched_rule_count"]))
                if row.get("matched_rule_count")
                else 0
            }
            record.model_version = "market-calendar-v2-night"
            record.generated_at = datetime.utcnow()
            record.updated_at = datetime.utcnow()

        _trim_prediction_history(db, limit=50)
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--replace", action="store_true", help="update existing rows with the supplied backtest")
    args = parser.parse_args()
    count = seed_predictions(args.input, limit=args.limit, replace=args.replace)
    print(f"seeded {count} market-calendar predictions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
