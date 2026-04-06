import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IndustryDailyFlow, InstStockFlow, StockMaster, DailyPrice

logger = logging.getLogger(__name__)

router = APIRouter(tags=["industries"])


# ── Response schemas ──────────────────────────────────────────────────────────

class IndustryFlowItem(BaseModel):
    industry_name: str
    total_net_amount: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    total_buy_amount: float
    total_sell_amount: float

    model_config = {"from_attributes": True}


class StockFlowItem(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: str
    chain: Optional[str]
    sub_industry: Optional[str]
    close_price: Optional[float]
    foreign_net_shares: float
    trust_net_shares: float
    dealer_net_shares: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/industries", response_model=List[IndustryFlowItem])
def get_industries(
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L0: Return all industries for the given date, sorted by total net institutional flow descending.
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
    logger.debug("Returning %d industries for %s", len(rows), date)
    return rows


@router.get("/industries/{industry_name}/stocks", response_model=List[StockFlowItem])
def get_industry_stocks(
    industry_name: str,
    date: date = Query(..., description="trade date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    L1: Return all stocks in the given industry for the given date, sorted by total net flow descending.
    industry_name matches stocks_master.sub_industry (or industry_name as fallback).
    """
    logger.info("GET /industries/%s/stocks date=%s", industry_name, date)

    # Find stocks belonging to this industry (sub_industry takes priority, fallback to industry_name)
    stocks = (
        db.query(StockMaster)
        .filter(
            (StockMaster.sub_industry == industry_name) |
            ((StockMaster.sub_industry == None) & (StockMaster.industry_name == industry_name))
        )
        .all()
    )
    if not stocks:
        logger.warning("Industry not found: %s", industry_name)
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    stock_map = {s.stock_id: s for s in stocks}
    logger.debug("Found %d stocks in industry '%s'", len(stock_ids), industry_name)

    # Load closing prices
    prices = (
        db.query(DailyPrice)
        .filter(DailyPrice.trade_date == date, DailyPrice.stock_id.in_(stock_ids))
        .all()
    )
    price_map = {p.stock_id: p.close_price for p in prices}

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
        result.append(StockFlowItem(
            stock_id=sid,
            stock_name=sm.stock_name,
            industry_name=sm.industry_name,
            chain=sm.chain,
            sub_industry=sm.sub_industry,
            close_price=price_map.get(sid),
            **vals,
        ))

    result.sort(
        key=lambda x: x.foreign_net_amount + x.trust_net_amount + x.dealer_net_amount,
        reverse=True,
    )
    logger.debug("Returning %d stocks for industry '%s' on %s", len(result), industry_name, date)
    return result
