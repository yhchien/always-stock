"""v5 `deterministic_signals` 後端化（2026-07-15，補 M27 refinement #6 的「延後」債）。

v4/v5 prompt 的 STEP 6 / 7 / 7.5 宣告：若 backend 提供 `deterministic_signals`
（chip_trend / technical_status / entry_quality / sector_rotation_status /
institution_flow_momentum / risk_gate_action / max_decision / risk_flags），
LLM **必須直接採用、不可改寫**；缺欄位才 fallback 自行判讀。
本模組把這些欄位從候選池既有欄位規則化算出，掛進每筆 candidate。

不算 `theme_maturity`：題材成熟度需要外部資訊（新聞 / 法說），留給 LLM STEP 2/3。

全部純函式、無 DB 依賴；輸入為「已過 soft filter 的候選 dict」
（需要 soft_hints / momentum_phase / industry_flow_* 等欄位）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.filters import (
    HINT_DISTRIBUTION,
    HINT_RANGE_BOUND,
    HINT_RETAIL_OVERHEATED,
    HINT_WEAKENING,
)

# chip_trend：accumulating 門檻
_CHIP_ACCUM_BUY_DAYS_MIN = 2
_CHIP_ACCUM_VOLUME_RATIO_MIN = 1.2

# institution_flow_momentum：今日 vs 3 日均量的加速 / 減速倍率
_FLOW_ACCEL_RATIO = 1.5   # flow_1d > 3 日日均 × 1.5 → accelerating
_FLOW_DECEL_RATIO = 0.5   # flow_1d < 3 日日均 × 0.5 → decelerating

# entry_quality
_ENTRY_SPIKE_VOL_RATIO = 2.0      # 急拉：當日量 / 5 日均量（與 filters._is_spike_breakout 對齊）
_ENTRY_SPIKE_PRICE_1D_PCT = 5.0
_ENTRY_BREAKOUT_VOLUME_MIN = 1.2  # 突破需帶量（當日 / 20 日均量）
_ENTRY_PULLBACK_DIST_HIGH_MIN = -10.0  # 回測：距 20 日高 -10% ~ -3%
_ENTRY_PULLBACK_DIST_HIGH_MAX = -3.0

# technical_status
_TECH_STEADY_TREND_EFF_MIN = 0.4

# risk_flags
_RS_DETERIORATION_RANK_DROP = -100


def attach_deterministic_signals(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """為每筆候選加 `deterministic_signals` dict（不 mutate 原 dict）。"""
    return [
        {**c, "deterministic_signals": build_deterministic_signals(c)}
        for c in candidates
    ]


def build_deterministic_signals(candidate: Dict[str, Any]) -> Dict[str, Any]:
    sector = _sector_rotation_status(candidate)
    flow_momentum = _institution_flow_momentum(candidate)
    technical = _technical_status(candidate)
    entry = _entry_quality(candidate, technical)
    chip = _chip_trend(candidate)
    flags = _risk_flags(candidate, sector, flow_momentum, entry)
    gate = _risk_gate_action(candidate, flags)
    return {
        "chip_trend": chip,
        "technical_status": technical,
        "entry_quality": entry,
        "sector_rotation_status": sector,
        "institution_flow_momentum": flow_momentum,
        "risk_gate_action": gate,
        "max_decision": "REMOVE" if gate == "EXCLUDE" else "WATCH",
        "risk_flags": flags,
    }


# ---------- 個別訊號 ----------


def _hints(candidate: Dict[str, Any]) -> List[str]:
    return candidate.get("soft_hints") or []


def _chip_trend(candidate: Dict[str, Any]) -> str:
    """優先序：weakening > retail_overheated > short_squeeze_potential > accumulating > neutral。"""
    hints = _hints(candidate)
    if HINT_WEAKENING in hints:
        return "weakening"
    if HINT_RETAIL_OVERHEATED in hints:
        return "retail_overheated"

    # 資減券增且股價未跌 → 軋空潛力
    margin_chg = candidate.get("margin_change_shares")
    short_chg = candidate.get("short_change_shares")
    pct_1d = candidate.get("price_change_1d")
    if (
        margin_chg is not None
        and margin_chg < 0
        and short_chg is not None
        and short_chg > 0
        and pct_1d is not None
        and pct_1d >= 0
    ):
        return "short_squeeze_potential"

    buy_days = candidate.get("consecutive_buy_days_3d") or 0
    vol_ratio = candidate.get("volume_5d_to_60d_ratio")
    price_5d = candidate.get("price_change_5d")
    if (
        buy_days >= _CHIP_ACCUM_BUY_DAYS_MIN
        and price_5d is not None
        and price_5d > 0
        and vol_ratio is not None
        and vol_ratio >= _CHIP_ACCUM_VOLUME_RATIO_MIN
    ):
        return "accumulating"

    return "neutral"


def _technical_status(candidate: Dict[str, Any]) -> str:
    """優先序：distribution > breakout > steady_uptrend > early_turn > range_bound > weak。"""
    hints = _hints(candidate)
    if HINT_DISTRIBUTION in hints:
        return "distribution"

    dist_high = candidate.get("distance_to_20d_high")
    if dist_high is not None and dist_high >= 0.0:
        return "breakout"

    dist_ma20 = candidate.get("distance_to_ma20")
    trend_eff = candidate.get("trend_efficiency_20d")
    ret_20d = candidate.get("return_20d")
    if (
        dist_ma20 is not None
        and dist_ma20 > 0
        and trend_eff is not None
        and trend_eff >= _TECH_STEADY_TREND_EFF_MIN
        and ret_20d is not None
        and ret_20d > 0
    ):
        return "steady_uptrend"

    close = candidate.get("close_1d")
    ma10 = candidate.get("ma_10d")
    improvement = candidate.get("rs_rank_improvement_5d")
    if (
        close is not None
        and ma10 is not None
        and close > ma10
        and improvement is not None
        and improvement > 0
    ):
        return "early_turn"

    if HINT_RANGE_BOUND in hints:
        return "range_bound"

    return "weak"


def _entry_quality(candidate: Dict[str, Any], technical: str) -> str:
    """優先序：extended_chase（危險先標）> breakout_confirmed > pullback_setup >
    failed_rotation > neutral。"""
    # 急拉追高（與 filters._is_spike_breakout 同門檻）或動能已過熱
    vol_1d_5d = candidate.get("volume_1d_to_5d_ratio")
    pct_1d = candidate.get("price_change_1d")
    if (
        vol_1d_5d is not None
        and vol_1d_5d > _ENTRY_SPIKE_VOL_RATIO
        and pct_1d is not None
        and pct_1d > _ENTRY_SPIKE_PRICE_1D_PCT
    ) or candidate.get("momentum_phase") == "extended":
        return "extended_chase"

    # 突破後仍有承接：創 20 日新高 + 帶量（非急拉）
    vol_1d_20d = candidate.get("volume_1d_to_20d_avg")
    if (
        technical == "breakout"
        and vol_1d_20d is not None
        and vol_1d_20d >= _ENTRY_BREAKOUT_VOLUME_MIN
    ):
        return "breakout_confirmed"

    # 回測再啟動：趨勢仍在（站上 MA20）+ 距高點 -10% ~ -3% + 量縮
    dist_ma20 = candidate.get("distance_to_ma20")
    dist_high = candidate.get("distance_to_20d_high")
    ret_20d = candidate.get("return_20d")
    if (
        dist_ma20 is not None
        and dist_ma20 > 0
        and dist_high is not None
        and _ENTRY_PULLBACK_DIST_HIGH_MIN <= dist_high <= _ENTRY_PULLBACK_DIST_HIGH_MAX
        and ret_20d is not None
        and ret_20d > 0
        and vol_1d_5d is not None
        and vol_1d_5d < 1.0
    ):
        return "pullback_setup"

    # 產業輪動失敗且個股價格無確認
    price_5d = candidate.get("price_change_5d")
    if _sector_rotation_status(candidate) == "failed_rotation" and (
        price_5d is None or price_5d <= 0
    ):
        return "failed_rotation"

    return "neutral"


def _sector_rotation_status(candidate: Dict[str, Any]) -> str:
    flow_3d = candidate.get("industry_flow_3d")
    flow_1d = candidate.get("industry_flow_1d")
    if flow_3d is None or flow_1d is None:
        return "neutral"
    if flow_3d > 0 and flow_1d > 0:
        return "inflow"
    if flow_3d > 0 and flow_1d <= 0:
        return "cooling"
    ind_rs = candidate.get("industry_rs_percentile_20d")
    if flow_3d <= 0 and ind_rs is not None and ind_rs < 50.0:
        return "failed_rotation"
    return "neutral"


def _institution_flow_momentum(candidate: Dict[str, Any]) -> str:
    flow_1d = candidate.get("total_institution_flow_1d")
    flow_3d = candidate.get("total_institution_flow_3d")
    if flow_1d is None or flow_3d is None:
        return "neutral"
    if flow_3d > 0 and flow_1d < 0:
        return "reversal"
    daily_avg_3d = flow_3d / 3.0
    if flow_1d > 0 and flow_3d > 0:
        if flow_1d > daily_avg_3d * _FLOW_ACCEL_RATIO:
            return "accelerating"
        if flow_1d < daily_avg_3d * _FLOW_DECEL_RATIO:
            return "decelerating"
        return "stable"
    return "neutral"


def _risk_flags(
    candidate: Dict[str, Any],
    sector: str,
    flow_momentum: str,
    entry: str,
) -> List[str]:
    flags: List[str] = []
    if flow_momentum == "reversal":
        flags.append("institution_flow_reversal")
    if sector == "failed_rotation":
        flags.append("failed_rotation")
    hints = _hints(candidate)
    if HINT_DISTRIBUTION in hints:
        flags.append("distribution")
    if HINT_RETAIL_OVERHEATED in hints:
        flags.append("retail_overheated")
    if entry == "extended_chase":
        flags.append("extended_chase")
    improvement = candidate.get("rs_rank_improvement_5d")
    if improvement is not None and improvement <= _RS_DETERIORATION_RANK_DROP:
        flags.append("rs_deterioration")
    return flags


def _risk_gate_action(candidate: Dict[str, Any], flags: List[str]) -> str:
    """PASS | DOWNGRADE_ONE_LEVEL | MAX_B | EXCLUDE（v5 STEP 7.5）。

    - EXCLUDE：出貨 + 法人反轉同時出現，或輪動失敗 + 動能轉弱 → 結構性風險
    - MAX_B：散戶過熱 / 急拉追高 → 可觀察但不可積極
    - DOWNGRADE_ONE_LEVEL：其餘任一風險旗標
    - PASS：無旗標
    """
    flag_set = set(flags)
    if "distribution" in flag_set and "institution_flow_reversal" in flag_set:
        return "EXCLUDE"
    if "failed_rotation" in flag_set and candidate.get("momentum_phase") == "weakening":
        return "EXCLUDE"
    if "retail_overheated" in flag_set or "extended_chase" in flag_set:
        return "MAX_B"
    if flag_set:
        return "DOWNGRADE_ONE_LEVEL"
    return "PASS"
