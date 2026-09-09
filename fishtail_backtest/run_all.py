import copy
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest")

from backtest.control_strategies import (
    control1_entry, control2_entry, control_exit_p4_only, control_rank,
)
from backtest.loader import load_daily_panel, load_etf_ids, trading_calendar
from backtest.metrics import compute_metrics
from backtest.report import (
    entry_type_attribution, format_summary, top_n,
    write_daily_portfolio_csv, write_trades_csv,
)
from backtest.run_backtest import run_backtest, _load_close_price_map

ROOT = "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest"
OUT = ROOT + "/output"

BASELINE_PARAMS = {
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
}

TW_FEE_RATE = 0.001425 * 0.5  # typical discounted retail brokerage, one side
TW_SELL_TAX = 0.003

rows = load_daily_panel(ROOT + "/daily_data.csv")
etf_ids = load_etf_ids(OUT + "/etf_ids.json")
close_map = _load_close_price_map(ROOT + "/raw_prices.json")
calendar = trading_calendar(rows)

print(f"Loaded {len(rows)} cohort-day rows across {len(calendar)} trading days "
      f"({calendar[0]} ~ {calendar[-1]}), {len(etf_ids)} ETF stock_ids identified.\n")


def run(params, label, **kwargs):
    result = run_backtest(rows, params, etf_ids=etf_ids, close_price_map=close_map, **kwargs)
    m = compute_metrics(
        initial_capital=params["initial_capital"],
        final_equity=result.final_equity,
        closed_trades=result.closed_trades,
        equity_curve=result.equity_curve,
        max_concurrent_stocks=result.max_concurrent_stocks,
        max_capital_usage_pct=result.max_capital_usage_pct,
    )
    return result, m


# =========================================================================
# Section 17: Baseline Strategy v1
# =========================================================================
print("#" * 70)
print("# BASELINE STRATEGY v1")
print("#" * 70)

params_v1_no_cost = copy.deepcopy(BASELINE_PARAMS)
params_v1_no_cost["fee_rate"] = 0.0
params_v1_no_cost["tax_rate"] = 0.0
result_v1, metrics_v1 = run(
    params_v1_no_cost, "v1 no-cost", real_stop_loss_pct=-8.0, allow_fractional_shares=True,
)
print(format_summary(metrics_v1, result_v1, "Strategy v1 -- Version 1 (no transaction cost)"))
write_trades_csv(result_v1, OUT + "/trades_v1_no_cost.csv")
write_daily_portfolio_csv(result_v1, OUT + "/daily_portfolio_v1_no_cost.csv")

params_v1_cost = copy.deepcopy(BASELINE_PARAMS)
params_v1_cost["fee_rate"] = TW_FEE_RATE
params_v1_cost["tax_rate"] = TW_SELL_TAX
result_v1c, metrics_v1c = run(
    params_v1_cost, "v1 with-cost", real_stop_loss_pct=-8.0, allow_fractional_shares=True,
)
print()
print(format_summary(metrics_v1c, result_v1c, "Strategy v1 -- Version 2 (with TW transaction cost)"))
write_trades_csv(result_v1c, OUT + "/trades_v1_with_cost.csv")
write_daily_portfolio_csv(result_v1c, OUT + "/daily_portfolio_v1_with_cost.csv")

print(f"\nSkipped events (v1 no-cost run): {len(result_v1.skipped_events)}")
for e in result_v1.skipped_events:
    print(f"  {e.date} {e.stock_id}: {e.reason}")
print(f"\nOpen positions at end (v1 no-cost run):")
for p in result_v1.open_positions_at_end:
    print(f"  {p}")

# ---- top winners / losers, entry type attribution ----
print("\n--- Top 10 winners (v1 no-cost) ---")
for t in top_n(result_v1.closed_trades, 10, winners=True):
    print(f"  {t.stock_id} {t.stock_name:8s} entry={t.entry_execution_date} exit={t.exit_execution_date} "
          f"({t.exit_reason}) pnl={t.realized_pnl:+,.0f} ({t.realized_return_pct:+.2f}%)")

print("\n--- Top 10 losers (v1 no-cost) ---")
for t in top_n(result_v1.closed_trades, 10, winners=False):
    print(f"  {t.stock_id} {t.stock_name:8s} entry={t.entry_execution_date} exit={t.exit_execution_date} "
          f"({t.exit_reason}) pnl={t.realized_pnl:+,.0f} ({t.realized_return_pct:+.2f}%)")

print("\n--- Entry type attribution (v1 no-cost) ---")
for etype, stats in entry_type_attribution(result_v1.closed_trades).items():
    print(f"  {etype}: n={stats['trade_count']}  win_rate={stats['win_rate_pct']:.1f}%  "
          f"total_pnl={stats['total_pnl']:+,.0f}  avg_return={stats['avg_return_pct']:+.2f}%")

