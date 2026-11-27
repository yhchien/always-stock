"""Incremental raw-data cache for the Taiwan market calendar.

The first run bootstraps the requested history.  Later runs fetch only the
newest completed Taiwan session and its next overnight session, while keeping
all previously fetched rows in the database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Tuple

import numpy as np
import pandas as pd

from app.models import MarketCalendarOvernight, MarketCalendarRawData
from tools.download_market_calendar_data import build_panel as download_base_panel, fetch_taiex
from tools.download_market_calendar_margin import aggregate_margin, fetch_margin_raw, load_finmind_token
from tools.download_market_calendar_overnight import build_overnight


CACHE_LIMIT = 180


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _payload_from_row(row: pd.Series) -> Tuple[dict, Any]:
    metadata = row.get("source_metadata")
    payload = {
        str(key): _jsonable(value)
        for key, value in row.items()
        if key not in {"trade_date", "source_metadata"}
    }
    return payload, _jsonable(metadata)


def _upsert_raw(db, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    frame = frame.drop_duplicates("trade_date", keep="last")
    for _, row in frame.iterrows():
        trade_date = pd.to_datetime(row["trade_date"]).date()
        payload, metadata = _payload_from_row(row)
        record = db.get(MarketCalendarRawData, trade_date)
        if record is None:
            record = MarketCalendarRawData(trade_date=trade_date)
            db.add(record)
        record.payload = payload
        record.source_metadata = metadata
        record.fetched_at = datetime.utcnow()
        record.updated_at = datetime.utcnow()


def _upsert_overnight(db, frame: pd.DataFrame) -> None:
    if frame.empty or "trade_date" not in frame:
        return
    frame = frame.drop_duplicates("trade_date", keep="last")
    for _, row in frame.iterrows():
        target_date = pd.to_datetime(row["trade_date"]).date()
        payload, metadata = _payload_from_row(row)
        record = db.get(MarketCalendarOvernight, target_date)
        if record is None:
            record = MarketCalendarOvernight(target_date=target_date)
            db.add(record)
        record.payload = payload
        record.source_metadata = metadata
        record.fetched_at = datetime.utcnow()
        record.updated_at = datetime.utcnow()


def _load_raw(db, limit: int = CACHE_LIMIT) -> pd.DataFrame:
    rows = (
        db.query(MarketCalendarRawData)
        .order_by(MarketCalendarRawData.trade_date.desc())
        .limit(limit)
        .all()
    )
    records = []
    for row in reversed(rows):
        record = {"trade_date": row.trade_date.isoformat(), **(row.payload or {})}
        if row.source_metadata is not None:
            record["source_metadata"] = row.source_metadata
        records.append(record)
    return pd.DataFrame(records)


def _load_overnight(db, limit: int = CACHE_LIMIT) -> pd.DataFrame:
    rows = (
        db.query(MarketCalendarOvernight)
        .order_by(MarketCalendarOvernight.target_date.desc())
        .limit(limit)
        .all()
    )
    records = []
    for row in reversed(rows):
        record = {"trade_date": row.target_date.isoformat(), **(row.payload or {})}
        if row.source_metadata is not None:
            record["source_metadata"] = row.source_metadata
        records.append(record)
    return pd.DataFrame(records)


def _bootstrap(db, target_date: date, history_days: int, token: str):
    history_start = target_date - timedelta(days=history_days)
    raw = download_base_panel(history_start, target_date)
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    margin = aggregate_margin(fetch_margin_raw(str(history_start), str(raw["trade_date"].max().date()), token))
    margin["trade_date"] = pd.to_datetime(margin["trade_date"])
    raw = raw.merge(margin, on="trade_date", how="left")
    overnight = build_overnight(
        history_start,
        target_date,
        target_dates=raw["trade_date"].dt.strftime("%Y-%m-%d").tolist(),
    )
    _upsert_raw(db, raw)
    _upsert_overnight(db, overnight)
    db.commit()


def _latest_signal_date(target_date: date) -> date:
    probe = fetch_taiex(target_date - timedelta(days=10), target_date)
    dates = pd.to_datetime(probe["trade_date"], errors="coerce").dt.date
    completed = [value for value in dates if value and value < target_date]
    if not completed:
        raise RuntimeError("no completed Taiwan session before target date")
    return max(completed)


def _source_is_missing(value: object) -> bool:
    return isinstance(value, dict) and value.get("status") == "missing"


def _needs_external_refresh(raw: pd.DataFrame, signal_date: date) -> bool:
    """Refresh a cached signal row when a previously late source is missing.

    A cached row is not treated as immutable if an external market had not
    published its close when the first run happened.  The raw row is still
    upserted by date (never deleted), so a later successful fetch can fill the
    source without changing the append-only retention policy.
    """
    rows = raw[raw["trade_date"].dt.date == signal_date]
    if rows.empty:
        return True
    metadata = rows.iloc[-1].get("source_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return True
    if not isinstance(metadata, dict):
        return True
    return any(
        _source_is_missing(metadata.get(name))
        for name in ("us_vix", "us10y")
    )


def _needs_overnight_refresh(db, target_date: date) -> bool:
    record = db.get(MarketCalendarOvernight, target_date)
    if record is None:
        return True
    metadata = record.source_metadata or {}
    if not isinstance(metadata, dict):
        return True
    return any(
        _source_is_missing(metadata.get(name))
        for name in ("us_sp500", "us_nasdaq", "us_sox", "us_es", "us_nq", "us_ym")
    )


def load_or_update_market_calendar_data(db, target_date: date, history_days: int, token: str):
    """Return cached raw panel and overnight factors, fetching only new rows."""
    raw = _load_raw(db)
    if raw.empty:
        _bootstrap(db, target_date, history_days, token)
        return _load_raw(db), _load_overnight(db)

    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    signal_date = _latest_signal_date(target_date)
    cached_dates = set(raw["trade_date"].dt.date)
    if signal_date not in cached_dates or _needs_external_refresh(raw, signal_date):
        # Include the preceding week so a US holiday or late publication can
        # be aligned to the latest effective US observation before this
        # Taiwan session.  We upsert only signal_date; historical rows stay in
        # the database and are never discarded.
        refresh_start = signal_date - timedelta(days=7)
        latest_panel = download_base_panel(refresh_start, signal_date)
        latest_panel["trade_date"] = pd.to_datetime(latest_panel["trade_date"])
        latest = latest_panel[latest_panel["trade_date"].dt.date == signal_date].copy()
        if latest.empty:
            raise RuntimeError(f"market calendar refresh returned no signal row for {signal_date}")
        latest_margin = aggregate_margin(fetch_margin_raw(str(signal_date), str(signal_date), token))
        latest_margin["trade_date"] = pd.to_datetime(latest_margin["trade_date"])
        latest = latest.merge(latest_margin, on="trade_date", how="left")
        _upsert_raw(db, latest)

    if _needs_overnight_refresh(db, target_date):
        latest_overnight = build_overnight(
            signal_date - timedelta(days=7),
            target_date,
            target_dates=[target_date.isoformat()],
        )
        _upsert_overnight(db, latest_overnight)
    db.commit()
    return _load_raw(db), _load_overnight(db)
