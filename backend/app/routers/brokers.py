"""
Broker branch trading API.

On-demand fetch: if data for a stock+date is not in DB, fetch from TWSE BSR
and cache. Background backfill for date ranges.
"""
import logging
import threading
import time
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.broker_config import BROKER_LOOKUP, CATEGORY_LABELS, get_categories, normalize
from app.database import DATABASE_URL, SessionLocal, get_db
from app.models import BrokerTrade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["brokers"])

# Rate limit: minimum seconds between TWSE BSR requests
BSR_RATE_LIMIT = 3.5
LOCK_RETRY_DELAY = 0.25
LOCK_RETRY_ATTEMPTS = 3
_IN_FLIGHT_FETCH_LOCK = threading.Lock()
_IN_FLIGHT_FETCHES: set[tuple[str, date]] = set()
_BROKER_WRITE_LOCK = threading.Lock()


# ── Response schemas ─────────────────────────────────────────────────────────

class BrokerTradeItem(BaseModel):
    broker_id: str
    broker_name: str
    display_name: str  # mapped display name (with dash) or original
    buy_shares: float
    sell_shares: float
    net_shares: float
    categories: List[str] = []  # category keys this broker belongs to


class BrokerTradeResponse(BaseModel):
    stock_id: str
    trade_date: date
    category: str
    category_label: str
    is_refreshing: bool = False
    brokers: List[BrokerTradeItem]


class BrokerCategoryInfo(BaseModel):
    category: str
    label: str
    broker_count: int


