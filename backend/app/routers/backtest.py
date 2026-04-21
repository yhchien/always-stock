from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_user
from app.backtest_catalog import BACKTEST_CAPABILITY_CATALOG, BACKTEST_TEMPLATES, DEFAULT_INITIAL_CAPITAL
from app.backtest_advisor import generate_backtest_advice
from app.backtest_engine import BacktestDay, run_backtest, summarize_dataset_warnings
from app.backtest_parser import (
    estimate_strategy_lookback_days,
    interpret_strategy_parts,
    interpret_strategy_text,
)
from app.database import get_db
from app.models import DailyPrice, InstStockFlow, StockMaster, User

router = APIRouter(tags=["backtest"])


class BacktestInterpretRequest(BaseModel):
    stock_id: str
    start_date: date
    end_date: date
    initial_capital: float = Field(default=DEFAULT_INITIAL_CAPITAL, gt=0)
    # 新流程：使用 entry_text / exit_text 分離，停損停利獨立欄位
    entry_text: Optional[str] = None
    exit_text: Optional[str] = None
    stop_loss_pct: Optional[float] = Field(default=None, gt=0)
    take_profit_pct: Optional[float] = Field(default=None, gt=0)
    # 舊流程（向後相容）：「買進：…；賣出：…」單一字串
    strategy_text: Optional[str] = None


def _interpret_request(payload: BacktestInterpretRequest) -> Dict[str, Any]:
    if (payload.entry_text and payload.entry_text.strip()) or (payload.exit_text and payload.exit_text.strip()):
        return interpret_strategy_parts(
            stock_id=payload.stock_id,
            start_date=payload.start_date.isoformat(),
            end_date=payload.end_date.isoformat(),
            entry_text=payload.entry_text or "",
            exit_text=payload.exit_text or "",
            stop_loss_pct=payload.stop_loss_pct,
            take_profit_pct=payload.take_profit_pct,
            initial_capital=payload.initial_capital,
        )
    return interpret_strategy_text(
        stock_id=payload.stock_id,
        start_date=payload.start_date.isoformat(),
        end_date=payload.end_date.isoformat(),
        strategy_text=payload.strategy_text or "",
        initial_capital=payload.initial_capital,
    )


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
    unsupported_conditions: List[str]
    ai_mapped_conditions: List[str]
    metrics: Dict[str, Any]
    equity_curve: List[EquityCurveItem]
    period_returns: Dict[str, List[PeriodReturnItem]]
    trades: List[TradeItem]
    latest_recommendation: LatestRecommendation
    warnings: List[str]


class BacktestAdviceRequest(BaseModel):
    stock_id: str
    strategy_text: str
    normalized_text: str
    metrics: Dict[str, Any]
    trades: List[Dict[str, Any]]
    latest_recommendation: Dict[str, Any]


class BacktestAdviceResponse(BaseModel):
    summary: str
    strengths: List[str]
    weaknesses: List[str]
    rewrite_suggestions: List[str]
    risk_notes: List[str]
    source: str


@router.get("/backtest/templates")
def get_backtest_templates():
    return BACKTEST_TEMPLATES


@router.get("/backtest/capabilities")
def get_backtest_capabilities():
    return BACKTEST_CAPABILITY_CATALOG


@router.post("/backtest/interpret")
def post_backtest_interpret(
    payload: BacktestInterpretRequest,
    user: User = Depends(require_user),
):
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=422, detail="start_date cannot be later than end_date")
    try:
        return _interpret_request(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/backtest/run", response_model=BacktestRunResponse)
def post_backtest_run(
    payload: BacktestInterpretRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=422, detail="start_date cannot be later than end_date")

    stock = db.get(StockMaster, payload.stock_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock not found: {payload.stock_id}")

    try:
        interpreted = _interpret_request(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if not interpreted["supported"]:
        unsupported = "、".join(interpreted["unsupported_conditions"]) or "unknown conditions"
        raise HTTPException(status_code=422, detail=f"Unsupported strategy conditions: {unsupported}")

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

    max_lookback = estimate_strategy_lookback_days(interpreted["strategy"])
    if len(prices) < 2:
        raise HTTPException(status_code=422, detail="At least 2 trading days are required for backtesting")

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
            high_price=price.high_price,
            low_price=price.low_price,
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

    warnings = list(interpreted["warnings"])
    warnings.extend(summarize_dataset_warnings(dataset, interpreted["strategy"]))
    if len(prices) < max_lookback:
        warnings.append(
            f"回測區間少於策略所需的 {max_lookback} 日 lookback，前段資料只會累積指標，不會立即觸發訊號。"
        )

    return {
        "supported": True,
        "normalized_text": interpreted["normalized_text"],
        "strategy": interpreted["strategy"],
        "unsupported_conditions": interpreted["unsupported_conditions"],
        "ai_mapped_conditions": interpreted.get("ai_mapped_conditions", []),
        "metrics": result["metrics"],
        "equity_curve": result["equity_curve"],
        "period_returns": result["period_returns"],
        "trades": result["trades"],
        "latest_recommendation": result["latest_recommendation"],
        "warnings": warnings,
    }


@router.post("/backtest/advice", response_model=BacktestAdviceResponse)
def post_backtest_advice(
    payload: BacktestAdviceRequest,
    user: User = Depends(require_user),
):
    return generate_backtest_advice(payload.model_dump())
