from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Dict, List, Optional


@dataclass
class BacktestDay:
    trade_date: date
    open_price: Optional[float]
    close_price: float
    volume: float
    foreign_net_shares: float
    trust_net_shares: float
    dealer_net_shares: float


def _rolling_mean(values: List[float], window: int, index: int) -> Optional[float]:
    if index + 1 < window:
        return None
    bucket = values[index - window + 1 : index + 1]
    return sum(bucket) / window


def _consecutive_positive(values: List[float], index: int, days: int) -> bool:
    if index + 1 < days:
        return False
    return all(value > 0 for value in values[index - days + 1 : index + 1])


def _evaluate_rule(rule: Dict, series: Dict[str, List[float]], index: int) -> bool:
    indicator = rule["indicator"]
    params = rule["params"]

    if indicator == "close_above_ma":
        moving_average = _rolling_mean(series["close"], params["window"], index)
        return moving_average is not None and series["close"][index] > moving_average

    if indicator == "close_below_ma":
        moving_average = _rolling_mean(series["close"], params["window"], index)
        return moving_average is not None and series["close"][index] < moving_average

    if indicator == "volume_above_ma":
        moving_average = _rolling_mean(series["volume"], params["window"], index)
        return moving_average is not None and series["volume"][index] > moving_average

    if indicator == "foreign_consecutive_buy":
        return _consecutive_positive(series["foreign"], index, params["days"])

    if indicator == "trust_consecutive_buy":
        return _consecutive_positive(series["trust"], index, params["days"])

    if indicator == "dealer_consecutive_buy":
        return _consecutive_positive(series["dealer"], index, params["days"])

    if indicator == "foreign_net_negative":
        return series["foreign"][index] < 0

    if indicator == "trust_net_negative":
        return series["trust"][index] < 0

    if indicator == "dealer_net_negative":
        return series["dealer"][index] < 0

    raise ValueError(f"Unsupported indicator: {indicator}")


def _evaluate_logic(results: List[bool], logic: str) -> bool:
    if not results:
        return False
    return all(results) if logic == "all" else any(results)


def _format_reason(triggered_rules: List[str]) -> str:
    if not triggered_rules:
        return "無明確訊號"
    return "、".join(triggered_rules)