# =========================================================================
# Section 25: Control groups (same capital/limits/execution assumptions)
# =========================================================================
print("\n" + "#" * 70)
print("# CONTROL GROUPS")
print("#" * 70)

control_common = {
    "initial_capital": 600000,
    "unit_capital": 100000,
    "max_stocks": 5,
    "max_units_per_stock": 2,
    "max_total_units": 6,
    "exclude_etf": True,
    "fee_rate": 0.0,
    "tax_rate": 0.0,
    "setup_a": BASELINE_PARAMS["setup_a"],  # unused by control strategies, kept for interface compat
    "setup_b": BASELINE_PARAMS["setup_b"],
    "take_profit_signal_pct": None,
}

result_c1, metrics_c1 = run(
    control_common, "control1",
    generate_entry_fn=control1_entry, generate_exit_fn=control_exit_p4_only, rank_fn=control_rank,
)
print(format_summary(metrics_c1, result_c1, "Control 1 -- P3 first pick only, hold to P4 STOP"))

result_c2, metrics_c2 = run(
    control_common, "control2",
    generate_entry_fn=control2_entry, generate_exit_fn=control_exit_p4_only, rank_fn=control_rank,
)
print()
print(format_summary(metrics_c2, result_c2, "Control 2 -- buy/add-on every P3 reselection, hold to P4 STOP"))

# Control 3 (§25): purely P4-driven; entry mirrors Control 1 in absence of a
# separate spec'd entry rule. Documented explicitly since it is expected to
# numerically match Control 1 on this dataset (see control_strategies.py).
result_c3, metrics_c3 = run(
    control_common, "control3",
    generate_entry_fn=control1_entry, generate_exit_fn=control_exit_p4_only, rank_fn=control_rank,
)
print()
print(format_summary(metrics_c3, result_c3, "Control 3 -- pure P4 lifecycle (CONTINUE/CAUTION=hold, STOP=sell); entry mirrors Control 1"))
print(f"\n[Control 1 vs Control 3 identical check] C1 return={metrics_c1.total_return_pct:.4f}% "
      f"C3 return={metrics_c3.total_return_pct:.4f}% -> "
      f"{'IDENTICAL as expected/documented' if abs(metrics_c1.total_return_pct - metrics_c3.total_return_pct) < 1e-9 else 'DIFFER -- investigate'}")

print("\n" + "=" * 70)
print("STRATEGY COMPARISON TABLE (same capital/limits/execution assumptions)")
print("=" * 70)
print(f"{'Strategy':45s} {'Return':>8s} {'Trades':>7s} {'WinRate':>8s} {'MaxDD':>7s}")
for label, m in [
    ("Strategy v1 (no cost)", metrics_v1),
    ("Strategy v1 (with cost)", metrics_v1c),
    ("Control 1: P3 first pick, hold to STOP", metrics_c1),
    ("Control 2: buy every P3 reselect", metrics_c2),
    ("Control 3: pure P4 lifecycle", metrics_c3),
]:
    wr = f"{m.win_rate_pct:.1f}%" if m.win_rate_pct is not None else "n/a"
    print(f"{label:45s} {m.total_return_pct:+7.2f}% {m.trade_count:7d} {wr:>8s} {m.max_drawdown_pct:6.2f}%")

# =========================================================================
# Section 19 / 24 F-J: detailed attribution on Strategy v1 (no-cost) trades
# =========================================================================
print("\n" + "#" * 70)
print("# DETAILED ATTRIBUTION (Strategy v1, no-cost)")
print("#" * 70)

trades = result_v1.closed_trades


