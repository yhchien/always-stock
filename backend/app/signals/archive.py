"""Read/write helpers for the M23 40-trading-day signal archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DailyPrice, SignalSnapshot, SignalWatchHit

ARCHIVE_RETENTION_TRADE_DAYS = 40
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SIGNALS_SAME_DAY_READY_TIME = time(hour=19, minute=0)


@dataclass
class ArchiveSummaryItem:
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    sub_industry: Optional[str]
    first_seen_date: date
    latest_hit_date: date
    tracking_day_index: int
    hit_count: int
    latest_signal_type: str
    baseline_trade_date: Optional[date]
    baseline_price: Optional[float]
    latest_eval_trade_date: Optional[date]
    latest_eval_price: Optional[float]
    return_pct: Optional[float]


def persist_signal_watch_hits(
    db: Session,
    target_date: date,
    payload: Dict[str, Any],
    job_id: str,
) -> None:
    """Replace a snapshot day's archive hits with the latest watchlist, then prune retention."""
    db.query(SignalWatchHit).filter(SignalWatchHit.snapshot_date == target_date).delete(
        synchronize_session=False
    )

    snapshot = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == target_date)
        .one_or_none()
    )
    generated_at = snapshot.generated_at if snapshot is not None else None
    watchlist = payload.get("watchlist") or []

    for item in watchlist:
        db.add(
            SignalWatchHit(
                snapshot_date=target_date,
                stock_id=str(item.get("stock") or ""),
                stock_name=str(item.get("name") or ""),
                signal_type=str(item.get("type") or "LEADER").upper(),
                industry_name=item.get("industry"),
                sub_industry=item.get("sub_industry"),
                business_summary=item.get("business_summary"),
                reason=str(item.get("reason") or ""),
                theme=item.get("theme") or {},
                group_info=item.get("group_info") or {},
                leader_check=item.get("leader_check") or {},
                signals=item.get("signals") or {},
                snapshot_generated_at=generated_at,
                job_id=job_id,
            )
        )

    _prune_signal_watch_hits(db)
    db.commit()


def _prune_signal_watch_hits(db: Session) -> None:
    keep_dates = [
        row[0]
        for row in (
            db.query(SignalSnapshot.snapshot_date)
            .order_by(SignalSnapshot.snapshot_date.desc())
            .limit(ARCHIVE_RETENTION_TRADE_DAYS)
            .all()
        )
    ]
    if keep_dates:
        db.query(SignalWatchHit).filter(
            ~SignalWatchHit.snapshot_date.in_(keep_dates)
        ).delete(synchronize_session=False)
    else:
        db.query(SignalWatchHit).delete(synchronize_session=False)


