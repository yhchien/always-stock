"""Shadow-mode entry point for Strategy v1 (frozen).

Design note on "resumable state" vs "replay-and-append":
Rather than hand-rolling incremental state persistence (fragile, easy to
desync from the real portfolio.py/run_backtest.py logic), this script
re-runs the FULL deterministic backtest against whatever daily_data.csv is
current, then appends rows ONLY for trade_dates that are not already present
in the permanent shadow log. Since Strategy v1's logic never changes after
being frozen, and the historical rows for already-processed days never
change either, re-deriving the whole history and diffing against the log is
equivalent to true incremental state -- and far less error-prone.

CRITICAL invariant enforced here: this script NEVER deletes or rewrites an
existing row in shadow_signal_log_v1.csv. If v1's logic is ever revised into
a new version, that must be a NEW script writing to a NEW log file -- v1's
log is an append-only, frozen record of what this exact version decided on
each day, using only the data available up to that day.

Usage (once new trading days exist beyond the current daily_data.csv):
    python3 run_shadow_daily.py --data-csv <path-to-updated-daily-data.csv>

For now (sanity-check phase, no new data exists yet), running this against
the current daily_data.csv is a no-op after the first run: every trade_date
is already logged, so zero new rows get appended.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest")

from backtest.loader import load_daily_panel, load_etf_ids
from backtest.run_backtest import run_backtest, _load_close_price_map

ROOT = "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest"
OUT = ROOT + "/output"

BASELINE_PARAMS_V1 = {
    "initial_capital": 600000, "unit_capital": 100000, "max_stocks": 5,
    "max_units_per_stock": 2, "max_total_units": 6,
    "setup_a": {"day_index_min": 2, "day_index_max": 3, "hit_count": 1,
                "momentum_min": 68, "momentum_max": 80, "return_min": -2.5,
                "return_max": 0, "p4": "CAUTION"},
    "setup_b": {"day_index_min": 2, "day_index_max": 4, "momentum_min": 65,
                "momentum_max": 85, "return_min": -10, "return_max": -8, "p4": "CAUTION"},
    "take_profit_signal_pct": 10, "exclude_etf": True, "fee_rate": 0.0, "tax_rate": 0.0,
}

V1_LOG_FIELDS = [
    "trade_date", "strategy_version", "stock_id", "stock_name", "action",
    "reason", "entry_execution_date", "exit_execution_date", "realized_return_pct",
    "virtual_cash", "virtual_invested_cost", "virtual_total_equity",
]


def _already_logged_dates(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8-sig") as f:
        return {row["trade_date"] for row in csv.DictReader(f)}


def _append_rows(path: str, fields: list, rows: list):
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def run_shadow_v1(data_csv: str, etf_ids: set, close_map: dict):
    rows = load_daily_panel(data_csv)
    result = run_backtest(rows, BASELINE_PARAMS_V1, etf_ids=etf_ids, close_price_map=close_map,
                           real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    log_path = OUT + "/shadow_signal_log_v1.csv"
    already = _already_logged_dates(log_path)

    new_rows = []
    for dp in result.daily_portfolio:
        d = dp.date.isoformat()
        if d in already:
            continue
        # one summary row per day; per-trade actions layered in from closed_trades/opens
        todays_exits = [t for t in result.closed_trades if t.exit_execution_date == dp.date]
        todays_entries_stocks = dp.open_positions.split(",") if dp.open_positions else []
        if not todays_exits and not todays_entries_stocks:
            new_rows.append({
                "trade_date": d, "strategy_version": "v1_frozen", "stock_id": "", "stock_name": "",
                "action": "NO_ACTIVITY", "reason": "", "entry_execution_date": "", "exit_execution_date": "",
                "realized_return_pct": "", "virtual_cash": dp.cash, "virtual_invested_cost": dp.invested_cost,
                "virtual_total_equity": dp.equity_if_available,
            })
        for t in todays_exits:
            new_rows.append({
                "trade_date": d, "strategy_version": "v1_frozen", "stock_id": t.stock_id, "stock_name": t.stock_name,
                "action": "SELL", "reason": t.exit_reason, "entry_execution_date": t.entry_execution_date.isoformat(),
                "exit_execution_date": t.exit_execution_date.isoformat(), "realized_return_pct": t.realized_return_pct,
                "virtual_cash": dp.cash, "virtual_invested_cost": dp.invested_cost,
                "virtual_total_equity": dp.equity_if_available,
            })
    _append_rows(log_path, V1_LOG_FIELDS, new_rows)
    print(f"[v1_frozen] appended {len(new_rows)} new rows to {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-csv", default=ROOT + "/daily_data.csv")
    args = parser.parse_args()

    etf_ids = load_etf_ids(OUT + "/etf_ids.json")
    close_map = _load_close_price_map(ROOT + "/raw_prices.json")

    run_shadow_v1(args.data_csv, etf_ids, close_map)
