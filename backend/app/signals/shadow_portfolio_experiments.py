"""Test A / Test B：Winner Management + Portfolio Rotation 假說驗證引擎。

**跟 `shadow_portfolio.py` 的 `v1_frozen` 完全獨立**——這裡是純記憶體回測引擎，
**不寫入任何 Shadow Portfolio DB 表**（`ShadowVirtualPortfolio`／`ShadowVirtualPosition`／
`ShadowStrategyOrder`／`ShadowCompletedTrade` 一律不動），零風險影響 v1_frozen 的歷史紀錄。
資料來源沿用 `shadow_portfolio.py` 已修好的證據建構函式（`build_daily_evidence`／
`resolve_fishtail_universe`／`generate_entry_signal`／`compute_entry_score`），唯讀查詢
production DB，跟沙盒 `fishtail_backtest/` 當初驗證 v1 時的定位完全相同：先在獨立環境驗證
假說，證明有效才考慮移植成正式的 `strategy_version`。

## 核心假說
固定 +10% 全部停利可能過早賣掉真正的大贏家。

## 三組比較
- **v1_frozen**：現有正式策略（+10% 固定全部停利），數字直接讀 `ShadowCompletedTrade`
  （`strategy_version="v1_frozen"`）既有紀錄，不重新模擬。
- **TEST_A_HOLD_FOREVER**：+10% 完全不賣，只剩三種硬出場（真實停損 -8%／P4_STOP／
  官方平倉日）；無 Winner Management、無 Rotation。
- **TEST_B_ROTATION**：+10% 進入 Winner Management（hold_score／weakening／rotation／
  capital allocation 決定是否賣、部分賣、換股）。

## Entry 邏輯完全沿用 v1（不修改）
`generate_entry_signal`／`compute_entry_score`／`V1_STRATEGY_PARAMS`（`setup_a`／
`setup_b`）直接從 `shadow_portfolio.py` import，一個字元都沒改。

## Portfolio 限制（Test A／Test B 共用，跟 v1 不同）
- 拿掉 `MAX_TOTAL_UNITS`／`MAX_UNITS_PER_STOCK`——60 萬現金本身就是總投入上限，同一檔
  股票可以持有超過 2 個單位
- 新增 `MAX_POSITION_EXPOSURE_PCT`（預設 0.50）：單一股票最多使用目前 Portfolio Equity
  的 50%，依「目前 equity」動態計算，不是寫死本金的 50%
- 不要求滿倉：沒有夠好的候選就保留現金
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DailyPrice, ShadowCompletedTrade
from app.signals.shadow_portfolio import (
    EvidenceRow,
    V1_STRATEGY_PARAMS,
    build_daily_evidence,
    compute_entry_score,
    filter_out_etfs,
    generate_entry_signal,
    resolve_fishtail_universe,
)

MODE_TEST_A = "TEST_A_HOLD_FOREVER"
MODE_TEST_B = "TEST_B_ROTATION"

ACTION_BUY = "BUY"
ACTION_ADD = "ADD"
ACTION_SELL = "SELL"
ACTION_PARTIAL_SELL = "PARTIAL_SELL"

EXIT_REASON_STOP_LOSS = "POSITION_STOP_LOSS"
EXIT_REASON_P4_STOP = "P4_STOP"
EXIT_REASON_OFFICIAL_EXIT = "OFFICIAL_EXIT"
EXIT_REASON_WINNER_WEAKENING = "WINNER_WEAKENING"
EXIT_REASON_ROTATION = "PORTFOLIO_ROTATION"

WINNER_STATE_NONE = "NONE"  # 尚未觸發 +10%，還不是 Winner
WINNER_STATE_STRONG = "WINNER_STRONG"
WINNER_STATE_HEALTHY = "WINNER_HEALTHY"
WINNER_STATE_WEAKENING = "WINNER_WEAKENING"
WINNER_STATE_ROTATION_ELIGIBLE = "ROTATION_ELIGIBLE"

# hold_score 理論範圍（P4:0~3 + momentum水準:0~3 + momentum趨勢:-2~2 + P3重選:0~2 +
# tracking趨勢:-2~2 + peak/drawdown:-3~2 = -7~14）；normalize 用這個範圍線性映射到 0~10。
_HOLD_SCORE_MIN = -7.0
_HOLD_SCORE_MAX = 14.0
# entry_score 理論範圍（day_bonus 0~3 + p3_bonus 0~1 + type_bonus 1~2 + momentum_bonus
# 0~2 + pullback_bonus 0~2 = 1~10），本來就落在 0~10，normalize 只是 clamp。
_ENTRY_SCORE_MIN = 0.0
_ENTRY_SCORE_MAX = 10.0


@dataclass(frozen=True)
class ExperimentConfig:
    initial_capital: float = 600_000.0
    unit_capital: float = 100_000.0
    max_stocks: int = 5
    max_position_exposure_pct: float = 0.50
    rotation_min_edge: float = 1.5
    real_stop_loss_pct: float = -0.08
    winner_trigger_pct: float = 0.10
    winner_weakening_confirm_days: int = 2


# ---------------------------------------------------------------------------
# Pure scoring functions
# ---------------------------------------------------------------------------
def normalize_entry_score(entry_score: float) -> float:
    return max(_ENTRY_SCORE_MIN, min(_ENTRY_SCORE_MAX, entry_score))


def normalize_hold_score(hold_score: float) -> float:
    span = _HOLD_SCORE_MAX - _HOLD_SCORE_MIN
    normalized = (hold_score - _HOLD_SCORE_MIN) / span * 10.0
    return max(0.0, min(10.0, normalized))


def compute_hold_score(
    evidence: EvidenceRow,
    *,
    previous_momentum_score: Optional[float],
    previous_tracking_return: Optional[float],
    drawdown_from_peak: Optional[float],
) -> float:
    """§11：衡量「這筆資金繼續留在這檔股票的價值」，不是拿 P&L 高低直接當分數。"""
    score = 0.0

    if evidence.p4_decision == "CONTINUE":
        score += 3.0
    elif evidence.p4_decision == "CAUTION":
        score += 1.0

    m = evidence.momentum_score
    if m is not None:
        if m >= 85:
            score += 3.0
        elif m >= 75:
            score += 2.0
        elif m >= 65:
            score += 1.0

    if m is not None and previous_momentum_score is not None:
        change = m - previous_momentum_score
        if change >= 0:
            score += 2.0
        elif change >= -5:
            score += 1.0
        elif change <= -10:
            score -= 2.0

    if evidence.p3_selected_today:
        score += 2.0

    if evidence.mark_to_market_return_pct is not None and previous_tracking_return is not None:
        change = evidence.mark_to_market_return_pct - previous_tracking_return
        if change > 0:
            score += 2.0
        elif change < -5:
            score -= 2.0

    if drawdown_from_peak is not None:
        if drawdown_from_peak >= -0.03:
            score += 2.0
        elif drawdown_from_peak <= -0.10:
            score -= 3.0
        # -0.06 <= drawdown < -0.03：+0（§12 baseline 明文的中段值）

    return score


def is_winner_weakening_triggered(
    *,
    drawdown_from_peak: Optional[float],
    momentum_change: Optional[float],
    tracking_return_change: Optional[float],
    p4_decision: Optional[str],
    previous_p4_decision: Optional[str],
) -> bool:
    """§22：至少 2 個條件成立才算 WINNER_WEAKENING（單日判定，連續 2 天才真正出場見
    §23，由呼叫端的 weakening_streak 處理）。"""
    hits = 0
    if drawdown_from_peak is not None and drawdown_from_peak <= -0.08:
        hits += 1
    if momentum_change is not None and momentum_change <= -10:
        hits += 1
    if tracking_return_change is not None and tracking_return_change <= -5:
        hits += 1
    if p4_decision == "CAUTION" and previous_p4_decision == "CAUTION":
        hits += 1
    return hits >= 2


def classify_winner_state(*, is_weakening: bool, hold_score: float) -> str:
    if is_weakening:
        return WINNER_STATE_WEAKENING
    if hold_score >= 7:
        return WINNER_STATE_STRONG
    if hold_score >= 3:
        return WINNER_STATE_HEALTHY
    return WINNER_STATE_ROTATION_ELIGIBLE


# ---------------------------------------------------------------------------
# In-memory portfolio state
# ---------------------------------------------------------------------------
@dataclass
class Lot:
    lot_id: int
    add_number: int  # 0 = 首次 BUY，1 = ADD #1，2 = ADD #2...
    entry_type: str
    entry_signal_date: date
    entry_execution_date: date
    entry_price: float
    shares: float
    allocation: float


@dataclass
class Position:
    stock_id: str
    stock_name: str
    first_seen_date: date
    lots: List[Lot] = field(default_factory=list)

    highest_close_since_entry: float = 0.0
    highest_position_return: float = 0.0
    previous_momentum_score: Optional[float] = None
    previous_tracking_return: Optional[float] = None
    previous_p4_decision: Optional[str] = None

    entered_winner_management: bool = False
    entered_winner_management_date: Optional[date] = None
    return_when_entered_winner: Optional[float] = None
    max_position_return_after_winner: Optional[float] = None
    weakening_streak: int = 0
    winner_state: str = WINNER_STATE_NONE
    add_count_after_winner: int = 0

    # 當天算好的 hold_score／position_priority（§13/§17 rotation 比較用，Step 1 寫入）
    last_hold_score: Optional[float] = None
    last_priority: Optional[float] = None

    @property
    def total_shares(self) -> float:
        return sum(lot.shares for lot in self.lots)

    @property
    def total_cost(self) -> float:
        return sum(lot.allocation for lot in self.lots)

    @property
    def units(self) -> int:
        return len(self.lots)

    @property
    def average_entry_price(self) -> Optional[float]:
        shares = self.total_shares
        return self.total_cost / shares if shares > 0 else None


@dataclass
class TradeRecord:
    """對應 §37 trading log 的一列（BUY/ADD/PARTIAL_SELL/SELL 皆用這個結構）。"""
    signal_date: date
    execution_date: date
    action: str
    stock_id: str
    stock_name: str
    units: float  # SELL/PARTIAL_SELL 用 shares 數量表示（非整數 unit，因為可 partial）
    price: float
    amount: float
    actual_average_cost_before: Optional[float]
    actual_position_return_before: Optional[float]
    entry_score: Optional[float]
    hold_score: Optional[float]
    allocation_priority: Optional[float]
    add_number: Optional[int]
    reason: str
    rotation_id: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_return_pct: Optional[float] = None
    holding_days: Optional[int] = None


@dataclass
class WinnerLogRow:
    """§38 winner_management_log.csv 一列。"""
    trade_date: date
    stock_id: str
    stock_name: str
    actual_position_return: float
    highest_position_return: float
    drawdown_from_peak: Optional[float]
    momentum_score: Optional[float]
    previous_momentum_score: Optional[float]
    momentum_change: Optional[float]
    tracking_return: Optional[float]
    tracking_return_change: Optional[float]
    p3_selected_today: bool
    p4_decision: Optional[str]
    hold_score: float
    winner_state: str
    action: str


@dataclass
class RotationLogRow:
    """§39 rotation_log.csv 一列。"""
    signal_date: date
    from_stock: str
    to_stock: str
    from_units_before: int
    from_shares_sold: float
    from_priority: float
    to_priority: float
    priority_edge: float
    full_or_partial: str
    execution_date: Optional[date] = None
    sell_execution_price: Optional[float] = None
    buy_execution_price: Optional[float] = None


@dataclass
class DailySnapshot:
    trade_date: date
    cash: float
    invested_cost: float
    market_value: float
    total_equity: float
    total_return_pct: float
    position_count: int
    total_units: int
    max_single_exposure_pct: float


@dataclass
class BacktestResult:
    mode: str
    daily_snapshots: List[DailySnapshot]
    trades: List[TradeRecord]
    completed: List[TradeRecord]  # 只含平倉列（有 realized_pnl）
    winner_log: List[WinnerLogRow]
    rotation_log: List[RotationLogRow]
    open_positions: Dict[str, Position]
    final_cash: float
    skipped_events: List[Tuple[date, str, str]]


# ---------------------------------------------------------------------------
# Pending order queue (T 日訊號、T+1 成交)
# ---------------------------------------------------------------------------
@dataclass
class _PendingOrder:
    action: str
    stock_id: str
    stock_name: str
    signal_date: date
    entry_type: Optional[str] = None
    entry_score: Optional[float] = None
    allocation_priority: Optional[float] = None
    shares_to_sell: Optional[float] = None  # PARTIAL_SELL 用；None = 全賣
    reason: str = ""
    rotation_id: Optional[str] = None
    add_number: Optional[int] = None
    hold_score: Optional[float] = None
    amount: Optional[float] = None  # BUY/ADD 用，None = 一個 unit_capital


class _PriceCache:
    """一次把回測範圍內所有 daily_price 撈進記憶體，避免逐股票逐日重複打 DB（跟
    `shadow_portfolio._SNAPSHOT_WATCHLIST_CACHE` 同樣的效能考量）。"""

    def __init__(self, db: Session, start: date, end: date):
        rows = (
            db.query(DailyPrice)
            .filter(DailyPrice.trade_date >= start, DailyPrice.trade_date <= end)
            .all()
        )
        self._by_stock_date: Dict[Tuple[str, date], DailyPrice] = {
            (r.stock_id, r.trade_date): r for r in rows
        }
        self.calendar: List[date] = sorted({r.trade_date for r in rows})

    def row(self, stock_id: str, d: date) -> Optional[DailyPrice]:
        return self._by_stock_date.get((stock_id, d))

    def close(self, stock_id: str, d: date) -> Optional[float]:
        r = self.row(stock_id, d)
        return float(r.close_price) if r is not None and r.close_price is not None else None

    def high(self, stock_id: str, d: date) -> Optional[float]:
        r = self.row(stock_id, d)
        return float(r.high_price) if r is not None and r.high_price is not None else None

    def low(self, stock_id: str, d: date) -> Optional[float]:
        r = self.row(stock_id, d)
        return float(r.low_price) if r is not None and r.low_price is not None else None

    def next_trading_day(self, d: date) -> Optional[date]:
        for cd in self.calendar:
            if cd > d:
                return cd
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class ExperimentEngine:
    def __init__(self, db: Session, *, mode: str, config: Optional[ExperimentConfig] = None):
        if mode not in (MODE_TEST_A, MODE_TEST_B):
            raise ValueError(f"unknown mode: {mode}")
        self.db = db
        self.mode = mode
        self.config = config or ExperimentConfig()
        self.cash = self.config.initial_capital
        self.positions: Dict[str, Position] = {}
        self._lot_seq = 0
        self._rotation_seq = 0
        self.trades: List[TradeRecord] = []
        self.completed: List[TradeRecord] = []
        self.winner_log: List[WinnerLogRow] = []
        self.rotation_log: List[RotationLogRow] = []
        self.daily_snapshots: List[DailySnapshot] = []
        self.skipped_events: List[Tuple[date, str, str]] = []
        self._pending: List[_PendingOrder] = []

    # -- helpers -------------------------------------------------------
    def _equity(self, price_cache: _PriceCache, as_of: date) -> float:
        market_value = 0.0
        for pos in self.positions.values():
            close = price_cache.close(pos.stock_id, as_of)
            market_value += pos.total_shares * close if close is not None else pos.total_cost
        return self.cash + market_value

    def _next_lot_id(self) -> int:
        self._lot_seq += 1
        return self._lot_seq

    def _next_rotation_id(self) -> str:
        self._rotation_seq += 1
        return f"ROT-{self._rotation_seq}"

    # -- orchestrator ----------------------------------------------------
    def run(self, *, start: date, end: date) -> BacktestResult:
        price_cache = _PriceCache(self.db, start, end + timedelta(days=10))
        calendar = [d for d in price_cache.calendar if start <= d <= end]

        for target_date in calendar:
            self._execute_pending(price_cache, target_date)
            self._decide_today(price_cache, target_date)

            eq = self._equity(price_cache, target_date)
            invested_cost = sum(p.total_cost for p in self.positions.values())
            market_value = eq - self.cash
            max_exposure_pct = 0.0
            for p in self.positions.values():
                close = price_cache.close(p.stock_id, target_date)
                value = p.total_shares * close if close is not None else p.total_cost
                if eq > 0:
                    max_exposure_pct = max(max_exposure_pct, value / eq)
            self.daily_snapshots.append(
                DailySnapshot(
                    trade_date=target_date,
                    cash=self.cash,
                    invested_cost=invested_cost,
                    market_value=market_value,
                    total_equity=eq,
                    total_return_pct=(eq - self.config.initial_capital) / self.config.initial_capital * 100.0,
                    position_count=len(self.positions),
                    total_units=sum(p.units for p in self.positions.values()),
                    max_single_exposure_pct=max_exposure_pct * 100.0,
                )
            )

        return BacktestResult(
            mode=self.mode,
            daily_snapshots=self.daily_snapshots,
            trades=self.trades,
            completed=self.completed,
            winner_log=self.winner_log,
            rotation_log=self.rotation_log,
            open_positions=dict(self.positions),
            final_cash=self.cash,
            skipped_events=self.skipped_events,
        )

    # -- Phase A: execute yesterday's pending orders using today's H/L ---
    def _execute_pending(self, price_cache: _PriceCache, target_date: date) -> None:
        if not self._pending:
            return
        remaining: List[_PendingOrder] = []
        # SELL/PARTIAL_SELL 先於 BUY/ADD，讓賣出釋放的現金當天可用（§29/沿用 v1 慣例）
        ordered = sorted(self._pending, key=lambda o: 0 if o.action in (ACTION_SELL, ACTION_PARTIAL_SELL) else 1)
        for order in ordered:
            if order.action in (ACTION_SELL, ACTION_PARTIAL_SELL):
                low = price_cache.low(order.stock_id, target_date)
                if low is None:
                    self.skipped_events.append((target_date, order.stock_id, "NO_NEXT_DAY_SELL_PRICE"))
                    remaining.append(order)
                    continue
                self._fill_sell(order, price=low, execution_date=target_date)
            else:
                high = price_cache.high(order.stock_id, target_date)
                if high is None:
                    self.skipped_events.append((target_date, order.stock_id, "NO_NEXT_DAY_BUY_PRICE"))
                    remaining.append(order)
                    continue
                self._fill_buy(order, price=high, execution_date=target_date)
        self._pending = remaining

    def _fill_sell(self, order: _PendingOrder, *, price: float, execution_date: date) -> None:
        position = self.positions.get(order.stock_id)
        if position is None:
            return
        shares_to_sell = order.shares_to_sell if order.shares_to_sell is not None else position.total_shares
        remaining_lots: List[Lot] = []
        sold_allocation = 0.0
        sold_shares = 0.0
        sold_realized = 0.0
        earliest_entry_date = min((lot.entry_execution_date for lot in position.lots), default=execution_date)
        for lot in sorted(position.lots, key=lambda l: l.entry_execution_date):
            if sold_shares >= shares_to_sell - 1e-9:
                remaining_lots.append(lot)
                continue
            take = min(lot.shares, shares_to_sell - sold_shares)
            take_allocation = lot.allocation * (take / lot.shares) if lot.shares else 0.0
            proceeds = take * price
            sold_realized += proceeds - take_allocation
            sold_allocation += take_allocation
            sold_shares += take
            if take < lot.shares - 1e-9:
                # lot 部分賣出：剩餘份額變成新的殘餘 lot（比例縮減 allocation/shares）
                remaining_lots.append(
                    Lot(
                        lot_id=lot.lot_id,
                        add_number=lot.add_number,
                        entry_type=lot.entry_type,
                        entry_signal_date=lot.entry_signal_date,
                        entry_execution_date=lot.entry_execution_date,
                        entry_price=lot.entry_price,
                        shares=lot.shares - take,
                        allocation=lot.allocation - take_allocation,
                    )
                )
        self.cash += sold_shares * price
        holding_days = (execution_date - earliest_entry_date).days
        record = TradeRecord(
            signal_date=order.signal_date,
            execution_date=execution_date,
            action=ACTION_SELL if order.shares_to_sell is None else ACTION_PARTIAL_SELL,
            stock_id=order.stock_id,
            stock_name=order.stock_name,
            units=sold_shares,
            price=price,
            amount=sold_shares * price,
            actual_average_cost_before=position.average_entry_price,
            actual_position_return_before=(
                (price / position.average_entry_price - 1.0) if position.average_entry_price else None
            ),
            entry_score=None,
            hold_score=order.hold_score,
            allocation_priority=None,
            add_number=None,
            reason=order.reason,
            rotation_id=order.rotation_id,
            realized_pnl=sold_realized,
            realized_return_pct=(sold_realized / sold_allocation * 100.0) if sold_allocation else 0.0,
            holding_days=holding_days,
        )
        self.trades.append(record)
        self.completed.append(record)

        if remaining_lots:
            position.lots = remaining_lots
        else:
            del self.positions[order.stock_id]

    def _fill_buy(self, order: _PendingOrder, *, price: float, execution_date: date) -> None:
        amount = order.amount if order.amount is not None else self.config.unit_capital
        if amount > self.cash + 1e-6:
            self.skipped_events.append((execution_date, order.stock_id, "INSUFFICIENT_CASH_AT_EXECUTION"))
            return
        shares = amount / price
        self.cash -= amount

        position = self.positions.get(order.stock_id)
        cost_before = position.average_entry_price if position is not None else None
        return_before = (price / cost_before - 1.0) if cost_before else None
        if position is None:
            position = Position(stock_id=order.stock_id, stock_name=order.stock_name, first_seen_date=order.signal_date)
            self.positions[order.stock_id] = position
            position.highest_close_since_entry = price

        add_number = order.add_number if order.add_number is not None else len(position.lots)
        position.lots.append(
            Lot(
                lot_id=self._next_lot_id(),
                add_number=add_number,
                entry_type=order.entry_type or "UNKNOWN",
                entry_signal_date=order.signal_date,
                entry_execution_date=execution_date,
                entry_price=price,
                shares=shares,
                allocation=amount,
            )
        )
        if position.entered_winner_management:
            position.add_count_after_winner += 1

        self.trades.append(
            TradeRecord(
                signal_date=order.signal_date,
                execution_date=execution_date,
                action=ACTION_BUY if add_number == 0 else ACTION_ADD,
                stock_id=order.stock_id,
                stock_name=order.stock_name,
                units=shares,
                price=price,
                amount=amount,
                actual_average_cost_before=cost_before,
                actual_position_return_before=return_before,
                entry_score=order.entry_score,
                hold_score=None,
                allocation_priority=order.allocation_priority,
                add_number=add_number,
                reason=order.reason,
                rotation_id=order.rotation_id,
            )
        )

    # -- Phase B: decide today's actions ---------------------------------
    def _decide_today(self, price_cache: _PriceCache, target_date: date) -> None:
        cfg = self.config
        universe = filter_out_etfs(
            self.db,
            {
                **resolve_fishtail_universe(self.db, target_date=target_date),
                **{
                    sid: (sid, pos.stock_name, pos.first_seen_date)
                    for sid, pos in self.positions.items()
                },
            },
        )
        evidence_by_stock: Dict[str, EvidenceRow] = {
            stock_id: build_daily_evidence(
                self.db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date,
                target_date=target_date,
            )
            for stock_id, (stock_id_, stock_name, first_seen_date) in universe.items()
        }

        decided_exits: Dict[str, Tuple[str, Optional[float], Optional[float]]] = {}  # stock_id -> (reason, hold_score, shares_to_sell)
        equity_today = self._equity(price_cache, target_date)

        # ---- Step 1：Hard exits + Winner Management state update ----
        for stock_id, position in list(self.positions.items()):
            evidence = evidence_by_stock.get(stock_id)
            close = price_cache.close(stock_id, target_date)
            avg_cost = position.average_entry_price
            actual_return = (close / avg_cost - 1.0) if (close is not None and avg_cost) else None

            if actual_return is not None and actual_return <= cfg.real_stop_loss_pct:
                decided_exits[stock_id] = (EXIT_REASON_STOP_LOSS, None, None)
                continue
            if evidence is not None and evidence.p4_decision == "STOP_OBSERVING":
                decided_exits[stock_id] = (EXIT_REASON_P4_STOP, None, None)
                continue
            if evidence is not None and evidence.is_official_exit_signal_day:
                decided_exits[stock_id] = (EXIT_REASON_OFFICIAL_EXIT, None, None)
                continue

            if close is not None:
                position.highest_close_since_entry = max(position.highest_close_since_entry, close)
            if actual_return is not None:
                position.highest_position_return = max(position.highest_position_return, actual_return)
            drawdown = (
                (close / position.highest_close_since_entry - 1.0)
                if close is not None and position.highest_close_since_entry
                else None
            )

            momentum_change = (
                evidence.momentum_score - position.previous_momentum_score
                if evidence is not None
                and evidence.momentum_score is not None
                and position.previous_momentum_score is not None
                else None
            )
            tracking_change = (
                evidence.mark_to_market_return_pct - position.previous_tracking_return
                if evidence is not None
                and evidence.mark_to_market_return_pct is not None
                and position.previous_tracking_return is not None
                else None
            )

            if actual_return is not None and actual_return >= cfg.winner_trigger_pct and not position.entered_winner_management:
                position.entered_winner_management = True
                position.entered_winner_management_date = target_date
                position.return_when_entered_winner = actual_return

            action_today = "HOLD"
            if self.mode == MODE_TEST_B and evidence is not None:
                hold_score = compute_hold_score(
                    evidence,
                    previous_momentum_score=position.previous_momentum_score,
                    previous_tracking_return=position.previous_tracking_return,
                    drawdown_from_peak=drawdown,
                )
                position.last_hold_score = hold_score
                position.last_priority = normalize_hold_score(hold_score)
                weakening_today = position.entered_winner_management and is_winner_weakening_triggered(
                    drawdown_from_peak=drawdown,
                    momentum_change=momentum_change,
                    tracking_return_change=tracking_change,
                    p4_decision=evidence.p4_decision,
                    previous_p4_decision=position.previous_p4_decision,
                )
                if position.entered_winner_management:
                    if weakening_today:
                        position.weakening_streak += 1
                    else:
                        position.weakening_streak = 0
                    position.winner_state = classify_winner_state(
                        is_weakening=position.weakening_streak > 0, hold_score=hold_score
                    )
                    if position.weakening_streak >= cfg.winner_weakening_confirm_days:
                        decided_exits[stock_id] = (EXIT_REASON_WINNER_WEAKENING, hold_score, None)
                        action_today = "SELL_QUEUED"
                else:
                    position.winner_state = WINNER_STATE_NONE

                if stock_id not in decided_exits:
                    self.winner_log.append(
                        WinnerLogRow(
                            trade_date=target_date, stock_id=stock_id, stock_name=position.stock_name,
                            actual_position_return=(actual_return or 0.0) * 100.0,
                            highest_position_return=position.highest_position_return * 100.0,
                            drawdown_from_peak=(drawdown * 100.0) if drawdown is not None else None,
                            momentum_score=evidence.momentum_score,
                            previous_momentum_score=position.previous_momentum_score,
                            momentum_change=momentum_change,
                            tracking_return=evidence.mark_to_market_return_pct,
                            tracking_return_change=tracking_change,
                            p3_selected_today=evidence.p3_selected_today,
                            p4_decision=evidence.p4_decision,
                            hold_score=hold_score,
                            winner_state=position.winner_state,
                            action=action_today,
                        )
                    )

            if evidence is not None:
                position.previous_momentum_score = evidence.momentum_score if evidence.momentum_score is not None else position.previous_momentum_score
                position.previous_tracking_return = evidence.mark_to_market_return_pct if evidence.mark_to_market_return_pct is not None else position.previous_tracking_return
                position.previous_p4_decision = evidence.p4_decision

        # ---- Step 2：進場/加碼候選 ----
        candidates: List[Tuple[str, EvidenceRow, float, str]] = []  # (kind, evidence, entry_score, entry_type)
        for stock_id, evidence in evidence_by_stock.items():
            if stock_id in decided_exits:
                continue
            sig = generate_entry_signal(evidence, V1_STRATEGY_PARAMS)
            if sig is None:
                continue
            kind = ACTION_ADD if stock_id in self.positions else ACTION_BUY
            candidates.append((kind, evidence, sig.entry_score, sig.entry_type))

        # ---- Step 3：queue hard/weakening exits ----
        projected_cash = self.cash
        projected_stock_ids = {sid for sid in self.positions if sid not in decided_exits}
        for stock_id, (reason, hold_score, _shares) in decided_exits.items():
            position = self.positions[stock_id]
            self._pending.append(
                _PendingOrder(
                    action=ACTION_SELL, stock_id=stock_id, stock_name=position.stock_name,
                    signal_date=target_date, reason=reason, hold_score=hold_score,
                )
            )
            projected_cash += position.total_cost  # 保守估計，忽略未平倉損益

        # ---- Step 4：資金分配（NEW_ENTRY + ADD_EXISTING 共同排序）----
        if self.mode == MODE_TEST_A:
            # Test A 沒有 rotation：直接依 entry_score 排序，能買就買，買不下就跳過
            allocation_candidates = sorted(candidates, key=lambda c: -c[2])
            for kind, evidence, entry_score, entry_type in allocation_candidates:
                priority = normalize_entry_score(entry_score)
                already_held = evidence.stock_id in projected_stock_ids

                if already_held:
                    position = self.positions[evidence.stock_id]
                    projected_value = position.total_cost + self.config.unit_capital
                    if projected_value > equity_today * self.config.max_position_exposure_pct + 1e-6:
                        self.skipped_events.append((target_date, evidence.stock_id, "MAX_POSITION_EXPOSURE_EXCEEDED"))
                        continue
                    if projected_cash < self.config.unit_capital:
                        self.skipped_events.append((target_date, evidence.stock_id, "SKIPPED_CAPACITY_NO_CASH"))
                        continue
                    self._queue_buy(evidence, entry_type, entry_score, priority, target_date, add_number=position.units)
                    projected_cash -= self.config.unit_capital
                    continue

                has_slot = len(projected_stock_ids) < self.config.max_stocks
                has_cash = projected_cash >= self.config.unit_capital
                if has_slot and has_cash:
                    self._queue_buy(evidence, entry_type, entry_score, priority, target_date, add_number=0)
                    projected_cash -= self.config.unit_capital
                    projected_stock_ids.add(evidence.stock_id)
                else:
                    self.skipped_events.append((target_date, evidence.stock_id, "SKIPPED_CAPACITY"))
            return

        # Test B：NEW_ENTRY / ADD_EXISTING 共同排序 + rotation
        allocation_candidates = sorted(candidates, key=lambda c: -c[2])
        for kind, evidence, entry_score, entry_type in allocation_candidates:
            priority = normalize_entry_score(entry_score)
            already_held = evidence.stock_id in projected_stock_ids

            if already_held:
                position = self.positions[evidence.stock_id]
                projected_value = position.total_cost + self.config.unit_capital
                if projected_value > equity_today * self.config.max_position_exposure_pct + 1e-6:
                    self.skipped_events.append((target_date, evidence.stock_id, "MAX_POSITION_EXPOSURE_EXCEEDED"))
                    continue
                if projected_cash < self.config.unit_capital:
                    self.skipped_events.append((target_date, evidence.stock_id, "SKIPPED_CAPACITY_NO_CASH"))
                    continue
                self._queue_buy(evidence, entry_type, entry_score, priority, target_date, add_number=position.units)
                projected_cash -= self.config.unit_capital
                continue

            has_slot = len(projected_stock_ids) < self.config.max_stocks
            has_cash = projected_cash >= self.config.unit_capital
            if has_slot and has_cash:
                self._queue_buy(evidence, entry_type, entry_score, priority, target_date, add_number=0)
                projected_cash -= self.config.unit_capital
                projected_stock_ids.add(evidence.stock_id)
                continue

            # ---- Rotation check（§16~21）----
            rotation_eligible = [
                sid for sid in projected_stock_ids
                if sid in self.positions and sid not in decided_exits
                and not any(o.stock_id == sid and o.action in (ACTION_SELL, ACTION_PARTIAL_SELL) for o in self._pending)
            ]
            if not rotation_eligible:
                self.skipped_events.append((target_date, evidence.stock_id, "NO_ROTATION_CANDIDATE"))
                continue

            weakest_id, weakest_priority = self._weakest_position(rotation_eligible, evidence_by_stock)
            if weakest_id is None:
                self.skipped_events.append((target_date, evidence.stock_id, "NO_ROTATION_CANDIDATE"))
                continue
            if priority < weakest_priority + self.config.rotation_min_edge:
                self.skipped_events.append((target_date, evidence.stock_id, "ROTATION_EDGE_NOT_MET"))
                continue

            weakest_position = self.positions[weakest_id]
            full_or_partial = "FULL" if (weakest_position.units <= 1 or not has_slot) else "PARTIAL"
            # stocks == 5 一定要完整釋放（§20 Case B）；stocks < 5 才允許 partial（Case A）
            if len(projected_stock_ids) >= self.config.max_stocks:
                full_or_partial = "FULL"
            rotation_id = self._next_rotation_id()
            shares_to_sell = None if full_or_partial == "FULL" else weakest_position.total_shares / weakest_position.units
            self._pending.append(
                _PendingOrder(
                    action=ACTION_SELL if full_or_partial == "FULL" else ACTION_PARTIAL_SELL,
                    stock_id=weakest_id, stock_name=weakest_position.stock_name,
                    signal_date=target_date, reason=EXIT_REASON_ROTATION, rotation_id=rotation_id,
                    shares_to_sell=shares_to_sell,
                )
            )
            self._queue_buy(
                evidence, entry_type, entry_score, priority, target_date, add_number=0, rotation_id=rotation_id,
            )
            self.rotation_log.append(
                RotationLogRow(
                    signal_date=target_date, from_stock=weakest_id, to_stock=evidence.stock_id,
                    from_units_before=weakest_position.units,
                    from_shares_sold=shares_to_sell if shares_to_sell is not None else weakest_position.total_shares,
                    from_priority=weakest_priority, to_priority=priority,
                    priority_edge=priority - weakest_priority, full_or_partial=full_or_partial,
                )
            )
            if full_or_partial == "FULL":
                projected_stock_ids.discard(weakest_id)
                projected_stock_ids.add(evidence.stock_id)
            projected_cash -= self.config.unit_capital

    def _queue_buy(
        self, evidence, entry_type, entry_score, priority, target_date, *, add_number, rotation_id=None,
    ) -> None:
        self._pending.append(
            _PendingOrder(
                action=ACTION_BUY if add_number == 0 else ACTION_ADD,
                stock_id=evidence.stock_id, stock_name=evidence.stock_name, signal_date=target_date,
                entry_type=entry_type, entry_score=entry_score, allocation_priority=priority,
                reason=f"entry_score={entry_score:.2f}", add_number=add_number, rotation_id=rotation_id,
            )
        )

    def _weakest_position(
        self, candidate_ids: List[str], evidence_by_stock: Dict[str, EvidenceRow]
    ) -> Tuple[Optional[str], float]:
        """§13/§17：position_priority 用 Step 1 當天已經算好、存在 `position.last_priority`
        的 hold_score（含 drawdown_from_peak，跟 Winner Management 用同一份數字），不
        重算——避免這裡拿不到當天收盤價又用 `drawdown_from_peak=None` 算出一份偏高、
        跟 Step 1 兜不起來的 priority。"""
        weakest_id, weakest_priority = None, float("inf")
        for sid in candidate_ids:
            position = self.positions.get(sid)
            if position is None or position.last_priority is None:
                continue
            if position.last_priority < weakest_priority:
                weakest_id, weakest_priority = sid, position.last_priority
        return weakest_id, weakest_priority


def run_experiment(
    db: Session, *, mode: str, start: date, end: date, config: Optional[ExperimentConfig] = None
) -> BacktestResult:
    engine = ExperimentEngine(db, mode=mode, config=config)
    return engine.run(start=start, end=end)


def load_v1_completed_trades(db: Session, *, strategy_version: str = "v1_frozen") -> List[ShadowCompletedTrade]:
    """讀取已經跑過、驗證過的 v1_frozen 平倉紀錄——不重新模擬，直接用既有正式資料當比較基準。"""
    return (
        db.query(ShadowCompletedTrade)
        .filter(ShadowCompletedTrade.strategy_version == strategy_version)
        .order_by(ShadowCompletedTrade.entry_execution_date.asc())
        .all()
    )