def run_backtest(days: List[BacktestDay], strategy: Dict) -> Dict:
    if len(days) < 2:
        raise ValueError("At least 2 trading days are required for backtesting")

    series = {
        "close": [day.close_price for day in days],
        "volume": [day.volume or 0.0 for day in days],
        "foreign": [day.foreign_net_shares for day in days],
        "trust": [day.trust_net_shares for day in days],
        "dealer": [day.dealer_net_shares for day in days],
    }

    initial_capital = float(strategy["initial_capital"])
    cash = initial_capital
    shares = 0.0
    position_entry_price: Optional[float] = None
    position_entry_date: Optional[date] = None
    pending_action: Optional[str] = None
    pending_reason = ""
    trades = []
    equity_curve = []
    daily_returns = []
    monthly_returns = defaultdict(list)
    quarterly_returns = defaultdict(list)
    yearly_returns = defaultdict(list)
    latest_recommendation = {
        "latest_signal_date": days[-1].trade_date.isoformat(),
        "action": "wait",
        "reason": "最新交易日沒有新的進出場訊號。",
    }

    for index, day in enumerate(days):
        if pending_action == "buy" and shares == 0:
            execution_price = day.open_price or day.close_price
            shares = cash / execution_price if execution_price else 0.0
            cash = 0.0
            position_entry_price = execution_price
            position_entry_date = day.trade_date
            pending_action = None
        elif pending_action == "sell" and shares > 0:
            execution_price = day.open_price or day.close_price
            cash = shares * execution_price
            if position_entry_price is not None and position_entry_date is not None:
                pnl_amount = cash - (shares * position_entry_price)
                pnl_pct = ((execution_price / position_entry_price) - 1) * 100
                trades.append(
                    {
                        "entry_date": position_entry_date.isoformat(),
                        "exit_date": day.trade_date.isoformat(),
                        "entry_price": round(position_entry_price, 4),
                        "exit_price": round(execution_price, 4),
                        "holding_days": max((day.trade_date - position_entry_date).days, 1),
                        "return_pct": round(pnl_pct, 4),
                        "pnl_amount": round(pnl_amount, 2),
                        "exit_reason": pending_reason,
                    }
                )
            shares = 0.0
            position_entry_price = None
            position_entry_date = None
            pending_action = None
            pending_reason = ""

        equity = cash if shares == 0 else shares * day.close_price
        benchmark_equity = initial_capital * (day.close_price / days[0].close_price)
        equity_curve.append(
            {
                "trade_date": day.trade_date.isoformat(),
                "equity": round(equity, 2),
                "benchmark_equity": round(benchmark_equity, 2),
            }
        )

        if index > 0:
            prev_equity = equity_curve[index - 1]["equity"]
            day_return = (equity / prev_equity) - 1 if prev_equity else 0.0
            daily_returns.append(day_return)
            month_key = day.trade_date.strftime("%Y-%m")
            quarter = ((day.trade_date.month - 1) // 3) + 1
            quarter_key = f"{day.trade_date.year}-Q{quarter}"
            year_key = str(day.trade_date.year)
            monthly_returns[month_key].append(day_return)
            quarterly_returns[quarter_key].append(day_return)
            yearly_returns[year_key].append(day_return)

        entry_results = [_evaluate_rule(rule, series, index) for rule in strategy["entry_rules"]]
        exit_results = [_evaluate_rule(rule, series, index) for rule in strategy["exit_rules"]]

        entry_triggered = _evaluate_logic(entry_results, strategy["entry_logic"])
        exit_triggered = _evaluate_logic(exit_results, strategy["exit_logic"])

        if index < len(days) - 1:
            if shares == 0 and pending_action is None and entry_triggered:
                pending_action = "buy"
                pending_reason = _format_reason(
                    [rule["indicator"] for rule, ok in zip(strategy["entry_rules"], entry_results) if ok]
                )
            elif shares > 0 and pending_action is None and exit_triggered:
                pending_action = "sell"
                pending_reason = _format_reason(
                    [rule["indicator"] for rule, ok in zip(strategy["exit_rules"], exit_results) if ok]
                )

        if index == len(days) - 1:
            if shares == 0 and entry_triggered:
                latest_recommendation = {
                    "latest_signal_date": day.trade_date.isoformat(),
                    "action": "observe_buy",
                    "reason": "最新交易日符合進場條件，下一交易日可觀察買進。",
                }
            elif shares > 0 and exit_triggered:
                latest_recommendation = {
                    "latest_signal_date": day.trade_date.isoformat(),
                    "action": "observe_sell",
                    "reason": "最新交易日觸發出場條件，下一交易日可觀察賣出。",
                }
            elif shares > 0:
                latest_recommendation = {
                    "latest_signal_date": day.trade_date.isoformat(),
                    "action": "hold",
                    "reason": "目前仍持有部位，且尚未出現新的出場訊號。",
                }

    ending_equity = equity_curve[-1]["equity"]
    total_return_pct = ((ending_equity / initial_capital) - 1) * 100
    years = max((days[-1].trade_date - days[0].trade_date).days / 365.25, 1 / 365.25)
    annual_return_pct = ((ending_equity / initial_capital) ** (1 / years) - 1) * 100

    running_peak = 0.0
    max_drawdown_pct = 0.0
    for point in equity_curve:
        equity = point["equity"]
        running_peak = max(running_peak, equity)
        if running_peak > 0:
            drawdown_pct = ((equity / running_peak) - 1) * 100
            max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

    sharpe_ratio = 0.0
    if daily_returns:
        volatility = pstdev(daily_returns)
        if volatility > 0:
            sharpe_ratio = (mean(daily_returns) / volatility) * math.sqrt(252)

    winning_trades = [trade for trade in trades if trade["return_pct"] > 0]
    losing_trades = [trade for trade in trades if trade["return_pct"] <= 0]
    gross_profit = sum(trade["pnl_amount"] for trade in winning_trades)
    gross_loss = abs(sum(trade["pnl_amount"] for trade in losing_trades))

    benchmark_return_pct = ((days[-1].close_price / days[0].close_price) - 1) * 100
    avg_trade_return_pct = mean([trade["return_pct"] for trade in trades]) if trades else 0.0
    avg_holding_days = mean([trade["holding_days"] for trade in trades]) if trades else 0.0
    avg_gain_pct = mean([trade["return_pct"] for trade in winning_trades]) if winning_trades else 0.0
    avg_loss_pct = mean([trade["return_pct"] for trade in losing_trades]) if losing_trades else 0.0

    def _compress_period_returns(bucket: Dict[str, List[float]]) -> List[Dict]:
        return [
            {
                "period": period,
                "return_pct": round(((math.prod([1 + value for value in returns]) - 1) * 100), 4),
            }
            for period, returns in sorted(bucket.items())
        ]

    return {
        "metrics": {
            "total_return_pct": round(total_return_pct, 4),
            "annual_return_pct": round(annual_return_pct, 4),
            "win_rate_pct": round((len(winning_trades) / len(trades) * 100), 4) if trades else 0.0,
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "trade_count": len(trades),
            "ending_equity": round(ending_equity, 2),
            "benchmark_return_pct": round(benchmark_return_pct, 4),
            "excess_return_pct": round(total_return_pct - benchmark_return_pct, 4),
            "avg_trade_return_pct": round(avg_trade_return_pct, 4),
            "avg_holding_days": round(avg_holding_days, 2),
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else 0.0,
            "avg_gain_pct": round(avg_gain_pct, 4),
            "avg_loss_pct": round(avg_loss_pct, 4),
        },
        "equity_curve": equity_curve,
        "period_returns": {
            "monthly": _compress_period_returns(monthly_returns),
            "quarterly": _compress_period_returns(quarterly_returns),
            "yearly": _compress_period_returns(yearly_returns),
        },
        "trades": trades,
        "latest_recommendation": latest_recommendation,
    }
