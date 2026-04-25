"""
M21 PART 2：chip_summary

個股籌碼 + 量價行為的結論層訊號。

Fields:
- foreign_buy_days / investment_trust_buy_days / dealer_buy_days: 緊貼 buy_date 往回數的連續淨買天數
- volume_trend: increasing / spike / flat / declining
- price_trend: uptrend / sideways / downtrend（7 日區間相對斜率）
- is_accumulation: price_trend=uptrend + volume_trend=increasing + 單日最大漲幅不超過門檻
- chip_strength: strong / neutral / weak

資料不足時回 None 欄位 + data_quality note，整體仍保持 dict 結構（不拋 exception）。
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.analysis.context_thresholds import (
    CHIP_ACCUMULATION_MAX_SINGLE_DAY_PCT,
    CHIP_PRIMARY_LOOKBACK_DAYS,
    CHIP_SECONDARY_LOOKBACK_DAYS,
    CHIP_VOLUME_DECLINING_PCT,
    CHIP_VOLUME_INCREASING_PCT,
    CHIP_VOLUME_SPIKE_PCT,
    INDUSTRY_VOLUME_BASELINE_DAYS,
    INDUSTRY_VOLUME_RECENT_DAYS,
    PRICE_TREND_DOWNTREND_SLOPE,
    PRICE_TREND_UPTREND_SLOPE,
)
from app.models import DailyPrice, InstStockFlow

_INST_TYPES = ("foreign", "trust", "dealer")
_FIELD_NAMES = {
    "foreign": "foreign_buy_days",
    "trust": "investment_trust_buy_days",
    "dealer": "dealer_buy_days",
}


def compute_chip_signals(
    db: Session,
    stock_id: str,
    buy_date: date,
    industry_name: str,  # noqa: ARG001
) -> Tuple[dict, List[str]]:
    notes: List[str] = []

    buy_days = _consecutive_buy_days(db, stock_id, buy_date)

    volume_trend = _classify_volume_trend(db, stock_id, buy_date)
    price_trend, max_single_day_pct = _classify_price_trend(
        db, stock_id, buy_date, CHIP_PRIMARY_LOOKBACK_DAYS
    )

    if volume_trend is None:
        notes.append(
            f"volume_trend is null because daily_price has insufficient history "
            f"for {stock_id} on/before {buy_date}"
        )
    if price_trend is None:
        notes.append(
            f"price_trend is null because daily_price has insufficient history "
            f"for {stock_id} on/before {buy_date}"
        )

    is_accumulation = _is_accumulation(price_trend, volume_trend, max_single_day_pct)
    chip_strength = _classify_chip_strength(buy_days, is_accumulation, volume_trend)

    return (
        {
            "foreign_buy_days": buy_days.get("foreign"),
            "investment_trust_buy_days": buy_days.get("trust"),
            "dealer_buy_days": buy_days.get("dealer"),
            "volume_trend": volume_trend,
            "price_trend": price_trend,
            "is_accumulation": is_accumulation,
            "chip_strength": chip_strength,
        },
        notes,
    )


def _consecutive_buy_days(db: Session, stock_id: str, buy_date: date) -> dict:
    """往回數 inst_type 連續淨買天數；任一日 net_shares <= 0 或缺資料即中斷。"""
    rows = (
        db.query(InstStockFlow.trade_date, InstStockFlow.inst_type, InstStockFlow.net_shares)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date <= buy_date,
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .order_by(InstStockFlow.trade_date.desc())
        .limit(CHIP_SECONDARY_LOOKBACK_DAYS * len(_INST_TYPES))
        .all()
    )

    by_type: dict[str, list[tuple[date, float]]] = {t: [] for t in _INST_TYPES}
    for r in rows:
        by_type[r.inst_type].append((r.trade_date, float(r.net_shares or 0)))

    result = {}
    for inst_type, entries in by_type.items():
        # entries 已按日期降冪
        count = 0
        for _, net in entries:
            if net > 0:
                count += 1
            else:
                break
        result[inst_type] = count
    return result


def _classify_volume_trend(db: Session, stock_id: str, buy_date: date) -> Optional[str]:
    """近 3 日 vs 前 5 日 volume avg 比較。"""
    required = INDUSTRY_VOLUME_RECENT_DAYS + INDUSTRY_VOLUME_BASELINE_DAYS
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.volume)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.volume.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(required)
        .all()
    )
    if len(rows) < required:
        return None

    # rows[0] 是最新
    recent = [float(r.volume) for r in rows[:INDUSTRY_VOLUME_RECENT_DAYS]]
    baseline = [float(r.volume) for r in rows[INDUSTRY_VOLUME_RECENT_DAYS:required]]

    if not baseline or sum(baseline) == 0:
        return None

    recent_avg = sum(recent) / len(recent)
    baseline_avg = sum(baseline) / len(baseline)

    # spike 判斷以最近單日最大值為準
    recent_max = max(recent)
    if recent_max >= baseline_avg * (1 + CHIP_VOLUME_SPIKE_PCT):
        return "spike"

    change_pct = (recent_avg - baseline_avg) / baseline_avg
    if change_pct >= CHIP_VOLUME_INCREASING_PCT:
        return "increasing"
    if change_pct <= CHIP_VOLUME_DECLINING_PCT:
        return "declining"
    return "flat"


def _classify_price_trend(
    db: Session, stock_id: str, buy_date: date, lookback: int
) -> Tuple[Optional[str], Optional[float]]:
    """Returns (trend_label, max_single_day_pct)。單日漲跌%取 |high-prev_close|/prev_close 近似，這裡用 close 間差。

    `max_single_day_pct` 是**雙向絕對值**（max of |close_t - close_{t-1}| / close_{t-1}），
    所以 -6% 跌停也會被視為「單日過大」而排除 gradual accumulation；命名保留以對齊 spec。
    """
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(lookback)
        .all()
    )
    if len(rows) < 2:
        return None, None

    closes = [float(r.close_price) for r in rows]
    closes.reverse()  # ascending

    if closes[0] == 0:
        return None, None
    slope = (closes[-1] - closes[0]) / closes[0]
    if slope >= PRICE_TREND_UPTREND_SLOPE:
        trend = "uptrend"
    elif slope <= PRICE_TREND_DOWNTREND_SLOPE:
        trend = "downtrend"
    else:
        trend = "sideways"

    max_single = 0.0
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            continue
        pct = (closes[i] - closes[i - 1]) / closes[i - 1]
        if abs(pct) > max_single:
            max_single = abs(pct)

    return trend, max_single


def _is_accumulation(
    price_trend: Optional[str],
    volume_trend: Optional[str],
    max_single_day_pct: Optional[float],
) -> Optional[bool]:
    if price_trend is None or volume_trend is None or max_single_day_pct is None:
        return None
    if price_trend != "uptrend":
        return False
    if volume_trend != "increasing":
        return False
    return max_single_day_pct <= CHIP_ACCUMULATION_MAX_SINGLE_DAY_PCT


def _classify_chip_strength(
    buy_days: dict,
    is_accumulation: Optional[bool],
    volume_trend: Optional[str],
) -> Optional[str]:
    if is_accumulation is None:
        return None

    foreign_days = buy_days.get("foreign", 0)
    trust_days = buy_days.get("trust", 0)
    dealer_days = buy_days.get("dealer", 0)
    combined = foreign_days + trust_days + dealer_days

    # strong: 外資連買 >= 2 且 is_accumulation=True；或三大法人合計 >= 3 且 is_accumulation=True
    if is_accumulation and (foreign_days >= 2 or combined >= 3):
        return "strong"

    # weak: spike 但無任何法人支撐 / 全部法人 0 天
    if volume_trend == "spike" and combined == 0:
        return "weak"
    if combined == 0 and not is_accumulation:
        return "weak"

    return "neutral"
