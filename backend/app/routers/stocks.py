import logging
import time
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from sqlalchemy import func, or_

from app.database import get_db
from app.models import DailyPrice, InstStockFlow, StockMaster

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stocks"])
LOCK_RETRY_DELAY = 0.15
LOCK_RETRY_ATTEMPTS = 2


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
                "SQLite database is locked for stocks query, retrying (%d/%d)",
                attempt + 1,
                LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(LOCK_RETRY_DELAY)
    if last_error is not None:
        raise last_error


def _raise_busy_if_locked(error: OperationalError) -> None:
    if _is_sqlite_lock_error(error):
        raise HTTPException(status_code=503, detail="Data is temporarily busy, please retry")
    raise error


# ── Response schemas ──────────────────────────────────────────────────────────

class StockHistoryItem(BaseModel):
    trade_date: date
    open_price: Optional[float]
    high_price: Optional[float]
    low_price: Optional[float]
    close_price: float
    foreign_net_shares: float
    trust_net_shares: float
    dealer_net_shares: float
    foreign_cumulative: float   # cumulative net shares bought
    trust_cumulative: float
    dealer_cumulative: float


class StockHistoryResponse(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: str
    sub_industry: Optional[str]
    history: List[StockHistoryItem]
    earliest_date: Optional[str] = None  # 此股票在 DB 中最早的交易日
    latest_date: Optional[str] = None    # 此股票在 DB 中最新的交易日


class StockSearchItem(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/stocks/search", response_model=List[StockSearchItem])
def search_stocks(
    q: str = Query(..., min_length=1, max_length=40, description="關鍵字（股票代號或名稱）"),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Autocomplete search for stocks. Matches prefix of stock_id or substring of stock_name.
    Prefix-on-ID matches rank first.
    """
    keyword = q.strip()
    if not keyword:
        return []

    like = f"%{keyword}%"
    try:
        rows: List[StockMaster] = _run_with_lock_retry(
            db,
            lambda: (
                db.query(StockMaster)
                .filter(
                    or_(
                        StockMaster.stock_id.like(f"{keyword}%"),
                        StockMaster.stock_name.like(like),
                    )
                )
                .limit(limit * 2)
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)

    # Sort: exact stock_id match first, then stock_id prefix, then name match
    def rank(m: StockMaster) -> tuple[int, str]:
        if m.stock_id == keyword:
            return (0, m.stock_id)
        if m.stock_id.startswith(keyword):
            return (1, m.stock_id)
        return (2, m.stock_id)

    sorted_rows = sorted(rows, key=rank)[:limit]
    return [
        StockSearchItem(
            stock_id=r.stock_id,
            stock_name=r.stock_name,
            industry_name=r.industry_name,
        )
        for r in sorted_rows
    ]




@router.get("/stocks/{stock_id}/history", response_model=StockHistoryResponse)
def get_stock_history(
    stock_id: str,
    days: int = Query(default=60, ge=1, le=3650, description="往前幾天，預設 60；有 start_date 時忽略此參數"),
    start_date: Optional[date] = Query(default=None, description="自訂起始日（提供時忽略 days）"),
    end_date: date = Query(default=None, description="結束日，預設為今日"),
    db: Session = Depends(get_db),
):
    """
    L2: Return daily closing price and institutional net buy/sell.
    Supports both relative (days) and absolute (start_date + end_date) range queries.
    Also returns the earliest/latest available date for this stock in the DB.
    """
    logger.info("GET /stocks/%s/history days=%d start_date=%s end_date=%s", stock_id, days, start_date, end_date)

    try:
        stock = _run_with_lock_retry(db, lambda: db.get(StockMaster, stock_id))
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not stock:
        logger.warning("Stock not found: %s", stock_id)
        raise HTTPException(status_code=404, detail=f"Stock not found: {stock_id}")

    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=days)

    try:
        prices = _run_with_lock_retry(
            db,
            lambda: (
                db.query(DailyPrice)
                .filter(
                    DailyPrice.stock_id == stock_id,
                    DailyPrice.trade_date >= start_date,
                    DailyPrice.trade_date <= end_date,
                )
                .order_by(DailyPrice.trade_date)
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not prices:
        logger.warning("No price data for %s in range %s–%s", stock_id, start_date, end_date)
        raise HTTPException(status_code=404, detail=f"No price data for {stock_id}")

    try:
        flows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(InstStockFlow)
                .filter(
                    InstStockFlow.stock_id == stock_id,
                    InstStockFlow.trade_date >= start_date,
                    InstStockFlow.trade_date <= end_date,
                )
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)

    # Index flows by {date: {inst_type: net_shares}}
    flow_map: dict = {}
    for f in flows:
        d = f.trade_date
        if d not in flow_map:
            flow_map[d] = {}
        flow_map[d][f.inst_type] = f.net_shares or 0.0

    history = []
    foreign_cum = trust_cum = dealer_cum = 0.0

    for p in prices:
        d = p.trade_date
        day_flows = flow_map.get(d, {})
        foreign_net = day_flows.get("foreign", 0.0)
        trust_net   = day_flows.get("trust",   0.0)
        dealer_net  = day_flows.get("dealer",  0.0)

        foreign_cum += foreign_net
        trust_cum   += trust_net
        dealer_cum  += dealer_net

        history.append(StockHistoryItem(
            trade_date=d,
            open_price=p.open_price,
            high_price=p.high_price,
            low_price=p.low_price,
            close_price=p.close_price,
            foreign_net_shares=foreign_net,
            trust_net_shares=trust_net,
            dealer_net_shares=dealer_net,
            foreign_cumulative=foreign_cum,
            trust_cumulative=trust_cum,
            dealer_cumulative=dealer_cum,
        ))

    # 查詢此股票在 DB 中的完整可用日期範圍
    try:
        date_range = _run_with_lock_retry(
            db,
            lambda: db.query(
                func.min(DailyPrice.trade_date),
                func.max(DailyPrice.trade_date),
            ).filter(DailyPrice.stock_id == stock_id).one(),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    earliest = str(date_range[0]) if date_range[0] else None
    latest = str(date_range[1]) if date_range[1] else None

    logger.debug("Returning %d days of history for %s", len(history), stock_id)
    return StockHistoryResponse(
        stock_id=stock_id,
        stock_name=stock.stock_name,
        industry_name=stock.industry_name,
        sub_industry=stock.sub_industry,
        history=history,
        earliest_date=earliest,
        latest_date=latest,
    )
