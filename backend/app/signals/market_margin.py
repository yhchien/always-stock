"""
大盤融資融券聚合（M23 daily signals 使用）

定位：把 `margin_trade` 全市場單日資料聚合成可注入 LLM prompt 的盤勢摘要，
配合個股融資融券細數據（candidate_pool）做 3:7 權重的盤勢 vs 個股分析。

只在 explanation / watch_reason 兩個 stage 注入 market_context，避免每檔 batch
都重算一次。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _recent_trade_dates(db: Session, target_date: date, *, limit: int) -> List[date]:
    """從 margin_trade 表往回找最近 N 個有資料的交易日（含 target_date）。"""
    rows = db.execute(
        text(
            "SELECT DISTINCT trade_date FROM margin_trade "
            "WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT :n"
        ),
        {"d": target_date, "n": limit},
    ).all()
    return sorted([r[0] for r in rows])


def _market_sums_for_date(db: Session, trade_date: date) -> Dict[str, Optional[int]]:
    """加總當日全市場融資 / 融券餘額與變化。"""
    row = db.execute(
        text(
            """
            SELECT
                SUM(margin_balance) AS margin_balance_sum,
                SUM(margin_change)  AS margin_change_sum,
                SUM(short_balance)  AS short_balance_sum,
                SUM(short_change)   AS short_change_sum,
                COUNT(*)            AS stock_count
            FROM margin_trade
            WHERE trade_date = :d
            """
        ),
        {"d": trade_date},
    ).first()
    if row is None:
        return {
            "margin_balance_sum": None,
            "margin_change_sum": None,
            "short_balance_sum": None,
            "short_change_sum": None,
            "stock_count": 0,
        }
    return {
        "margin_balance_sum": int(row.margin_balance_sum or 0),
        "margin_change_sum": int(row.margin_change_sum or 0),
        "short_balance_sum": int(row.short_balance_sum or 0),
        "short_change_sum": int(row.short_change_sum or 0),
        "stock_count": int(row.stock_count or 0),
    }


def _ratio_pct(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    """券資比 = 融券餘額 / 融資餘額 × 100；分母 0 或缺值回 None。"""
    if not numerator or not denominator or denominator == 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _change_pct(latest: Optional[int], baseline: Optional[int]) -> Optional[float]:
    if not latest or not baseline or baseline == 0:
        return None
    return round((latest - baseline) / baseline * 100.0, 2)


def compute_market_margin_snapshot(
    db: Session,
    target_date: date,
    *,
    short_lookback: int = 5,
) -> Dict[str, Any]:
    """產出大盤融資融券盤勢摘要。

    輸出 schema（注入 prompt 的 market_context.margin_climate）：
        {
            "target_date": "YYYY-MM-DD",
            "data_available": bool,
            "today": {
                "margin_balance_shares": int,  # 全市場融資餘額（張）
                "margin_change_shares": int,   # 當日融資增減（張）
                "short_balance_shares": int,
                "short_change_shares": int,
                "margin_short_ratio_pct": float | None,
                "stock_count": int,
            },
            "trend_5d": {
                "baseline_date": "YYYY-MM-DD" | None,
                "margin_change_pct": float | None,  # vs 5 個交易日前餘額
                "short_change_pct": float | None,
                "margin_short_ratio_pct_change": float | None,
            },
            "climate_label": "expansive" | "neutral" | "contractive" | "unknown",
            "climate_reason": "繁體中文一句話，給 LLM 引用",
        }

    `climate_label` 規則（pure rule，不打 LLM）：
        - margin_change_pct > +2% → expansive（融資熱絡）
        - margin_change_pct < -2% → contractive（融資退場）
        - 介於 → neutral
        - 無資料 → unknown
    """
    today_sums = _market_sums_for_date(db, target_date)
    data_available = today_sums["stock_count"] > 0

    today = {
        "margin_balance_shares": today_sums["margin_balance_sum"],
        "margin_change_shares": today_sums["margin_change_sum"],
        "short_balance_shares": today_sums["short_balance_sum"],
        "short_change_shares": today_sums["short_change_sum"],
        "margin_short_ratio_pct": _ratio_pct(
            today_sums["short_balance_sum"], today_sums["margin_balance_sum"]
        ),
        "stock_count": today_sums["stock_count"],
    }

    trend_5d: Dict[str, Any] = {
        "baseline_date": None,
        "margin_change_pct": None,
        "short_change_pct": None,
        "margin_short_ratio_pct_change": None,
    }

    if data_available:
        recent = _recent_trade_dates(db, target_date, limit=short_lookback)
        if len(recent) >= 2:
            baseline = recent[0]
            baseline_sums = _market_sums_for_date(db, baseline)
            trend_5d["baseline_date"] = baseline.isoformat()
            trend_5d["margin_change_pct"] = _change_pct(
                today_sums["margin_balance_sum"], baseline_sums["margin_balance_sum"]
            )
            trend_5d["short_change_pct"] = _change_pct(
                today_sums["short_balance_sum"], baseline_sums["short_balance_sum"]
            )
            baseline_ratio = _ratio_pct(
                baseline_sums["short_balance_sum"], baseline_sums["margin_balance_sum"]
            )
            today_ratio = today["margin_short_ratio_pct"]
            if baseline_ratio is not None and today_ratio is not None:
                trend_5d["margin_short_ratio_pct_change"] = round(
                    today_ratio - baseline_ratio, 2
                )

    climate_label, climate_reason = _classify_climate(trend_5d, today)

    return {
        "target_date": target_date.isoformat(),
        "data_available": data_available,
        "today": today,
        "trend_5d": trend_5d,
        "climate_label": climate_label,
        "climate_reason": climate_reason,
    }


def _classify_climate(
    trend_5d: Dict[str, Any],
    today: Dict[str, Any],
) -> tuple[str, str]:
    """純規則分類大盤融資環境。

    - expansive：融資餘額 5 日 +2% 以上 → 散戶資金擴張，需小心追高過熱
    - contractive：融資餘額 5 日 -2% 以下 → 散戶撤資，個股若仍 +融資代表逆勢追價
    - neutral：介於兩者之間
    - unknown：無資料

    回傳 (label, 一句話中文 reason)，給 LLM prompt 直接引用。
    """
    pct = trend_5d.get("margin_change_pct")
    if pct is None:
        return "unknown", "大盤融資資料不足，無法判斷整體環境。"

    short_pct = trend_5d.get("short_change_pct")
    ratio_change = trend_5d.get("margin_short_ratio_pct_change")

    if pct >= 2.0:
        reason = (
            f"大盤融資餘額 5 日累計 +{pct:.1f}%，散戶資金明顯擴張"
            f"（融券同步變化 {short_pct or 0:+.1f}%），整體偏熱，"
            f"個股若再大幅 +融資需特別注意追高過熱風險。"
        )
        return "expansive", reason

    if pct <= -2.0:
        reason = (
            f"大盤融資餘額 5 日累計 {pct:.1f}%，散戶資金退潮"
            f"（融券變化 {short_pct or 0:+.1f}%），整體偏冷；"
            f"個股若逆勢 +融資代表有特定資金主動進場。"
        )
        return "contractive", reason

    ratio_note = ""
    if ratio_change is not None and abs(ratio_change) >= 0.05:
        ratio_note = f"，券資比變化 {ratio_change:+.2f}pp"
    reason = (
        f"大盤融資 5 日變化 {pct:+.1f}%，整體區間震盪"
        f"{ratio_note}，盤勢平淡，請以個股自身籌碼為主要判斷依據。"
    )
    return "neutral", reason
