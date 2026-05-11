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

# 提前結算規則：return_pct 首次跌破 -30% 後，再給 EARLY_EXIT_GRACE_TRADE_DAYS 個交易日的
# 反彈寬限期；若寬限期內任一天 return_pct >= EARLY_EXIT_THRESHOLD_PCT（漲回到 >= -30%）
# 就視為止跌、警示解除；若三天都仍 < -30% 則提前結算到永久紀錄。
EARLY_EXIT_THRESHOLD_PCT = -30.0
EARLY_EXIT_GRACE_TRADE_DAYS = 3
PEAK_MILESTONE_PCT = 45.0
CLOSURE_REASON_COMPLETED_40_DAYS = "completed_40_days"
CLOSURE_REASON_EARLY_EXIT_STOP_LOSS = "early_exit_stop_loss"


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
    max_positive_return_pct: Optional[float]
    max_positive_return_trade_date: Optional[date]
    max_negative_return_pct: Optional[float]
    max_negative_return_trade_date: Optional[date]


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
    max_positive_return_pct: Optional[float]
    max_positive_return_trade_date: Optional[date]
    max_negative_return_pct: Optional[float]
    max_negative_return_trade_date: Optional[date]
    completed_trade_date: date
    closure_reason: str = CLOSURE_REASON_COMPLETED_40_DAYS


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
                max_positive_return_pct=prior.get("max_positive_return_pct"),
                max_positive_return_trade_date=prior.get("max_positive_return_trade_date"),
                max_negative_return_pct=prior.get("max_negative_return_pct"),
                max_negative_return_trade_date=prior.get("max_negative_return_trade_date"),
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
                    max_positive_return_pct=row.max_positive_return_pct,
                    max_positive_return_trade_date=row.max_positive_return_trade_date,
                    max_negative_return_pct=row.max_negative_return_pct,
                    max_negative_return_trade_date=row.max_negative_return_trade_date,
                    completed_trade_date=row.completed_trade_date,
                    closure_reason=(
                        row.closure_reason or CLOSURE_REASON_COMPLETED_40_DAYS
                    ),
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
        # 走 40 天滿期路徑，顯式覆寫 closure_reason（避免被舊 early-exit 殘留錯標）。
        completed_item.closure_reason = CLOSURE_REASON_COMPLETED_40_DAYS
        _upsert_completed_archive(db, completed_item)
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
            "max_positive_return_pct": row.max_positive_return_pct,
            "max_positive_return_trade_date": row.max_positive_return_trade_date,
            "max_negative_return_pct": row.max_negative_return_pct,
            "max_negative_return_trade_date": row.max_negative_return_trade_date,
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
    (
        max_positive_return_pct,
        max_positive_return_trade_date,
        max_negative_return_pct,
        max_negative_return_trade_date,
    ) = _resolve_return_extrema(
        db,
        stock_id=stock_id,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
        through_trade_date=completed_trade_date,
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
        max_positive_return_pct=max_positive_return_pct,
        max_positive_return_trade_date=max_positive_return_trade_date,
        max_negative_return_pct=max_negative_return_pct,
        max_negative_return_trade_date=max_negative_return_trade_date,
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
        max_positive_return_pct=latest_row.max_positive_return_pct,
        max_positive_return_trade_date=latest_row.max_positive_return_trade_date,
        max_negative_return_pct=latest_row.max_negative_return_pct,
        max_negative_return_trade_date=latest_row.max_negative_return_trade_date,
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


def _resolve_return_extrema(
    db: Session,
    *,
    stock_id: str,
    baseline_trade_date: Optional[date],
    baseline_price: Optional[float],
    through_trade_date: Optional[date],
) -> tuple[Optional[float], Optional[date], Optional[float], Optional[date]]:
    if baseline_trade_date is None or baseline_price in (None, 0) or through_trade_date is None:
        return (None, None, None, None)
    if through_trade_date <= baseline_trade_date:
        return (None, None, None, None)

    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date > baseline_trade_date,
            DailyPrice.trade_date <= through_trade_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.asc())
        .all()
    )
    if not rows:
        return (None, None, None, None)

    max_positive_return_pct: Optional[float] = None
    max_positive_return_trade_date: Optional[date] = None
    max_negative_return_pct: Optional[float] = None
    max_negative_return_trade_date: Optional[date] = None
    baseline = float(baseline_price)

    for row in rows:
        pct = ((float(row.close_price) - baseline) / baseline) * 100.0
        if pct > 0 and (max_positive_return_pct is None or pct > max_positive_return_pct):
            max_positive_return_pct = pct
            max_positive_return_trade_date = row.trade_date
        if pct < 0 and (max_negative_return_pct is None or pct < max_negative_return_pct):
            max_negative_return_pct = pct
            max_negative_return_trade_date = row.trade_date

    return (
        max_positive_return_pct,
        max_positive_return_trade_date,
        max_negative_return_pct,
        max_negative_return_trade_date,
    )


