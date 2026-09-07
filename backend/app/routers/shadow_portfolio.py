"""
魚尾每日模擬交易（Shadow Portfolio）Phase 1 API。

兩個公開唯讀 endpoint（比照 `routers/signals.py` 既有慣例：純 `Depends(get_db)`，
不需要登入）：
- GET /api/signals/shadow-portfolio          目前 portfolio 摘要 + 持倉列表
- GET /api/signals/shadow-portfolio/actions  目前 PENDING 的訂單（下一交易日預計動作）

只讀既有 shadow_* 表，不觸發任何策略運算（策略運算只在 `run_shadow_portfolio.py`／
`backfill_shadow_portfolio_replay.py` 這兩個背景 script 裡執行）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    DailyPrice,
    ShadowPortfolioDailySnapshot,
    ShadowPositionLot,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
)
from app.signals.shadow_portfolio import STRATEGY_VERSION, V1_STRATEGY_PARAMS

router = APIRouter(prefix="/signals/shadow-portfolio", tags=["signals"])


class ShadowPositionResponse(BaseModel):
    stock_id: str
    stock_name: str
    first_seen_date: date
    units: int
    total_shares: float
    average_entry_price: float
    latest_close: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_return_pct: Optional[float] = None


class ShadowPortfolioResponse(BaseModel):
    strategy_version: str
    initial_capital: float
    cash: float
    realized_pnl_cumulative: float
    invested_cost: Optional[float] = None
    market_value: Optional[float] = None
    total_equity: Optional[float] = None
    total_return_pct: Optional[float] = None
    position_count: int
    total_units: int
    max_stocks: int
    max_units_per_stock: int
    max_total_units: int
    as_of_trade_date: Optional[date] = None
    updated_at: Optional[datetime] = None
    positions: List[ShadowPositionResponse]


class ShadowPendingActionResponse(BaseModel):
    id: int
    stock_id: str
    stock_name: str
    action: str
    signal_date: date
    scheduled_execution_date: date
    status: str
    reason: Optional[str] = None
    entry_pattern: Optional[str] = None
    units: int
    planned_amount: Optional[float] = None


class ShadowPendingActionsResponse(BaseModel):
    strategy_version: str
    actions: List[ShadowPendingActionResponse]


def _latest_close(db: Session, stock_id: str) -> Optional[float]:
    row = (
        db.query(DailyPrice.close_price)
        .filter(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )
    return float(row[0]) if row is not None and row[0] is not None else None


@router.get("", response_model=ShadowPortfolioResponse)
def get_shadow_portfolio(
    strategy_version: str = STRATEGY_VERSION,
    db: Session = Depends(get_db),
) -> ShadowPortfolioResponse:
    portfolio = (
        db.query(ShadowVirtualPortfolio)
        .filter(ShadowVirtualPortfolio.strategy_version == strategy_version)
        .first()
    )
    cash = portfolio.cash if portfolio else V1_STRATEGY_PARAMS["initial_capital"]
    realized_pnl = portfolio.realized_pnl_cumulative if portfolio else 0.0
    updated_at = portfolio.updated_at if portfolio else None

    latest_snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
        .order_by(ShadowPortfolioDailySnapshot.trade_date.desc())
        .first()
    )

    positions_rows = (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .order_by(ShadowVirtualPosition.stock_id.asc())
        .all()
    )
    positions: List[ShadowPositionResponse] = []
    for pos in positions_rows:
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == pos.id).all()
        total_shares = sum(lot.shares for lot in lots)
        total_cost = sum(lot.allocation for lot in lots)
        average_entry_price = total_cost / total_shares if total_shares else 0.0
        latest_close = _latest_close(db, pos.stock_id)
        market_value = latest_close * total_shares if latest_close is not None else None
        unrealized_pnl = market_value - total_cost if market_value is not None else None
        unrealized_return_pct = (
            (latest_close / average_entry_price - 1) * 100.0
            if latest_close is not None and average_entry_price
            else None
        )
        positions.append(
            ShadowPositionResponse(
                stock_id=pos.stock_id,
                stock_name=pos.stock_name,
                first_seen_date=pos.first_seen_date,
                units=len(lots),
                total_shares=total_shares,
                average_entry_price=average_entry_price,
                latest_close=latest_close,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_return_pct=unrealized_return_pct,
            )
        )

    return ShadowPortfolioResponse(
        strategy_version=strategy_version,
        initial_capital=V1_STRATEGY_PARAMS["initial_capital"],
        cash=cash,
        realized_pnl_cumulative=realized_pnl,
        invested_cost=latest_snapshot.invested_cost if latest_snapshot else None,
        market_value=latest_snapshot.market_value if latest_snapshot else None,
        total_equity=latest_snapshot.total_equity if latest_snapshot else cash,
        total_return_pct=latest_snapshot.total_return_pct if latest_snapshot else 0.0,
        position_count=len(positions),
        total_units=sum(p.units for p in positions),
        max_stocks=V1_STRATEGY_PARAMS["max_stocks"],
        max_units_per_stock=V1_STRATEGY_PARAMS["max_units_per_stock"],
        max_total_units=V1_STRATEGY_PARAMS["max_total_units"],
        as_of_trade_date=latest_snapshot.trade_date if latest_snapshot else None,
        updated_at=updated_at,
        positions=positions,
    )


@router.get("/actions", response_model=ShadowPendingActionsResponse)
def get_shadow_pending_actions(
    strategy_version: str = STRATEGY_VERSION,
    db: Session = Depends(get_db),
) -> ShadowPendingActionsResponse:
    """下一交易日預計動作——今晚看到的永遠代表「明天預計執行」，不是已成交。"""
    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == "PENDING",
        )
        .order_by(ShadowStrategyOrder.scheduled_execution_date.asc(), ShadowStrategyOrder.id.asc())
        .all()
    )
    return ShadowPendingActionsResponse(
        strategy_version=strategy_version,
        actions=[
            ShadowPendingActionResponse(
                id=o.id,
                stock_id=o.stock_id,
                stock_name=o.stock_name,
                action=o.action,
                signal_date=o.signal_date,
                scheduled_execution_date=o.scheduled_execution_date,
                status=o.status,
                reason=o.reason,
                entry_pattern=o.entry_pattern,
                units=o.units,
                planned_amount=o.planned_amount,
            )
            for o in orders
        ],
    )
