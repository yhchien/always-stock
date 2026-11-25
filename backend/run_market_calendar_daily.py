"""Fetch, score, and persist the daily Taiwan market-calendar forecast.

The job is designed for the 06:30 Asia/Taipei pre-market run.  It uses the
previous Taiwan session plus the completed US and TAIFEX night sessions for
the target trading day, then upserts the result into the database.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.download_market_calendar_data import build_panel as download_base_panel  # noqa: E402
from tools.download_market_calendar_margin import (  # noqa: E402
    aggregate_margin,
    fetch_margin_raw,
    load_finmind_token,
)
from tools.download_market_calendar_overnight import build_overnight  # noqa: E402
from tools.market_calendar_rules import (  # noqa: E402
    FACTOR_COLUMNS,
    add_factor_states,
    binary_label_from_return,
    binary_prediction_with_momentum,
    build_rule_panel,
    discover_rules,
    expected_return_with_factor_blend,
    expected_return_interval,
    fit_factor_return_model,
    fit_overnight_return_model,
    format_rule,
    interpret_factor_changes,
    predict_with_rules,
)
from app.market_calendar_ai import interpret_market_calendar  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import MarketCalendarPrediction  # noqa: E402

logger = logging.getLogger("market_calendar_daily")


def _json_number(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _result_status(predicted: Optional[str], actual: Optional[str], actual_return: Optional[float]) -> str:
    if not actual:
        return "pending"
    if predicted == actual:
        return "correct"
    if actual_return is not None and abs(actual_return) <= 0.30:
        return "tolerated"
    return "incorrect"


def _factor_snapshot(row: pd.Series) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Optional[float]]] = {}
    total = 0
    available = 0
    for group, columns in FACTOR_COLUMNS.items():
        values: Dict[str, Optional[float]] = {}
        for column in columns:
            if column not in row:
                continue
            total += 1
            value = _json_number(row[column])
            if value is not None:
                available += 1
            values[column] = value
        groups[group] = values
    missing_columns = [
        column
        for columns in FACTOR_COLUMNS.values()
        for column in columns
        if column in row and _json_number(row[column]) is None
    ]
    source_metadata = row.get("source_metadata")
    if isinstance(source_metadata, str):
        try:
            source_metadata = json.loads(source_metadata)
        except json.JSONDecodeError:
            source_metadata = None
    return {
        "groups": groups,
        "available_columns": available,
        "total_columns": total,
        "missing_columns": missing_columns,
        "source_metadata": source_metadata or {},
    }


def _next_overnight_date(overnight: pd.DataFrame, signal_date: pd.Timestamp) -> Optional[date]:
    if overnight.empty or "trade_date" not in overnight:
        return None
    dates = pd.to_datetime(overnight["trade_date"], errors="coerce").dropna()
    future = dates[dates > signal_date]
    return future.min().date() if not future.empty else None


def _update_actuals(db, panel: pd.DataFrame) -> None:
    """Backfill known target results for previously stored forecasts."""
    rows = panel.dropna(subset=["target_return"]).copy()
    for _, source in rows.iterrows():
        signal_date = source["trade_date"].date()
        record = (
            db.query(MarketCalendarPrediction)
            .filter(MarketCalendarPrediction.signal_date == signal_date)
            .one_or_none()
        )
        if record is None:
            continue
        actual_return = _json_number(source["target_return"])
        actual_binary = binary_label_from_return(actual_return)
        record.target_date = source["target_date"].date() if not pd.isna(source["target_date"]) else record.target_date
        record.actual_return = actual_return
        record.actual_binary = actual_binary
        record.actual_direction = source.get("target_direction")
        record.actual_broad = source.get("target_broad")
        record.result_status = _result_status(record.predicted_binary, actual_binary, actual_return)
        record.updated_at = datetime.utcnow()


def generate_daily_prediction(target_date: date, history_days: int = 120) -> Dict[str, Any]:
    history_start = target_date - timedelta(days=history_days)
    logger.info("fetching market-calendar data %s to %s", history_start, target_date)

    raw = download_base_panel(history_start, target_date)
    if raw.empty:
        raise RuntimeError("TAIEX data source returned no rows")
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    signal_date = raw["trade_date"].max()
    if signal_date >= pd.Timestamp(target_date):
        raw = raw[raw["trade_date"] < pd.Timestamp(target_date)].copy()
        signal_date = raw["trade_date"].max()
    if pd.isna(signal_date):
        raise RuntimeError("no completed Taiwan session before target date")

    token = load_finmind_token()
    margin_raw = fetch_margin_raw(str(history_start), str(signal_date.date()), token)
    margin = aggregate_margin(margin_raw)
    overnight = build_overnight(history_start, target_date)
    panel = build_rule_panel(raw, margin, overnight)

    signal_rows = panel[panel["trade_date"] == signal_date]
    if signal_rows.empty:
        raise RuntimeError(f"missing signal row for {signal_date.date()}")
    signal_row = signal_rows.iloc[0]
    overnight_target = _next_overnight_date(overnight, signal_date)
    if overnight_target != target_date:
        raise RuntimeError(
            f"target date {target_date} is not the next completed overnight session "
            f"after {signal_date.date()} (found {overnight_target})"
        )

    train = panel[panel["trade_date"] < signal_date].dropna(
        subset=["target_direction", "target_return"]
    ).copy()
    if len(train) < 15:
        raise RuntimeError(f"not enough historical rows for rule discovery: {len(train)}")

    rules, cutoffs, train_states = discover_rules(train, min_support=4, target_column="target_direction")
    latest_states = add_factor_states(pd.DataFrame([signal_row]), cutoffs)
    latest_row = latest_states.iloc[0]
    fallback = float(train["target_return"].mean())
    direction, expected_return, matched = predict_with_rules(latest_row, rules, fallback)
    binary = binary_prediction_with_momentum(latest_row, expected_return)
    overnight_model = fit_overnight_return_model(train)
    factor_model = fit_factor_return_model(train)
    final_return = expected_return_with_factor_blend(
        latest_row,
        expected_return,
        binary,
        overnight_model,
        factor_model,
    )
    interval_low, interval_high = expected_return_interval(
        train, final_return, overnight_model
    )
    snapshot = _factor_snapshot(signal_row)
    completeness = (
        snapshot["available_columns"] / snapshot["total_columns"]
        if snapshot["total_columns"]
        else None
    )
    history = train["target_return"]
    explanation = {
        "rule_expected_return": _json_number(expected_return),
        "overnight_return_model": overnight_model,
        "factor_return_model": factor_model,
        "factor_interpretation": interpret_factor_changes(latest_row),
        "final_predicted_return": _json_number(final_return),
        "matched_rules": [format_rule(rule) for rule in matched],
        "history_count": int(len(history)),
        "q25": _json_number(history.quantile(0.25)),
        "q75": _json_number(history.quantile(0.75)),
    }
    ai_payload = {
        "as_of_date": signal_date.date().isoformat(),
        "target_date": target_date.isoformat(),
        "baseline_prediction": {
            "predicted_binary": binary or "偏跌",
            "predicted_return": _json_number(final_return),
            "predicted_return_low": _json_number(interval_low),
            "predicted_return_high": _json_number(interval_high),
        },
        "rule_expected_return": _json_number(expected_return),
        "matched_rules": [format_rule(rule) for rule in matched],
        "deterministic_evidence": interpret_factor_changes(latest_row),
        "factors": {
            group: {
                name: _json_number(signal_row.get(name))
                for name in names
                if name in signal_row
            }
            for group, names in FACTOR_COLUMNS.items()
        },
    }
    ai_interpretation = interpret_market_calendar(ai_payload)
    explanation["ai_interpretation"] = ai_interpretation

    Base.metadata.create_all(bind=engine, tables=[MarketCalendarPrediction.__table__])
    db = SessionLocal()
    try:
        _update_actuals(db, panel)
        record = (
            db.query(MarketCalendarPrediction)
            .filter(MarketCalendarPrediction.signal_date == signal_date.date())
            .one_or_none()
        )
        if record is None:
            record = MarketCalendarPrediction(signal_date=signal_date.date())
            db.add(record)
        record.target_date = target_date
        record.predicted_binary = binary or "偏跌"
        record.predicted_direction = direction
        record.predicted_return = _json_number(final_return)
        record.predicted_return_low = _json_number(interval_low)
        record.predicted_return_high = _json_number(interval_high)
        record.confidence = "high" if completeness is not None and completeness >= 0.85 else "low"
        record.data_completeness = completeness
        record.factor_snapshot = snapshot
        record.explanation = explanation
        record.result_status = "pending"
        record.model_version = "market-calendar-v3-blend"
        record.generated_at = datetime.utcnow()
        record.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "signal_date": signal_date.date().isoformat(),
        "target_date": target_date.isoformat(),
        "predicted_binary": binary,
        "predicted_direction": direction,
        "predicted_return": _json_number(final_return),
        "data_completeness": completeness,
        "matched_rule_count": len(matched),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", type=date.fromisoformat)
    parser.add_argument("--history-days", type=int, default=120)
    args = parser.parse_args()
    target_date = args.target_date or datetime.now(ZoneInfo("Asia/Taipei")).date()
    result = generate_daily_prediction(target_date, history_days=args.history_days)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
