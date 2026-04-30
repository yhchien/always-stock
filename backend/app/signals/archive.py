"""Read/write helpers for the M23 40-trading-day signal archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DailyPrice,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
)

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


@dataclass
class CompletedArchiveItem:
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    sub_industry: Optional[str]
    first_seen_date: date
    latest_hit_date: date
    hit_count: int
    latest_signal_type: str
    baseline_trade_date: Optional[date]
    baseline_price: Optional[float]
    return_day_10_pct: Optional[float]
    return_day_20_pct: Optional[float]
    return_day_30_pct: Optional[float]
    return_day_40_pct: Optional[float]
    completed_trade_date: date


def persist_signal_watch_hits(
    db: Session,
    target_date: date,
    payload: Dict[str, Any],
    job_id: str,
) -> None:
    """Replace a snapshot day's archive hits with the latest watchlist, then prune retention."""
    carried_state = _load_latest_return_state_by_stock(db, before_date=target_date)
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
        stock_id = str(item.get("stock") or "")
        prior = carried_state.get(stock_id) or {}
        db.add(
            SignalWatchHit(
                snapshot_date=target_date,
                stock_id=stock_id,
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
                baseline_trade_date=prior.get("baseline_trade_date"),
                baseline_price=prior.get("baseline_price"),
                latest_eval_trade_date=prior.get("latest_eval_trade_date"),
                latest_eval_price=prior.get("latest_eval_price"),
                return_pct=prior.get("return_pct"),
                snapshot_generated_at=generated_at,
                job_id=job_id,
            )
        )

    refresh_completed_signal_cycles(
        db,
        as_of_trade_date=target_date,
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


def list_completed_archive_summary(
    db: Session,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    query = db.query(SignalWatchCompletedArchive).order_by(
        SignalWatchCompletedArchive.completed_trade_date.desc(),
        SignalWatchCompletedArchive.first_seen_date.desc(),
        SignalWatchCompletedArchive.stock_id.asc(),
    )
    if limit > 0:
        query = query.limit(limit)
    rows = query.all()
    return {
        "items": [
            _serialize_completed_archive_item(
                CompletedArchiveItem(
                    stock_id=row.stock_id,
                    stock_name=row.stock_name,
                    industry_name=row.industry_name,
                    sub_industry=row.sub_industry,
                    first_seen_date=row.first_seen_date,
                    latest_hit_date=row.latest_hit_date,
                    hit_count=row.hit_count,
                    latest_signal_type=row.latest_signal_type,
                    baseline_trade_date=row.baseline_trade_date,
                    baseline_price=row.baseline_price,
                    return_day_10_pct=row.return_day_10_pct,
                    return_day_20_pct=row.return_day_20_pct,
                    return_day_30_pct=row.return_day_30_pct,
                    return_day_40_pct=row.return_day_40_pct,
                    completed_trade_date=row.completed_trade_date,
                )
            )
            for row in rows
        ]
    }


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


def refresh_completed_signal_cycles(
    db: Session,
    *,
    as_of_trade_date: Optional[date] = None,
) -> int:
    trade_date = as_of_trade_date or resolve_archive_as_of_trade_date(
        db,
        now=datetime.now(TAIPEI_TZ),
    )
    if trade_date is None:
        return 0

    grouped = _load_grouped_hits(db)
    if not grouped:
        return 0

    tracking_day_cache: dict[date, int] = {}
    trade_date_cache: dict[tuple[date, int], Optional[date]] = {}
    price_cache: dict[tuple[str, date], Optional[DailyPrice]] = {}
    upserted = 0

    for stock_id, rows in grouped.items():
        first_seen_date = rows[0].snapshot_date
        tracking_day_index = _count_tracking_days(
            db,
            first_seen_date=first_seen_date,
            as_of_trade_date=trade_date,
            cache=tracking_day_cache,
        )
        if tracking_day_index < ARCHIVE_RETENTION_TRADE_DAYS:
            continue

        completed_item = _build_completed_archive_item(
            db,
            stock_id=stock_id,
            rows=rows,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        )
        existing = (
            db.query(SignalWatchCompletedArchive)
            .filter(
                SignalWatchCompletedArchive.stock_id == stock_id,
                SignalWatchCompletedArchive.first_seen_date == first_seen_date,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(
                SignalWatchCompletedArchive(
                    stock_id=completed_item.stock_id,
                    stock_name=completed_item.stock_name,
                    industry_name=completed_item.industry_name,
                    sub_industry=completed_item.sub_industry,
                    first_seen_date=completed_item.first_seen_date,
                    latest_hit_date=completed_item.latest_hit_date,
                    hit_count=completed_item.hit_count,
                    latest_signal_type=completed_item.latest_signal_type,
                    baseline_trade_date=completed_item.baseline_trade_date,
                    baseline_price=completed_item.baseline_price,
                    return_day_10_pct=completed_item.return_day_10_pct,
                    return_day_20_pct=completed_item.return_day_20_pct,
                    return_day_30_pct=completed_item.return_day_30_pct,
                    return_day_40_pct=completed_item.return_day_40_pct,
                    completed_trade_date=completed_item.completed_trade_date,
                )
            )
        else:
            existing.stock_name = completed_item.stock_name
            existing.industry_name = completed_item.industry_name
            existing.sub_industry = completed_item.sub_industry
            existing.latest_hit_date = completed_item.latest_hit_date
            existing.hit_count = completed_item.hit_count
            existing.latest_signal_type = completed_item.latest_signal_type
            existing.baseline_trade_date = completed_item.baseline_trade_date
            existing.baseline_price = completed_item.baseline_price
            existing.return_day_10_pct = completed_item.return_day_10_pct
            existing.return_day_20_pct = completed_item.return_day_20_pct
            existing.return_day_30_pct = completed_item.return_day_30_pct
            existing.return_day_40_pct = completed_item.return_day_40_pct
            existing.completed_trade_date = completed_item.completed_trade_date
            existing.updated_at = datetime.utcnow()
        upserted += 1

    return upserted


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


def _load_latest_return_state_by_stock(
    db: Session,
    *,
    before_date: Optional[date] = None,
) -> dict[str, dict[str, Optional[float] | Optional[date]]]:
    query = db.query(SignalWatchHit)
    if before_date is not None:
        query = query.filter(SignalWatchHit.snapshot_date < before_date)
    rows = query.order_by(SignalWatchHit.stock_id, SignalWatchHit.snapshot_date.desc()).all()

    state_by_stock: dict[str, dict[str, Optional[float] | Optional[date]]] = {}
    for row in rows:
        if row.stock_id in state_by_stock:
            continue
        state_by_stock[row.stock_id] = {
            "baseline_trade_date": row.baseline_trade_date,
            "baseline_price": row.baseline_price,
            "latest_eval_trade_date": row.latest_eval_trade_date,
            "latest_eval_price": row.latest_eval_price,
            "return_pct": row.return_pct,
        }
    return state_by_stock


def _build_completed_archive_item(
    db: Session,
    *,
    stock_id: str,
    rows: list[SignalWatchHit],
    trade_date_cache: dict[tuple[date, int], Optional[date]],
    price_cache: dict[tuple[str, date], Optional[DailyPrice]],
) -> CompletedArchiveItem:
    first_row = rows[0]
    latest_row = rows[-1]
    first_seen_date = first_row.snapshot_date
    completed_trade_date = _resolve_nth_trade_date(
        db,
        first_seen_date=first_seen_date,
        day_index=ARCHIVE_RETENTION_TRADE_DAYS,
        cache=trade_date_cache,
    ) or latest_row.snapshot_date
    baseline_trade_date = _resolve_nth_trade_date(
        db,
        first_seen_date=first_seen_date,
        day_index=2,
        cache=trade_date_cache,
    )
    baseline_price = _resolve_baseline_price(
        db,
        stock_id=stock_id,
        baseline_trade_date=baseline_trade_date,
        cache=price_cache,
    )
    return CompletedArchiveItem(
        stock_id=stock_id,
        stock_name=latest_row.stock_name,
        industry_name=latest_row.industry_name,
        sub_industry=latest_row.sub_industry,
        first_seen_date=first_seen_date,
        latest_hit_date=latest_row.snapshot_date,
        hit_count=len(rows),
        latest_signal_type=latest_row.signal_type,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
        return_day_10_pct=_resolve_return_for_tracking_day(
            db,
            stock_id=stock_id,
            first_seen_date=first_seen_date,
            tracking_day=10,
            baseline_price=baseline_price,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        ),
        return_day_20_pct=_resolve_return_for_tracking_day(
            db,
            stock_id=stock_id,
            first_seen_date=first_seen_date,
            tracking_day=20,
            baseline_price=baseline_price,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        ),
        return_day_30_pct=_resolve_return_for_tracking_day(
            db,
            stock_id=stock_id,
            first_seen_date=first_seen_date,
            tracking_day=30,
            baseline_price=baseline_price,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        ),
        return_day_40_pct=_resolve_return_for_tracking_day(
            db,
            stock_id=stock_id,
            first_seen_date=first_seen_date,
            tracking_day=40,
            baseline_price=baseline_price,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        ),
        completed_trade_date=completed_trade_date,
    )


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
        baseline_trade_date=latest_row.baseline_trade_date,
        baseline_price=latest_row.baseline_price,
        latest_eval_trade_date=latest_row.latest_eval_trade_date,
        latest_eval_price=latest_row.latest_eval_price,
        return_pct=latest_row.return_pct,
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


def _resolve_nth_trade_date(
    db: Session,
    *,
    first_seen_date: date,
    day_index: int,
    cache: dict[tuple[date, int], Optional[date]],
) -> Optional[date]:
    key = (first_seen_date, day_index)
    if key in cache:
        return cache[key]
    rows = (
        db.query(DailyPrice.trade_date)
        .filter(DailyPrice.trade_date >= first_seen_date)
        .distinct()
        .order_by(DailyPrice.trade_date.asc())
        .limit(day_index)
        .all()
    )
    trade_date = rows[-1][0] if len(rows) >= day_index else None
    cache[key] = trade_date
    return trade_date


def _resolve_price_row(
    db: Session,
    *,
    stock_id: str,
    trade_date: Optional[date],
    cache: dict[tuple[str, date], Optional[DailyPrice]],
) -> Optional[DailyPrice]:
    if trade_date is None:
        return None
    key = (stock_id, trade_date)
    if key in cache:
        return cache[key]
    row = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date == trade_date,
        )
        .first()
    )
    cache[key] = row
    return row


def _resolve_baseline_price(
    db: Session,
    *,
    stock_id: str,
    baseline_trade_date: Optional[date],
    cache: dict[tuple[str, date], Optional[DailyPrice]],
) -> Optional[float]:
    row = _resolve_price_row(
        db,
        stock_id=stock_id,
        trade_date=baseline_trade_date,
        cache=cache,
    )
    if row is None or row.open_price is None or row.close_price is None:
        return None
    return (float(row.open_price) + float(row.close_price)) / 2.0


def _resolve_return_for_tracking_day(
    db: Session,
    *,
    stock_id: str,
    first_seen_date: date,
    tracking_day: int,
    baseline_price: Optional[float],
    trade_date_cache: dict[tuple[date, int], Optional[date]],
    price_cache: dict[tuple[str, date], Optional[DailyPrice]],
) -> Optional[float]:
    if baseline_price in (None, 0):
        return None
    trade_date = _resolve_nth_trade_date(
        db,
        first_seen_date=first_seen_date,
        day_index=tracking_day,
        cache=trade_date_cache,
    )
    row = _resolve_price_row(
        db,
        stock_id=stock_id,
        trade_date=trade_date,
        cache=price_cache,
    )
    if row is None or row.close_price is None:
        return None
    return (float(row.close_price) - float(baseline_price)) / float(baseline_price) * 100.0


def update_signal_watch_returns(
    db: Session,
    *,
    as_of_trade_date: Optional[date] = None,
) -> int:
    """Update persisted archive returns for the latest tracked row of each stock."""
    trade_date = as_of_trade_date or resolve_archive_as_of_trade_date(
        db,
        now=datetime.now(TAIPEI_TZ),
    )
    if trade_date is None:
        return 0

    grouped = _load_grouped_hits(db)
    if not grouped:
        return 0

    updated = 0
    for stock_id, rows in grouped.items():
        latest_row = rows[-1]
        first_seen_date = rows[0].snapshot_date
        price_row = (
            db.query(DailyPrice)
            .filter(
                DailyPrice.stock_id == stock_id,
                DailyPrice.trade_date == trade_date,
                DailyPrice.open_price.isnot(None),
                DailyPrice.close_price.isnot(None),
            )
            .first()
        )
        if price_row is None:
            continue

        close_price = float(price_row.close_price)
        if latest_row.baseline_price not in (None, 0) and latest_row.return_pct is not None:
            latest_row.latest_eval_trade_date = trade_date
            latest_row.latest_eval_price = close_price
            latest_row.return_pct = (
                (close_price - float(latest_row.baseline_price))
                / float(latest_row.baseline_price)
                * 100.0
            )
            updated += 1
            continue

        if first_seen_date < trade_date:
            baseline_price = (float(price_row.open_price) + close_price) / 2.0
            latest_row.baseline_trade_date = trade_date
            latest_row.baseline_price = baseline_price
            latest_row.latest_eval_trade_date = trade_date
            latest_row.latest_eval_price = baseline_price
            latest_row.return_pct = 0.0
            updated += 1

    completed_upserts = refresh_completed_signal_cycles(db, as_of_trade_date=trade_date)
    if updated or completed_upserts:
        db.commit()
    return updated


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


def _serialize_completed_archive_item(item: CompletedArchiveItem) -> dict[str, Any]:
    return {
        "stock_id": item.stock_id,
        "stock_name": item.stock_name,
        "industry_name": item.industry_name,
        "sub_industry": item.sub_industry,
        "first_seen_date": item.first_seen_date,
        "latest_hit_date": item.latest_hit_date,
        "hit_count": item.hit_count,
        "latest_signal_type": item.latest_signal_type,
        "baseline_trade_date": item.baseline_trade_date,
        "baseline_price": item.baseline_price,
        "return_day_10_pct": item.return_day_10_pct,
        "return_day_20_pct": item.return_day_20_pct,
        "return_day_30_pct": item.return_day_30_pct,
        "return_day_40_pct": item.return_day_40_pct,
        "completed_trade_date": item.completed_trade_date,
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
