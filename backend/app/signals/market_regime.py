"""M27 Market Regime Gate — deterministic 大盤狀態判定。

用 daily_price 內 `TAIEX` 指數歷史收盤（含 high/low）算 MA10 / MA20 / MA60 +
近 5 / 10 日報酬 + MA20 斜率，deterministic 判 BULL_TREND / VOLATILE_RANGE / RISK_OFF。

設計原則（對齊 CLAUDE.md「deterministic 是骨幹、LLM 是輔助」）：
- regime 是 backend authoritative，LLM 不可改寫
- 資料不足時保守視為 VOLATILE_RANGE（不可假設大多頭）
- 純計算 `classify_regime(metrics)` 與 DB 載入分離，方便單元測試

背景：2026-06 觀察到 6/5 後魚尾命中勝率從 ~67% 掉到 ~39%，主因是 prompt 在震盪盤
仍用「多頭追強」邏輯把 Follower / Laggard / 單次命中 / 急拉突破股評太高。regime gate
讓震盪 / 退潮盤自動收斂選股範圍。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import DailyPrice

_TAIEX_SYMBOL = "TAIEX"
_LOOKBACK_ROWS = 65  # 夠算 MA60 + MA20 斜率（需 25 根）

REGIME_BULL_TREND = "BULL_TREND"
REGIME_VOLATILE_RANGE = "VOLATILE_RANGE"
REGIME_RISK_OFF = "RISK_OFF"

REGIME_LABEL_ZH = {
    REGIME_BULL_TREND: "大多頭",
    REGIME_VOLATILE_RANGE: "震盪盤",
    REGIME_RISK_OFF: "風險退潮",
}

# ---- 分類門檻（集中常數，方便日後校準）----
_MIN_CLOSES_FOR_REGIME = 20          # 少於此筆數 → 保守視為震盪
_RISK_OFF_RETURN_5D_PCT = -3.0       # 近 5 日跌幅達此值（且收破 MA20）→ 退潮
_BULL_RETURN_10D_PCT = 0.0           # 近 10 日報酬需為正才可能 BULL

# 波動度 overlay（關鍵）：指數即使創高，只要盤中震盪大 / 創高急殺，就不算多頭，視為震盪。
# 用 2026-06 實測校準：穩定多頭週 intraday range 5d avg ~1.6%、無創高急殺；
# 6/8~6/12 那種震盪段 range avg ~3.3% 且頻繁創高急殺。
_VOL_RANGE_HIGH_PCT = 2.8            # 近 5 日平均盤中振幅 (high-low)/close 達此值 → 視為高波動
_BIG_DOWN_1D_PCT = -2.5             # 近 3 日內單日跌幅達此值 → 高波動
_REVERSAL_CLOSE_VS_HIGH_PCT = -2.0  # 收盤距當日高點 ≤ -2%（創高後殺尾）
_REVERSAL_RET_1D_PCT = -1.0         # 且當日收黑 → 認定為「創高急殺」反轉日
_REVERSAL_DAYS_5D_MIN = 1           # 近 5 日出現幾根反轉日即算高波動


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def compute_regime_metrics(
    closes: Sequence[float],
    *,
    highs: Optional[Sequence[float]] = None,
    lows: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    """從 OHLC 序列（升序，最後一筆 = 最新）算 regime 判定所需指標。

    highs / lows 給「盤中振幅」與「創高急殺反轉日」用；缺則對應波動度指標為 None / 0。
    """
    closes = [float(c) for c in closes if c is not None]
    n = len(closes)
    close = closes[-1] if n else None

    ma10 = _mean(closes[-10:]) if n >= 10 else None
    ma20 = _mean(closes[-20:]) if n >= 20 else None
    ma60 = _mean(closes[-60:]) if n >= 60 else None
    ma20_prev = _mean(closes[-25:-5]) if n >= 25 else None
    ma20_slope_5d = (
        ma20 - ma20_prev if (ma20 is not None and ma20_prev is not None) else None
    )

    return_5d_pct = (
        (close / closes[-6] - 1.0) * 100.0 if n >= 6 and closes[-6] else None
    )
    return_10d_pct = (
        (close / closes[-11] - 1.0) * 100.0 if n >= 11 and closes[-11] else None
    )

    # 日報酬序列（收對收）
    rets_1d = [
        (closes[i] / closes[i - 1] - 1.0) * 100.0
        for i in range(1, n)
        if closes[i - 1]
    ]
    max_down_1d_3d_pct = min(rets_1d[-3:]) if rets_1d else None
    # 當日大盤報酬（Phase 2 hard exclusion REVERSAL_FAILURE 用：個股相對大盤的
    # excess return 需要這個當分母比較基準；純新增欄位，不影響 classify_regime 判斷）
    return_1d_pct = rets_1d[-1] if rets_1d else None

    # 盤中振幅 + 創高急殺反轉日
    intraday_range_5d_avg_pct = None
    reversal_days_5d = 0
    if highs is not None and lows is not None:
        highs = [float(h) for h in highs]
        lows = [float(low) for low in lows]
        if len(highs) == n and len(lows) == n and n:
            ranges = [
                (highs[i] - lows[i]) / closes[i] * 100.0
                for i in range(n)
                if closes[i]
            ]
            intraday_range_5d_avg_pct = _mean(ranges[-5:]) if ranges else None
            for i in range(max(0, n - 5), n):
                if not highs[i]:
                    continue
                close_vs_high = (closes[i] - highs[i]) / highs[i] * 100.0
                ret_1d = (
                    (closes[i] / closes[i - 1] - 1.0) * 100.0
                    if i >= 1 and closes[i - 1]
                    else None
                )
                if (
                    close_vs_high <= _REVERSAL_CLOSE_VS_HIGH_PCT
                    and ret_1d is not None
                    and ret_1d <= _REVERSAL_RET_1D_PCT
                ):
                    reversal_days_5d += 1

    return {
        "close": close,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "ma20_slope_5d": ma20_slope_5d,
        "return_5d_pct": return_5d_pct,
        "return_10d_pct": return_10d_pct,
        "return_1d_pct": return_1d_pct,
        "intraday_range_5d_avg_pct": intraday_range_5d_avg_pct,
        "reversal_days_5d": reversal_days_5d,
        "max_down_1d_3d_pct": max_down_1d_3d_pct,
        "sample_size": n,
    }


def _is_high_volatility(metrics: dict[str, Any]) -> bool:
    """盤中震盪大 / 創高急殺 / 近 3 日有大跌 → 高波動（即使指數仍創高也視為震盪）。"""
    vol_range = metrics.get("intraday_range_5d_avg_pct")
    reversal_days = metrics.get("reversal_days_5d") or 0
    max_down = metrics.get("max_down_1d_3d_pct")
    return (
        (vol_range is not None and vol_range >= _VOL_RANGE_HIGH_PCT)
        or reversal_days >= _REVERSAL_DAYS_5D_MIN
        or (max_down is not None and max_down <= _BIG_DOWN_1D_PCT)
    )


def classify_regime(metrics: dict[str, Any]) -> tuple[str, str]:
    """純函式：依指標回 (regime, 中文 reason)。優先序 RISK_OFF > 高波動 VOLATILE > BULL > VOLATILE。"""
    n = metrics.get("sample_size") or 0
    close = metrics.get("close")
    ma10 = metrics.get("ma10")
    ma20 = metrics.get("ma20")
    slope = metrics.get("ma20_slope_5d")
    ret5 = metrics.get("return_5d_pct")
    ret10 = metrics.get("return_10d_pct")

    if n < _MIN_CLOSES_FOR_REGIME or close is None or ma20 is None:
        return (
            REGIME_VOLATILE_RANGE,
            "大盤指數資料不足，保守視為震盪盤。",
        )

    # 1) RISK_OFF：收盤跌破 MA20，且（短均也壓在長均下 或 近 5 日明顯下跌）
    if close < ma20 and (
        (ma10 is not None and ma10 < ma20)
        or (ret5 is not None and ret5 <= _RISK_OFF_RETURN_5D_PCT)
    ):
        return (
            REGIME_RISK_OFF,
            f"加權收盤 {close:.0f} 跌破 MA20 {ma20:.0f}，短均下彎或近 5 日下跌，風險退潮。",
        )

    # 2) 高波動 overlay（最關鍵）：指數即使創高，只要盤中震盪大 / 創高急殺，視為震盪盤
    if _is_high_volatility(metrics):
        vol = metrics.get("intraday_range_5d_avg_pct")
        rev = metrics.get("reversal_days_5d") or 0
        vol_txt = f"近 5 日盤中振幅 {vol:.1f}%" if vol is not None else "盤中振幅偏大"
        return (
            REGIME_VOLATILE_RANGE,
            f"指數雖未轉空，但{vol_txt}、近 5 日創高急殺 {rev} 次，換手劇烈，視為震盪盤。",
        )

    # 3) BULL_TREND：多頭排列 + MA20 上揚 + 近 10 日報酬為正 + 波動不大
    if (
        ma10 is not None
        and close >= ma10
        and ma10 >= ma20
        and (slope is not None and slope > 0)
        and (ret10 is not None and ret10 > _BULL_RETURN_10D_PCT)
    ):
        return (
            REGIME_BULL_TREND,
            f"加權站上 MA10 {ma10:.0f}、多頭排列、MA20 上揚、近 10 日 {ret10:+.1f}% 且波動可控，趨勢偏多。",
        )

    # 4) 其餘皆視為震盪
    return (
        REGIME_VOLATILE_RANGE,
        "加權在均線附近震盪、無明確多頭排列，視為震盪盤。",
    )


def _load_taiex_rows(db: Session, target_date: date) -> list[DailyPrice]:
    rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == _TAIEX_SYMBOL,
            DailyPrice.trade_date <= target_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(_LOOKBACK_ROWS)
        .all()
    )
    rows.reverse()
    return rows


def compute_market_regime(db: Session, target_date: date) -> dict[str, Any]:
    """主入口：回 {regime, regime_label, reason, metrics}。

    無 TAIEX 資料 → 保守 VOLATILE_RANGE。LLM 不可改寫此 regime（backend authoritative）。
    """
    rows = _load_taiex_rows(db, target_date)
    # 只用 OHLC 齊全的列，保持 closes / highs / lows 等長對齊
    rows = [
        r
        for r in rows
        if r.close_price is not None
        and r.high_price is not None
        and r.low_price is not None
    ]
    closes = [float(r.close_price) for r in rows]
    highs = [float(r.high_price) for r in rows]
    lows = [float(r.low_price) for r in rows]
    metrics = compute_regime_metrics(closes, highs=highs, lows=lows)
    regime, reason = classify_regime(metrics)
    return {
        "regime": regime,
        "regime_label": REGIME_LABEL_ZH[regime],
        "reason": reason,
        "metrics": metrics,
    }
