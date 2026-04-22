import logging
import time
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.hot_money_service import compute_hot_money
from app.industry_flow_service import (
    get_latest_industry_trade_date,
    get_recent_industry_trade_dates,
    load_industry_flow_rows,
    load_industry_flow_rows_for_dates,
)
from app.models import InstStockFlow, StockMaster, DailyPrice
from app.routers.market import HotMoneyResponse, serialize_hot_money_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["industries"])
LOCK_RETRY_DELAY = 0.15
LOCK_RETRY_ATTEMPTS = 2


# ── Streak helper ────────────────────────────────────────────────────────────

def compute_streak(net_amounts_desc: list) -> int:
    """
    Given a list of net_amount values sorted by date DESCENDING,
    return the consecutive streak count.
    Positive = consecutive buy days, negative = consecutive sell days, 0 = no streak.
    """
    if not net_amounts_desc:
        return 0
    first = net_amounts_desc[0]
    if first == 0:
        return 0
    positive = first > 0
    count = 0
    for val in net_amounts_desc:
        if (positive and val > 0) or (not positive and val < 0):
            count += 1
        else:
            break
    return count if positive else -count


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
                "SQLite database is locked for industries query, retrying (%d/%d)",
                attempt + 1,
                LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(LOCK_RETRY_DELAY)
    if last_error is not None:
        raise last_error


# ── Response schemas ──────────────────────────────────────────────────────────

class IndustryFlowItem(BaseModel):
    industry_name: str
    total_net_amount: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    total_buy_amount: float
    total_sell_amount: float
    streak: int  # positive = consecutive buy days, negative = consecutive sell days

    model_config = {"from_attributes": True}


