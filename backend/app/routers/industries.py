import logging
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IndustryDailyFlow, InstStockFlow, StockMaster, DailyPrice

logger = logging.getLogger(__name__)

router = APIRouter(tags=["industries"])


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
    chain: Optional[str]
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
    chain: Optional[str]
    total_net_amount: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    streak: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/industries", response_model=List[IndustryFlowItem])
def get_industries(
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L0: Return all industries for the given date with streak info.
    """
    logger.info("GET /industries date=%s", date)
    rows = (
        db.query(IndustryDailyFlow)
        .filter(IndustryDailyFlow.trade_date == date)
        .order_by(IndustryDailyFlow.total_net_amount.desc())
        .all()
    )
    if not rows:
        logger.warning("No industry flow data for %s", date)
        raise HTTPException(status_code=404, detail=f"No data for {date}")

    # Compute streaks: load recent history per industry (up to the given date)
    industry_names = [r.industry_name for r in rows]
    history = (
        db.query(IndustryDailyFlow.industry_name, IndustryDailyFlow.trade_date, IndustryDailyFlow.total_net_amount)
        .filter(
            IndustryDailyFlow.industry_name.in_(industry_names),
            IndustryDailyFlow.trade_date <= date,
        )
        .order_by(IndustryDailyFlow.industry_name, IndustryDailyFlow.trade_date.desc())
        .all()
    )
    # Group by industry → list of net amounts (date desc)
    streak_data: Dict[str, list] = defaultdict(list)
    for name, _, net_amt in history:
        streak_data[name].append(net_amt)

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

    logger.debug("Returning %d industries for %s", len(result), date)
    return result


@router.get("/industries/{industry_name}/summary", response_model=List[SubIndustrySummaryItem])
def get_industry_sub_summary(
    industry_name: str,
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L1 summary: Return sub_industry level aggregation for a given industry,
    with chain grouping and streak info.
    """
    logger.info("GET /industries/%s/summary date=%s", industry_name, date)

    # Find all stocks in this industry
    stocks = (
        db.query(StockMaster)
        .filter(StockMaster.industry_name == industry_name)
        .all()
    )
    if not stocks:
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    # stock_id → (sub_industry, chain)
    stock_sub = {
        s.stock_id: (s.sub_industry or s.industry_name, s.chain)
        for s in stocks
    }
    # Collect unique sub_industries with their chain
    sub_chain: Dict[str, Optional[str]] = {}
    for s in stocks:
        sub = s.sub_industry or s.industry_name
        if sub not in sub_chain:
            sub_chain[sub] = s.chain

    # Load flows for the target date
    flows = (
        db.query(InstStockFlow)
        .filter(InstStockFlow.trade_date == date, InstStockFlow.stock_id.in_(stock_ids))
        .all()
    )

    # Aggregate by sub_industry for target date
    agg: Dict[str, dict] = defaultdict(lambda: {
        "foreign_net": 0.0, "trust_net": 0.0, "dealer_net": 0.0,
    })
    for f in flows:
        sub, _ = stock_sub.get(f.stock_id, (None, None))
        if sub is None:
            continue
        if f.inst_type == "foreign":
            agg[sub]["foreign_net"] += f.net_amount_est or 0.0
        elif f.inst_type == "trust":
            agg[sub]["trust_net"] += f.net_amount_est or 0.0
        elif f.inst_type == "dealer":
            agg[sub]["dealer_net"] += f.net_amount_est or 0.0

    # Load historical flows for streak calculation (all dates up to target)
    all_flows = (
        db.query(InstStockFlow)
        .filter(InstStockFlow.trade_date <= date, InstStockFlow.stock_id.in_(stock_ids))
        .all()
    )

    # Aggregate by (sub_industry, date) → total net
    daily_sub_net: Dict[str, Dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for f in all_flows:
        sub, _ = stock_sub.get(f.stock_id, (None, None))
        if sub is None:
            continue
        daily_sub_net[sub][f.trade_date] += f.net_amount_est or 0.0

    # Compute streak per sub_industry
    sub_streaks: Dict[str, int] = {}
    for sub, date_nets in daily_sub_net.items():
        sorted_vals = [v for _, v in sorted(date_nets.items(), reverse=True)]
        sub_streaks[sub] = compute_streak(sorted_vals)

    # Build result
    result = []
    for sub, chain in sub_chain.items():
        vals = agg.get(sub, {"foreign_net": 0.0, "trust_net": 0.0, "dealer_net": 0.0})
        total = vals["foreign_net"] + vals["trust_net"] + vals["dealer_net"]
        result.append(SubIndustrySummaryItem(
            sub_industry=sub,
            chain=chain,
            total_net_amount=total,
            foreign_net_amount=vals["foreign_net"],
            trust_net_amount=vals["trust_net"],
            dealer_net_amount=vals["dealer_net"],
            streak=sub_streaks.get(sub, 0),
        ))

    result.sort(key=lambda x: x.total_net_amount, reverse=True)
    logger.debug("Returning %d sub-industry summaries for '%s' on %s", len(result), industry_name, date)
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
    logger.info("GET /industries/%s/stocks date=%s", industry_name, date)

    # Find stocks belonging to this industry (by broad Fugle category)
    stocks = (
        db.query(StockMaster)
        .filter(StockMaster.industry_name == industry_name)
        .all()
    )
    if not stocks:
        logger.warning("Industry not found: %s", industry_name)
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    stock_map = {s.stock_id: s for s in stocks}
    logger.debug("Found %d stocks in industry '%s'", len(stock_ids), industry_name)

    # Load closing prices for the target date
    prices = (
        db.query(DailyPrice)
        .filter(DailyPrice.trade_date == date, DailyPrice.stock_id.in_(stock_ids))
        .all()
    )
    price_map = {p.stock_id: p.close_price for p in prices}

    # Load previous trading day's prices for price change calculation
    prev_date = (
        db.query(func.max(DailyPrice.trade_date))
        .filter(DailyPrice.trade_date < date)
        .scalar()
    )
    prev_price_map: dict = {}
    if prev_date:
        prev_prices = (
            db.query(DailyPrice)
            .filter(DailyPrice.trade_date == prev_date, DailyPrice.stock_id.in_(stock_ids))
            .all()
        )
        prev_price_map = {p.stock_id: p.close_price for p in prev_prices}

    # Load institutional flows
    flows = (
        db.query(InstStockFlow)
        .filter(InstStockFlow.trade_date == date, InstStockFlow.stock_id.in_(stock_ids))
        .all()
    )

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
            chain=sm.chain,
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
    logger.debug("Returning %d stocks for industry '%s' on %s", len(result), industry_name, date)
    return result
