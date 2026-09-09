"""Core day-by-day backtest orchestrator.

Timing model (matches spec §13 exactly):
  - On day T we use T's rows ONLY to DECIDE: which held stocks should exit,
    which new cohorts should enter, ranked and capacity-checked.
  - Every decision made on day T is a PENDING order, queued with the fill
    price that was already computed for T's "next trading day" (i.e. it
    executes on T+1's calendar date). No information later than T is used
    to make the decision itself.
  - On T+1, pending sells execute first (freeing cash), then pending buys
    execute using that freed cash -- this is why capacity/cash for step 5's
    buy decisions on day T is evaluated against a *projected* post-sell
    state, matching "賣出的 cash 可以用於當天 BUY".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .execution import next_trading_day, resolve_buy_fill, resolve_sell_fill
from .loader import CohortDayRow, group_by_trade_date, trading_calendar
from .portfolio import ClosedTrade, Portfolio
from .signals import (
    EntrySignal,
    ExitSignal,
    EXIT_REASON_END_OF_DATA_FORCED,
    EXIT_REASON_REAL_STOP_LOSS,
    generate_entry_signal,
    generate_exit_signal,
    rank_candidates,
)


@dataclass
class PendingSell:
    stock_id: str
    reason: str
    signal_date: date
    exit_price: float
    exit_row: Optional[CohortDayRow]


@dataclass
class PendingBuy:
    signal: EntrySignal
    buy_price: float


@dataclass
class DailyPortfolioRow:
    date: date
    cash: float
    invested_cost: float
    number_of_stocks: int
    number_of_units: int
    realized_pnl_today: float
    cumulative_realized_pnl: float
    equity_if_available: float
    open_positions: str


@dataclass
class SkippedEvent:
    date: date
    stock_id: str
    reason: str


@dataclass
class BacktestResult:
    params: dict
    closed_trades: List[ClosedTrade]
    daily_portfolio: List[DailyPortfolioRow]
    open_positions_at_end: List[dict]
    skipped_events: List[SkippedEvent]
    final_equity: float
    equity_curve: List[float]
    max_concurrent_stocks: int
    max_capital_usage_pct: float


def _load_close_price_map(raw_prices_path: str) -> Dict[str, Dict[str, float]]:
    with open(raw_prices_path) as f:
        raw = json.load(f)
    out: Dict[str, Dict[str, float]] = {}
    for stock_id, by_date in raw.items():
        out[stock_id] = {d: v["close"] for d, v in by_date.items() if v.get("close") is not None}
    return out


def _dedup_exit_candidates(rows_today: List[CohortDayRow], held_stock_ids: set) -> Dict[str, List[CohortDayRow]]:
    """§14: group today's rows by stock_id for stocks we currently hold,
    across ALL cohorts referencing that stock_id today."""
    out: Dict[str, List[CohortDayRow]] = {}
    for r in rows_today:
        if r.stock_id in held_stock_ids:
            out.setdefault(r.stock_id, []).append(r)
    return out


def _dedup_entry_candidates(rows_today: List[CohortDayRow], params: dict, generate_entry_fn) -> List[EntrySignal]:
    """§14: if multiple cohorts of the same stock_id both fire an entry
    signal today, keep only the highest-scoring cohort's signal."""
    best_by_stock: Dict[str, EntrySignal] = {}
    for r in rows_today:
        sig = generate_entry_fn(r, params)
        if sig is None:
            continue
        existing = best_by_stock.get(r.stock_id)
        if existing is None or sig.entry_score > existing.entry_score:
            best_by_stock[r.stock_id] = sig
    return list(best_by_stock.values())


