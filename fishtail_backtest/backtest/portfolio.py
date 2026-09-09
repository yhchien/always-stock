"""Portfolio state machine: cash, open positions, lots, trade log.

A Position is keyed by stock_id (not by cohort) -- per spec §14/§4, the
portfolio never holds "the same stock_id twice" just because two different
(stock_id, first_seen_date) cohorts both fired a signal. A Position can
contain 1..MAX_UNITS_PER_STOCK independent Lots, each recording its own
entry price/date/cohort/entry_type for later first-buy-vs-add-on analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .loader import CohortDayRow


@dataclass
class Lot:
    stock_id: str
    stock_name: str
    entry_type: str
    signal_first_seen_date: date
    entry_signal_date: date
    entry_execution_date: date
    entry_price: float
    shares: float
    allocation: float
    entry_row: CohortDayRow


@dataclass
class Position:
    stock_id: str
    stock_name: str
    lots: List[Lot] = field(default_factory=list)

    @property
    def units(self) -> int:
        return len(self.lots)

    @property
    def total_shares(self) -> float:
        return sum(lot.shares for lot in self.lots)

    @property
    def total_cost(self) -> float:
        return sum(lot.allocation for lot in self.lots)

    @property
    def average_entry_price(self) -> float:
        shares = self.total_shares
        return self.total_cost / shares if shares else 0.0

    def market_value(self, current_price: float) -> float:
        return self.total_shares * current_price


@dataclass
class ClosedTrade:
    """One realized round trip for one Lot (a position with 2 lots exiting
    on the same day produces 2 ClosedTrade rows, so per-lot P&L stays
    inspectable)."""

    stock_id: str
    stock_name: str
    entry_type: str
    signal_first_seen_date: date
    entry_signal_date: date
    entry_execution_date: date
    entry_day_index: int
    entry_p3_selected: bool
    entry_hit_count: int
    entry_momentum: Optional[float]
    entry_p4: Optional[str]
    entry_mark_to_market_return: Optional[float]
    entry_price: float
    allocation: float
    shares: float
    exit_reason: str
    exit_signal_date: date
    exit_execution_date: date
    exit_p4: Optional[str]
    exit_mark_to_market_return: Optional[float]
    exit_price: float
    holding_days: int
    realized_pnl: float
    realized_return_pct: float


class Portfolio:
    def __init__(self, initial_capital: float, unit_capital: float, max_stocks: int,
                 max_units_per_stock: int, max_total_units: int,
                 fee_rate: float = 0.0, tax_rate: float = 0.0):
        self.cash = initial_capital
        self.initial_capital = initial_capital
        self.unit_capital = unit_capital
        self.max_stocks = max_stocks
        self.max_units_per_stock = max_units_per_stock
        self.max_total_units = max_total_units
        self.fee_rate = fee_rate
        self.tax_rate = tax_rate

        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[ClosedTrade] = []
        self.realized_pnl_cumulative = 0.0

    # ---- capacity checks (§4, §13 step 5) ----
    @property
    def total_units(self) -> int:
        return sum(p.units for p in self.positions.values())

    def can_open_new_stock(self) -> bool:
        return len(self.positions) < self.max_stocks

    def can_add_unit(self, stock_id: str) -> bool:
        if self.total_units >= self.max_total_units:
            return False
        existing = self.positions.get(stock_id)
        if existing is None:
            return self.can_open_new_stock()
        return existing.units < self.max_units_per_stock

    def available_allocation(self) -> Optional[float]:
        alloc = min(self.unit_capital, self.cash)
        if alloc < self.unit_capital:
            return None  # spec §4: if cash < unit_capital, do not open
        return alloc

    # ---- buy / sell ----
    def buy(self, *, stock_id: str, stock_name: str, entry_type: str,
            signal_first_seen_date: date, entry_signal_date: date,
            entry_execution_date: date, entry_price: float, allow_fractional: bool,
            entry_row: CohortDayRow) -> Optional[Lot]:
        if not self.can_add_unit(stock_id):
            return None
        allocation = self.available_allocation()
        if allocation is None:
            return None
        buy_cost = allocation * (1 + self.fee_rate)
        if buy_cost > self.cash + 1e-9:
            allocation = self.cash / (1 + self.fee_rate)
            if allocation < 1e-9:
                return None
        shares = allocation / entry_price
        if not allow_fractional:
            shares = float(int(shares))
            allocation = shares * entry_price
            if shares <= 0:
                return None
        cost = allocation * (1 + self.fee_rate)
        self.cash -= cost
        lot = Lot(
            stock_id=stock_id,
            stock_name=stock_name,
            entry_type=entry_type,
            signal_first_seen_date=signal_first_seen_date,
            entry_signal_date=entry_signal_date,
            entry_execution_date=entry_execution_date,
            entry_price=entry_price,
            shares=shares,
            allocation=allocation,
            entry_row=entry_row,
        )
        pos = self.positions.setdefault(stock_id, Position(stock_id=stock_id, stock_name=stock_name))
        pos.lots.append(lot)
        return lot

    def sell_all(self, *, stock_id: str, exit_reason: str, exit_signal_date: date,
                 exit_execution_date: date, exit_price: float,
                 exit_row: Optional[CohortDayRow]) -> List[ClosedTrade]:
        pos = self.positions.get(stock_id)
        if pos is None or not pos.lots:
            return []
        closed: List[ClosedTrade] = []
        for lot in pos.lots:
            gross_proceeds = lot.shares * exit_price
            proceeds = gross_proceeds * (1 - self.fee_rate - self.tax_rate)
            pnl = proceeds - lot.allocation
            self.cash += proceeds
            self.realized_pnl_cumulative += pnl
            holding_days = (exit_execution_date - lot.entry_execution_date).days
            closed.append(
                ClosedTrade(
                    stock_id=stock_id,
                    stock_name=lot.stock_name,
                    entry_type=lot.entry_type,
                    signal_first_seen_date=lot.signal_first_seen_date,
                    entry_signal_date=lot.entry_signal_date,
                    entry_execution_date=lot.entry_execution_date,
                    entry_day_index=lot.entry_row.day_index,
                    entry_p3_selected=lot.entry_row.p3_selected_today,
                    entry_hit_count=lot.entry_row.hit_count_so_far,
                    entry_momentum=lot.entry_row.momentum_score,
                    entry_p4=lot.entry_row.p4_decision,
                    entry_mark_to_market_return=lot.entry_row.mark_to_market_return_pct,
                    entry_price=lot.entry_price,
                    allocation=lot.allocation,
                    shares=lot.shares,
                    exit_reason=exit_reason,
                    exit_signal_date=exit_signal_date,
                    exit_execution_date=exit_execution_date,
                    exit_p4=exit_row.p4_decision if exit_row else None,
                    exit_mark_to_market_return=exit_row.mark_to_market_return_pct if exit_row else None,
                    exit_price=exit_price,
                    holding_days=holding_days,
                    realized_pnl=pnl,
                    realized_return_pct=pnl / lot.allocation * 100 if lot.allocation else 0.0,
                )
            )
        self.closed_trades.extend(closed)
        del self.positions[stock_id]
        return closed

    def equity(self, price_lookup) -> float:
        """price_lookup: callable(stock_id) -> Optional[float] latest known price."""
        value = self.cash
        for stock_id, pos in self.positions.items():
            px = price_lookup(stock_id)
            if px is not None:
                value += pos.market_value(px)
            else:
                value += pos.total_cost  # fallback: cost basis if no price known
        return value
