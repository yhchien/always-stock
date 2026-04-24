"""
M21 PART 5：price_structure

純 OHLC 計算的技術面結構訊號。不跨股、不吃 inst flow、不吃 fundamental。

Fields:
- trend: uptrend / sideways / downtrend（10 日 close 線性斜率 / 起點）
- is_breakout: 最新收盤 > 前 20 交易日最高 close
- is_consolidation: 近 10 交易日 (max - min) / min < 5%
- is_accelerating: 近 5 日斜率 / 前 5 日斜率 >= 1.5 且同向

資料不足時（歷史交易日不夠）所有欄位回 None + data_quality note。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.analysis.context_thresholds import (
    PRICE_ACCELERATING_RATIO,
    PRICE_BREAKOUT_BASELINE_DAYS,
    PRICE_CONSOLIDATION_RANGE_PCT,
    PRICE_TREND_DOWNTREND_SLOPE,
    PRICE_TREND_LOOKBACK_DAYS,
    PRICE_TREND_UPTREND_SLOPE,
)
from app.models import DailyPrice

# 計算所有欄位需要的最少交易日數（以最嚴格的 breakout 為準 + 1 天 latest）
_MIN_HISTORY = PRICE_BREAKOUT_BASELINE_DAYS + 1


def compute_price_structure(
    db: Session,
    stock_id: str,
    buy_date: date,
    industry_name: str,  # noqa: ARG001 — 保留與其他 section module 一致的簽章
) -> Tuple[dict, List[str]]:
    """Compute price_structure section for trade quality context."""
    prices = _recent_closes(db, stock_id, buy_date, limit=_MIN_HISTORY + 5)

    if len(prices) < _MIN_HISTORY:
        notes = [
            f"price_structure is null because daily_price has {len(prices)} rows "
            f"for {stock_id} on/before {buy_date} (need >= {_MIN_HISTORY})"
        ]
        return (
            {
                "trend": None,
                "is_breakout": None,
                "is_consolidation": None,
                "is_accelerating": None,
            },
            notes,
        )

    trend = _classify_trend(prices[-PRICE_TREND_LOOKBACK_DAYS:])
    is_breakout = _is_breakout(prices)
    is_consolidation = _is_consolidation(prices[-PRICE_TREND_LOOKBACK_DAYS:])
    is_accelerating = _is_accelerating(prices[-PRICE_TREND_LOOKBACK_DAYS:])

    return (
        {
            "trend": trend,
            "is_breakout": is_breakout,
            "is_consolidation": is_consolidation,
            "is_accelerating": is_accelerating,
        },
        [],
    )


def _recent_closes(db: Session, stock_id: str, buy_date: date, limit: int) -> List[float]:
    """Ascending order, most-recent last."""
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [float(r.close_price) for r in rows]


def _classify_trend(window: List[float]) -> str:
    """用區間起終點相對斜率判斷方向；穩定 deterministic 勝過 numpy polyfit。"""
    if len(window) < 2 or window[0] == 0:
        return "sideways"
    slope = (window[-1] - window[0]) / window[0]
    if slope >= PRICE_TREND_UPTREND_SLOPE:
        return "uptrend"
    if slope <= PRICE_TREND_DOWNTREND_SLOPE:
        return "downtrend"
    return "sideways"


def _is_breakout(prices: List[float]) -> bool:
    """最新收盤是否高於前 N 個交易日（不含最新）的最高收盤。"""
    latest = prices[-1]
    prior = prices[-(PRICE_BREAKOUT_BASELINE_DAYS + 1):-1]
    if not prior:
        return False
    return latest > max(prior)


def _is_consolidation(window: List[float]) -> bool:
    """區間 high/low 幅度小於門檻視為盤整。"""
    if not window:
        return False
    lo = min(window)
    hi = max(window)
    if lo <= 0:
        return False
    return (hi - lo) / lo < PRICE_CONSOLIDATION_RANGE_PCT


def _is_accelerating(window: List[float]) -> bool:
    """近半段斜率 vs 前半段斜率，絕對值放大且同向才算 accelerating。"""
    if len(window) < 4:
        return False
    mid = len(window) // 2
    first_half = window[:mid + 1]  # 重疊中點以含 mid 收盤
    second_half = window[mid:]

    first_slope = _relative_slope(first_half)
    second_slope = _relative_slope(second_half)

    if first_slope == 0:
        return False
    # 同向 + 放大
    if (first_slope > 0) != (second_slope > 0):
        return False
    return abs(second_slope) / abs(first_slope) >= PRICE_ACCELERATING_RATIO


def _relative_slope(window: List[float]) -> float:
    if len(window) < 2 or window[0] == 0:
        return 0.0
    return (window[-1] - window[0]) / window[0]