class BrokerStatusResponse(BaseModel):
    stock_id: str
    requested_start: date
    requested_end: date
    cached_dates: int
    total_dates: int
    categories: List[BrokerCategoryInfo]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_cached_dates(db: Session, stock_id: str, start: date, end: date) -> set[date]:
    """Get dates that already have broker data in DB."""
    rows = (
        db.query(BrokerTrade.trade_date)
        .filter(
            BrokerTrade.stock_id == stock_id,
            BrokerTrade.trade_date >= start,
            BrokerTrade.trade_date <= end,
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _is_sqlite_lock_error(error: OperationalError) -> bool:
    return "database is locked" in str(error).lower()


def _run_with_lock_retry(db: Session, fn):
    last_error = None
    for attempt in range(LOCK_RETRY_ATTEMPTS):
        try:
            return fn()
        except OperationalError as error:
            db.rollback()
            if not _is_sqlite_lock_error(error) or attempt == LOCK_RETRY_ATTEMPTS - 1:
                raise
            last_error = error
            logger.warning(
                "SQLite database is locked, retrying (%d/%d)",
                attempt + 1,
                LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(LOCK_RETRY_DELAY)
    if last_error is not None:
        raise last_error


def _mark_missing_for_backfill(stock_id: str, missing_dates: list[date]) -> list[date]:
    queued: list[date] = []
    with _IN_FLIGHT_FETCH_LOCK:
        for trade_date in missing_dates:
            key = (stock_id, trade_date)
            if key in _IN_FLIGHT_FETCHES:
                continue
            _IN_FLIGHT_FETCHES.add(key)
            queued.append(trade_date)
    return queued


def _trading_dates(start: date, end: date) -> list[date]:
    """Generate weekday dates in range."""
    dates = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _backfill_missing(stock_id: str, missing_dates: list[date]) -> None:
    """Background task: fetch missing dates from TWSE BSR."""
    from etl.fetch_broker_trade import fetch_and_upsert_broker_trade

    db = SessionLocal()
    try:
        for i, d in enumerate(missing_dates):
            try:
                # SQLite only allows one writer at a time; serialize broker backfill writes.
                with _BROKER_WRITE_LOCK:
                    fetch_and_upsert_broker_trade(db, stock_id, d)
            except Exception:
                db.rollback()
                logger.exception("Backfill failed: %s on %s", stock_id, d)
            finally:
                with _IN_FLIGHT_FETCH_LOCK:
                    _IN_FLIGHT_FETCHES.discard((stock_id, d))
            if i < len(missing_dates) - 1:
                time.sleep(BSR_RATE_LIMIT)
    finally:
        db.close()


def _map_display_name(bsr_name: str) -> str:
    """Map BSR broker name back to display format (with dash)."""
    normalized = normalize(bsr_name)
    # Search all category lists for a matching name
    from app.broker_config import BROKER_CATEGORIES
    for brokers in BROKER_CATEGORIES.values():
        for display in brokers:
            if normalize(display) == normalized:
                return display
    return bsr_name


def _is_sqlite_dev_mode() -> bool:
    return DATABASE_URL.startswith("sqlite:///")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/stocks/{stock_id}/brokers", response_model=BrokerTradeResponse)
def get_broker_trades(
    stock_id: str,
    background_tasks: BackgroundTasks,
    category: str = Query(default="day_trade", description="day_trade|next_day|short_term|swing"),
    trade_date: Optional[date] = Query(default=None, alias="date", description="defaults to latest cached"),
    days: int = Query(default=1, ge=1, le=120, description="days to aggregate"),
    db: Session = Depends(get_db),
):
    """
    Get broker trading data for a stock, filtered by category.

    - First call for a stock triggers background fetch from TWSE BSR.
    - Returns whatever data is cached in DB.
    - Aggregates buy/sell across the requested date range.
    """
    logger.info(
        "GET /stocks/%s/brokers category=%s date=%s days=%d",
        stock_id, category, trade_date, days,
    )

    if category not in CATEGORY_LABELS:
        raise HTTPException(400, f"Invalid category: {category}")

    # Determine date range
    if trade_date is None:
        # Use latest date in DB, or today
        latest = (
            db.query(func.max(BrokerTrade.trade_date))
            .filter(BrokerTrade.stock_id == stock_id)
            .scalar()
        )
        trade_date = latest or date.today()

    end_date = trade_date
    start_date = end_date - timedelta(days=days - 1) if days > 1 else end_date

    # Check cached dates and trigger backfill
    all_dates = _trading_dates(start_date, end_date)
    cached = _run_with_lock_retry(db, lambda: _get_cached_dates(db, stock_id, start_date, end_date))
    missing = [d for d in all_dates if d not in cached]

    is_refreshing = False
    live_rows: list[BrokerTradeItem] = []
    if missing:
        if _is_sqlite_dev_mode() and days == 1:
            # In local SQLite mode, avoid background writes entirely; fetch live rows
            # and return them directly so interactive browsing doesn't lock the DB.
            from etl.fetch_broker_trade import fetch_broker_trade_rows

            try:
                raw_rows = fetch_broker_trade_rows(stock_id, missing[0])
                for row in raw_rows:
                    if row["broker_id"] == stock_id or str(row["broker_name"]).startswith("/"):
                        continue
                    live_rows.append(BrokerTradeItem(
                        broker_id=row["broker_id"],
                        broker_name=row["broker_name"],
                        display_name=_map_display_name(row["broker_name"]),
                        buy_shares=row["buy_shares"] or 0,
                        sell_shares=row["sell_shares"] or 0,
                        net_shares=row["net_shares"] or 0,
                    ))
            except Exception:
                logger.exception("Live broker fetch failed: %s on %s", stock_id, missing[0])
        else:
            queued_dates = _mark_missing_for_backfill(stock_id, missing)
            if queued_dates:
                background_tasks.add_task(_backfill_missing, stock_id, queued_dates)
            is_refreshing = True

    # Query aggregated broker data
    try:
        rows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(
                    BrokerTrade.broker_id,
                    BrokerTrade.broker_name,
                    func.sum(BrokerTrade.buy_shares).label("buy"),
                    func.sum(BrokerTrade.sell_shares).label("sell"),
                    func.sum(BrokerTrade.net_shares).label("net"),
                )
                .filter(
                    BrokerTrade.stock_id == stock_id,
                    BrokerTrade.trade_date >= start_date,
                    BrokerTrade.trade_date <= end_date,
                )
                .group_by(BrokerTrade.broker_id, BrokerTrade.broker_name)
                .all()
            )
        )
    except OperationalError as error:
        if _is_sqlite_lock_error(error):
            raise HTTPException(status_code=503, detail="Broker data is temporarily busy, please retry")
        raise

    # Filter by category and attach categories list
    filtered = []
    for r in rows:
        cats = get_categories(r.broker_name)
        if category in cats:
            filtered.append(BrokerTradeItem(
                broker_id=r.broker_id,
                broker_name=r.broker_name,
                display_name=_map_display_name(r.broker_name),
                buy_shares=r.buy or 0,
                sell_shares=r.sell or 0,
                net_shares=r.net or 0,
                categories=sorted(cats),
            ))

    for row in live_rows:
        cats = get_categories(row.broker_name)
        if category in cats:
            filtered.append(BrokerTradeItem(
                broker_id=row.broker_id,
                broker_name=row.broker_name,
                display_name=row.display_name,
                buy_shares=row.buy_shares,
                sell_shares=row.sell_shares,
                net_shares=row.net_shares,
                categories=sorted(cats),
            ))

    # Sort by absolute net_shares descending, take top 10
    filtered.sort(key=lambda x: abs(x.net_shares), reverse=True)

    return BrokerTradeResponse(
        stock_id=stock_id,
        trade_date=trade_date,
        category=category,
        category_label=CATEGORY_LABELS[category],
        is_refreshing=is_refreshing,
        brokers=filtered[:10],
    )


class BrokerRankedResponse(BaseModel):
    stock_id: str
    trade_date: date
    is_refreshing: bool = False
    buy_top: List[BrokerTradeItem]
    sell_top: List[BrokerTradeItem]


@router.get("/stocks/{stock_id}/brokers/ranked", response_model=BrokerRankedResponse)
def get_broker_ranked(
    stock_id: str,
    background_tasks: BackgroundTasks,
    trade_date: Optional[date] = Query(default=None, alias="date"),
    days: int = Query(default=1, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """
    Return top-10 buyers and top-10 sellers (by buy_shares / sell_shares),
    with each broker's category tags attached.

    This endpoint does not filter by category — it returns ALL brokers,
    sorted by buy_shares desc (buy_top) and sell_shares desc (sell_top).
    """
    # Determine date range (same logic as get_broker_trades)
    if trade_date is None:
        latest = (
            db.query(func.max(BrokerTrade.trade_date))
            .filter(BrokerTrade.stock_id == stock_id)
            .scalar()
        )
        trade_date = latest or date.today()

    end_date = trade_date
    start_date = end_date - timedelta(days=days - 1) if days > 1 else end_date

    all_dates = _trading_dates(start_date, end_date)
    cached = _run_with_lock_retry(db, lambda: _get_cached_dates(db, stock_id, start_date, end_date))
    missing = [d for d in all_dates if d not in cached]

    is_refreshing = False
    live_rows: list[BrokerTradeItem] = []
    if missing:
        if _is_sqlite_dev_mode() and days == 1:
            from etl.fetch_broker_trade import fetch_broker_trade_rows
            try:
                raw_rows = fetch_broker_trade_rows(stock_id, missing[0])
                for row in raw_rows:
                    if row["broker_id"] == stock_id or str(row["broker_name"]).startswith("/"):
                        continue
                    cats = get_categories(row["broker_name"])
                    live_rows.append(BrokerTradeItem(
                        broker_id=row["broker_id"],
                        broker_name=row["broker_name"],
                        display_name=_map_display_name(row["broker_name"]),
                        buy_shares=row["buy_shares"] or 0,
                        sell_shares=row["sell_shares"] or 0,
                        net_shares=row["net_shares"] or 0,
                        categories=sorted(cats),
                    ))
            except Exception:
                logger.exception("Live broker fetch failed: %s on %s", stock_id, missing[0])
        else:
            queued_dates = _mark_missing_for_backfill(stock_id, missing)
            if queued_dates:
                background_tasks.add_task(_backfill_missing, stock_id, queued_dates)
            is_refreshing = True

    try:
        rows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(
                    BrokerTrade.broker_id,
                    BrokerTrade.broker_name,
                    func.sum(BrokerTrade.buy_shares).label("buy"),
                    func.sum(BrokerTrade.sell_shares).label("sell"),
                    func.sum(BrokerTrade.net_shares).label("net"),
                )
                .filter(
                    BrokerTrade.stock_id == stock_id,
                    BrokerTrade.trade_date >= start_date,
                    BrokerTrade.trade_date <= end_date,
                )
                .group_by(BrokerTrade.broker_id, BrokerTrade.broker_name)
                .all()
            )
        )
    except OperationalError as error:
        if _is_sqlite_lock_error(error):
            raise HTTPException(status_code=503, detail="Broker data is temporarily busy, please retry")
        raise

    all_items: list[BrokerTradeItem] = []
    for r in rows:
        cats = get_categories(r.broker_name)
        all_items.append(BrokerTradeItem(
            broker_id=r.broker_id,
            broker_name=r.broker_name,
            display_name=_map_display_name(r.broker_name),
            buy_shares=r.buy or 0,
            sell_shares=r.sell or 0,
            net_shares=r.net or 0,
            categories=sorted(cats),
        ))

    all_items.extend(live_rows)

    buy_top = sorted(
        [i for i in all_items if i.net_shares > 0],
        key=lambda x: x.net_shares,
        reverse=True,
    )[:10]
    sell_top = sorted(
        [i for i in all_items if i.net_shares < 0],
        key=lambda x: x.net_shares,
    )[:10]

    return BrokerRankedResponse(
        stock_id=stock_id,
        trade_date=trade_date,
        is_refreshing=is_refreshing,
        buy_top=buy_top,
        sell_top=sell_top,
    )


class BrokerDailyItem(BaseModel):
    trade_date: date
    buy_shares: float
    sell_shares: float
    net_shares: float


class BrokerHistoryResponse(BaseModel):
    stock_id: str
    broker_id: str
    broker_name: str
    display_name: str
    history: List[BrokerDailyItem]


@router.get("/stocks/{stock_id}/brokers/{broker_id}/history", response_model=BrokerHistoryResponse)
def get_broker_history(
    stock_id: str,
    broker_id: str,
    start_date: date = Query(alias="start"),
    end_date: date = Query(alias="end"),
    db: Session = Depends(get_db),
):
    """
    Return daily buy/sell/net for a specific broker on a specific stock,
    within [start_date, end_date]. Used to render the broker bar chart
    overlaid with the K-line time range.
    """
    try:
        rows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(
                    BrokerTrade.trade_date,
                    func.sum(BrokerTrade.buy_shares).label("buy"),
                    func.sum(BrokerTrade.sell_shares).label("sell"),
                    func.sum(BrokerTrade.net_shares).label("net"),
                    BrokerTrade.broker_name,
                )
                .filter(
                    BrokerTrade.stock_id == stock_id,
                    BrokerTrade.broker_id == broker_id,
                    BrokerTrade.trade_date >= start_date,
                    BrokerTrade.trade_date <= end_date,
                )
                .group_by(BrokerTrade.trade_date, BrokerTrade.broker_name)
                .order_by(BrokerTrade.trade_date)
                .all()
            )
        )
    except OperationalError as error:
        if _is_sqlite_lock_error(error):
            raise HTTPException(status_code=503, detail="Broker data is temporarily busy, please retry")
        raise

    if not rows:
        raise HTTPException(status_code=404, detail=f"No broker history for {broker_id} on {stock_id}")

    broker_name_raw = rows[0].broker_name
    history = [
        BrokerDailyItem(
            trade_date=r.trade_date,
            buy_shares=r.buy or 0,
            sell_shares=r.sell or 0,
            net_shares=r.net or 0,
        )
        for r in rows
    ]

    return BrokerHistoryResponse(
        stock_id=stock_id,
        broker_id=broker_id,
        broker_name=broker_name_raw,
        display_name=_map_display_name(broker_name_raw),
        history=history,
    )


