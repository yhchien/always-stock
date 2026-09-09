import sys
from datetime import date

sys.path.insert(0, "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest")

from backtest.loader import CohortDayRow, load_daily_panel, trading_calendar
from backtest.execution import next_trading_day, resolve_buy_fill, resolve_sell_fill
from backtest.portfolio import Portfolio
from backtest.run_backtest import run_backtest


CSV_PATH = "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/daily_data.csv"

BASE_PARAMS = {
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
    "exclude_etf": False,
    "fee_rate": 0.0,
    "tax_rate": 0.0,
}


def make_row(**overrides):
    defaults = dict(
        stock_id="TEST", stock_name="測試", first_seen_date=date(2026, 8, 1),
        day_index=1, trade_date=date(2026, 8, 1), p3_selected_today=True,
        hit_count_so_far=1, momentum_score=75.0, p4_decision=None,
        mark_to_market_return_pct=None, next_day_buy_price_if_entering_today=100.0,
        next_day_sell_price_if_exiting_today=95.0, is_official_exit_signal_day=False,
    )
    defaults.update(overrides)
    return CohortDayRow(**defaults)


# ---- 1. No lookahead: generate_entry_signal / generate_exit_signal never
# touch next_day_* fields (structural test: verify by removing them). ----
def test_entry_signal_ignores_next_day_prices():
    from backtest import signals
    row = make_row(day_index=2, p4_decision="CAUTION", momentum_score=75,
                    mark_to_market_return_pct=-1.0, hit_count_so_far=1,
                    next_day_buy_price_if_entering_today=None,
                    next_day_sell_price_if_exiting_today=None)
    sig = signals.generate_entry_signal(row, BASE_PARAMS)
    assert sig is not None, "entry signal must fire using only same-day fields, even if next-day prices are missing"
    print("PASS: entry signal ignores next_day_* fields")


def test_exit_signal_ignores_next_day_prices():
    from backtest import signals
    row = make_row(p4_decision="STOP_OBSERVING",
                    next_day_buy_price_if_entering_today=None,
                    next_day_sell_price_if_exiting_today=None)
    sig = signals.generate_exit_signal(row, None, BASE_PARAMS)
    assert sig is not None and sig.reason == "P4_STOP"
    print("PASS: exit signal ignores next_day_* fields")


# ---- 2. BUY uses next_day_buy_price ----
def test_buy_fill_uses_next_day_high():
    calendar = [date(2026, 8, 1), date(2026, 8, 2)]
    row = make_row(trade_date=date(2026, 8, 1), next_day_buy_price_if_entering_today=123.4)
    exec_date, price = resolve_buy_fill(row, calendar)
    assert exec_date == date(2026, 8, 2)
    assert price == 123.4
    print("PASS: buy fill uses next_day_buy_price_if_entering_today")


# ---- 3. SELL uses next_day_sell_price ----
def test_sell_fill_uses_next_day_low():
    calendar = [date(2026, 8, 1), date(2026, 8, 2)]
    row = make_row(trade_date=date(2026, 8, 1), next_day_sell_price_if_exiting_today=88.8)
    exec_date, price = resolve_sell_fill(row, calendar)
    assert exec_date == date(2026, 8, 2)
    assert price == 88.8
    print("PASS: sell fill uses next_day_sell_price_if_exiting_today")


# ---- 4/5/6/7. Portfolio limits + cash never negative ----
def test_portfolio_enforces_stock_and_unit_limits():
    p = Portfolio(initial_capital=1000, unit_capital=100, max_stocks=2,
                  max_units_per_stock=1, max_total_units=2)
    row = make_row()
    lot1 = p.buy(stock_id="A", stock_name="A", entry_type="X", signal_first_seen_date=row.first_seen_date,
                 entry_signal_date=row.trade_date, entry_execution_date=row.trade_date,
                 entry_price=10, allow_fractional=True, entry_row=row)
    assert lot1 is not None
    lot2 = p.buy(stock_id="B", stock_name="B", entry_type="X", signal_first_seen_date=row.first_seen_date,
                 entry_signal_date=row.trade_date, entry_execution_date=row.trade_date,
                 entry_price=10, allow_fractional=True, entry_row=row)
    assert lot2 is not None
    # 3rd stock should be rejected: max_stocks=2
    lot3 = p.buy(stock_id="C", stock_name="C", entry_type="X", signal_first_seen_date=row.first_seen_date,
                 entry_signal_date=row.trade_date, entry_execution_date=row.trade_date,
                 entry_price=10, allow_fractional=True, entry_row=row)
    assert lot3 is None, "must not exceed max_stocks"
    # add-on to A should be rejected: max_units_per_stock=1
    lot4 = p.buy(stock_id="A", stock_name="A", entry_type="X", signal_first_seen_date=row.first_seen_date,
                 entry_signal_date=row.trade_date, entry_execution_date=row.trade_date,
                 entry_price=10, allow_fractional=True, entry_row=row)
    assert lot4 is None, "must not exceed max_units_per_stock"
    assert p.cash >= 0
    print("PASS: portfolio enforces max_stocks / max_units_per_stock / max_total_units, cash never negative")


