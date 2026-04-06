from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IndustryDailyFlow, InstStockFlow, StockMaster, DailyPrice

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
    date: date = Query(..., description="交易日期，格式 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    L0：回傳指定日期所有產業的法人流向，依三大法人合計淨買超降冪排序。
    """
    rows = (
        db.query(IndustryDailyFlow)
        .filter(IndustryDailyFlow.trade_date == date)
        .order_by(IndustryDailyFlow.total_net_amount.desc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for {date}")
    return rows


@router.get("/industries/{industry_name}/stocks", response_model=List[StockFlowItem])
def get_industry_stocks(
    industry_name: str,
    date: date = Query(..., description="交易日期，格式 YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    L1：回傳指定產業在指定日期的所有個股法人明細，依三大法人合計淨買超降冪排序。
    industry_name 對應 stocks_master.sub_industry（或 industry_name）。
    """
    # 找屬於此產業的股票（sub_industry 優先，fallback industry_name）
    stocks = (
        db.query(StockMaster)
        .filter(
            (StockMaster.sub_industry == industry_name) |
            ((StockMaster.sub_industry == None) & (StockMaster.industry_name == industry_name))
        )
        .all()
    )
    if not stocks:
        raise HTTPException(status_code=404, detail=f"Industry not found: {industry_name}")

    stock_ids = [s.stock_id for s in stocks]
    stock_map = {s.stock_id: s for s in stocks}

    # 收盤價
    prices = (
        db.query(DailyPrice)
        .filter(DailyPrice.trade_date == date, DailyPrice.stock_id.in_(stock_ids))
        .all()
    )
    price_map = {p.stock_id: p.close_price for p in prices}

    # 法人流量
    flows = (
        db.query(InstStockFlow)
        .filter(InstStockFlow.trade_date == date, InstStockFlow.stock_id.in_(stock_ids))
        .all()
    )

    # 依 stock_id 彙整三法人
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
    return result