def list_archive_summary(
    db: Session,
    *,
    sort_by: str = "tracking_days_desc",
    signal_type: Optional[str] = None,
    limit: int = 200,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    as_of_trade_date = resolve_archive_as_of_trade_date(db, now=now)
    grouped = _load_grouped_hits(db)
    if not grouped:
        return {
            "as_of_trade_date": as_of_trade_date,
            "retention_trade_days": ARCHIVE_RETENTION_TRADE_DAYS,
            "items": [],
        }

    tracking_day_cache: dict[date, int] = {}
    items: List[ArchiveSummaryItem] = []
    for stock_id, rows in grouped.items():
        summary = _build_archive_summary_item(
            db,
            stock_id=stock_id,
            rows=rows,
            as_of_trade_date=as_of_trade_date,
            tracking_day_cache=tracking_day_cache,
        )
        if signal_type and summary.latest_signal_type != signal_type.upper():
            continue
        items.append(summary)

    ordered = sorted(items, key=lambda item: _summary_sort_key(item, sort_by))
    if limit > 0:
        ordered = ordered[:limit]
    return {
        "as_of_trade_date": as_of_trade_date,
        "retention_trade_days": ARCHIVE_RETENTION_TRADE_DAYS,
        "items": [_serialize_summary_item(item) for item in ordered],
    }


def get_archive_detail(
    db: Session,
    stock_id: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    grouped = _load_grouped_hits(db, stock_ids=[stock_id])
    rows = grouped.get(stock_id)
    if not rows:
        return None

    as_of_trade_date = resolve_archive_as_of_trade_date(db, now=now)
    summary = _build_archive_summary_item(
        db,
        stock_id=stock_id,
        rows=rows,
        as_of_trade_date=as_of_trade_date,
        tracking_day_cache={},
    )
    reports = [
        {
            "snapshot_date": row.snapshot_date,
            "signal_type": row.signal_type,
            "reason": row.reason,
            "business_summary": row.business_summary,
            "snapshot_generated_at": row.snapshot_generated_at,
        }
        for row in sorted(rows, key=lambda row: row.snapshot_date, reverse=True)
    ]
    payload = _serialize_summary_item(summary)
    payload["reports"] = reports
    return payload


def resolve_archive_as_of_trade_date(
    db: Session,
    *,
    now: Optional[datetime] = None,
) -> Optional[date]:
    now = now or datetime.now(TAIPEI_TZ)
    ceiling = (
        now.date()
        if now.time() >= SIGNALS_SAME_DAY_READY_TIME
        else now.date() - timedelta(days=1)
    )
    return (
        db.query(func.max(DailyPrice.trade_date))
        .filter(DailyPrice.trade_date <= ceiling)
        .scalar()
    )


def _load_grouped_hits(
    db: Session,
    *,
    stock_ids: Optional[Iterable[str]] = None,
) -> dict[str, list[SignalWatchHit]]:
    query = db.query(SignalWatchHit)
    if stock_ids:
        query = query.filter(SignalWatchHit.stock_id.in_(list(stock_ids)))
    rows = query.order_by(SignalWatchHit.stock_id, SignalWatchHit.snapshot_date).all()
    grouped: dict[str, list[SignalWatchHit]] = {}
    for row in rows:
        grouped.setdefault(row.stock_id, []).append(row)
    return grouped


def _build_archive_summary_item(
    db: Session,
    *,
    stock_id: str,
    rows: list[SignalWatchHit],
    as_of_trade_date: Optional[date],
    tracking_day_cache: dict[date, int],
) -> ArchiveSummaryItem:
    first_row = rows[0]
    latest_row = rows[-1]
    first_seen_date = first_row.snapshot_date
    latest_hit_date = latest_row.snapshot_date
    hit_count = len(rows)
    tracking_day_index = _count_tracking_days(
        db,
        first_seen_date=first_seen_date,
        as_of_trade_date=as_of_trade_date,
        cache=tracking_day_cache,
    )
    baseline_trade_date, baseline_price = _resolve_baseline_for_stock(
        db, stock_id=stock_id, first_seen_date=first_seen_date
    )
    latest_eval_trade_date, latest_eval_price, return_pct = _resolve_return_for_stock(
        db,
        stock_id=stock_id,
        as_of_trade_date=as_of_trade_date,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
    )
    return ArchiveSummaryItem(
        stock_id=stock_id,
        stock_name=latest_row.stock_name,
        industry_name=latest_row.industry_name,
        sub_industry=latest_row.sub_industry,
        first_seen_date=first_seen_date,
        latest_hit_date=latest_hit_date,
        tracking_day_index=tracking_day_index,
        hit_count=hit_count,
        latest_signal_type=latest_row.signal_type,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
        latest_eval_trade_date=latest_eval_trade_date,
        latest_eval_price=latest_eval_price,
        return_pct=return_pct,
    )


def _count_tracking_days(
    db: Session,
    *,
    first_seen_date: date,
    as_of_trade_date: Optional[date],
    cache: dict[date, int],
) -> int:
    if as_of_trade_date is None or as_of_trade_date < first_seen_date:
        return 1
    if first_seen_date in cache:
        return cache[first_seen_date]
    count = (
        db.query(func.count(func.distinct(DailyPrice.trade_date)))
        .filter(
            DailyPrice.trade_date >= first_seen_date,
            DailyPrice.trade_date <= as_of_trade_date,
        )
        .scalar()
        or 0
    )
    cache[first_seen_date] = max(int(count), 1)
    return cache[first_seen_date]


def _resolve_baseline_for_stock(
    db: Session,
    *,
    stock_id: str,
    first_seen_date: date,
) -> tuple[Optional[date], Optional[float]]:
    row = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date > first_seen_date,
            DailyPrice.open_price.isnot(None),
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.asc())
        .first()
    )
    if row is None or row.open_price is None or row.close_price is None:
        return None, None
    baseline_price = (float(row.open_price) + float(row.close_price)) / 2.0
    return row.trade_date, baseline_price


def _resolve_return_for_stock(
    db: Session,
    *,
    stock_id: str,
    as_of_trade_date: Optional[date],
    baseline_trade_date: Optional[date],
    baseline_price: Optional[float],
) -> tuple[Optional[date], Optional[float], Optional[float]]:
    if (
        as_of_trade_date is None
        or baseline_trade_date is None
        or baseline_price in (None, 0)
        or as_of_trade_date < baseline_trade_date
    ):
        return None, None, None

    if as_of_trade_date == baseline_trade_date:
        return baseline_trade_date, baseline_price, 0.0

    eval_price = (
        db.query(DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date == as_of_trade_date,
            DailyPrice.close_price.isnot(None),
        )
        .scalar()
    )
    if eval_price is None:
        return as_of_trade_date, None, None
    eval_price = float(eval_price)
    return_pct = (eval_price - baseline_price) / baseline_price * 100.0
    return as_of_trade_date, eval_price, return_pct


def _serialize_summary_item(item: ArchiveSummaryItem) -> dict[str, Any]:
    return {
        "stock_id": item.stock_id,
        "stock_name": item.stock_name,
        "industry_name": item.industry_name,
        "sub_industry": item.sub_industry,
        "first_seen_date": item.first_seen_date,
        "latest_hit_date": item.latest_hit_date,
        "tracking_day_index": item.tracking_day_index,
        "hit_count": item.hit_count,
        "latest_signal_type": item.latest_signal_type,
        "baseline_trade_date": item.baseline_trade_date,
        "baseline_price": item.baseline_price,
        "latest_eval_trade_date": item.latest_eval_trade_date,
        "latest_eval_price": item.latest_eval_price,
        "return_pct": item.return_pct,
    }


def _summary_sort_key(item: ArchiveSummaryItem, sort_by: str) -> tuple:
    normalized = (sort_by or "tracking_days_desc").lower()
    if normalized == "return_desc":
        return (_null_last_desc(item.return_pct), -item.hit_count, item.stock_id)
    if normalized == "return_asc":
        return (_null_last_asc(item.return_pct), -item.hit_count, item.stock_id)
    if normalized == "hit_count_desc":
        return (-item.hit_count, -item.tracking_day_index, item.stock_id)
    if normalized == "latest_hit_desc":
        return (-item.latest_hit_date.toordinal(), -item.hit_count, item.stock_id)
    if normalized == "stock_id_asc":
        return (item.stock_id,)
    return (-item.tracking_day_index, -item.hit_count, item.stock_id)


def _null_last_desc(value: Optional[float]) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, -value)


def _null_last_asc(value: Optional[float]) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, value)
