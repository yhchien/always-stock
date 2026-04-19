"""K 棒型態與技術型態偵測。

所有 detector 接收 series（dict of lists）與目前 index，回傳 bool。
型態偵測採啟發式，閾值偏寬，用於策略訊號參考。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _body(opens: List[float], closes: List[float], i: int) -> float:
    return abs(closes[i] - opens[i])


def _range(highs: List[float], lows: List[float], i: int) -> float:
    return highs[i] - lows[i]


def _upper_shadow(opens: List[float], closes: List[float], highs: List[float], i: int) -> float:
    return highs[i] - max(opens[i], closes[i])


def _lower_shadow(opens: List[float], closes: List[float], lows: List[float], i: int) -> float:
    return min(opens[i], closes[i]) - lows[i]


def _is_bull(opens: List[float], closes: List[float], i: int) -> bool:
    return closes[i] > opens[i]


def _is_bear(opens: List[float], closes: List[float], i: int) -> bool:
    return closes[i] < opens[i]


def _is_downtrend(closes: List[float], i: int, lookback: int = 5) -> bool:
    if i < lookback:
        return False
    return closes[i - 1] < closes[i - lookback]


def _is_uptrend(closes: List[float], i: int, lookback: int = 5) -> bool:
    if i < lookback:
        return False
    return closes[i - 1] > closes[i - lookback]


def _avg_body(opens: List[float], closes: List[float], end_exclusive: int, window: int) -> Optional[float]:
    if end_exclusive < window:
        return None
    total = 0.0
    for j in range(end_exclusive - window, end_exclusive):
        total += abs(closes[j] - opens[j])
    return total / window


# ── 單根 K 棒 ──────────────────────────────────────────────────────────────

def detect_doji(series: Dict[str, List[float]], i: int) -> bool:
    rng = _range(series["high"], series["low"], i)
    if rng <= 0:
        return False
    body = _body(series["open"], series["close"], i)
    return body / rng <= 0.10


def _small_body_long_lower_shadow(series: Dict[str, List[float]], i: int) -> bool:
    opens, closes, highs, lows = series["open"], series["close"], series["high"], series["low"]
    body = _body(opens, closes, i)
    rng = _range(highs, lows, i)
    if rng <= 0 or body <= 0:
        return False
    if body / rng > 0.35:
        return False
    lower = _lower_shadow(opens, closes, lows, i)
    upper = _upper_shadow(opens, closes, highs, i)
    if lower < body * 2:
        return False
    if upper > rng * 0.15:
        return False
    return True


def _small_body_long_upper_shadow(series: Dict[str, List[float]], i: int) -> bool:
    opens, closes, highs, lows = series["open"], series["close"], series["high"], series["low"]
    body = _body(opens, closes, i)
    rng = _range(highs, lows, i)
    if rng <= 0 or body <= 0:
        return False
    if body / rng > 0.35:
        return False
    upper = _upper_shadow(opens, closes, highs, i)
    lower = _lower_shadow(opens, closes, lows, i)
    if upper < body * 2:
        return False
    if lower > rng * 0.15:
        return False
    return True


def detect_hammer(series: Dict[str, List[float]], i: int) -> bool:
    return _small_body_long_lower_shadow(series, i) and _is_downtrend(series["close"], i, 5)


def detect_hanging_man(series: Dict[str, List[float]], i: int) -> bool:
    return _small_body_long_lower_shadow(series, i) and _is_uptrend(series["close"], i, 5)


def detect_shooting_star(series: Dict[str, List[float]], i: int) -> bool:
    return _small_body_long_upper_shadow(series, i) and _is_uptrend(series["close"], i, 5)


def detect_inverted_hammer(series: Dict[str, List[float]], i: int) -> bool:
    return _small_body_long_upper_shadow(series, i) and _is_downtrend(series["close"], i, 5)


def detect_long_bullish(series: Dict[str, List[float]], i: int) -> bool:
    opens, closes, highs, lows = series["open"], series["close"], series["high"], series["low"]
    if not _is_bull(opens, closes, i):
        return False
    body = _body(opens, closes, i)
    rng = _range(highs, lows, i)
    if rng <= 0:
        return False
    if body / rng < 0.7:
        return False
    avg_body = _avg_body(opens, closes, i, 10)
    return avg_body is not None and body >= avg_body * 1.5


def detect_long_bearish(series: Dict[str, List[float]], i: int) -> bool:
    opens, closes, highs, lows = series["open"], series["close"], series["high"], series["low"]
    if not _is_bear(opens, closes, i):
        return False
    body = _body(opens, closes, i)
    rng = _range(highs, lows, i)
    if rng <= 0:
        return False
    if body / rng < 0.7:
        return False
    avg_body = _avg_body(opens, closes, i, 10)
    return avg_body is not None and body >= avg_body * 1.5


# ── 組合 K 棒 ──────────────────────────────────────────────────────────────

def detect_bullish_engulfing(series: Dict[str, List[float]], i: int) -> bool:
    if i < 1:
        return False
    opens, closes = series["open"], series["close"]
    if not _is_bear(opens, closes, i - 1):
        return False
    if not _is_bull(opens, closes, i):
        return False
    return opens[i] <= closes[i - 1] and closes[i] >= opens[i - 1]


def detect_bearish_engulfing(series: Dict[str, List[float]], i: int) -> bool:
    if i < 1:
        return False
    opens, closes = series["open"], series["close"]
    if not _is_bull(opens, closes, i - 1):
        return False
    if not _is_bear(opens, closes, i):
        return False
    return opens[i] >= closes[i - 1] and closes[i] <= opens[i - 1]


def detect_three_white_soldiers(series: Dict[str, List[float]], i: int) -> bool:
    if i < 2:
        return False
    opens, closes = series["open"], series["close"]
    for j in (i - 2, i - 1, i):
        if not _is_bull(opens, closes, j):
            return False
    if not (closes[i - 1] > closes[i - 2] and closes[i] > closes[i - 1]):
        return False
    if not (opens[i - 1] > opens[i - 2] and opens[i - 1] <= closes[i - 2]):
        return False
    if not (opens[i] > opens[i - 1] and opens[i] <= closes[i - 1]):
        return False
    return True


def detect_three_black_crows(series: Dict[str, List[float]], i: int) -> bool:
    if i < 2:
        return False
    opens, closes = series["open"], series["close"]
    for j in (i - 2, i - 1, i):
        if not _is_bear(opens, closes, j):
            return False
    if not (closes[i - 1] < closes[i - 2] and closes[i] < closes[i - 1]):
        return False
    if not (opens[i - 1] < opens[i - 2] and opens[i - 1] >= closes[i - 2]):
        return False
    if not (opens[i] < opens[i - 1] and opens[i] >= closes[i - 1]):
        return False
    return True


def detect_morning_star(series: Dict[str, List[float]], i: int) -> bool:
    if i < 2:
        return False
    opens, closes = series["open"], series["close"]
    day0_body = _body(opens, closes, i - 2)
    if not _is_bear(opens, closes, i - 2) or day0_body <= 0:
        return False
    day1_body = _body(opens, closes, i - 1)
    if day1_body >= day0_body * 0.5:
        return False
    day1_body_high = max(opens[i - 1], closes[i - 1])
    day0_body_low = min(opens[i - 2], closes[i - 2])
    if day1_body_high >= day0_body_low:
        return False
    if not _is_bull(opens, closes, i):
        return False
    day0_mid = (opens[i - 2] + closes[i - 2]) / 2
    return closes[i] >= day0_mid


def detect_evening_star(series: Dict[str, List[float]], i: int) -> bool:
    if i < 2:
        return False
    opens, closes = series["open"], series["close"]
    day0_body = _body(opens, closes, i - 2)
    if not _is_bull(opens, closes, i - 2) or day0_body <= 0:
        return False
    day1_body = _body(opens, closes, i - 1)
    if day1_body >= day0_body * 0.5:
        return False
    day1_body_low = min(opens[i - 1], closes[i - 1])
    day0_body_high = max(opens[i - 2], closes[i - 2])
    if day1_body_low <= day0_body_high:
        return False
    if not _is_bear(opens, closes, i):
        return False
    day0_mid = (opens[i - 2] + closes[i - 2]) / 2
    return closes[i] <= day0_mid


def detect_bullish_piercing(series: Dict[str, List[float]], i: int) -> bool:
    if i < 1:
        return False
    opens, closes, lows = series["open"], series["close"], series["low"]
    if not _is_bear(opens, closes, i - 1):
        return False
    if not _is_bull(opens, closes, i):
        return False
    if opens[i] >= lows[i - 1]:
        return False
    mid = (opens[i - 1] + closes[i - 1]) / 2
    return mid <= closes[i] < opens[i - 1]


def detect_dark_cloud_cover(series: Dict[str, List[float]], i: int) -> bool:
    if i < 1:
        return False
    opens, closes, highs = series["open"], series["close"], series["high"]
    if not _is_bull(opens, closes, i - 1):
        return False
    if not _is_bear(opens, closes, i):
        return False
    if opens[i] <= highs[i - 1]:
        return False
    mid = (opens[i - 1] + closes[i - 1]) / 2
    return opens[i - 1] < closes[i] <= mid


# ── 型態（需要 lookback） ───────────────────────────────────────────────────

def _find_local_peaks(values: List[float], radius: int = 3) -> List[Tuple[int, float]]:
    peaks: List[Tuple[int, float]] = []
    n = len(values)
    for i in range(radius, n - radius):
        window = values[i - radius : i + radius + 1]
        if values[i] == max(window) and values[i] > values[i - 1]:
            peaks.append((i, values[i]))
    return peaks


def _find_local_troughs(values: List[float], radius: int = 3) -> List[Tuple[int, float]]:
    troughs: List[Tuple[int, float]] = []
    n = len(values)
    for i in range(radius, n - radius):
        window = values[i - radius : i + radius + 1]
        if values[i] == min(window) and values[i] < values[i - 1]:
            troughs.append((i, values[i]))
    return troughs


def detect_head_shoulders_top(series: Dict[str, List[float]], i: int, lookback: int = 40) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    highs_win = series["high"][start : i + 1]
    lows_win = series["low"][start : i + 1]
    peaks = _find_local_peaks(highs_win, radius=3)
    if len(peaks) < 3:
        return False
    left, head, right = peaks[-3], peaks[-2], peaks[-1]
    if head[1] <= left[1] or head[1] <= right[1]:
        return False
    shoulders_diff = abs(left[1] - right[1]) / max(left[1], right[1])
    if shoulders_diff > 0.05:
        return False
    neckline = min(lows_win[left[0] : right[0] + 1])
    return series["close"][i] < neckline


def detect_head_shoulders_bottom(series: Dict[str, List[float]], i: int, lookback: int = 40) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    highs_win = series["high"][start : i + 1]
    lows_win = series["low"][start : i + 1]
    troughs = _find_local_troughs(lows_win, radius=3)
    if len(troughs) < 3:
        return False
    left, head, right = troughs[-3], troughs[-2], troughs[-1]
    if head[1] >= left[1] or head[1] >= right[1]:
        return False
    shoulders_diff = abs(left[1] - right[1]) / max(left[1], right[1])
    if shoulders_diff > 0.05:
        return False
    neckline = max(highs_win[left[0] : right[0] + 1])
    return series["close"][i] > neckline


def detect_double_top(series: Dict[str, List[float]], i: int, lookback: int = 40) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    highs_win = series["high"][start : i + 1]
    lows_win = series["low"][start : i + 1]
    peaks = _find_local_peaks(highs_win, radius=3)
    if len(peaks) < 2:
        return False
    first, second = peaks[-2], peaks[-1]
    diff = abs(first[1] - second[1]) / max(first[1], second[1])
    if diff > 0.03:
        return False
    trough = min(lows_win[first[0] : second[0] + 1])
    drop = (second[1] - trough) / second[1] if second[1] > 0 else 0
    if drop < 0.05:
        return False
    return series["close"][i] < trough


def detect_double_bottom(series: Dict[str, List[float]], i: int, lookback: int = 40) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    highs_win = series["high"][start : i + 1]
    lows_win = series["low"][start : i + 1]
    troughs = _find_local_troughs(lows_win, radius=3)
    if len(troughs) < 2:
        return False
    first, second = troughs[-2], troughs[-1]
    diff = abs(first[1] - second[1]) / max(first[1], second[1])
    if diff > 0.03:
        return False
    peak = max(highs_win[first[0] : second[0] + 1])
    rise = (peak - second[1]) / second[1] if second[1] > 0 else 0
    if rise < 0.05:
        return False
    return series["close"][i] > peak


def detect_v_reversal(series: Dict[str, List[float]], i: int, lookback: int = 15) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    window = series["close"][start : i + 1]
    min_val = min(window)
    min_pos = window.index(min_val)
    if not (lookback * 0.25 <= min_pos <= lookback * 0.75):
        return False
    if window[0] <= 0 or min_val <= 0:
        return False
    left_drop = (window[0] - min_val) / window[0]
    right_rise = (window[-1] - min_val) / min_val
    return left_drop >= 0.08 and right_rise >= 0.08


def detect_a_reversal(series: Dict[str, List[float]], i: int, lookback: int = 15) -> bool:
    if i < lookback - 1:
        return False
    start = i - lookback + 1
    window = series["close"][start : i + 1]
    max_val = max(window)
    max_pos = window.index(max_val)
    if not (lookback * 0.25 <= max_pos <= lookback * 0.75):
        return False
    if window[0] <= 0 or max_val <= 0:
        return False
    left_rise = (max_val - window[0]) / window[0]
    right_drop = (max_val - window[-1]) / max_val
    return left_rise >= 0.08 and right_drop >= 0.08


PATTERN_DISPATCH = {
    "candle_doji": detect_doji,
    "candle_hammer": detect_hammer,
    "candle_hanging_man": detect_hanging_man,
    "candle_shooting_star": detect_shooting_star,
    "candle_inverted_hammer": detect_inverted_hammer,
    "candle_long_bullish": detect_long_bullish,
    "candle_long_bearish": detect_long_bearish,
    "candle_bullish_engulfing": detect_bullish_engulfing,
    "candle_bearish_engulfing": detect_bearish_engulfing,
    "candle_three_white_soldiers": detect_three_white_soldiers,
    "candle_three_black_crows": detect_three_black_crows,
    "candle_morning_star": detect_morning_star,
    "candle_evening_star": detect_evening_star,
    "candle_bullish_piercing": detect_bullish_piercing,
    "candle_dark_cloud_cover": detect_dark_cloud_cover,
    "pattern_head_shoulders_top": detect_head_shoulders_top,
    "pattern_head_shoulders_bottom": detect_head_shoulders_bottom,
    "pattern_double_top": detect_double_top,
    "pattern_double_bottom": detect_double_bottom,
    "pattern_v_reversal": detect_v_reversal,
    "pattern_a_reversal": detect_a_reversal,
}


PATTERN_LOOKBACK = {
    "candle_doji": 1,
    "candle_hammer": 6,
    "candle_hanging_man": 6,
    "candle_shooting_star": 6,
    "candle_inverted_hammer": 6,
    "candle_long_bullish": 11,
    "candle_long_bearish": 11,
    "candle_bullish_engulfing": 2,
    "candle_bearish_engulfing": 2,
    "candle_three_white_soldiers": 3,
    "candle_three_black_crows": 3,
    "candle_morning_star": 3,
    "candle_evening_star": 3,
    "candle_bullish_piercing": 2,
    "candle_dark_cloud_cover": 2,
    "pattern_head_shoulders_top": 40,
    "pattern_head_shoulders_bottom": 40,
    "pattern_double_top": 40,
    "pattern_double_bottom": 40,
    "pattern_v_reversal": 15,
    "pattern_a_reversal": 15,
}