@router.get("/stocks/{stock_id}/brokers/status", response_model=BrokerStatusResponse)
def get_broker_status(
    stock_id: str,
    start_date: date = Query(alias="start"),
    end_date: date = Query(alias="end"),
    db: Session = Depends(get_db),
):
    """Check how many dates are cached for a stock's broker data."""
    all_dates = _trading_dates(start_date, end_date)
    cached = _run_with_lock_retry(db, lambda: _get_cached_dates(db, stock_id, start_date, end_date))

    # Count brokers per category in cached data
    cat_counts = []
    if cached:
        rows = (
            db.query(BrokerTrade.broker_name)
            .filter(
                BrokerTrade.stock_id == stock_id,
                BrokerTrade.trade_date >= start_date,
                BrokerTrade.trade_date <= end_date,
            )
            .distinct()
            .all()
        )
        broker_names = [r[0] for r in rows]
        for cat, label in CATEGORY_LABELS.items():
            count = sum(1 for n in broker_names if cat in get_categories(n))
            cat_counts.append(BrokerCategoryInfo(
                category=cat, label=label, broker_count=count,
            ))
    else:
        for cat, label in CATEGORY_LABELS.items():
            cat_counts.append(BrokerCategoryInfo(
                category=cat, label=label, broker_count=0,
            ))

    return BrokerStatusResponse(
        stock_id=stock_id,
        requested_start=start_date,
        requested_end=end_date,
        cached_dates=len(cached),
        total_dates=len(all_dates),
        categories=cat_counts,
    )
