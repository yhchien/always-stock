"""
魚尾每日模擬交易（Shadow Portfolio）API。

公開唯讀 endpoint（比照 `routers/signals.py` 既有慣例：純 `Depends(get_db)`，
不需要登入）：
- GET /api/signals/shadow-portfolio                  目前 portfolio 摘要 + 持倉列表
- GET /api/signals/shadow-portfolio/actions           目前 PENDING 的訂單（下一交易日預計動作）
- GET /api/signals/shadow-portfolio/trades            逐筆已平倉交易（可排序/篩選 cycle）
- GET /api/signals/shadow-portfolio/trades/by-stock   依股票分組統計（次數/平均報酬排序）

只讀既有 shadow_* 表，不觸發任何策略運算（策略運算只在 `run_shadow_portfolio.py`／
`backfill_shadow_portfolio_replay.py` 這兩個背景 script 裡執行）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    DailyPrice,
    ShadowCompletedTrade,
    ShadowPortfolioDailySnapshot,
    ShadowPositionLot,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
)
from app.signals.shadow_portfolio import CYCLE_LENGTH_TRADING_DAYS, STRATEGY_VERSION, V1_STRATEGY_PARAMS

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
    cycle_number: int
    cycle_start_trade_date: Optional[date] = None
    cycle_length_trading_days: int
    cycle_trading_days_elapsed: Optional[int] = None


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

    cycle_number = portfolio.cycle_number if portfolio else 1
    cycle_start_trade_date = portfolio.cycle_start_trade_date if portfolio else None
    cycle_trading_days_elapsed: Optional[int] = None
    if cycle_start_trade_date is not None and latest_snapshot is not None:
        cycle_trading_days_elapsed = (
            db.query(func.count(func.distinct(ShadowPortfolioDailySnapshot.trade_date)))
            .filter(
                ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
                ShadowPortfolioDailySnapshot.trade_date >= cycle_start_trade_date,
                ShadowPortfolioDailySnapshot.trade_date <= latest_snapshot.trade_date,
            )
            .scalar()
            or 0
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
        cycle_number=cycle_number,
        cycle_start_trade_date=cycle_start_trade_date,
        cycle_length_trading_days=CYCLE_LENGTH_TRADING_DAYS,
        cycle_trading_days_elapsed=cycle_trading_days_elapsed,
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


class ShadowCompletedTradeResponse(BaseModel):
    id: int
    cycle_number: int
    stock_id: str
    stock_name: str
    entry_type: str
    entry_signal_date: date
    entry_execution_date: date
    entry_price: float
    entry_day_index: Optional[int] = None
    entry_hit_count: Optional[int] = None
    entry_momentum: Optional[float] = None
    entry_p4_decision: Optional[str] = None
    entry_mark_to_market_return: Optional[float] = None
    exit_reason: str
    exit_signal_date: date
    exit_execution_date: date
    exit_price: float
    shares: float
    allocation: float
    realized_pnl: float
    realized_return_pct: float
    holding_days: int
    followed_by_rotation: bool


class ShadowCompletedTradesResponse(BaseModel):
    strategy_version: str
    trades: List[ShadowCompletedTradeResponse]


TradeSortBy = Literal["return_desc", "return_asc", "entry_date_desc", "entry_date_asc"]


@router.get("/trades", response_model=ShadowCompletedTradesResponse)
def get_shadow_completed_trades(
    strategy_version: str = STRATEGY_VERSION,
    cycle_number: Optional[int] = Query(default=None, description="只看特定循環；不帶則回全部歷史循環"),
    sort_by: TradeSortBy = Query(default="entry_date_desc"),
    db: Session = Depends(get_db),
) -> ShadowCompletedTradesResponse:
    """逐筆已平倉交易——`ShadowCompletedTrade` 是永久保存的紀錄，不受 35 交易日
    循環強制重置影響，`cycle_number` 只是篩選條件，不篩選就回傳這個策略版本
    有史以來全部循環的交易。"""
    query = db.query(ShadowCompletedTrade).filter(ShadowCompletedTrade.strategy_version == strategy_version)
    if cycle_number is not None:
        query = query.filter(ShadowCompletedTrade.cycle_number == cycle_number)

    if sort_by == "return_desc":
        query = query.order_by(ShadowCompletedTrade.realized_return_pct.desc())
    elif sort_by == "return_asc":
        query = query.order_by(ShadowCompletedTrade.realized_return_pct.asc())
    elif sort_by == "entry_date_asc":
        query = query.order_by(ShadowCompletedTrade.entry_execution_date.asc())
    else:
        query = query.order_by(ShadowCompletedTrade.entry_execution_date.desc())

    trades = query.all()
    return ShadowCompletedTradesResponse(
        strategy_version=strategy_version,
        trades=[
            ShadowCompletedTradeResponse(
                id=t.id, cycle_number=t.cycle_number, stock_id=t.stock_id, stock_name=t.stock_name,
                entry_type=t.entry_type, entry_signal_date=t.entry_signal_date,
                entry_execution_date=t.entry_execution_date, entry_price=t.entry_price,
                entry_day_index=t.entry_day_index, entry_hit_count=t.entry_hit_count,
                entry_momentum=t.entry_momentum, entry_p4_decision=t.entry_p4_decision,
                entry_mark_to_market_return=t.entry_mark_to_market_return,
                exit_reason=t.exit_reason, exit_signal_date=t.exit_signal_date,
                exit_execution_date=t.exit_execution_date, exit_price=t.exit_price,
                shares=t.shares, allocation=t.allocation, realized_pnl=t.realized_pnl,
                realized_return_pct=t.realized_return_pct, holding_days=t.holding_days,
                followed_by_rotation=t.followed_by_rotation,
            )
            for t in trades
        ],
    )


class ShadowStockTradeStat(BaseModel):
    stock_id: str
    stock_name: str
    trade_count: int
    total_realized_pnl: float
    avg_return_pct: float
    win_rate_pct: float


class ShadowStockTradeStatsResponse(BaseModel):
    strategy_version: str
    stats: List[ShadowStockTradeStat]


StockStatSortBy = Literal["trade_count_desc", "avg_return_desc", "avg_return_asc", "total_pnl_desc"]


@router.get("/trades/by-stock", response_model=ShadowStockTradeStatsResponse)
def get_shadow_trades_by_stock(
    strategy_version: str = STRATEGY_VERSION,
    cycle_number: Optional[int] = Query(default=None, description="只看特定循環；不帶則統計全部歷史循環"),
    sort_by: StockStatSortBy = Query(default="trade_count_desc"),
    db: Session = Depends(get_db),
) -> ShadowStockTradeStatsResponse:
    """依股票分組統計——滿足「同一張股票操作次數排名」需求：`trade_count` 是這檔
    股票被完整買賣過幾次（entry+exit 算一次，同一天平掉 2 個 lot 算 2 次，跟
    `/trades` 逐筆列表的計數口徑一致）。"""
    wins = func.sum(case((ShadowCompletedTrade.realized_pnl > 0, 1), else_=0))
    query = (
        db.query(
            ShadowCompletedTrade.stock_id,
            func.max(ShadowCompletedTrade.stock_name).label("stock_name"),
            func.count(ShadowCompletedTrade.id).label("trade_count"),
            func.sum(ShadowCompletedTrade.realized_pnl).label("total_realized_pnl"),
            func.avg(ShadowCompletedTrade.realized_return_pct).label("avg_return_pct"),
            wins.label("wins"),
        )
        .filter(ShadowCompletedTrade.strategy_version == strategy_version)
        .group_by(ShadowCompletedTrade.stock_id)
    )
    if cycle_number is not None:
        query = query.filter(ShadowCompletedTrade.cycle_number == cycle_number)

    rows = query.all()
    stats = [
        ShadowStockTradeStat(
            stock_id=row.stock_id,
            stock_name=row.stock_name,
            trade_count=row.trade_count,
            total_realized_pnl=row.total_realized_pnl or 0.0,
            avg_return_pct=row.avg_return_pct or 0.0,
            win_rate_pct=((row.wins or 0) / row.trade_count * 100.0) if row.trade_count else 0.0,
        )
        for row in rows
    ]

    if sort_by == "avg_return_desc":
        stats.sort(key=lambda s: s.avg_return_pct, reverse=True)
    elif sort_by == "avg_return_asc":
        stats.sort(key=lambda s: s.avg_return_pct)
    elif sort_by == "total_pnl_desc":
        stats.sort(key=lambda s: s.total_realized_pnl, reverse=True)
    else:
        stats.sort(key=lambda s: s.trade_count, reverse=True)

    return ShadowStockTradeStatsResponse(strategy_version=strategy_version, stats=stats)