def _post_baseline_returns(
    db: Session,
    *,
    stock_id: str,
    baseline_trade_date: date,
    baseline_price: float,
    through_trade_date: date,
) -> List[tuple[date, float]]:
    """Trading-day-ordered (date, return_pct) list for days strictly after baseline."""
    if through_trade_date <= baseline_trade_date:
        return []
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date > baseline_trade_date,
            DailyPrice.trade_date <= through_trade_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.asc())
        .all()
    )
    base = float(baseline_price)
    if base == 0:
        return []
    return [
        (row.trade_date, (float(row.close_price) - base) / base * 100.0)
        for row in rows
    ]


def _resolve_early_exit_settle_date(
    returns: List[tuple[date, float]],
) -> Optional[date]:
    """
    決定是否觸發提前結算並回傳結算交易日。

    規則：
    - 取 baseline 之後的所有 (trade_date, return_pct) 升序。
    - 找最後一次 return_pct >= EARLY_EXIT_THRESHOLD_PCT 的索引；之後第一天就是「觸發日 D」
      （若一路都 < threshold，則第一天就是 D）。
    - 觸發日 D 後再給 EARLY_EXIT_GRACE_TRADE_DAYS 個交易日（D+1, D+2, …）做反彈寬限。
      若這 N 天全部仍 < threshold，於最後一個寬限日結算。
    - 若寬限期未過完，回 None；不結算。
    """
    if not returns:
        return None

    last_above_idx = -1
    for idx, (_, pct) in enumerate(returns):
        if pct >= EARLY_EXIT_THRESHOLD_PCT:
            last_above_idx = idx

    trigger_idx = last_above_idx + 1
    if trigger_idx >= len(returns):
        # 從未跌破 threshold
        return None

    grace_end_idx = trigger_idx + EARLY_EXIT_GRACE_TRADE_DAYS
    if grace_end_idx >= len(returns):
        # 觸發後寬限期尚未跑完整
        return None

    return returns[grace_end_idx][0]


def _build_early_exit_archive_item(
    db: Session,
    *,
    stock_id: str,
    rows: list[SignalWatchHit],
    baseline_trade_date: date,
    baseline_price: float,
    settle_trade_date: date,
    trade_date_cache: dict[tuple[date, int], Optional[date]],
    price_cache: dict[tuple[str, date], Optional[DailyPrice]],
) -> CompletedArchiveItem:
    """Build a CompletedArchiveItem for an early-exit closure.

    Day-N return columns are populated only when the relevant trading day已落在 settle 之前；
    否則仍寫 None（避免用未來資料寫入）。
    """
    first_row = rows[0]
    latest_row = rows[-1]
    first_seen_date = first_row.snapshot_date

    (
        max_positive_return_pct,
        max_positive_return_trade_date,
        max_negative_return_pct,
        max_negative_return_trade_date,
    ) = _resolve_return_extrema(
        db,
        stock_id=stock_id,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
        through_trade_date=settle_trade_date,
    )

    def _day_n_return(tracking_day: int) -> Optional[float]:
        nth = _resolve_nth_trade_date(
            db,
            first_seen_date=first_seen_date,
            day_index=tracking_day,
            cache=trade_date_cache,
        )
        if nth is None or nth > settle_trade_date:
            return None
        return _resolve_return_for_tracking_day(
            db,
            stock_id=stock_id,
            first_seen_date=first_seen_date,
            tracking_day=tracking_day,
            baseline_price=baseline_price,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
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
        return_day_10_pct=_day_n_return(10),
        return_day_20_pct=_day_n_return(20),
        return_day_30_pct=_day_n_return(30),
        return_day_40_pct=_day_n_return(40),
        max_positive_return_pct=max_positive_return_pct,
        max_positive_return_trade_date=max_positive_return_trade_date,
        max_negative_return_pct=max_negative_return_pct,
        max_negative_return_trade_date=max_negative_return_trade_date,
        completed_trade_date=settle_trade_date,
        closure_reason=CLOSURE_REASON_EARLY_EXIT_STOP_LOSS,
    )


