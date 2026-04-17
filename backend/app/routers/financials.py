import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app.models import DailyValuation, MonthlyRevenue, FinancialStatement, StockMaster

logger = logging.getLogger(__name__)

router = APIRouter(tags=["financials"])


# ── Response schemas ──────────────────────────────────────────────────────────

class ValuationItem(BaseModel):
    trade_date: date
    per: Optional[float]
    pbr: Optional[float]
    dividend_yield: Optional[float]


class ValuationResponse(BaseModel):
    stock_id: str
    stock_name: str
    history: List[ValuationItem]


class RevenueItem(BaseModel):
    revenue_month: date
    revenue: Optional[float]
    yoy_pct: Optional[float]
    mom_pct: Optional[float]


class RevenueResponse(BaseModel):
    stock_id: str
    stock_name: str
    history: List[RevenueItem]


class FinancialItem(BaseModel):
    report_date: date
    item_name: str
    item_code: Optional[str]
    value: Optional[float]
    period_type: str
    unit: Optional[str]


class FinancialResponse(BaseModel):
    stock_id: str
    stock_name: str
    items: List[FinancialItem]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/stocks/{stock_id}/valuation", response_model=ValuationResponse)
def get_stock_valuation(
    stock_id: str,
    start_date: Optional[date] = Query(default=None, description="起始日，預設一年前"),
    end_date: Optional[date] = Query(default=None, description="結束日，預設今日"),
    db: Session = Depends(get_db),
):
    """L2: 個股 PER / PBR / 殖利率走勢"""
    stock = db.get(StockMaster, stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {stock_id}")

    if end_date is None:
        end_date = date.today()
    if start_date is None:
        from datetime import timedelta
        start_date = end_date.replace(year=end_date.year - 1)

    rows = (
        db.query(DailyValuation)
        .filter(
            DailyValuation.stock_id == stock_id,
            DailyValuation.trade_date >= start_date,
            DailyValuation.trade_date <= end_date,
        )
        .order_by(DailyValuation.trade_date)
        .all()
    )

    return ValuationResponse(
        stock_id=stock_id,
        stock_name=stock.stock_name,
        history=[
            ValuationItem(
                trade_date=r.trade_date,
                per=r.per,
                pbr=r.pbr,
                dividend_yield=r.dividend_yield,
            )
            for r in rows
        ],
    )


@router.get("/stocks/{stock_id}/revenue", response_model=RevenueResponse)
def get_stock_revenue(
    stock_id: str,
    months: int = Query(default=24, ge=1, le=120, description="往前幾個月，預設 24"),
    db: Session = Depends(get_db),
):
    """L2: 個股月營收 + YoY / MoM"""
    stock = db.get(StockMaster, stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {stock_id}")

    rows = (
        db.query(MonthlyRevenue)
        .filter(MonthlyRevenue.stock_id == stock_id)
        .order_by(MonthlyRevenue.revenue_month.desc())
        .limit(months)
        .all()
    )
    rows = list(reversed(rows))

    return RevenueResponse(
        stock_id=stock_id,
        stock_name=stock.stock_name,
        history=[
            RevenueItem(
                revenue_month=r.revenue_month,
                revenue=r.revenue,
                yoy_pct=r.yoy_pct,
                mom_pct=r.mom_pct,
            )
            for r in rows
        ],
    )


@router.get("/stocks/{stock_id}/financials", response_model=FinancialResponse)
def get_stock_financials(
    stock_id: str,
    item_names: Optional[str] = Query(
        default=None,
        description="篩選特定項目，逗號分隔（如 EPS,ROE），不傳回傳全部",
    ),
    quarters: int = Query(default=8, ge=1, le=40, description="往前幾季，預設 8"),
    db: Session = Depends(get_db),
):
    """L2: 個股財報項目（EPS、ROE、營業收入等）"""
    stock = db.get(StockMaster, stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {stock_id}")

    # 取最近 N 季的 report_date
    recent_dates = (
        db.query(FinancialStatement.report_date)
        .filter(FinancialStatement.stock_id == stock_id)
        .distinct()
        .order_by(FinancialStatement.report_date.desc())
        .limit(quarters)
        .all()
    )
    if not recent_dates:
        return FinancialResponse(stock_id=stock_id, stock_name=stock.stock_name, items=[])

    date_list = [r[0] for r in recent_dates]

    query = db.query(FinancialStatement).filter(
        FinancialStatement.stock_id == stock_id,
        FinancialStatement.report_date.in_(date_list),
    )

    if item_names:
        names = [n.strip() for n in item_names.split(",") if n.strip()]
        query = query.filter(FinancialStatement.item_name.in_(names))

    rows = query.order_by(
        FinancialStatement.report_date,
        FinancialStatement.item_name,
    ).all()

    return FinancialResponse(
        stock_id=stock_id,
        stock_name=stock.stock_name,
        items=[
            FinancialItem(
                report_date=r.report_date,
                item_name=r.item_name,
                item_code=r.item_code,
                value=r.value,
                period_type=r.period_type,
                unit=r.unit,
            )
            for r in rows
        ],
    )
