"""Read/write helpers for the M23 30-trading-day signal archive.

歷史備註：本模組 2026-04 上線時 retention=40 個交易日，2026-05-21 全面調整為 30
（包含 DB column 與 closure_reason 字面值）。`main.py` lifespan 跑一次 idempotent
migration：DROP COLUMN return_day_40_pct + UPDATE closure_reason 字面值 + ALTER DEFAULT。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DailyPrice,
    SignalExpectationPrice,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
)

ARCHIVE_RETENTION_TRADE_DAYS = 30
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SIGNALS_SAME_DAY_READY_TIME = time(hour=19, minute=0)

# 提前結算規則 1（stop loss）：return_pct 首次跌破 -30% 後，再給 EARLY_EXIT_GRACE_TRADE_DAYS
# 個交易日的反彈寬限期；若寬限期內任一天 return_pct >= EARLY_EXIT_THRESHOLD_PCT（漲回到 >= -30%）
# 就視為止跌、警示解除；若三天都仍 < -30% 則提前結算到永久紀錄。
EARLY_EXIT_THRESHOLD_PCT = -30.0
EARLY_EXIT_GRACE_TRADE_DAYS = 3
PEAK_MILESTONE_PCT = 45.0

# 提前結算規則 2（drawdown from peak，2026-05-18 新增）：當 max_positive_return > 0 且
# current_return < 0 時，計算 drawdown = current_return - max_positive_return；
# 若 drawdown <= -DRAWDOWN_EXIT_THRESHOLD_PCT，從觸發日 D 起算 D+1, D+2, D+3 共 3 個交易日寬限；
# 若寬限期內任一天 drawdown 回到 < threshold（漲回 -30% 以內），警示解除；
# 若 3 天都仍 <= threshold 則 D+3 結算。
#
# 差異：規則 1 看絕對虧損（baseline 之後一路跌 -30%）；規則 2 看從高點回落（漲過再跌下來）。
# 後者更貼近實務停利紀律 — 漲過 +15% 又跌回 -15% (drawdown -30%) 雖然 return 還沒到 -30%，
# 但已是賺錢變賠錢的失控狀態。兩規則並存，取較早觸發者結算。
DRAWDOWN_EXIT_THRESHOLD_PCT = 30.0
DRAWDOWN_EXIT_GRACE_TRADE_DAYS = 3

CLOSURE_REASON_COMPLETED_30_DAYS = "completed_30_days"
CLOSURE_REASON_EARLY_EXIT_STOP_LOSS = "early_exit_stop_loss"
CLOSURE_REASON_EARLY_EXIT_DRAWDOWN = "early_exit_drawdown_from_peak"

# 半年表格起算日（2026-05-01）；之後每 6 個月一段。
HALF_YEAR_PERIOD_ANCHOR = date(2026, 5, 1)
HALF_YEAR_PERIOD_MONTHS = 6


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
    # M26：對應 (stock_id, first_seen_date) 的 SignalExpectationPrice 預測；舊資料無 → None
    conservative_price: Optional[float] = None
    dream_price: Optional[float] = None


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
    max_positive_return_pct: Optional[float]
    max_positive_return_trade_date: Optional[date]
    max_negative_return_pct: Optional[float]
    max_negative_return_trade_date: Optional[date]
    completed_trade_date: date
    closure_reason: str = CLOSURE_REASON_COMPLETED_30_DAYS
    # M26：對應 (stock_id, first_seen_date) 的 SignalExpectationPrice 預測；舊資料無 → None
    conservative_price: Optional[float] = None
    dream_price: Optional[float] = None


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


def _load_expectation_prices_map(
    db: Session,
    keys: Iterable[tuple[str, date]],
) -> dict[tuple[str, date], tuple[Optional[float], Optional[float]]]:
    """批次撈 (stock_id, first_detected_date) → (conservative_price, dream_price)。

    舊 archive row（M26 上線前）不會有對應 expectation 資料 → 該 key 不在回傳 dict 中。
    只取 status='ok' 的 row，failed/null 視同沒有預測（前端顯示「—」）。
    """
    key_list = list(keys)
    if not key_list:
        return {}
    stock_ids = list({sid for sid, _ in key_list})
    first_dates = list({d for _, d in key_list})
    rows = (
        db.query(
            SignalExpectationPrice.stock_id,
            SignalExpectationPrice.first_detected_date,
            SignalExpectationPrice.conservative_price,
            SignalExpectationPrice.dream_price,
        )
        .filter(
            SignalExpectationPrice.stock_id.in_(stock_ids),
            SignalExpectationPrice.first_detected_date.in_(first_dates),
            SignalExpectationPrice.status == "ok",
        )
        .all()
    )
    wanted = set(key_list)
    return {
        (sid, fdate): (cp, dp)
        for sid, fdate, cp, dp in rows
        if (sid, fdate) in wanted
    }


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

    # M26：批次補上 expectation_price 預測
    expectation_map = _load_expectation_prices_map(
        db, [(item.stock_id, item.first_seen_date) for item in ordered]
    )
    for item in ordered:
        prediction = expectation_map.get((item.stock_id, item.first_seen_date))
        if prediction is not None:
            item.conservative_price, item.dream_price = prediction

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
    # M26：補上 expectation_price 預測
    expectation_map = _load_expectation_prices_map(
        db, [(summary.stock_id, summary.first_seen_date)]
    )
    prediction = expectation_map.get((summary.stock_id, summary.first_seen_date))
    if prediction is not None:
        summary.conservative_price, summary.dream_price = prediction
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


def half_year_period_start(d: date) -> date:
    """把任意 date 對齊到所屬半年區間的起始日。

    區間：以 HALF_YEAR_PERIOD_ANCHOR (2026-05-01) 為起點，每 HALF_YEAR_PERIOD_MONTHS (6) 個月一段：
      - 2026-05-01 ~ 2026-10-31
      - 2026-11-01 ~ 2027-04-30
      - 2027-05-01 ~ 2027-10-31
      - …

    輸入早於 anchor → 回 anchor（最早可呈現的區間）。
    """
    if d < HALF_YEAR_PERIOD_ANCHOR:
        return HALF_YEAR_PERIOD_ANCHOR
    months_since_anchor = (d.year - HALF_YEAR_PERIOD_ANCHOR.year) * 12 + (
        d.month - HALF_YEAR_PERIOD_ANCHOR.month
    )
    bucket = months_since_anchor // HALF_YEAR_PERIOD_MONTHS
    total_months = HALF_YEAR_PERIOD_ANCHOR.month + bucket * HALF_YEAR_PERIOD_MONTHS - 1
    year = HALF_YEAR_PERIOD_ANCHOR.year + total_months // 12
    month = total_months % 12 + 1
    return date(year, month, 1)


def half_year_period_end(start: date) -> date:
    """半年區間 end date（含），即下一段 start - 1 日。"""
    total_months = start.month - 1 + HALF_YEAR_PERIOD_MONTHS
    year = start.year + total_months // 12
    month = total_months % 12 + 1
    next_start = date(year, month, 1)
    return next_start - timedelta(days=1)


def list_completed_archive_summary(
    db: Session,
    *,
    limit: int = 200,
    period_start: Optional[date] = None,
) -> dict[str, Any]:
    """回封存表的 items + 半年期間 meta。

    period_start：若提供（必須對齊半年區間起始日），只回該區間 completed_trade_date
                  落在 [start, end] 的 rows；否則回全部（受 limit）。

    periods：永遠回所有「有資料的半年區間」list（依 start 倒序），含每段 count，供前端做 tab。
    """
    # ── periods meta：對全表 group by half-year period ────────────────────────
    all_completed_dates = db.query(SignalWatchCompletedArchive.completed_trade_date).all()
    period_counts: dict[date, int] = {}
    for (cdate,) in all_completed_dates:
        if cdate is None:
            continue
        pstart = half_year_period_start(cdate)
        period_counts[pstart] = period_counts.get(pstart, 0) + 1

    periods = sorted(
        (
            {
                "period_start": p,
                "period_end": half_year_period_end(p),
                "count": count,
            }
            for p, count in period_counts.items()
        ),
        key=lambda x: x["period_start"],
        reverse=True,
    )

    # ── items：依 period_start filter ─────────────────────────────────────────
    query = db.query(SignalWatchCompletedArchive)
    if period_start is not None:
        pend = half_year_period_end(period_start)
        query = query.filter(
            SignalWatchCompletedArchive.completed_trade_date >= period_start,
            SignalWatchCompletedArchive.completed_trade_date <= pend,
        )
    query = query.order_by(
        SignalWatchCompletedArchive.completed_trade_date.desc(),
        SignalWatchCompletedArchive.first_seen_date.desc(),
        SignalWatchCompletedArchive.stock_id.asc(),
    )
    if limit > 0:
        query = query.limit(limit)
    rows = query.all()

    # M26：批次補上 expectation_price 預測
    expectation_map = _load_expectation_prices_map(
        db, [(row.stock_id, row.first_seen_date) for row in rows]
    )

    items: list[CompletedArchiveItem] = []
    for row in rows:
        item = CompletedArchiveItem(
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
            max_positive_return_pct=row.max_positive_return_pct,
            max_positive_return_trade_date=row.max_positive_return_trade_date,
            max_negative_return_pct=row.max_negative_return_pct,
            max_negative_return_trade_date=row.max_negative_return_trade_date,
            completed_trade_date=row.completed_trade_date,
            closure_reason=(
                row.closure_reason or CLOSURE_REASON_COMPLETED_30_DAYS
            ),
        )
        prediction = expectation_map.get((row.stock_id, row.first_seen_date))
        if prediction is not None:
            item.conservative_price, item.dream_price = prediction
        items.append(item)

    return {
        "items": [_serialize_completed_archive_item(item) for item in items],
        "periods": periods,
        "selected_period_start": period_start,
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
        # 走滿期路徑（retention 屆滿），顯式覆寫 closure_reason（避免被舊 early-exit 殘留錯標）。
        completed_item.closure_reason = CLOSURE_REASON_COMPLETED_30_DAYS
        _upsert_completed_archive(db, completed_item)
        # archive 寫入後清掉 active hits，與 early-exit 行為對齊：
        # 該股下次再被魚尾抓到時 first_seen_date = 那天的 snapshot_date，
        # 算「全新 cycle」而非繼續用舊基準。
        # （不清的話 _prune_signal_watch_hits 雖會自然汰換，但邊界 case 下舊 hits
        #  仍可能殘留導致 first_seen_date 指向舊 cycle。）
        db.query(SignalWatchHit).filter(
            SignalWatchHit.stock_id == stock_id
        ).delete(synchronize_session=False)
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


def _resolve_drawdown_exit_settle_date(
    returns: List[tuple[date, float]],
) -> Optional[date]:
    """規則 2（drawdown from peak）：回 settle_date 或 None。

    流程：
    - 沿時間軸 sweep，維護「至今 max_positive_return」
    - 觸發條件：max_positive_return > 0 AND current_return < 0
                AND (max_positive - current) >= DRAWDOWN_EXIT_THRESHOLD_PCT
    - 觸發日 D 之後給 DRAWDOWN_EXIT_GRACE_TRADE_DAYS 個交易日（D+1, D+2, D+3）做寬限
    - 寬限期內任一天 drawdown 回到 < threshold（不論 current 是否仍負）→ 警示解除、繼續 sweep
    - 寬限期 3 天全部仍超標 → 在最後一天 D+3 結算
    - 從未觸發或寬限期未跑完 → None

    Returns 應為升序 (trade_date, return_pct) tuples。
    """
    if not returns:
        return None

    max_positive: float = 0.0
    trigger_idx: Optional[int] = None

    for idx, (_, pct) in enumerate(returns):
        # 更新歷史高點（只計正報酬）
        if pct > max_positive:
            max_positive = pct

        # 觸發條件：max_positive 必須 > 0、當下 < 0、回落 >= threshold
        triggered = (
            max_positive > 0
            and pct < 0
            and (max_positive - pct) >= DRAWDOWN_EXIT_THRESHOLD_PCT
        )

        if trigger_idx is None:
            if triggered:
                trigger_idx = idx
            continue

        # 已在 grace 期內：檢查是否解除警示
        if not triggered:
            # 回到 -30% 以內 → 警示解除，重新等下一次觸發
            trigger_idx = None
            continue

        # 仍觸發中，檢查 grace 是否已跑完（D+1, D+2, D+3）
        days_after_trigger = idx - trigger_idx
        if days_after_trigger >= DRAWDOWN_EXIT_GRACE_TRADE_DAYS:
            return returns[idx][0]

    return None


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
    closure_reason: str = CLOSURE_REASON_EARLY_EXIT_STOP_LOSS,
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
        max_positive_return_pct=max_positive_return_pct,
        max_positive_return_trade_date=max_positive_return_trade_date,
        max_negative_return_pct=max_negative_return_pct,
        max_negative_return_trade_date=max_negative_return_trade_date,
        completed_trade_date=settle_trade_date,
        closure_reason=closure_reason,
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
    # 提前結算 tuple：(stock_id, rows, baseline_trade_date, baseline_price, settle_date, closure_reason)
    early_exits: List[tuple[str, list[SignalWatchHit], date, float, date, str]] = []
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

            # 提前結算檢查（兩規則並存，取較早觸發者）：
            # 規則 1：return_pct <= -30% 3 日寬限（CLOSURE_REASON_EARLY_EXIT_STOP_LOSS）
            # 規則 2：drawdown from peak >= 30% 3 日寬限（CLOSURE_REASON_EARLY_EXIT_DRAWDOWN）
            post_baseline = _post_baseline_returns(
                db,
                stock_id=stock_id,
                baseline_trade_date=baseline_trade_date,
                baseline_price=baseline_price,
                through_trade_date=trade_date,
            )
            stop_loss_date = _resolve_early_exit_settle_date(post_baseline)
            drawdown_date = _resolve_drawdown_exit_settle_date(post_baseline)

            chosen_settle: Optional[date] = None
            chosen_reason = CLOSURE_REASON_EARLY_EXIT_STOP_LOSS
            if stop_loss_date is not None and drawdown_date is not None:
                if drawdown_date <= stop_loss_date:
                    chosen_settle, chosen_reason = drawdown_date, CLOSURE_REASON_EARLY_EXIT_DRAWDOWN
                else:
                    chosen_settle, chosen_reason = stop_loss_date, CLOSURE_REASON_EARLY_EXIT_STOP_LOSS
            elif drawdown_date is not None:
                chosen_settle, chosen_reason = drawdown_date, CLOSURE_REASON_EARLY_EXIT_DRAWDOWN
            elif stop_loss_date is not None:
                chosen_settle, chosen_reason = stop_loss_date, CLOSURE_REASON_EARLY_EXIT_STOP_LOSS

            if chosen_settle is not None:
                early_exits.append(
                    (
                        stock_id,
                        rows,
                        baseline_trade_date,
                        baseline_price,
                        chosen_settle,
                        chosen_reason,
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
        closure_reason,
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
            closure_reason=closure_reason,
        )
        _upsert_completed_archive(db, item)
        # synchronize_session="evaluate"：這些 row 在 1170-1179 已被改寫成 dirty（待 UPDATE）。
        # session 為 autoflush=False，若用 False 不會把它們移出 session，commit flush 時會對
        # 已刪除的 row 發 UPDATE → StaleDataError。evaluate 會以 stock_id == X 在 Python 端
        # 比對並把對應物件移出 session，丟棄那筆注定被刪 row 的無效 UPDATE。
        db.query(SignalWatchHit).filter(
            SignalWatchHit.stock_id == stock_id
        ).delete(synchronize_session="evaluate")

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
        "conservative_price": item.conservative_price,
        "dream_price": item.dream_price,
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
        "max_positive_return_pct": item.max_positive_return_pct,
        "max_positive_return_trade_date": item.max_positive_return_trade_date,
        "max_negative_return_pct": item.max_negative_return_pct,
        "max_negative_return_trade_date": item.max_negative_return_trade_date,
        "completed_trade_date": item.completed_trade_date,
        "closure_reason": item.closure_reason,
        "conservative_price": item.conservative_price,
        "dream_price": item.dream_price,
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
