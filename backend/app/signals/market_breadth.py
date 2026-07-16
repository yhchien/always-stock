"""魚尾 v2.2 市場廣度模組（fishtail momentum upgrade spec §7.1 / §7.2）。

解「指數很強但個股很弱」的誤判：TAIEX 創高時若全市場只有少數權值股撐盤
（廣度弱），BULL_TREND 不該用寬鬆的追強邏輯。

- `compute_breadth_from_frame(frame, masters)`：從 momentum frame 的內部欄位
  （`_above_ma20` / `_above_ma60` / `_ret_1d` / `_new_high_20d` / `_new_low_20d` /
  `return_5d` / `industry_return_20d`）聚合出 breadth metrics + `breadth_score`。
  **與 momentum frame 共用同一次全市場 query**，不重複掃 daily_price。
- universe 與 candidate pool 排除規則一致（frame 已排除 ETF / 金融 / 黑名單，
  spec §7.1 注意事項）。
- `resolve_regime_detail(regime, breadth_score)`：把 3 態 regime 疊 breadth 升成
  4 態 detail（BROAD_BULL / NARROW_BULL / VOLATILE_RANGE / RISK_OFF）。
  **對 LLM 的 `market_regime` 契約維持 3 態不變**（v5 prompt enum 固定）；
  NARROW_BULL 只作用在 deterministic gate（filters.apply_regime_gate）與
  snapshot / signal_metrics 的觀察欄位。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models import StockMaster
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)

# 4 態 regime detail（spec §7.2）
REGIME_DETAIL_BROAD_BULL = "BROAD_BULL"
REGIME_DETAIL_NARROW_BULL = "NARROW_BULL"

# 樣本 guard：全市場有效樣本太少（資料缺漏日）→ breadth 不可信，回 None
MIN_SAMPLES_FOR_BREADTH = 100

# breadth_score 權重（deterministic；加總 = 1.0）
_W_ABOVE_MA20 = 0.30
_W_ABOVE_MA60 = 0.20
_W_ADVANCE_DECLINE = 0.20
_W_NEW_HIGH_LOW = 0.15
_W_STRONG_INDUSTRY = 0.15

# BULL_TREND 疊 breadth 的分界：score < 此值 → NARROW_BULL（少數股撐盤）
NARROW_BULL_BREADTH_MAX = 50.0


def empty_breadth() -> Dict[str, Any]:
    return {
        "pct_above_ma20": None,
        "pct_above_ma60": None,
        "advance_decline_ratio": None,
        "new_high_20d_count": None,
        "new_low_20d_count": None,
        "median_stock_return_5d": None,
        "strong_industry_ratio": None,
        "breadth_score": None,
        "sample_size": 0,
    }


def compute_breadth_from_frame(
    frame: Dict[str, Dict[str, Any]],
    masters: Optional[Dict[str, StockMaster]] = None,
) -> Dict[str, Any]:
    """從 momentum frame 聚合 daily breadth metrics（spec §7.1）。

    frame 樣本不足（< MIN_SAMPLES_FOR_BREADTH 檔有 MA20 資料）→ 全部 None
    （保守：breadth 不可信時 regime detail 走 BROAD_BULL 不加嚴，維持 v2.1 行為）。
    """
    out = empty_breadth()
    if not frame:
        return out

    ma20_flags: List[bool] = []
    ma60_flags: List[bool] = []
    advancers = 0
    decliners = 0
    new_highs = 0
    new_lows = 0
    returns_5d: List[float] = []

    for feats in frame.values():
        above20 = feats.get("_above_ma20")
        if above20 is not None:
            ma20_flags.append(bool(above20))
        above60 = feats.get("_above_ma60")
        if above60 is not None:
            ma60_flags.append(bool(above60))
        ret1 = feats.get("_ret_1d")
        if ret1 is not None:
            if ret1 > 0:
                advancers += 1
            elif ret1 < 0:
                decliners += 1
        if feats.get("_new_high_20d"):
            new_highs += 1
        if feats.get("_new_low_20d"):
            new_lows += 1
        ret5 = feats.get("return_5d")
        if ret5 is not None:
            returns_5d.append(float(ret5))

    sample = len(ma20_flags)
    out["sample_size"] = sample
    if sample < MIN_SAMPLES_FOR_BREADTH:
        return out

    out["pct_above_ma20"] = 100.0 * sum(ma20_flags) / sample
    if ma60_flags:
        out["pct_above_ma60"] = 100.0 * sum(ma60_flags) / len(ma60_flags)
    if decliners > 0:
        out["advance_decline_ratio"] = advancers / decliners
    out["new_high_20d_count"] = new_highs
    out["new_low_20d_count"] = new_lows
    if returns_5d:
        out["median_stock_return_5d"] = _median(returns_5d)

    # 強勢產業比例：產業平均 20 日報酬 > 0 的產業佔比（frame 已算 industry_return_20d）
    industry_rets: Dict[str, float] = {}
    for sid, feats in frame.items():
        ind_ret = feats.get("industry_return_20d")
        if ind_ret is None:
            continue
        master = (masters or {}).get(sid)
        ind = master.industry_name if master is not None else None
        if ind:
            industry_rets[ind] = float(ind_ret)
    if industry_rets:
        strong = sum(1 for v in industry_rets.values() if v > 0)
        out["strong_industry_ratio"] = 100.0 * strong / len(industry_rets)

    out["breadth_score"] = _breadth_score(out, advancers, decliners, new_highs, new_lows)
    return out


def _breadth_score(
    metrics: Dict[str, Any],
    advancers: int,
    decliners: int,
    new_highs: int,
    new_lows: int,
) -> float:
    """0~100 deterministic 加權；子項缺值以中性 50 計（避免單一缺欄拉垮整體）。"""

    def _or_neutral(value: Optional[float]) -> float:
        return float(value) if value is not None else 50.0

    ad_component = 50.0
    total_moves = advancers + decliners
    if total_moves > 0:
        ad_component = 100.0 * advancers / total_moves

    hl_component = 50.0
    total_extremes = new_highs + new_lows
    if total_extremes > 0:
        hl_component = 100.0 * new_highs / total_extremes

    score = (
        _W_ABOVE_MA20 * _or_neutral(metrics.get("pct_above_ma20"))
        + _W_ABOVE_MA60 * _or_neutral(metrics.get("pct_above_ma60"))
        + _W_ADVANCE_DECLINE * ad_component
        + _W_NEW_HIGH_LOW * hl_component
        + _W_STRONG_INDUSTRY * _or_neutral(metrics.get("strong_industry_ratio"))
    )
    return round(max(0.0, min(100.0, score)), 1)


def resolve_regime_detail(regime: str, breadth_score: Optional[float]) -> str:
    """3 態 regime + breadth → 4 態 detail（spec §7.2）。

    - BULL_TREND + breadth 弱（score < NARROW_BULL_BREADTH_MAX）→ NARROW_BULL
    - BULL_TREND 其餘（含 breadth 缺值 → 保守不加嚴）→ BROAD_BULL
    - VOLATILE_RANGE / RISK_OFF 原樣
    """
    if regime == REGIME_BULL_TREND:
        if breadth_score is not None and breadth_score < NARROW_BULL_BREADTH_MAX:
            return REGIME_DETAIL_NARROW_BULL
        return REGIME_DETAIL_BROAD_BULL
    if regime in (REGIME_VOLATILE_RANGE, REGIME_RISK_OFF):
        return regime
    return REGIME_VOLATILE_RANGE


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