def _upsert_completed_archive(
    db: Session,
    item: CompletedArchiveItem,
) -> None:
    existing = (
        db.query(SignalWatchCompletedArchive)
        .filter(
            SignalWatchCompletedArchive.stock_id == item.stock_id,
            SignalWatchCompletedArchive.first_seen_date == item.first_seen_date,
        )
        .one_or_none()
    )
    if existing is None:
        db.add(
            SignalWatchCompletedArchive(
                stock_id=item.stock_id,
                stock_name=item.stock_name,
                industry_name=item.industry_name,
                sub_industry=item.sub_industry,
                first_seen_date=item.first_seen_date,
                latest_hit_date=item.latest_hit_date,
                hit_count=item.hit_count,
                latest_signal_type=item.latest_signal_type,
                baseline_trade_date=item.baseline_trade_date,
                baseline_price=item.baseline_price,
                return_day_10_pct=item.return_day_10_pct,
                return_day_20_pct=item.return_day_20_pct,
                return_day_30_pct=item.return_day_30_pct,
                return_day_40_pct=item.return_day_40_pct,
                max_positive_return_pct=item.max_positive_return_pct,
                max_positive_return_trade_date=item.max_positive_return_trade_date,
                max_negative_return_pct=item.max_negative_return_pct,
                max_negative_return_trade_date=item.max_negative_return_trade_date,
                completed_trade_date=item.completed_trade_date,
                closure_reason=item.closure_reason,
            )
        )
    else:
        existing.stock_name = item.stock_name
        existing.industry_name = item.industry_name
        existing.sub_industry = item.sub_industry
        existing.latest_hit_date = item.latest_hit_date
        existing.hit_count = item.hit_count
        existing.latest_signal_type = item.latest_signal_type
        existing.baseline_trade_date = item.baseline_trade_date
        existing.baseline_price = item.baseline_price
        existing.return_day_10_pct = item.return_day_10_pct
        existing.return_day_20_pct = item.return_day_20_pct
        existing.return_day_30_pct = item.return_day_30_pct
        existing.return_day_40_pct = item.return_day_40_pct
        existing.max_positive_return_pct = item.max_positive_return_pct
        existing.max_positive_return_trade_date = item.max_positive_return_trade_date
        existing.max_negative_return_pct = item.max_negative_return_pct
        existing.max_negative_return_trade_date = item.max_negative_return_trade_date
        existing.completed_trade_date = item.completed_trade_date
        existing.closure_reason = item.closure_reason
        existing.updated_at = datetime.utcnow()


