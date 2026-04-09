"""
Broker branch trading API.

On-demand fetch: if data for a stock+date is not in DB, fetch from TWSE BSR
and cache. Background backfill for date ranges.
"""
import logging
import time
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.broker_config import BROKER_LOOKUP, CATEGORY_LABELS, get_categories, normalize
from app.database import SessionLocal, get_db
from app.models import BrokerTrade

logger = logging.getLogger(__name__)

router = APIRouter(tags=["brokers"])

# Rate limit: minimum seconds between TWSE BSR requests
BSR_RATE_LIMIT = 3.5


# ── Response schemas ─────────────────────────────────────────────────────────

class BrokerTradeItem(BaseModel):
    broker_id: str
    broker_name: str
    display_name: str  # mapped display name (with dash) or original
    buy_shares: float
    sell_shares: float
    net_shares: float


class BrokerTradeResponse(BaseModel):
    stock_id: str
    trade_date: date
    category: str
    category_label: str
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
                fetch_and_upsert_broker_trade(db, stock_id, d)
            except Exception:
                logger.exception("Backfill failed: %s on %s", stock_id, d)
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
    cached = _get_cached_dates(db, stock_id, start_date, end_date)
    missing = [d for d in all_dates if d not in cached]

    if missing:
        # Fetch first missing date synchronously for immediate result
        from etl.fetch_broker_trade import fetch_and_upsert_broker_trade
        try:
            fetch_and_upsert_broker_trade(db, stock_id, missing[0])
            cached.add(missing[0])
        except Exception:
            logger.exception("Sync fetch failed: %s on %s", stock_id, missing[0])

        # Backfill remaining in background
        if len(missing) > 1:
            background_tasks.add_task(_backfill_missing, stock_id, missing[1:])

    # Query aggregated broker data
    rows = (
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

    # Filter by category
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
            ))

    # Sort by absolute net_shares descending, take top 10
    filtered.sort(key=lambda x: abs(x.net_shares), reverse=True)

    return BrokerTradeResponse(
        stock_id=stock_id,
        trade_date=trade_date,
        category=category,
        category_label=CATEGORY_LABELS[category],
        brokers=filtered[:10],
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
    cached = _get_cached_dates(db, stock_id, start_date, end_date)

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
