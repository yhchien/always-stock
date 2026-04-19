"""K 棒與技術型態 detector 單元測試。

每個 detector 直接吃 series dict + index，不依賴 DB。
"""
from app.backtest_patterns import (
    PATTERN_DISPATCH,
    PATTERN_LOOKBACK,
    detect_a_reversal,
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_bullish_piercing,
    detect_dark_cloud_cover,
    detect_doji,
    detect_double_bottom,
    detect_double_top,
    detect_evening_star,
    detect_hammer,
    detect_hanging_man,
    detect_head_shoulders_bottom,
    detect_head_shoulders_top,
    detect_inverted_hammer,
    detect_long_bearish,
    detect_long_bullish,
    detect_morning_star,
    detect_shooting_star,
    detect_three_black_crows,
    detect_three_white_soldiers,
    detect_v_reversal,
)


def _make_series(ohlc_rows):
    """ohlc_rows: list of (open, high, low, close)."""
    return {
        "open": [row[0] for row in ohlc_rows],
        "high": [row[1] for row in ohlc_rows],
        "low": [row[2] for row in ohlc_rows],
        "close": [row[3] for row in ohlc_rows],
    }


# ── 單根 K 棒 ──────────────────────────────────────────────────────────────

def test_doji_detected_when_body_small():
    series = _make_series([(100.0, 102.0, 98.0, 100.1)])
    assert detect_doji(series, 0) is True


def test_doji_not_detected_when_body_large():
    series = _make_series([(100.0, 105.0, 99.0, 104.5)])
    assert detect_doji(series, 0) is False


def test_hammer_requires_downtrend_before():
    # 前 5 日收盤遞減（120 → 108），第 6 日出現錘子線（長下影、小實體）
    rows = [(120.0 - i * 3, 121.0 - i * 3, 118.0 - i * 3, 120.0 - (i + 1) * 3) for i in range(5)]
    rows.append((106.0, 106.5, 99.0, 105.5))
    series = _make_series(rows)
    assert detect_hammer(series, 5) is True


def test_hammer_rejected_when_uptrend():
    rows = [(100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i) for i in range(5)]
    rows.append((106.0, 106.5, 99.0, 105.5))
    series = _make_series(rows)
    # 上漲後的錘子應屬於 hanging_man，不是 hammer
    assert detect_hammer(series, 5) is False
    assert detect_hanging_man(series, 5) is True


def test_shooting_star_requires_uptrend():
    rows = [(100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i) for i in range(5)]
    rows.append((105.0, 115.0, 104.5, 105.5))
    series = _make_series(rows)
    assert detect_shooting_star(series, 5) is True


def test_inverted_hammer_requires_downtrend():
    rows = [(120.0 - i * 3, 121.0 - i * 3, 118.0 - i * 3, 120.0 - (i + 1) * 3) for i in range(5)]
    rows.append((105.0, 115.0, 104.5, 105.5))
    series = _make_series(rows)
    assert detect_inverted_hammer(series, 5) is True


def test_long_bullish_needs_large_body_vs_recent():
    # 前 10 日小實體 ±0.2，第 11 日長紅 body=5
    rows = [(100.0, 100.3, 99.7, 100.1) for _ in range(10)]
    rows.append((100.0, 105.5, 100.0, 105.0))
    series = _make_series(rows)
    assert detect_long_bullish(series, 10) is True


def test_long_bearish_symmetric():
    rows = [(100.0, 100.3, 99.7, 100.1) for _ in range(10)]
    rows.append((105.0, 105.0, 99.5, 100.0))
    series = _make_series(rows)
    assert detect_long_bearish(series, 10) is True


# ── 組合 K 棒 ──────────────────────────────────────────────────────────────

def test_bullish_engulfing():
    rows = [
        (103.0, 104.0, 100.0, 101.0),  # 黑 K：open 103 > close 101
        (100.0, 106.0, 99.5, 105.0),  # 紅 K：實體包住前一根
    ]
    series = _make_series(rows)
    assert detect_bullish_engulfing(series, 1) is True


def test_bearish_engulfing():
    rows = [
        (100.0, 104.0, 99.5, 103.0),  # 紅 K
        (104.0, 105.0, 98.0, 99.0),  # 黑 K：實體包住前一根
    ]
    series = _make_series(rows)
    assert detect_bearish_engulfing(series, 1) is True


def test_three_white_soldiers():
    rows = [
        (100.0, 103.0, 99.5, 102.0),
        (101.5, 105.0, 101.0, 104.0),  # open 在前一根實體內、close 新高
        (103.5, 107.0, 103.0, 106.0),
    ]
    series = _make_series(rows)
    assert detect_three_white_soldiers(series, 2) is True


def test_three_black_crows():
    rows = [
        (106.0, 106.5, 103.0, 104.0),
        (105.0, 105.5, 101.0, 102.0),
        (103.0, 103.5, 99.0, 100.0),
    ]
    series = _make_series(rows)
    assert detect_three_black_crows(series, 2) is True