def update_signal_watch_returns(
    db: Session,
    *,
    as_of_trade_date: Optional[date] = None,
) -> int:
    """Update persisted archive returns for all tracked rows of each stock cycle."""
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
    early_exits: List[tuple[str, list[SignalWatchHit], date, float, date]] = []
    trade_date_cache: dict[tuple[date, int], Optional[date]] = {}
    price_cache: dict[tuple[str, date], Optional[DailyPrice]] = {}

    for stock_id, rows in grouped.items():
        latest_row = rows[-1]
        first_seen_date = rows[0].snapshot_date
        baseline_row = next((row for row in reversed(rows) if row.baseline_price not in (None, 0)), None)
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
        if baseline_row is not None:
            baseline_trade_date = baseline_row.baseline_trade_date
            baseline_price = float(baseline_row.baseline_price)
            if baseline_trade_date == trade_date:
                latest_eval_price = baseline_price
                return_pct = 0.0
            else:
                latest_eval_price = close_price
                return_pct = (
                    (close_price - baseline_price)
                    / baseline_price
                    * 100.0
                )
            (
                max_positive_return_pct,
                max_positive_return_trade_date,
                max_negative_return_pct,
                max_negative_return_trade_date,
            ) = _resolve_return_extrema(
                db,
                stock_id=stock_id,
                baseline_trade_date=baseline_trade_date,
                baseline_price=baseline_price,
                through_trade_date=trade_date,
            )
            for row in rows:
                row.baseline_trade_date = baseline_trade_date
                row.baseline_price = baseline_price
                row.latest_eval_trade_date = trade_date
                row.latest_eval_price = latest_eval_price
                row.return_pct = return_pct
                row.max_positive_return_pct = max_positive_return_pct
                row.max_positive_return_trade_date = max_positive_return_trade_date
                row.max_negative_return_pct = max_negative_return_pct
                row.max_negative_return_trade_date = max_negative_return_trade_date
            updated += 1

            # 提前結算檢查：首次跌破 -30% 後再給 3 個交易日反彈寬限，若都未漲回則結算。
            post_baseline = _post_baseline_returns(
                db,
                stock_id=stock_id,
                baseline_trade_date=baseline_trade_date,
                baseline_price=baseline_price,
                through_trade_date=trade_date,
            )
            settle_date = _resolve_early_exit_settle_date(post_baseline)
            if settle_date is not None:
                early_exits.append(
                    (
                        stock_id,
                        rows,
                        baseline_trade_date,
                        baseline_price,
                        settle_date,
                    )
                )
            continue

        if first_seen_date < trade_date:
            baseline_price = (float(price_row.open_price) + close_price) / 2.0
            for row in rows:
                row.baseline_trade_date = trade_date
                row.baseline_price = baseline_price
                row.latest_eval_trade_date = trade_date
                row.latest_eval_price = baseline_price
                row.return_pct = 0.0
                row.max_positive_return_pct = None
                row.max_positive_return_trade_date = None
                row.max_negative_return_pct = None
                row.max_negative_return_trade_date = None
            updated += 1

    # 將所有 early-exit cycle 寫入永久紀錄並清掉 active rows。
    for (
        stock_id,
        rows,
        baseline_trade_date,
        baseline_price,
        settle_date,
    ) in early_exits:
        item = _build_early_exit_archive_item(
            db,
            stock_id=stock_id,
            rows=rows,
            baseline_trade_date=baseline_trade_date,
            baseline_price=baseline_price,
            settle_trade_date=settle_date,
            trade_date_cache=trade_date_cache,
            price_cache=price_cache,
        )
        _upsert_completed_archive(db, item)
        db.query(SignalWatchHit).filter(
            SignalWatchHit.stock_id == stock_id
        ).delete(synchronize_session=False)

    completed_upserts = refresh_completed_signal_cycles(db, as_of_trade_date=trade_date)
    if updated or completed_upserts or early_exits:
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
        "max_positive_return_pct": item.max_positive_return_pct,
        "max_positive_return_trade_date": item.max_positive_return_trade_date,
        "max_negative_return_pct": item.max_negative_return_pct,
        "max_negative_return_trade_date": item.max_negative_return_trade_date,
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
        "max_positive_return_pct": item.max_positive_return_pct,
        "max_positive_return_trade_date": item.max_positive_return_trade_date,
        "max_negative_return_pct": item.max_negative_return_pct,
        "max_negative_return_trade_date": item.max_negative_return_trade_date,
        "completed_trade_date": item.completed_trade_date,
        "closure_reason": item.closure_reason,
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
