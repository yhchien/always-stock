"""
Unit tests for app.analysis.price_structure.compute_price_structure.

Covers: trend 三分類、breakout、consolidation、accelerating、資料不足 null 路徑、no-hindsight。
"""
from datetime import date, timedelta

import pytest

from app.analysis.context_thresholds import (
    PRICE_BREAKOUT_BASELINE_DAYS,
    PRICE_CONSOLIDATION_RANGE_PCT,
    PRICE_TREND_UPTREND_SLOPE,
)
from app.analysis.price_structure import compute_price_structure
from app.models import DailyPrice

STOCK_ID = "2330"
INDUSTRY = "半導體業"


def _seed_prices(db, closes: list[float], end_date: date = date(2026, 4, 22)) -> None:
    """Seed `closes` ending at end_date (one trading day per entry, no weekend guard — SQLite accepts any)."""
    for i, c in enumerate(closes):
        d = end_date - timedelta(days=len(closes) - 1 - i)
        db.add(DailyPrice(trade_date=d, stock_id=STOCK_ID, close_price=c, volume=1_000_000))
    db.commit()


def test_returns_null_when_history_too_short(db):
    _seed_prices(db, [100.0] * 5)  # need >= 21

    result, notes = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result == {
        "trend": None,
        "is_breakout": None,
        "is_consolidation": None,
        "is_accelerating": None,
    }
    assert len(notes) == 1
    assert "price_structure is null" in notes[0]
    assert "5 rows" in notes[0]


def test_uptrend_detected_with_rising_closes(db):
    closes = [100 + i * 2 for i in range(25)]  # 100..148, monotonically up
    _seed_prices(db, closes)

    result, notes = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "uptrend"
    assert notes == []


def test_downtrend_detected_with_falling_closes(db):
    closes = [200 - i * 2 for i in range(25)]
    _seed_prices(db, closes)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "downtrend"


def test_sideways_when_slope_within_thresholds(db):
    # 整段 close 在 ±0.5% 內波動
    closes = [100.0, 100.3, 99.8, 100.1, 100.2, 99.9, 100.0, 100.1, 100.2, 100.0] * 3
    _seed_prices(db, closes[:25])

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "sideways"


def test_is_breakout_true_when_latest_above_prior_max(db):
    # 前 20 天收盤都在 100~102，最後一天跳到 110
    closes = [100.0 + (i % 3) for i in range(20)] + [110.0]
    _seed_prices(db, closes)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_breakout"] is True


def test_is_breakout_false_when_latest_below_prior_max(db):
    closes = [100.0 + i * 0.5 for i in range(20)] + [105.0]  # prior_max = 109.5
    _seed_prices(db, closes)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_breakout"] is False


def test_is_consolidation_true_when_recent_range_narrow(db):
    # 先 20 天爬升讓 breakout baseline 充分，再 10 天收斂在 1% 內
    warmup = [80.0 + i * 0.5 for i in range(21)]
    tight = [100.0, 100.3, 100.1, 100.4, 100.2, 100.0, 100.3, 100.1, 100.2, 100.0]
    _seed_prices(db, warmup + tight)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_consolidation"] is True


def test_is_consolidation_false_when_range_wide(db):
    closes = [80.0 + i * 0.5 for i in range(21)] + [90, 110, 95, 108, 92, 111, 94, 109, 93, 112]
    _seed_prices(db, closes)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_consolidation"] is False


def test_is_accelerating_true_when_second_half_slope_steepens(db):
    warmup = [50.0] * 21  # flat baseline (無 breakout 干擾 accelerating 判斷)
    # 前半 10 天幾乎持平，後半 5 天快速拉升（在 10 日視窗內前後半對比 >= 1.5）
    tail = [50.0, 50.1, 50.2, 50.3, 50.4, 55.0, 60.0, 65.0, 70.0, 75.0]
    _seed_prices(db, warmup + tail)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_accelerating"] is True


def test_is_accelerating_false_when_slope_flat(db):
    closes = [100.0 + i * 0.1 for i in range(25)]  # 穩定線性、不加速
    _seed_prices(db, closes)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["is_accelerating"] is False


def test_no_hindsight_future_data_does_not_affect_output(db):
    closes = [100.0 + i * 2 for i in range(25)]
    _seed_prices(db, closes, end_date=date(2026, 4, 22))
    # 塞一筆 buy_date 之後的資料（若有 hindsight 會污染 trend）
    db.add(DailyPrice(trade_date=date(2026, 4, 25), stock_id=STOCK_ID, close_price=10.0))
    db.commit()

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "uptrend"  # 若 hindsight 進來 trend 會變 downtrend


def test_ignores_other_stocks(db):
    closes = [100.0 + i * 2 for i in range(25)]
    _seed_prices(db, closes)
    # 別檔股票的 noise 資料
    for i in range(25):
        db.add(DailyPrice(
            trade_date=date(2026, 4, 22) - timedelta(days=24 - i),
            stock_id="9999",
            close_price=999.0,
        ))
    db.commit()

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "uptrend"


def test_uptrend_threshold_edge_case(db):
    """slope 剛好等於 threshold 也算 uptrend（>=）。"""
    # 起點 100，要讓 (end - 100)/100 >= PRICE_TREND_UPTREND_SLOPE
    target_end = 100 * (1 + PRICE_TREND_UPTREND_SLOPE)
    # 先 15 天 ramp up 讓 breakout baseline 穩定，最後 10 天從 100 線性到 target_end
    warmup = [80.0 + i * 1 for i in range(15)]
    last_10 = [100.0 + i * ((target_end - 100) / 9) for i in range(10)]
    _seed_prices(db, warmup + last_10)

    result, _ = compute_price_structure(db, STOCK_ID, date(2026, 4, 22), INDUSTRY)

    assert result["trend"] == "uptrend"