def test_morning_star():
    rows = [
        (110.0, 110.5, 103.0, 103.5),  # 長黑
        (102.5, 103.0, 101.5, 102.0),  # 小實體且實體在前一根實體下方
        (102.5, 109.5, 102.0, 108.5),  # 長紅收回前一根實體中點以上（mid = 106.75）
    ]
    series = _make_series(rows)
    assert detect_morning_star(series, 2) is True


def test_evening_star():
    rows = [
        (100.0, 110.5, 99.5, 110.0),  # 長紅
        (111.0, 112.0, 110.5, 111.5),  # 小實體 gap up
        (111.0, 111.5, 104.0, 104.5),  # 長黑跌回中點以下 (mid=105)
    ]
    series = _make_series(rows)
    assert detect_evening_star(series, 2) is True


def test_bullish_piercing():
    rows = [
        (108.0, 109.0, 100.0, 100.5),  # 長黑，low=100
        (98.0, 107.0, 97.5, 105.0),  # 今天開盤 98 < 昨天 low 100，收盤 105 超過中點 104.25 且低於昨天開盤 108
    ]
    series = _make_series(rows)
    assert detect_bullish_piercing(series, 1) is True


def test_dark_cloud_cover():
    rows = [
        (100.0, 108.5, 99.5, 108.0),  # 長紅，high=108.5
        (110.0, 110.5, 102.0, 103.0),  # 今天開盤 > 昨天 high，收盤低於中點 104
    ]
    series = _make_series(rows)
    assert detect_dark_cloud_cover(series, 1) is True


# ── 型態 ─────────────────────────────────────────────────────────────────

def _pattern_series(closes, high_offset=0.5, low_offset=0.5):
    """快速建立型態測試序列：high = close + offset, low = close - offset, open = close。"""
    return {
        "open": list(closes),
        "close": list(closes),
        "high": [c + high_offset for c in closes],
        "low": [c - low_offset for c in closes],
    }


def test_v_reversal():
    closes = [120.0 - i * 3.0 for i in range(6)] + [102.0] + [102.0 + i * 2.5 for i in range(1, 9)]
    series = _pattern_series(closes)
    assert detect_v_reversal(series, len(closes) - 1, lookback=len(closes)) is True


def test_a_reversal():
    closes = [100.0 + i * 3.0 for i in range(6)] + [118.0] + [118.0 - i * 2.5 for i in range(1, 9)]
    series = _pattern_series(closes)
    assert detect_a_reversal(series, len(closes) - 1, lookback=len(closes)) is True


def test_double_bottom():
    # 兩個低點接近、中間有 peak、最後收盤突破 peak
    closes = [100, 96, 92, 90, 95, 100, 95, 92, 90.5, 95, 99, 101, 103]
    series = {
        "open": [float(c) for c in closes],
        "close": [float(c) for c in closes],
        "high": [float(c) + 1 for c in closes],
        "low": [float(c) - 0.3 for c in closes],
    }
    assert detect_double_bottom(series, len(closes) - 1, lookback=len(closes)) is True


def test_double_top():
    closes = [100, 104, 108, 110, 105, 100, 105, 108, 109.5, 104, 101, 99, 97]
    series = {
        "open": [float(c) for c in closes],
        "close": [float(c) for c in closes],
        "high": [float(c) + 0.3 for c in closes],
        "low": [float(c) - 1 for c in closes],
    }
    assert detect_double_top(series, len(closes) - 1, lookback=len(closes)) is True


def test_head_shoulders_top():
    # 左肩 → trough → 頭（更高）→ trough → 右肩（和左肩接近）→ 收盤 breakdown neckline
    closes = [92, 100, 106, 108, 106, 100, 95, 108, 115, 110, 100, 95, 102, 108, 105, 98, 92, 88]
    series = {
        "open": [float(c) for c in closes],
        "close": [float(c) for c in closes],
        "high": [float(c) + 0.3 for c in closes],
        "low": [float(c) - 1 for c in closes],
    }
    assert detect_head_shoulders_top(series, len(closes) - 1, lookback=len(closes)) is True


def test_head_shoulders_bottom():
    closes = [108, 100, 94, 92, 94, 100, 105, 92, 85, 90, 100, 105, 98, 92, 95, 102, 108, 112]
    series = {
        "open": [float(c) for c in closes],
        "close": [float(c) for c in closes],
        "high": [float(c) + 1 for c in closes],
        "low": [float(c) - 0.3 for c in closes],
    }
    assert detect_head_shoulders_bottom(series, len(closes) - 1, lookback=len(closes)) is True


# ── dispatch 完整性 ──────────────────────────────────────────────────────

def test_all_patterns_have_lookback():
    assert set(PATTERN_DISPATCH.keys()) == set(PATTERN_LOOKBACK.keys())
