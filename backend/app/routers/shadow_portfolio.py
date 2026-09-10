"""
魚尾每日模擬交易（Shadow Portfolio）API。

公開唯讀 endpoint（比照 `routers/signals.py` 既有慣例：純 `Depends(get_db)`，
不需要登入）：
- GET /api/signals/shadow-portfolio                  目前 portfolio 摘要 + 持倉列表
- GET /api/signals/shadow-portfolio/actions           目前 PENDING 的訂單（下一交易日預計動作）
- GET /api/signals/shadow-portfolio/history          歷史回放逐交易日表現 + 當日交易
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
from app.signals.shadow_portfolio import STRATEGY_PARAMS_BY_VERSION, STRATEGY_VERSION

router = APIRouter(prefix="/signals/shadow-portfolio", tags=["signals"])


def _resolve_params(strategy_version: str) -> dict:
    """未知的 `strategy_version` fallback 回 v1 的參數（不是 404）——這個 API 從
    2026-04 就是公開唯讀 endpoint，前端舊版本可能還沒更新、或使用者手動改 URL
    query string 打錯字，靜默 fallback 比讓整個模擬交易頁掛掉更安全；`strategy_
    version` 欄位本身仍會照使用者傳入的原始字串回傳，不會偷偷改寫。"""
    return STRATEGY_PARAMS_BY_VERSION.get(strategy_version, STRATEGY_PARAMS_BY_VERSION[STRATEGY_VERSION])


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
    # None = 這個策略版本沒有這項上限（FORWARD_V1_202609：無 unit 上限、無曝險%上限則為 v1/Clean
    # 系列沒有這個概念）——前端要把 None 顯示成「無上限」，不能當成 0 或當成錯誤
    max_units_per_stock: Optional[int] = None
    max_total_units: Optional[int] = None
    max_position_exposure_pct: Optional[float] = None
    as_of_trade_date: Optional[date] = None
    updated_at: Optional[datetime] = None
    positions: List[ShadowPositionResponse]
    cycle_number: int
    cycle_start_trade_date: Optional[date] = None
    # None = 這個策略版本沒有強制循環重置概念（Clean Baselines / FORWARD_V1_202609）
    cycle_length_trading_days: Optional[int] = None
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


class ShadowHistoryOrderResponse(BaseModel):
    id: int
    action: str
    stock_id: str
    stock_name: str
    signal_date: date
    scheduled_execution_date: date
    status: str
    reason: Optional[str] = None
    entry_pattern: Optional[str] = None
    units: int
    planned_amount: Optional[float] = None
    execution_price: Optional[float] = None


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
    params = _resolve_params(strategy_version)
    portfolio = (
        db.query(ShadowVirtualPortfolio)
        .filter(ShadowVirtualPortfolio.strategy_version == strategy_version)
        .first()
    )
    cash = portfolio.cash if portfolio else params["initial_capital"]
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
        initial_capital=params["initial_capital"],
        cash=cash,
        realized_pnl_cumulative=realized_pnl,
        invested_cost=latest_snapshot.invested_cost if latest_snapshot else None,
        market_value=latest_snapshot.market_value if latest_snapshot else None,
        total_equity=latest_snapshot.total_equity if latest_snapshot else cash,
        total_return_pct=latest_snapshot.total_return_pct if latest_snapshot else 0.0,
        position_count=len(positions),
        total_units=sum(p.units for p in positions),
        max_stocks=params["max_stocks"],
        max_units_per_stock=params.get("max_units_per_stock"),
        max_total_units=params.get("max_total_units"),
        max_position_exposure_pct=params.get("max_position_exposure_pct"),
        as_of_trade_date=latest_snapshot.trade_date if latest_snapshot else None,
        updated_at=updated_at,
        positions=positions,
        cycle_number=cycle_number,
        cycle_start_trade_date=cycle_start_trade_date,
        cycle_length_trading_days=params.get("cycle_reset_trading_days"),
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


class ShadowHistoryDayResponse(BaseModel):
    trade_date: date
    cash: float
    invested_cost: float
    market_value: Optional[float] = None
    total_equity: float
    total_return_pct: float
    daily_return_pct: float
    realized_pnl: float
    unrealized_pnl: Optional[float] = None
    position_count: int
    total_units: int
    settlement_reset: bool = False
    settlement_cash: Optional[float] = None
    executed_orders: List[ShadowHistoryOrderResponse]
    completed_trades: List[ShadowCompletedTradeResponse]


class ShadowHistoryResponse(BaseModel):
    strategy_version: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    trading_day_count: int
    start_equity: Optional[float] = None
    end_equity: Optional[float] = None
    period_return_pct: Optional[float] = None
    completed_trade_count: int = 0
    winning_trade_count: int = 0
    win_rate_pct: Optional[float] = None
    settlement_cash: Optional[float] = None
    trading_days: List[ShadowHistoryDayResponse]


def _completed_trade_response(trade: ShadowCompletedTrade) -> ShadowCompletedTradeResponse:
    return ShadowCompletedTradeResponse(
        id=trade.id,
        cycle_number=trade.cycle_number,
        stock_id=trade.stock_id,
        stock_name=trade.stock_name,
        entry_type=trade.entry_type,
        entry_signal_date=trade.entry_signal_date,
        entry_execution_date=trade.entry_execution_date,
        entry_price=trade.entry_price,
        entry_day_index=trade.entry_day_index,
        entry_hit_count=trade.entry_hit_count,
        entry_momentum=trade.entry_momentum,
        entry_p4_decision=trade.entry_p4_decision,
        entry_mark_to_market_return=trade.entry_mark_to_market_return,
        exit_reason=trade.exit_reason,
        exit_signal_date=trade.exit_signal_date,
        exit_execution_date=trade.exit_execution_date,
        exit_price=trade.exit_price,
        shares=trade.shares,
        allocation=trade.allocation,
        realized_pnl=trade.realized_pnl,
        realized_return_pct=trade.realized_return_pct,
        holding_days=trade.holding_days,
        followed_by_rotation=trade.followed_by_rotation,
    )


@router.get("/history", response_model=ShadowHistoryResponse)
def get_shadow_history(
    strategy_version: str = STRATEGY_VERSION,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
) -> ShadowHistoryResponse:
    """Return a bounded historical replay grouped by trading day.

    If neither boundary is supplied, use the latest completed settlement interval
    for this strategy; before the first settlement, fall back to the latest 25
    available replay days. Explicit boundaries let the UI inspect any appended
    historical interval, while the endpoint deliberately reads immutable snapshots
    and executed orders instead of the current portfolio singleton.
    """
    snapshot_date_query = (
        db.query(ShadowPortfolioDailySnapshot.trade_date)
        .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
        .distinct()
    )
    if start_date is None and end_date is None:
        latest_settlement = (
            db.query(ShadowCompletedTrade.exit_execution_date)
            .filter(
                ShadowCompletedTrade.strategy_version == strategy_version,
                ShadowCompletedTrade.exit_reason == "PERIOD_END_SETTLEMENT",
            )
            .distinct()
            .order_by(ShadowCompletedTrade.exit_execution_date.desc())
            .first()
        )
        if latest_settlement is not None:
            end_date = latest_settlement[0]
            previous_settlement = (
                db.query(ShadowCompletedTrade.exit_execution_date)
                .filter(
                    ShadowCompletedTrade.strategy_version == strategy_version,
                    ShadowCompletedTrade.exit_reason == "PERIOD_END_SETTLEMENT",
                    ShadowCompletedTrade.exit_execution_date < end_date,
                )
                .distinct()
                .order_by(ShadowCompletedTrade.exit_execution_date.desc())
                .first()
            )
            period_snapshots = snapshot_date_query.filter(
                ShadowPortfolioDailySnapshot.trade_date <= end_date,
            )
            if previous_settlement is not None:
                period_snapshots = period_snapshots.filter(
                    ShadowPortfolioDailySnapshot.trade_date > previous_settlement[0],
                )
            first_period_snapshot = period_snapshots.order_by(
                ShadowPortfolioDailySnapshot.trade_date.asc(),
            ).first()
            start_date = first_period_snapshot[0] if first_period_snapshot is not None else end_date
        else:
            recent_dates = [
                row[0]
                for row in snapshot_date_query
                .order_by(ShadowPortfolioDailySnapshot.trade_date.desc())
                .limit(25)
                .all()
            ]
            if recent_dates:
                start_date = min(recent_dates)
                end_date = max(recent_dates)
    else:
        if start_date is None:
            earliest = snapshot_date_query.order_by(ShadowPortfolioDailySnapshot.trade_date.asc()).first()
            start_date = earliest[0] if earliest is not None else end_date
        if end_date is None:
            latest = snapshot_date_query.order_by(ShadowPortfolioDailySnapshot.trade_date.desc()).first()
            end_date = latest[0] if latest is not None else start_date

    # Keep a well-typed empty response when this strategy has no replay data yet.
    if start_date is None or end_date is None:
        return ShadowHistoryResponse(
            strategy_version=strategy_version,
            trading_day_count=0,
            trading_days=[],
        )

    if end_date < start_date:
        return ShadowHistoryResponse(
            strategy_version=strategy_version,
            start_date=start_date,
            end_date=end_date,
            trading_day_count=0,
            trading_days=[],
        )

    snapshots = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
            ShadowPortfolioDailySnapshot.trade_date >= start_date,
            ShadowPortfolioDailySnapshot.trade_date <= end_date,
        )
        .order_by(ShadowPortfolioDailySnapshot.trade_date.asc())
        .all()
    )
    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == "EXECUTED",
            ShadowStrategyOrder.scheduled_execution_date >= start_date,
            ShadowStrategyOrder.scheduled_execution_date <= end_date,
        )
        .order_by(ShadowStrategyOrder.scheduled_execution_date.asc(), ShadowStrategyOrder.id.asc())
        .all()
    )
    completed_trades = (
        db.query(ShadowCompletedTrade)
        .filter(
            ShadowCompletedTrade.strategy_version == strategy_version,
            ShadowCompletedTrade.exit_execution_date >= start_date,
            ShadowCompletedTrade.exit_execution_date <= end_date,
        )
        .order_by(ShadowCompletedTrade.exit_execution_date.asc(), ShadowCompletedTrade.id.asc())
        .all()
    )
    orders_by_date: dict[date, List[ShadowHistoryOrderResponse]] = {}
    for order in orders:
        orders_by_date.setdefault(order.scheduled_execution_date, []).append(
            ShadowHistoryOrderResponse(
                id=order.id,
                action=order.action,
                stock_id=order.stock_id,
                stock_name=order.stock_name,
                signal_date=order.signal_date,
                scheduled_execution_date=order.scheduled_execution_date,
                status=order.status,
                reason=order.reason,
                entry_pattern=order.entry_pattern,
                units=order.units,
                planned_amount=order.planned_amount,
                execution_price=order.execution_price,
            )
        )
    trades_by_date: dict[date, List[ShadowCompletedTradeResponse]] = {}
    for trade in completed_trades:
        trades_by_date.setdefault(trade.exit_execution_date, []).append(_completed_trade_response(trade))

    completed_trade_count = len(completed_trades)
    winning_trade_count = sum(1 for trade in completed_trades if float(trade.realized_return_pct) > 0)
    win_rate_pct = (
        winning_trade_count / completed_trade_count * 100.0 if completed_trade_count else None
    )
    settlement_dates = {
        trade.exit_execution_date
        for trade in completed_trades
        if trade.exit_reason == "PERIOD_END_SETTLEMENT"
    }
    params = _resolve_params(strategy_version)
    settlement_cash = float(params["initial_capital"]) if settlement_dates else None
    settlement_equity_by_date: dict[date, float] = {}
    settlement_realized_pnl_by_date: dict[date, float] = {}
    if settlement_dates:
        snapshots_by_date = {snapshot.trade_date: snapshot for snapshot in snapshots}
        for settlement_date in settlement_dates:
            snapshot = snapshots_by_date.get(settlement_date)
            if snapshot is None:
                continue
            settlement_pnl = sum(
                float(trade.realized_pnl)
                for trade in completed_trades
                if trade.exit_execution_date == settlement_date
                and trade.exit_reason == "PERIOD_END_SETTLEMENT"
            )
            # Snapshot 是期末結算前的收盤估值；把未實現損益換成實際以最低價
            # 平倉後的已實現損益，才是遵守 SELL 成交規則的期末權益。
            settlement_equity_by_date[settlement_date] = (
                float(snapshot.total_equity) - float(snapshot.unrealized_pnl or 0.0) + settlement_pnl
            )
            settlement_realized_pnl_by_date[settlement_date] = (
                float(snapshot.realized_pnl) + settlement_pnl
            )
    previous_equity = float(params["initial_capital"])
    days: List[ShadowHistoryDayResponse] = []
    for snapshot in snapshots:
        is_settlement_day = snapshot.trade_date in settlement_equity_by_date
        equity = settlement_equity_by_date.get(snapshot.trade_date, float(snapshot.total_equity))
        daily_return_pct = (equity / previous_equity - 1.0) * 100.0 if previous_equity else 0.0
        days.append(
            ShadowHistoryDayResponse(
                trade_date=snapshot.trade_date,
                cash=equity if is_settlement_day else snapshot.cash,
                invested_cost=0.0 if is_settlement_day else snapshot.invested_cost,
                market_value=0.0 if is_settlement_day else snapshot.market_value,
                total_equity=equity,
                total_return_pct=(equity / float(params["initial_capital"]) - 1.0) * 100.0
                if is_settlement_day
                else snapshot.total_return_pct,
                daily_return_pct=daily_return_pct,
                realized_pnl=(settlement_realized_pnl_by_date[snapshot.trade_date]
                              if is_settlement_day else snapshot.realized_pnl),
                unrealized_pnl=0.0 if is_settlement_day else snapshot.unrealized_pnl,
                position_count=0 if is_settlement_day else snapshot.position_count,
                total_units=0 if is_settlement_day else snapshot.total_units,
                settlement_reset=snapshot.trade_date in settlement_dates,
                settlement_cash=settlement_cash if snapshot.trade_date in settlement_dates else None,
                executed_orders=orders_by_date.get(snapshot.trade_date, []),
                completed_trades=trades_by_date.get(snapshot.trade_date, []),
            )
        )
        previous_equity = equity

    start_equity = days[0].total_equity if days else None
    end_equity = days[-1].total_equity if days else None
    period_return_pct = (
        (end_equity / float(params["initial_capital"]) - 1.0) * 100.0
        if end_equity is not None and params["initial_capital"]
        else None
    )
    return ShadowHistoryResponse(
        strategy_version=strategy_version,
        start_date=days[0].trade_date if days else start_date,
        end_date=days[-1].trade_date if days else end_date,
        trading_day_count=len(days),
        start_equity=start_equity,
        end_equity=end_equity,
        period_return_pct=period_return_pct,
        completed_trade_count=completed_trade_count,
        winning_trade_count=winning_trade_count,
        win_rate_pct=win_rate_pct,
        settlement_cash=settlement_cash,
        trading_days=days,
    )


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
