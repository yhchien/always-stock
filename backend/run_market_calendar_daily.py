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
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.download_market_calendar_margin import load_finmind_token  # noqa: E402
from market_calendar_raw_cache import load_or_update_market_calendar_data  # noqa: E402
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
from app.market_calendar_ai import (  # noqa: E402
    interpret_market_calendar,
    recover_missing_market_calendar_factors,
)

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    MarketCalendarOvernight,
    MarketCalendarPrediction,
    MarketCalendarRawData,
)

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
            # Count the declared schema, not only columns that happened to
            # arrive in the DataFrame.  Otherwise a whole missing source can
            # make the denominator smaller and falsely look complete.
            total += 1
            if column not in row:
                values[column] = None
                continue
            value = _json_number(row[column])
            if value is not None:
                available += 1
            values[column] = value
        groups[group] = values
    missing_columns = [
        column
        for columns in FACTOR_COLUMNS.values()
        for column in columns
        if column not in row or _json_number(row[column]) is None
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


def _require_complete_factors(snapshot: Dict[str, Any]) -> None:
    """Reject a forecast unless every declared factor is present.

    A missing external observation is not a bearish or neutral observation.
    The daily action must retry/fallback at the source layer instead of
    writing a prediction from an incomplete factor row.
    """
    missing_columns = list(snapshot.get("missing_columns", []))
    if not missing_columns and snapshot.get("available_columns") == snapshot.get("total_columns"):
        return
    metadata = snapshot.get("source_metadata") or {}
    missing_sources = [
        name
        for name, value in metadata.items()
        if isinstance(value, dict) and value.get("status") == "missing"
    ]
    source_text = ", ".join(missing_sources) if missing_sources else "derived fields"
    raise RuntimeError(
        "market calendar forecast blocked: data completeness is not 100%; "
        f"missing columns={','.join(missing_columns)}; missing sources={source_text}"
    )


def _recovery_definitions(missing_columns: Iterable[str]) -> Dict[str, str]:
    definitions = {
        "us_sp500_change": "S&P 500 現貨相對前一個美國有效交易日的百分比變化",
        "us_nasdaq_change": "Nasdaq Composite 現貨相對前一個美國有效交易日的百分比變化，不是 Nasdaq-100 期貨",
        "us_sox_change": "Philadelphia Semiconductor Index 現貨相對前一個美國有效交易日的百分比變化；優先查 Nasdaq SOX history、Nasdaq official SOX page 或 FRED NASDAQSOX",
        "us_es_change": "E-mini S&P 500 ES 連續期貨的隔夜百分比變化；優先查 Yahoo ES=F history、Finabase ES=F history、Investing S&P 500 Futures historical data 或 CME",
        "us_nq_change": "E-mini Nasdaq-100 NQ 連續期貨的隔夜百分比變化；優先查 Yahoo NQ=F history、Finabase NQ=F history、Trading-Commodity NQ daily history 或 CME",
        "us_ym_change": "E-mini Dow YM 連續期貨的隔夜百分比變化；優先查 Yahoo YM=F history、Finabase YM=F history、Investing Dow Futures historical data 或 CME",
        "tx_night_return_pct": "TAIFEX TX 盤後收盤相對台股現貨訊號日收盤的百分比變化",
        "mtx_night_return_pct": "TAIFEX MTX 盤後收盤相對台股現貨訊號日收盤的百分比變化",
    }
    return {column: definitions.get(column, "已宣告的台股行事曆因素欄位；必須找到同日原始資料並依單位回傳") for column in missing_columns}


def _apply_valid_recovery(
    row: pd.Series,
    snapshot: Dict[str, Any],
    recovery: Dict[str, Any],
    signal_date: pd.Timestamp,
    target_date: date,
) -> Dict[str, Any]:
    """Apply only source-backed, same-date numeric recovery results."""
    missing = set(snapshot.get("missing_columns", []))
    accepted = []
    rejected = []
    allowed_dates = {signal_date.date().isoformat(), target_date.isoformat()}
    metadata = snapshot.get("source_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    for item in recovery.get("results", []) if isinstance(recovery, dict) else []:
        column = item.get("column") if isinstance(item, dict) else None
        value = _json_number(item.get("value")) if isinstance(item, dict) else None
        observed_raw = item.get("observed_date", "") if isinstance(item, dict) else ""
        observed_timestamp = pd.to_datetime(observed_raw, errors="coerce")
        observed_date = (
            observed_timestamp.date().isoformat()
            if not pd.isna(observed_timestamp)
            else ""
        )
        source_url = str(item.get("source_url", "")).strip() if isinstance(item, dict) else ""
        parsed_url = urlparse(source_url)
        valid = (
            column in missing
            and value is not None
            and observed_date in allowed_dates
            and parsed_url.scheme in {"http", "https"}
            and bool(parsed_url.netloc)
            and bool(str(item.get("unit", "")).strip())
        )
        if not valid:
            rejected.append({"item": item, "reason": "欄位、數值、觀測日、單位或來源 URL 驗證失敗"})
            continue
        row[column] = value
        metadata[f"ai_recovery:{column}"] = {
            "source": source_url,
            "series_id": item.get("source_name"),
            "unit": item.get("unit"),
            "observed_date": observed_date,
            "status": "available",
            "recovered_by": "openai_web_search",
            "evidence": item.get("evidence", ""),
        }
        accepted.append({
            "column": column,
            "value": value,
            "unit": item.get("unit"),
            "observed_date": observed_date,
            "source_url": source_url,
            "source_name": item.get("source_name"),
        })
    if accepted:
        row["source_metadata"] = metadata
    return {"accepted": accepted, "rejected": rejected}


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
    token = load_finmind_token()
    Base.metadata.create_all(
        bind=engine,
        tables=[
            MarketCalendarPrediction.__table__,
            MarketCalendarRawData.__table__,
            MarketCalendarOvernight.__table__,
        ],
    )
    db = SessionLocal()
    try:
        raw, overnight = load_or_update_market_calendar_data(
            db,
            target_date,
            history_days,
            token,
        )
        if raw.empty:
            raise RuntimeError("market calendar raw cache returned no rows")
        raw["trade_date"] = pd.to_datetime(raw["trade_date"])
        raw = raw[raw["trade_date"] < pd.Timestamp(target_date)].copy()
        raw = raw.sort_values("trade_date").tail(180).reset_index(drop=True)
        signal_date = raw["trade_date"].max()
        if pd.isna(signal_date):
            raise RuntimeError("no completed Taiwan session before target date")
        panel = build_rule_panel(raw, None, overnight)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    signal_rows = panel[panel["trade_date"] == signal_date]
    if signal_rows.empty:
        raise RuntimeError(f"missing signal row for {signal_date.date()}")
    signal_row = signal_rows.iloc[0].copy()
    overnight_target = _next_overnight_date(overnight, signal_date)
    if overnight_target != target_date:
        raise RuntimeError(
            f"target date {target_date} is not the next completed overnight session "
            f"after {signal_date.date()} (found {overnight_target})"
        )

    snapshot = _factor_snapshot(signal_row)
    recovery_attempts = []
    for attempt in range(1, 4):
        completeness = (
            snapshot["available_columns"] / snapshot["total_columns"]
            if snapshot["total_columns"]
            else None
        )
        if completeness == 1.0:
            break
        recovery_payload = {
            "signal_date": signal_date.date().isoformat(),
            "target_date": target_date.isoformat(),
            "missing_columns": snapshot["missing_columns"],
            "factor_definitions": _recovery_definitions(snapshot["missing_columns"]),
            "source_metadata": snapshot.get("source_metadata", {}),
            "previous_recovery_attempts": recovery_attempts,
            "instruction": "只補齊缺失欄位；找到同日可靠來源前不要回傳數值。",
        }
        recovery = recover_missing_market_calendar_factors(recovery_payload)
        applied = _apply_valid_recovery(
            signal_row,
            snapshot,
            recovery,
            signal_date,
            target_date,
        )
        recovery_attempts.append({
            "attempt": attempt,
            "status": recovery.get("status") if isinstance(recovery, dict) else "invalid",
            "search_notes": recovery.get("search_notes", "") if isinstance(recovery, dict) else "",
            "accepted": applied["accepted"],
            "rejected_count": len(applied["rejected"]),
        })
        logger.info(
            "market-calendar recovery attempt=%s status=%s accepted=%s unresolved=%s rejected=%s",
            attempt,
            recovery.get("status") if isinstance(recovery, dict) else "invalid",
            [item.get("column") for item in applied["accepted"]],
            recovery.get("unresolved", []) if isinstance(recovery, dict) else [],
            len(applied["rejected"]),
        )
        snapshot = _factor_snapshot(signal_row)
        if not applied["accepted"] and recovery.get("status") in {"not_found", "unavailable"}:
            # A bounded retry prevents a source outage from causing an
            # unbounded paid web-search loop.  The final completeness gate
            # still blocks the forecast if all three attempts fail.
            continue
    completeness = (
        snapshot["available_columns"] / snapshot["total_columns"]
        if snapshot["total_columns"]
        else None
    )
    _require_complete_factors(snapshot)

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
        "data_recovery": recovery_attempts,
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
        "data_recovery": recovery_attempts,
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
        record.result_status = _result_status(
            record.predicted_binary,
            record.actual_binary,
            record.actual_return,
        )
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
