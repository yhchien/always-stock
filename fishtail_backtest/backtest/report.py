"""CSV writers and text summary builders."""
from __future__ import annotations

import csv
from typing import List

from .metrics import Metrics
from .portfolio import ClosedTrade
from .run_backtest import BacktestResult


def write_trades_csv(result: BacktestResult, path: str):
    fields = [
        "stock_id", "stock_name", "entry_type", "signal_first_seen_date",
        "entry_signal_date", "entry_execution_date", "entry_day_index",
        "entry_p3_selected", "entry_hit_count", "entry_momentum", "entry_p4",
        "entry_mark_to_market_return", "entry_price", "allocation", "shares",
        "exit_reason", "exit_signal_date", "exit_execution_date", "exit_p4",
        "exit_mark_to_market_return", "exit_price", "holding_days",
        "realized_pnl", "realized_return_pct",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in result.closed_trades:
            row = {k: getattr(t, k) for k in fields}
            w.writerow(row)


def write_daily_portfolio_csv(result: BacktestResult, path: str):
    fields = [
        "date", "cash", "invested_cost", "number_of_stocks", "number_of_units",
        "realized_pnl_today", "cumulative_realized_pnl", "equity_if_available",
        "open_positions",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for d in result.daily_portfolio:
            w.writerow({k: getattr(d, k) for k in fields})


def entry_type_attribution(closed_trades: List[ClosedTrade]) -> dict:
    out = {}
    for entry_type in {t.entry_type for t in closed_trades}:
        group = [t for t in closed_trades if t.entry_type == entry_type]
        n = len(group)
        wins = sum(1 for t in group if t.realized_pnl > 0)
        out[entry_type] = {
            "trade_count": n,
            "win_rate_pct": wins / n * 100 if n else None,
            "total_pnl": sum(t.realized_pnl for t in group),
            "avg_return_pct": sum(t.realized_return_pct for t in group) / n if n else None,
        }
    return out


def top_n(closed_trades: List[ClosedTrade], n: int, winners: bool) -> List[ClosedTrade]:
    return sorted(closed_trades, key=lambda t: -t.realized_pnl if winners else t.realized_pnl)[:n]


def format_summary(metrics: Metrics, result: BacktestResult, label: str) -> str:
    lines = [f"=== {label} ==="]
    lines.append(f"Initial capital: {result.params['initial_capital']:,.0f}")
    lines.append(f"Final capital:   {metrics.final_equity:,.0f}")
    lines.append(f"Return:          {metrics.total_return_pct:+.2f}%")
    lines.append(f"Max drawdown:    {metrics.max_drawdown_pct:.2f}%")
    lines.append(f"Trades:          {metrics.trade_count}")
    if metrics.win_rate_pct is not None:
        lines.append(f"Win rate:        {metrics.win_rate_pct:.1f}%")
        lines.append(f"Profit factor:   {metrics.profit_factor:.2f}" if metrics.profit_factor else "Profit factor:   n/a (no losers)")
        lines.append(f"Avg trade:       {metrics.avg_trade_return_pct:+.2f}%  (median {metrics.median_trade_return_pct:+.2f}%)")
        lines.append(f"Avg winner:      {metrics.avg_winner_pct:+.2f}%" if metrics.avg_winner_pct is not None else "Avg winner:      n/a")
        lines.append(f"Avg loser:       {metrics.avg_loser_pct:+.2f}%" if metrics.avg_loser_pct is not None else "Avg loser:       n/a")
        lines.append(f"Largest winner:  {metrics.largest_winner_pct:+.2f}%")
        lines.append(f"Largest loser:   {metrics.largest_loser_pct:+.2f}%")
    lines.append(f"Max concurrent stocks: {metrics.max_concurrent_stocks}")
    lines.append(f"Max capital usage:     {metrics.max_capital_usage_pct:.1f}%")
    lines.append(f"Return w/o best trade: {metrics.return_without_best_trade_pct:+.2f}%")
    lines.append(f"Return w/o top-3:      {metrics.return_without_top_3_trades_pct:+.2f}%")
    return "\n".join(lines)