def test_portfolio_cash_never_negative_when_insufficient():
    p = Portfolio(initial_capital=50, unit_capital=100, max_stocks=5,
                  max_units_per_stock=2, max_total_units=6)
    row = make_row()
    lot = p.buy(stock_id="A", stock_name="A", entry_type="X", signal_first_seen_date=row.first_seen_date,
                entry_signal_date=row.trade_date, entry_execution_date=row.trade_date,
                entry_price=10, allow_fractional=True, entry_row=row)
    assert lot is None, "spec §4: if cash < unit_capital, do not open a new position"
    assert p.cash == 50
    print("PASS: no new position opened when cash < unit_capital; cash unchanged")


# ---- 8. Same stock, different cohorts, not double-counted as different stocks ----
def test_duplicate_cohort_same_stock_not_double_held():
    p = Portfolio(initial_capital=1000, unit_capital=100, max_stocks=5,
                  max_units_per_stock=2, max_total_units=6)
    row_a = make_row(stock_id="X", first_seen_date=date(2026, 8, 1))
    row_b = make_row(stock_id="X", first_seen_date=date(2026, 8, 5))
    p.buy(stock_id="X", stock_name="X", entry_type="A", signal_first_seen_date=row_a.first_seen_date,
          entry_signal_date=row_a.trade_date, entry_execution_date=row_a.trade_date,
          entry_price=10, allow_fractional=True, entry_row=row_a)
    assert len(p.positions) == 1
    p.buy(stock_id="X", stock_name="X", entry_type="B", signal_first_seen_date=row_b.first_seen_date,
          entry_signal_date=row_b.trade_date, entry_execution_date=row_b.trade_date,
          entry_price=12, allow_fractional=True, entry_row=row_b)
    assert len(p.positions) == 1, "same stock_id from a different cohort must add a unit, not a new position"
    assert p.positions["X"].units == 2
    print("PASS: two cohorts of the same stock_id share a single Position")


# ---- 9. STOP_OBSERVING triggers exit correctly (integration, via run_backtest) ----
def test_stop_observing_triggers_exit_end_to_end():
    rows = load_daily_panel(CSV_PATH)
    result = run_backtest(rows, BASE_PARAMS, allow_fractional_shares=True)
    p4_stop_trades = [t for t in result.closed_trades if t.exit_reason == "P4_STOP"]
    assert len(p4_stop_trades) > 0, "expected at least one real trade to exit via P4_STOP in this dataset"
    for t in p4_stop_trades:
        assert t.exit_p4 == "STOP_OBSERVING"
    print(f"PASS: {len(p4_stop_trades)} trades correctly exited on P4_STOP with exit_p4=STOP_OBSERVING")


# ---- 10. Missing execution price at end of data produces no fake fill ----
def test_missing_price_does_not_fake_fill():
    rows = load_daily_panel(CSV_PATH)
    result = run_backtest(rows, BASE_PARAMS, allow_fractional_shares=True)
    no_price_skips = [e for e in result.skipped_events if "PRICE" in e.reason]
    # dataset's last day (2026-09-04) has several rows with blank next-day
    # prices (see CSV) -- assert none of those silently became a trade with
    # a fabricated price.
    traded_stock_dates = {(t.stock_id, t.entry_execution_date) for t in result.closed_trades}
    for e in no_price_skips:
        assert (e.stock_id, e.date) not in traded_stock_dates
    print(f"PASS: {len(no_price_skips)} missing-price events recorded, none produced a fake fill")


# ---- 11. Every realized_pnl is recomputable from entry/exit price+shares ----
def test_realized_pnl_recomputable_from_trade_log():
    rows = load_daily_panel(CSV_PATH)
    result = run_backtest(rows, BASE_PARAMS, allow_fractional_shares=True)
    assert len(result.closed_trades) > 0
    for t in result.closed_trades:
        recomputed = t.shares * t.exit_price - t.allocation
        assert abs(recomputed - t.realized_pnl) < 1e-6, (
            f"{t.stock_id}: recomputed {recomputed} != logged {t.realized_pnl}"
        )
    print(f"PASS: all {len(result.closed_trades)} trades' realized_pnl recompute exactly from the trade log")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
