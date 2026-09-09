import sys
sys.path.insert(0, "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest")

from backtest.loader import load_daily_panel, load_etf_ids
from backtest.run_backtest import run_backtest, _load_close_price_map

PARAMS = {
    "initial_capital": 600000,
    "unit_capital": 100000,
    "max_stocks": 5,
    "max_units_per_stock": 2,
    "max_total_units": 6,
    "setup_a": {
        "day_index_min": 2, "day_index_max": 3, "hit_count": 1,
        "momentum_min": 68, "momentum_max": 80,
        "return_min": -2.5, "return_max": 0, "p4": "CAUTION",
    },
    "setup_b": {
        "day_index_min": 2, "day_index_max": 4,
        "momentum_min": 65, "momentum_max": 85,
        "return_min": -10, "return_max": -8, "p4": "CAUTION",
    },
    "take_profit_signal_pct": 10,
    "exclude_etf": True,
    "fee_rate": 0.0,
    "tax_rate": 0.0,
}

rows = load_daily_panel("/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/daily_data.csv")
etf_ids = load_etf_ids("/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/output/etf_ids.json")
close_map = _load_close_price_map("/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/raw_prices.json")

print(f"loaded {len(rows)} rows, {len(etf_ids)} etf ids")

result = run_backtest(
    rows, PARAMS, etf_ids=etf_ids, close_price_map=close_map,
    real_stop_loss_pct=-8.0, allow_fractional_shares=True,
)

print(f"final_equity={result.final_equity:,.0f}  trades={len(result.closed_trades)}  skipped={len(result.skipped_events)}")
print(f"total_return_pct={(result.final_equity - PARAMS['initial_capital'])/PARAMS['initial_capital']*100:+.2f}%")
for t in result.closed_trades[:5]:
    print(t.stock_id, t.entry_type, t.entry_execution_date, t.exit_execution_date, t.exit_reason, round(t.realized_return_pct, 2))
print("open at end:", result.open_positions_at_end)
print("skipped sample:", result.skipped_events[:5])