def bucket_report(title, bucket_fn, order):
    print(f"\n--- {title} ---")
    groups = defaultdict(list)
    for t in trades:
        groups[bucket_fn(t)].append(t)
    for key in order:
        g = groups.get(key, [])
        if not g:
            print(f"  {key}: n=0")
            continue
        n = len(g)
        wins = sum(1 for t in g if t.realized_pnl > 0)
        avg = sum(t.realized_return_pct for t in g) / n
        med = sorted(t.realized_return_pct for t in g)[n // 2]
        total_pnl = sum(t.realized_pnl for t in g)
        print(f"  {key:12s} n={n:3d}  win_rate={wins/n*100:5.1f}%  avg={avg:+6.2f}%  median={med:+6.2f}%  total_pnl={total_pnl:+10,.0f}")


# F. P3 reselected vs not
bucket_report(
    "F: entry_p3_selected (did P3 also pick this stock on the entry day?)",
    lambda t: "reselected" if t.entry_p3_selected else "not_reselected",
    ["reselected", "not_reselected"],
)

# G. hit_count at entry
def hit_bucket(t):
    if t.entry_hit_count <= 1:
        return "1"
    if t.entry_hit_count == 2:
        return "2"
    return "3+"

bucket_report("G: entry_hit_count", hit_bucket, ["1", "2", "3+"])

# H. day_index at entry
def day_bucket(t):
    if t.entry_day_index <= 1:
        return "1"
    if t.entry_day_index == 2:
        return "2"
    if t.entry_day_index == 3:
        return "3"
    if t.entry_day_index == 4:
        return "4"
    return "5+"

bucket_report("H: entry_day_index", day_bucket, ["1", "2", "3", "4", "5+"])

# I. momentum_score bucket
def mom_bucket(t):
    m = t.entry_momentum
    if m is None:
        return "unknown"
    for lo, hi, label in [(0, 60, "<60"), (60, 65, "60-65"), (65, 70, "65-70"),
                           (70, 75, "70-75"), (75, 80, "75-80"), (80, 85, "80-85"),
                           (85, 1000, "85+")]:
        if lo <= m < hi:
            return label
    return "unknown"

bucket_report("I: entry_momentum bucket", mom_bucket,
              ["<60", "60-65", "65-70", "70-75", "75-80", "80-85", "85+", "unknown"])

# J. mark_to_market_return_pct at entry bucket
def ret_bucket(t):
    r = t.entry_mark_to_market_return
    if r is None:
        return "unknown"
    if r > 0:
        return ">0"
    for lo, hi, label in [(-2, 0, "0~-2"), (-4, -2, "-2~-4"), (-6, -4, "-4~-6"),
                           (-8, -6, "-6~-8"), (-10, -8, "-8~-10")]:
        if lo <= r < hi:
            return label
    return "<-10"

bucket_report("J: entry_mark_to_market_return bucket", ret_bucket,
              [">0", "0~-2", "-2~-4", "-4~-6", "-6~-8", "-8~-10", "<-10", "unknown"])

# =========================================================================
# Section 24: honest analysis A-E
# =========================================================================
print("\n" + "#" * 70)
print("# ANSWERS TO SECTION 24 QUESTIONS")
print("#" * 70)
print(f"""
A. Full-period Strategy v1 (no cost) return >= +10%?
   -> {metrics_v1.total_return_pct:+.2f}%  {'YES' if metrics_v1.total_return_pct >= 10 else 'NO'}

B. After transaction cost, still >= +10%?
   -> {metrics_v1c.total_return_pct:+.2f}%  {'YES' if metrics_v1c.total_return_pct >= 10 else 'NO'}

C. Remove the single best trade -- still positive?
   -> {metrics_v1.return_without_best_trade_pct:+.2f}%  {'YES, still positive' if metrics_v1.return_without_best_trade_pct > 0 else 'NO, turns negative/flat'}

D. Remove the top-3 trades -- still positive?
   -> {metrics_v1.return_without_top_3_trades_pct:+.2f}%  {'YES, still positive' if metrics_v1.return_without_top_3_trades_pct > 0 else 'NO, turns negative/flat'}
""")

attribution = entry_type_attribution(trades)
print("E. SETUP_A vs SETUP_B -- which has real edge?")
for k, v in attribution.items():
    print(f"   {k}: n={v['trade_count']} win_rate={v['win_rate_pct']:.1f}% avg_return={v['avg_return_pct']:+.2f}% total_pnl={v['total_pnl']:+,.0f}")

# =========================================================================
# Section 18: Parameter robustness (REDUCED grid -- honest note in report.py
# call site: full cartesian product from the spec is 5*2*4*4*4*3*6*3*2 =
# tens of thousands of combos; with only ~21 trading days / ~28 baseline
# trades, that grid would be pure overfitting theater, not robustness
# testing. Instead we vary the dimensions the baseline run already flagged
# as decisive -- SETUP_B's return band (since 100% of the edge sits there),
# take-profit threshold, and max_stocks -- one dimension at a time.)
# =========================================================================
print("\n" + "#" * 70)
print("# PARAMETER ROBUSTNESS (targeted, not full grid -- see note above)")
print("#" * 70)

print("\n--- Setup B return_min/return_max sensitivity ---")
for rmin, rmax in [(-12, -8), (-11, -7), (-10, -8), (-10, -6), (-9, -7)]:
    p = copy.deepcopy(BASELINE_PARAMS)
    p["setup_b"]["return_min"] = rmin
    p["setup_b"]["return_max"] = rmax
    p["fee_rate"] = 0.0
    p["tax_rate"] = 0.0
    r, m = run(p, f"setup_b_{rmin}_{rmax}", real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    print(f"  return_min={rmin:>4} return_max={rmax:>3}: return={m.total_return_pct:+7.2f}%  "
          f"trades={m.trade_count:3d}  win_rate={m.win_rate_pct or 0:.1f}%  "
          f"w/o_top3={m.return_without_top_3_trades_pct:+.2f}%")

print("\n--- Take-profit threshold sensitivity ---")
for tp in [8, 9, 10, 11, 12, 15]:
    p = copy.deepcopy(BASELINE_PARAMS)
    p["take_profit_signal_pct"] = tp
    p["fee_rate"] = 0.0
    p["tax_rate"] = 0.0
    r, m = run(p, f"tp_{tp}", real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    print(f"  take_profit={tp:>2}%: return={m.total_return_pct:+7.2f}%  trades={m.trade_count:3d}  "
          f"win_rate={m.win_rate_pct or 0:.1f}%")

print("\n--- max_stocks sensitivity ---")
for ms in [3, 4, 5]:
    p = copy.deepcopy(BASELINE_PARAMS)
    p["max_stocks"] = ms
    p["fee_rate"] = 0.0
    p["tax_rate"] = 0.0
    r, m = run(p, f"ms_{ms}", real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    print(f"  max_stocks={ms}: return={m.total_return_pct:+7.2f}%  trades={m.trade_count:3d}  "
          f"win_rate={m.win_rate_pct or 0:.1f}%")

print("\n--- max_units_per_stock sensitivity ---")
for mu in [1, 2]:
    p = copy.deepcopy(BASELINE_PARAMS)
    p["max_units_per_stock"] = mu
    p["fee_rate"] = 0.0
    p["tax_rate"] = 0.0
    r, m = run(p, f"mu_{mu}", real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    print(f"  max_units_per_stock={mu}: return={m.total_return_pct:+7.2f}%  trades={m.trade_count:3d}  "
          f"win_rate={m.win_rate_pct or 0:.1f}%")

print("\n--- Setup A return_min/momentum sensitivity (the *large* n=25 bucket) ---")
for rmin, rmax in [(-1.5, 0), (-2.0, 0), (-2.5, 0), (-3.0, 0), (-3.5, 0), (-2.5, 0.5)]:
    p = copy.deepcopy(BASELINE_PARAMS)
    p["setup_a"]["return_min"] = rmin
    p["setup_a"]["return_max"] = rmax
    p["fee_rate"] = 0.0
    p["tax_rate"] = 0.0
    r, m = run(p, f"setup_a_{rmin}_{rmax}", real_stop_loss_pct=-8.0, allow_fractional_shares=True)
    print(f"  return_min={rmin:>5} return_max={rmax:>4}: return={m.total_return_pct:+7.2f}%  "
          f"trades={m.trade_count:3d}  win_rate={m.win_rate_pct or 0:.1f}%")

# =========================================================================
# Section 20: Walk-forward / time-segmented check
#
# HONEST CAVEAT: only 21 trading days total exist in this dataset. A
# textbook train(60%)/val(20%)/test(20%) split gives ~12/4/5 trading days
# per segment -- nowhere near enough to fit AND validate parameters
# independently. We do NOT re-fit SETUP_A/B on an early segment and test on
# a later one (there isn't enough data in either half to do that
# meaningfully). Instead, as the most honest thing achievable with this
# sample size, we take the SAME baseline params (chosen by inspecting the
# whole window, including 光鼎/台虹) and check whether the trades that
# generated the return are spread across the window or concentrated in one
# narrow slice -- if the edge is real and not a look-back artifact, it
# should not depend entirely on one segment.
# =========================================================================
print("\n" + "#" * 70)
print("# TIME-SEGMENTED CHECK (see honesty caveat above -- not a true walk-forward)")
print("#" * 70)

n_days = len(calendar)
cut1 = calendar[int(n_days * 0.4)]
cut2 = calendar[int(n_days * 0.7)]
print(f"Calendar: {calendar[0]} ~ {calendar[-1]} ({n_days} days)")
print(f"Segment 1 (early, entry_execution_date < {cut1}): first 40% of days")
print(f"Segment 2 (mid,   {cut1} <= entry_execution_date < {cut2}): next 30%")
print(f"Segment 3 (late,  entry_execution_date >= {cut2}): last 30%")

for label, lo, hi in [("Segment 1 (early)", date.min, cut1), ("Segment 2 (mid)", cut1, cut2), ("Segment 3 (late)", cut2, date.max)]:
    seg_trades = [t for t in trades if lo <= t.entry_execution_date < hi]
    n = len(seg_trades)
    if n == 0:
        print(f"  {label}: n=0 trades")
        continue
    wins = sum(1 for t in seg_trades if t.realized_pnl > 0)
    total_pnl = sum(t.realized_pnl for t in seg_trades)
    avg = sum(t.realized_return_pct for t in seg_trades) / n
    deep = sum(1 for t in seg_trades if t.entry_type == "DEEP_PULLBACK")
    print(f"  {label}: n={n:3d}  win_rate={wins/n*100:5.1f}%  avg={avg:+.2f}%  total_pnl={total_pnl:+10,.0f}  (DEEP_PULLBACK count: {deep})")