def run_backtest(
    rows: List[CohortDayRow],
    params: dict,
    *,
    etf_ids: Optional[set] = None,
    close_price_map: Optional[Dict[str, Dict[str, float]]] = None,
    real_stop_loss_pct: Optional[float] = None,
    allow_fractional_shares: bool = True,
    force_liquidate_at_end: bool = False,
    generate_entry_fn=generate_entry_signal,
    generate_exit_fn=generate_exit_signal,
    rank_fn=rank_candidates,
) -> BacktestResult:
    etf_ids = etf_ids or set()
    exclude_etf = params.get("exclude_etf", False)

    calendar = trading_calendar(rows)
    by_day = group_by_trade_date(rows)

    portfolio = Portfolio(
        initial_capital=params["initial_capital"],
        unit_capital=params["unit_capital"],
        max_stocks=params["max_stocks"],
        max_units_per_stock=params["max_units_per_stock"],
        max_total_units=params["max_total_units"],
        fee_rate=params.get("fee_rate", 0.0),
        tax_rate=params.get("tax_rate", 0.0),
    )

    pending_sells: List[PendingSell] = []
    pending_buys: List[PendingBuy] = []
    daily_portfolio: List[DailyPortfolioRow] = []
    skipped_events: List[SkippedEvent] = []
    equity_curve: List[float] = []
    max_concurrent_stocks = 0
    max_capital_usage_pct = 0.0

    def latest_close(stock_id: str, as_of: date) -> Optional[float]:
        if close_price_map is None:
            return None
        prices = close_price_map.get(stock_id, {})
        for d in reversed([dd for dd in calendar if dd <= as_of]):
            v = prices.get(d.isoformat())
            if v is not None:
                return v
        return None

    for day_idx, T in enumerate(calendar):
        realized_pnl_today = 0.0

        # ---- Phase A: execute orders queued on the PREVIOUS day (T-1),
        # using the price already fixed at queue time (T-1's next-day price,
        # which equals today T). Sells first, then buys (spec §13). ----
        for ps in pending_sells:
            closed = portfolio.sell_all(
                stock_id=ps.stock_id,
                exit_reason=ps.reason,
                exit_signal_date=ps.signal_date,
                exit_execution_date=T,
                exit_price=ps.exit_price,
                exit_row=ps.exit_row,
            )
            realized_pnl_today += sum(c.realized_pnl for c in closed)
        pending_sells = []

        for pb in pending_buys:
            sig = pb.signal
            row = sig.row
            portfolio.buy(
                stock_id=row.stock_id,
                stock_name=row.stock_name,
                entry_type=sig.entry_type,
                signal_first_seen_date=row.first_seen_date,
                entry_signal_date=row.trade_date,
                entry_execution_date=T,
                entry_price=pb.buy_price,
                allow_fractional=allow_fractional_shares,
                entry_row=row,
            )
        pending_buys = []

        max_concurrent_stocks = max(max_concurrent_stocks, len(portfolio.positions))
        used_capital = portfolio.initial_capital - portfolio.cash
        # capital usage should reflect *cost basis currently deployed*, not
        # cumulative cash spent net of realized P&L noise
        deployed_cost = sum(p.total_cost for p in portfolio.positions.values())
        max_capital_usage_pct = max(max_capital_usage_pct, deployed_cost / portfolio.initial_capital * 100)

        # ---- Phase B: real (price-based) stop loss check using TODAY's
        # actual close (§12). Independent of the cohort mark-to-market
        # field, uses OUR OWN average_entry_price. Legit same-day info. ----
        real_stop_candidates: Dict[str, CohortDayRow] = {}
        if real_stop_loss_pct is not None and close_price_map is not None:
            for stock_id, pos in list(portfolio.positions.items()):
                close = latest_close(stock_id, T)
                if close is None:
                    continue
                pos_return = (close / pos.average_entry_price - 1) * 100
                if pos_return <= real_stop_loss_pct:
                    any_row_today = next((r for r in by_day.get(T, []) if r.stock_id == stock_id), None)
                    real_stop_candidates[stock_id] = any_row_today

        # ---- Step 1: exit decisions for currently held stocks, using T's
        # rows across all cohorts referencing that stock_id (§14). ----
        rows_today = by_day.get(T, [])
        rows_today = [r for r in rows_today if not (exclude_etf and r.stock_id in etf_ids)]
        held_ids = set(portfolio.positions.keys())
        exit_groups = _dedup_exit_candidates(rows_today, held_ids)

        decided_exits: Dict[str, ExitSignal] = {}
        for stock_id in held_ids:
            if stock_id in real_stop_candidates:
                fake_row = real_stop_candidates[stock_id] or CohortDayRow(
                    stock_id=stock_id, stock_name=portfolio.positions[stock_id].stock_name,
                    first_seen_date=T, day_index=0, trade_date=T, p3_selected_today=False,
                    hit_count_so_far=0, momentum_score=None, p4_decision=None,
                    mark_to_market_return_pct=None, next_day_buy_price_if_entering_today=None,
                    next_day_sell_price_if_exiting_today=None, is_official_exit_signal_day=False,
                )
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_REAL_STOP_LOSS, row=fake_row)
                continue
            for r in exit_groups.get(stock_id, []):
                pos = portfolio.positions.get(stock_id)
                sig = generate_exit_fn(r, pos, params)
                if sig is not None:
                    decided_exits[stock_id] = sig
                    break

        # queue sells, projecting freed capacity/cash for today's buy step
        projected_freed_units = 0
        projected_freed_cash = 0.0
        for stock_id, exit_sig in decided_exits.items():
            exec_date, price = resolve_sell_fill(exit_sig.row, calendar)
            if exit_sig.reason == "REAL_POSITION_STOP_LOSS":
                # real stop loss row carries no next_day price of its own;
                # look up the *actual* held stock's row today for pricing.
                actual_row = next((r for r in rows_today if r.stock_id == stock_id), None)
                if actual_row is not None:
                    exec_date, price = resolve_sell_fill(actual_row, calendar)
            if price is None or exec_date is None:
                skipped_events.append(
                    SkippedEvent(date=T, stock_id=stock_id, reason="NO_NEXT_DAY_EXIT_PRICE")
                )
                continue
            pos = portfolio.positions[stock_id]
            pending_sells.append(
                PendingSell(stock_id=stock_id, reason=exit_sig.reason, signal_date=T,
                            exit_price=price, exit_row=exit_sig.row)
            )
            projected_freed_units += pos.units
            projected_freed_cash += pos.total_cost  # conservative: ignore P&L when projecting capacity

        # ---- Step 3-4: entry candidates + ranking ----
        candidates = _dedup_entry_candidates(rows_today, params, generate_entry_fn)
        # never re-enter a stock we're queuing an exit for today, and never
        # duplicate a stock already at max units unless it's an add-on
        candidates = [c for c in candidates if c.row.stock_id not in decided_exits]
        candidates = rank_fn(candidates)

        # ---- Step 5: capacity + cash checks, using projected post-sell state ----
        projected_units = portfolio.total_units - projected_freed_units
        projected_stocks = {sid for sid in portfolio.positions if sid not in decided_exits}
        projected_cash = portfolio.cash + projected_freed_cash

        for sig in candidates:
            stock_id = sig.row.stock_id
            already_held = stock_id in projected_stocks
            if not already_held and len(projected_stocks) >= params["max_stocks"]:
                continue
            existing_units = portfolio.positions[stock_id].units if already_held else 0
            if existing_units >= params["max_units_per_stock"]:
                continue
            if projected_units >= params["max_total_units"]:
                continue
            if projected_cash < params["unit_capital"]:
                continue
            exec_date, price = resolve_buy_fill(sig.row, calendar)
            if price is None or exec_date is None:
                skipped_events.append(
                    SkippedEvent(date=T, stock_id=stock_id, reason="NO_NEXT_DAY_BUY_PRICE")
                )
                continue
            pending_buys.append(PendingBuy(signal=sig, buy_price=price))
            projected_units += 1
            projected_stocks.add(stock_id)
            projected_cash -= params["unit_capital"]

        # ---- bookkeeping for today ----
        def price_lookup(stock_id: str) -> Optional[float]:
            return latest_close(stock_id, T)

        eq = portfolio.equity(price_lookup)
        equity_curve.append(eq)
        daily_portfolio.append(
            DailyPortfolioRow(
                date=T,
                cash=portfolio.cash,
                invested_cost=sum(p.total_cost for p in portfolio.positions.values()),
                number_of_stocks=len(portfolio.positions),
                number_of_units=portfolio.total_units,
                realized_pnl_today=realized_pnl_today,
                cumulative_realized_pnl=portfolio.realized_pnl_cumulative,
                equity_if_available=eq,
                open_positions=",".join(sorted(portfolio.positions.keys())),
            )
        )

    # ---- end of data: handle any still-pending queued orders that never executed ----
    for ps in pending_sells:
        skipped_events.append(SkippedEvent(date=calendar[-1], stock_id=ps.stock_id, reason="PENDING_SELL_AT_END_OF_DATA"))
    for pb in pending_buys:
        skipped_events.append(SkippedEvent(date=calendar[-1], stock_id=pb.signal.row.stock_id, reason="PENDING_BUY_AT_END_OF_DATA"))

    open_positions_at_end = []
    if force_liquidate_at_end and portfolio.positions:
        last_day = calendar[-1]
        for stock_id in list(portfolio.positions.keys()):
            close = latest_close(stock_id, last_day)
            if close is None:
                continue
            closed = portfolio.sell_all(
                stock_id=stock_id, exit_reason=EXIT_REASON_END_OF_DATA_FORCED,
                exit_signal_date=last_day, exit_execution_date=last_day,
                exit_price=close, exit_row=None,
            )
        final_eq = portfolio.equity(lambda sid: latest_close(sid, last_day))
        equity_curve[-1] = final_eq
    else:
        for stock_id, pos in portfolio.positions.items():
            close = latest_close(stock_id, calendar[-1])
            open_positions_at_end.append(
                {
                    "stock_id": stock_id,
                    "stock_name": pos.stock_name,
                    "units": pos.units,
                    "total_cost": pos.total_cost,
                    "average_entry_price": pos.average_entry_price,
                    "latest_close": close,
                    "unrealized_pnl": (pos.market_value(close) - pos.total_cost) if close else None,
                }
            )

    final_equity = equity_curve[-1] if equity_curve else portfolio.initial_capital

    return BacktestResult(
        params=params,
        closed_trades=portfolio.closed_trades,
        daily_portfolio=daily_portfolio,
        open_positions_at_end=open_positions_at_end,
        skipped_events=skipped_events,
        final_equity=final_equity,
        equity_curve=equity_curve,
        max_concurrent_stocks=max_concurrent_stocks,
        max_capital_usage_pct=max_capital_usage_pct,
    )
