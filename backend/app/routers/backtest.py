from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.backtest_catalog import BACKTEST_TEMPLATES, DEFAULT_INITIAL_CAPITAL
from app.backtest_engine import BacktestDay, run_backtest
from app.backtest_parser import interpret_strategy_text
from app.database import get_db
from app.models import DailyPrice, InstStockFlow, StockMaster

router = APIRouter(tags=["backtest"])


class BacktestInterpretRequest(BaseModel):
    stock_id: str
    start_date: date
    end_date: date
    strategy_text: str
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)


class PeriodReturnItem(BaseModel):
    period: str
    return_pct: float


class TradeItem(BaseModel):
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    holding_days: int
    return_pct: float
    pnl_amount: float
    exit_reason: str


class EquityCurveItem(BaseModel):
    trade_date: str
    equity: float
    benchmark_equity: float


class LatestRecommendation(BaseModel):
    latest_signal_date: str
    action: str
    reason: str


class BacktestRunResponse(BaseModel):
    supported: bool
    normalized_text: str
    strategy: Dict[str, Any]
    metrics: Dict[str, Any]
    equity_curve: List[EquityCurveItem]
    period_returns: Dict[str, List[PeriodReturnItem]]
    trades: List[TradeItem]
    latest_recommendation: LatestRecommendation
    warnings: List[str]


@router.get("/backtest/templates")
def get_backtest_templates():
    return BACKTEST_TEMPLATES


@router.post("/backtest/interpret")
def post_backtest_interpret(payload: BacktestInterpretRequest):
    try:
        return interpret_strategy_text(
            stock_id=payload.stock_id,
            start_date=payload.start_date.isoformat(),
            end_date=payload.end_date.isoformat(),
            strategy_text=payload.strategy_text,
            initial_capital=payload.initial_capital,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/backtest/run", response_model=BacktestRunResponse)
def post_backtest_run(payload: BacktestInterpretRequest, db: Session = Depends(get_db)):
    stock = db.get(StockMaster, payload.stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {payload.stock_id}")

    try:
        interpreted = interpret_strategy_text(
            stock_id=payload.stock_id,
            start_date=payload.start_date.isoformat(),
            end_date=payload.end_date.isoformat(),
            strategy_text=payload.strategy_text,
            initial_capital=payload.initial_capital,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    prices = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == payload.stock_id,
            DailyPrice.trade_date >= payload.start_date,
            DailyPrice.trade_date <= payload.end_date,
        )
        .order_by(DailyPrice.trade_date)
        .all()
    )
    if not prices:
        raise HTTPException(status_code=404, detail=f"No price data for {payload.stock_id}")

    flows = (
        db.query(InstStockFlow)
        .filter(
            InstStockFlow.stock_id == payload.stock_id,
            InstStockFlow.trade_date >= payload.start_date,
            InstStockFlow.trade_date <= payload.end_date,
        )
        .all()
    )

    flow_map: Dict[date, Dict[str, float]] = {}
    for flow in flows:
        if flow.trade_date not in flow_map:
            flow_map[flow.trade_date] = {}
        flow_map[flow.trade_date][flow.inst_type] = flow.net_shares or 0.0

    dataset = [
        BacktestDay(
            trade_date=price.trade_date,
            open_price=price.open_price,
            close_price=price.close_price,
            volume=price.volume or 0.0,
            foreign_net_shares=flow_map.get(price.trade_date, {}).get("foreign", 0.0),
            trust_net_shares=flow_map.get(price.trade_date, {}).get("trust", 0.0),
            dealer_net_shares=flow_map.get(price.trade_date, {}).get("dealer", 0.0),
        )
        for price in prices
    ]

    try:
        result = run_backtest(dataset, interpreted["strategy"])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "supported": True,
        "normalized_text": interpreted["normalized_text"],
        "strategy": interpreted["strategy"],
        "metrics": result["metrics"],
        "equity_curve": result["equity_curve"],
        "period_returns": result["period_returns"],
        "trades": result["trades"],
        "latest_recommendation": result["latest_recommendation"],
        "warnings": interpreted["warnings"],
    }