class StockFlowItem(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: str
    sub_industry: Optional[str]
    close_price: Optional[float]
    prev_close_price: Optional[float]
    price_change: Optional[float]
    price_change_pct: Optional[float]
    foreign_net_shares: float
    trust_net_shares: float
    dealer_net_shares: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float


class SubIndustrySummaryItem(BaseModel):
    sub_industry: str
    total_net_amount: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    streak: int


def _raise_busy_if_locked(error: OperationalError) -> None:
    if _is_sqlite_lock_error(error):
        raise HTTPException(status_code=503, detail="Data is temporarily busy, please retry")
    raise error


def _load_stocks_by_industry_name(db: Session, industry_name: str) -> List[StockMaster]:
    """直接用 FinMind 分類名稱查 stocks_master，不再做 TWSE/Fugle fallback。"""
    return _run_with_lock_retry(
        db,
        lambda: (
            db.query(StockMaster)
            .filter(StockMaster.industry_name == industry_name)
            .all()
        ),
    )


def _resolve_market_trade_date(db: Session, requested_date: date) -> Optional[date]:
    return _run_with_lock_retry(
        db,
        lambda: get_latest_industry_trade_date(db, requested_date),
    )


def _resolve_stock_trade_date(
    db: Session,
    requested_date: date,
    stock_ids: List[str],
) -> Optional[date]:
    if not stock_ids:
        return None

    return _run_with_lock_retry(
        db,
        lambda: (
            db.query(func.max(InstStockFlow.trade_date))
            .filter(
                InstStockFlow.trade_date <= requested_date,
                InstStockFlow.stock_id.in_(stock_ids),
            )
            .scalar()
        ),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/industries", response_model=List[IndustryFlowItem])
def get_industries(
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L0: Return all industries for the given date with streak info.
    """
    try:
        resolved_date = _resolve_market_trade_date(db, date)
    except OperationalError as error:
        _raise_busy_if_locked(error)

    logger.info("GET /industries requested=%s resolved=%s", date, resolved_date)
    if resolved_date is None:
        logger.warning("No industry flow data on or before %s", date)
        raise HTTPException(status_code=404, detail=f"No data for {date}")

    try:
        rows = _run_with_lock_retry(
            db,
            lambda: load_industry_flow_rows(db, resolved_date),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not rows:
        logger.warning("No industry flow data for requested=%s resolved=%s", date, resolved_date)
        raise HTTPException(status_code=404, detail=f"No data for {date}")

    # Compute streaks: load last 31 trading dates per industry (enough to detect 30+ streak)
    industry_names = [r.industry_name for r in rows]
    try:
        recent_dates = get_recent_industry_trade_dates(db, resolved_date, limit=31)
        history = _run_with_lock_retry(
            db,
            lambda: load_industry_flow_rows_for_dates(db, recent_dates, industry_names),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    # Group by industry → list of net amounts (date desc)
    streak_data: Dict[str, list] = defaultdict(list)
    for row in history:
        streak_data[row.industry_name].append(row.total_net_amount)

    result = []
    for r in rows:
        result.append(IndustryFlowItem(
            industry_name=r.industry_name,
            total_net_amount=r.total_net_amount,
            foreign_net_amount=r.foreign_net_amount,
            trust_net_amount=r.trust_net_amount,
            dealer_net_amount=r.dealer_net_amount,
            total_buy_amount=r.total_buy_amount,
            total_sell_amount=r.total_sell_amount,
            streak=compute_streak(streak_data.get(r.industry_name, [])),
        ))

    logger.debug("Returning %d industries for requested=%s resolved=%s", len(result), date, resolved_date)
    return result


@router.get("/industries/{industry_name}/summary", response_model=List[SubIndustrySummaryItem])
def get_industry_sub_summary(
    industry_name: str,
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L1 summary: Return sub_industry level aggregation for a given industry,
    with streak info.
    """
    logger.info("GET /industries/%s/summary requested=%s", industry_name, date)

    try:
        stocks = _load_stocks_by_industry_name(db, industry_name)
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not stocks:
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    try:
        resolved_date = _resolve_stock_trade_date(db, date, stock_ids)
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if resolved_date is None:
        raise HTTPException(status_code=404, detail=f"No data for {date}")

    stock_sub = {
        s.stock_id: s.sub_industry or s.industry_name
        for s in stocks
    }
    sub_set: set[str] = {s.sub_industry or s.industry_name for s in stocks}

    # Load flows for the target date
    try:
        flows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(InstStockFlow)
                .filter(InstStockFlow.trade_date == resolved_date, InstStockFlow.stock_id.in_(stock_ids))
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)

    # Aggregate by sub_industry for target date
    agg: Dict[str, dict] = defaultdict(lambda: {
        "foreign_net": 0.0, "trust_net": 0.0, "dealer_net": 0.0,
    })
    for f in flows:
        sub = stock_sub.get(f.stock_id)
        if sub is None:
            continue
        if f.inst_type == "foreign":
            agg[sub]["foreign_net"] += f.net_amount_est or 0.0
        elif f.inst_type == "trust":
            agg[sub]["trust_net"] += f.net_amount_est or 0.0
        elif f.inst_type == "dealer":
            agg[sub]["dealer_net"] += f.net_amount_est or 0.0

    # Load recent trading dates for streak calculation (last 31 distinct dates ≤ target)
    # No need to load full history — streak only cares about consecutive days.
    recent_dates = (
        db.query(InstStockFlow.trade_date)
        .filter(InstStockFlow.trade_date <= resolved_date, InstStockFlow.stock_id.in_(stock_ids))
        .distinct()
        .order_by(InstStockFlow.trade_date.desc())
        .limit(31)
        .subquery()
    )
    try:
        all_flows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(InstStockFlow)
                .filter(
                    InstStockFlow.trade_date.in_(select(recent_dates.c.trade_date)),
                    InstStockFlow.stock_id.in_(stock_ids),
                )
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)

    daily_sub_net: Dict[str, Dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for f in all_flows:
        sub = stock_sub.get(f.stock_id)
        if sub is None:
            continue
        daily_sub_net[sub][f.trade_date] += f.net_amount_est or 0.0

    sub_streaks: Dict[str, int] = {}
    for sub, date_nets in daily_sub_net.items():
        sorted_vals = [v for _, v in sorted(date_nets.items(), reverse=True)]
        sub_streaks[sub] = compute_streak(sorted_vals)

    result = []
    for sub in sub_set:
        vals = agg.get(sub, {"foreign_net": 0.0, "trust_net": 0.0, "dealer_net": 0.0})
        total = vals["foreign_net"] + vals["trust_net"] + vals["dealer_net"]
        result.append(SubIndustrySummaryItem(
            sub_industry=sub,
            total_net_amount=total,
            foreign_net_amount=vals["foreign_net"],
            trust_net_amount=vals["trust_net"],
            dealer_net_amount=vals["dealer_net"],
            streak=sub_streaks.get(sub, 0),
        ))

    result.sort(key=lambda x: x.total_net_amount, reverse=True)
    logger.debug(
        "Returning %d sub-industry summaries for '%s' requested=%s resolved=%s",
        len(result), industry_name, date, resolved_date,
    )
    return result


@router.get("/industries/{industry_name}/stocks", response_model=List[StockFlowItem])
def get_industry_stocks(
    industry_name: str,
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L1: Return all stocks in the given industry for the given date, sorted by total net flow descending.
    industry_name matches stocks_master.industry_name (Fugle broad category).
    """
    logger.info("GET /industries/%s/stocks requested=%s", industry_name, date)

    try:
        stocks = _load_stocks_by_industry_name(db, industry_name)
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not stocks:
        logger.warning("Industry not found: %s", industry_name)
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    stock_map = {s.stock_id: s for s in stocks}
    try:
        resolved_date = _resolve_stock_trade_date(db, date, stock_ids)
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if resolved_date is None:
        raise HTTPException(status_code=404, detail=f"No data for {date}")

    logger.debug("Found %d stocks in industry '%s'", len(stock_ids), industry_name)

    # Load closing prices for the target date
    try:
        prices = _run_with_lock_retry(
            db,
            lambda: (
                db.query(DailyPrice)
                .filter(DailyPrice.trade_date == resolved_date, DailyPrice.stock_id.in_(stock_ids))
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    price_map = {p.stock_id: p.close_price for p in prices}

    # Load previous trading day's prices for price change calculation.
    # Use per-stock lookup so suspended stocks still get their most recent prev close.
    prev_subq = (
        db.query(
            DailyPrice.stock_id,
            func.max(DailyPrice.trade_date).label("prev_date"),
        )
        .filter(DailyPrice.trade_date < resolved_date, DailyPrice.stock_id.in_(stock_ids))
        .group_by(DailyPrice.stock_id)
        .subquery()
    )
    try:
        prev_prices = _run_with_lock_retry(
            db,
            lambda: (
                db.query(DailyPrice)
                .join(
                    prev_subq,
                    and_(
                        DailyPrice.stock_id == prev_subq.c.stock_id,
                        DailyPrice.trade_date == prev_subq.c.prev_date,
                    ),
                )
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)
    prev_price_map = {p.stock_id: p.close_price for p in prev_prices}

    # Load institutional flows
    try:
        flows = _run_with_lock_retry(
            db,
            lambda: (
                db.query(InstStockFlow)
                .filter(InstStockFlow.trade_date == resolved_date, InstStockFlow.stock_id.in_(stock_ids))
                .all()
            ),
        )
    except OperationalError as error:
        _raise_busy_if_locked(error)

    # Aggregate flows per stock_id
    agg: dict = {}
    for f in flows:
        sid = f.stock_id
        if sid not in agg:
            agg[sid] = {
                "foreign_net_shares": 0.0, "trust_net_shares": 0.0, "dealer_net_shares": 0.0,
                "foreign_net_amount": 0.0, "trust_net_amount": 0.0, "dealer_net_amount": 0.0,
            }
        key_shares = f"{f.inst_type}_net_shares"
        key_amount = f"{f.inst_type}_net_amount"
        agg[sid][key_shares] = f.net_shares or 0.0
        agg[sid][key_amount] = f.net_amount_est or 0.0

    result = []
    for sid in stock_ids:
        sm = stock_map[sid]
        vals = agg.get(sid, {
            "foreign_net_shares": 0.0, "trust_net_shares": 0.0, "dealer_net_shares": 0.0,
            "foreign_net_amount": 0.0, "trust_net_amount": 0.0, "dealer_net_amount": 0.0,
        })

        close = price_map.get(sid)
        prev = prev_price_map.get(sid)
        change = None
        change_pct = None
        if close is not None and prev is not None and prev != 0:
            change = close - prev
            change_pct = (change / prev) * 100.0

        result.append(StockFlowItem(
            stock_id=sid,
            stock_name=sm.stock_name,
            industry_name=sm.industry_name,
            sub_industry=sm.sub_industry,
            close_price=close,
            prev_close_price=prev,
            price_change=change,
            price_change_pct=change_pct,
            **vals,
        ))

    result.sort(
        key=lambda x: x.foreign_net_amount + x.trust_net_amount + x.dealer_net_amount,
        reverse=True,
    )
    logger.debug(
        "Returning %d stocks for industry '%s' requested=%s resolved=%s",
        len(result), industry_name, date, resolved_date,
    )
    return result


@router.get("/industries/{industry_name}/hot-money", response_model=HotMoneyResponse)
def get_industry_hot_money(
    industry_name: str,
    date: Optional[date] = Query(default=None, description="trade date YYYY-MM-DD; defaults to latest available"),
    days: int = Query(default=3, ge=1, le=20, description="window size in trading days"),
    limit: int = Query(default=10, ge=1, le=50, description="max items to return"),
    sub_industry: Optional[str] = Query(default=None, description="optional sub_industry filter"),
    db: Session = Depends(get_db),
):
    """
    M22: 單一產業近 N 日三大法人累計淨買超 Top K（L1 產業頁頂部用）。
    帶 `sub_industry` 時只篩該子產業個股。
    """
    logger.info(
        "GET /industries/%s/hot-money requested=%s days=%s sub=%s",
        industry_name, date, days, sub_industry,
    )

    try:
        stocks = _load_stocks_by_industry_name(db, industry_name)
    except OperationalError as error:
        _raise_busy_if_locked(error)
    if not stocks:
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    if sub_industry:
        stocks = [s for s in stocks if (s.sub_industry or s.industry_name) == sub_industry]
    stock_ids = [s.stock_id for s in stocks]

    try:
        resolved_date = _resolve_stock_trade_date(db, date, stock_ids) if date else None
    except OperationalError as error:
        _raise_busy_if_locked(error)

    if resolved_date is None:
        try:
            resolved_date = _resolve_market_trade_date(db, date) if date else get_latest_industry_trade_date(db)
        except OperationalError as error:
            _raise_busy_if_locked(error)

    if resolved_date is None or not stock_ids:
        return HotMoneyResponse(start_date=None, end_date=None, trade_dates=[], items=[])

    result = compute_hot_money(
        db,
        end_date=resolved_date,
        days=days,
        limit=limit,
        stock_ids=stock_ids,
    )
    return serialize_hot_money_result(result)
