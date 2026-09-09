"""Compute performance metrics from a completed BacktestResult."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .portfolio import ClosedTrade


@dataclass
class Metrics:
    total_return_pct: float
    final_equity: float
    max_drawdown_pct: float
    trade_count: int
    win_rate_pct: Optional[float]
    profit_factor: Optional[float]
    avg_trade_return_pct: Optional[float]
    median_trade_return_pct: Optional[float]
    avg_winner_pct: Optional[float]
    avg_loser_pct: Optional[float]
    largest_winner_pct: Optional[float]
    largest_loser_pct: Optional[float]
    max_concurrent_stocks: int
    max_capital_usage_pct: float
    turnover_trade_count: int
    return_without_best_trade_pct: float
    return_without_top_3_trades_pct: float


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def compute_max_drawdown_pct(equity_curve: List[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak * 100
            max_dd = min(max_dd, dd)
    return max_dd


def compute_metrics(
    *,
    initial_capital: float,
    final_equity: float,
    closed_trades: List[ClosedTrade],
    equity_curve: List[float],
    max_concurrent_stocks: int,
    max_capital_usage_pct: float,
) -> Metrics:
    n = len(closed_trades)
    total_return_pct = (final_equity - initial_capital) / initial_capital * 100
    max_dd = compute_max_drawdown_pct(equity_curve)

    if n == 0:
        return Metrics(
            total_return_pct=total_return_pct,
            final_equity=final_equity,
            max_drawdown_pct=max_dd,
            trade_count=0,
            win_rate_pct=None,
            profit_factor=None,
            avg_trade_return_pct=None,
            median_trade_return_pct=None,
            avg_winner_pct=None,
            avg_loser_pct=None,
            largest_winner_pct=None,
            largest_loser_pct=None,
            max_concurrent_stocks=max_concurrent_stocks,
            max_capital_usage_pct=max_capital_usage_pct,
            turnover_trade_count=0,
            return_without_best_trade_pct=total_return_pct,
            return_without_top_3_trades_pct=total_return_pct,
        )

    returns = [t.realized_return_pct for t in closed_trades]
    pnls = [t.realized_pnl for t in closed_trades]
    winners = [r for r in returns if r > 0]
    losers = [r for r in returns if r <= 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)

    sorted_by_pnl_desc = sorted(closed_trades, key=lambda t: -t.realized_pnl)
    best_pnl = sorted_by_pnl_desc[0].realized_pnl if sorted_by_pnl_desc else 0.0
    top3_pnl = sum(t.realized_pnl for t in sorted_by_pnl_desc[:3])

    return_without_best = (final_equity - best_pnl - initial_capital) / initial_capital * 100
    return_without_top3 = (final_equity - top3_pnl - initial_capital) / initial_capital * 100

    return Metrics(
        total_return_pct=total_return_pct,
        final_equity=final_equity,
        max_drawdown_pct=max_dd,
        trade_count=n,
        win_rate_pct=len(winners) / n * 100,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        avg_trade_return_pct=sum(returns) / n,
        median_trade_return_pct=_median(returns),
        avg_winner_pct=(sum(winners) / len(winners)) if winners else None,
        avg_loser_pct=(sum(losers) / len(losers)) if losers else None,
        largest_winner_pct=max(returns) if returns else None,
        largest_loser_pct=min(returns) if returns else None,
        max_concurrent_stocks=max_concurrent_stocks,
        max_capital_usage_pct=max_capital_usage_pct,
        turnover_trade_count=n,
        return_without_best_trade_pct=return_without_best,
        return_without_top_3_trades_pct=return_without_top3,
    )
