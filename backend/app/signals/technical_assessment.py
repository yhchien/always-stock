"""Deterministic technical assessment shared by fishtail and trading layers.

The raw indicator set is intentionally collapsed into independent evidence
families before it reaches an LLM or a trading rule.  This prevents MA5/MA10
or KDJ/RSI/MACD from being counted as several independent votes for the same
underlying price move.

This module is pure and point-in-time: it only reads fields already calculated
for the target date and never looks at future prices.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


TECHNICAL_ASSESSMENT_VERSION = "technical_v1"

# Directional weights sum to 90.  Volatility is reported as a risk modifier,
# not as a bullish/bearish vote, so a high-ATR stock cannot become weak merely
# because it is volatile.
FAMILY_WEIGHTS = {
    "price_structure": 30.0,
    "trend_ma": 20.0,
    "relative_strength": 15.0,
    "volume_price": 15.0,
    "momentum": 10.0,
}
_DIRECTIONAL_WEIGHT_TOTAL = sum(FAMILY_WEIGHTS.values())

STRONG_STATE = "STRONG"
IMPROVING_STATE = "IMPROVING"
NEUTRAL_STATE = "NEUTRAL"
WEAKENING_STATE = "WEAKENING"
BROKEN_STATE = "BROKEN"
CONFLICTED_STATE = "CONFLICTED"
INSUFFICIENT_STATE = "INSUFFICIENT_DATA"


def _number(candidate: Dict[str, Any], key: str) -> Optional[float]:
    value = candidate.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _family(
    *,
    state: str,
    strength: float = 0.0,
    weakness: float = 0.0,
    signals: Iterable[str] = (),
    available: bool = True,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "state": state,
        "strength": round(max(0.0, min(1.0, strength)), 3),
        "weakness": round(max(0.0, min(1.0, weakness)), 3),
        "signals": list(signals),
        "available": bool(available),
        "detail": detail or {},
    }


def _price_structure(candidate: Dict[str, Any]) -> Dict[str, Any]:
    breakout = candidate.get("breakout_20d_confirmed")
    breakdown = candidate.get("breakdown_20d_confirmed")
    close_vs_support = _number(candidate, "distance_to_support_20d")
    close_vs_resistance = _number(candidate, "distance_to_resistance_20d")
    support_hold = candidate.get("support_20d_hold")
    resistance_retest = candidate.get("resistance_retest_hold")

    available = any(
        value is not None
        for value in (breakout, breakdown, close_vs_support, close_vs_resistance, support_hold)
    )
    if not available:
        return _family(state=INSUFFICIENT_STATE, available=False)
    if breakdown is True:
        return _family(
            state=BROKEN_STATE,
            weakness=1.0,
            signals=["SUPPORT_BREAKDOWN"],
            detail={"distance_to_support_20d": close_vs_support},
        )
    if breakout is True and (resistance_retest is not False):
        return _family(
            state=STRONG_STATE,
            strength=1.0,
            signals=["BREAKOUT_CONFIRMED"],
            detail={"distance_to_resistance_20d": close_vs_resistance},
        )
    if support_hold is True:
        return _family(
            state=IMPROVING_STATE,
            strength=0.65,
            signals=["SUPPORT_HOLD"],
            detail={"distance_to_support_20d": close_vs_support},
        )
    if resistance_retest is False:
        return _family(
            state=WEAKENING_STATE,
            weakness=0.75,
            signals=["RESISTANCE_RETEST_FAILED"],
            detail={"distance_to_resistance_20d": close_vs_resistance},
        )
    return _family(
        state=NEUTRAL_STATE,
        strength=0.25 if close_vs_resistance is not None and close_vs_resistance <= 2.0 else 0.0,
        signals=["PRICE_STRUCTURE_UNCONFIRMED"],
        detail={
            "distance_to_support_20d": close_vs_support,
            "distance_to_resistance_20d": close_vs_resistance,
        },
    )


def _trend_ma(candidate: Dict[str, Any]) -> Dict[str, Any]:
    close_vs_20 = _number(candidate, "distance_to_ma20")
    close_vs_60 = _number(candidate, "distance_to_sma60_pct")
    slope_20 = _number(candidate, "sma20_slope_5d_pct")
    alignment = str(candidate.get("ma_alignment") or "").upper()
    available = any(value is not None for value in (close_vs_20, close_vs_60, slope_20)) or bool(alignment)
    if not available:
        return _family(state=INSUFFICIENT_STATE, available=False)

    if close_vs_60 is not None and close_vs_60 < 0 and close_vs_20 is not None and close_vs_20 < 0:
        return _family(
            state=BROKEN_STATE,
            weakness=1.0,
            signals=["BELOW_MA20_AND_MA60"],
            detail={"distance_to_ma20": close_vs_20, "distance_to_sma60_pct": close_vs_60},
        )
    if close_vs_20 is not None and close_vs_20 > 0 and slope_20 is not None and slope_20 > 0:
        strength = 0.8 if alignment in {"BULLISH", "BULLISH_SHORT_MEDIUM"} else 0.65
        return _family(
            state=STRONG_STATE,
            strength=strength,
            signals=["ABOVE_MA20", "MA20_SLOPE_UP"]
            + (["BULLISH_MA_ALIGNMENT"] if alignment.startswith("BULLISH") else []),
            detail={
                "distance_to_ma20": close_vs_20,
                "distance_to_sma60_pct": close_vs_60,
                "sma20_slope_5d_pct": slope_20,
                "ma_alignment": alignment or None,
            },
        )
    if close_vs_20 is not None and close_vs_20 < 0 and slope_20 is not None and slope_20 < 0:
        return _family(
            state=WEAKENING_STATE,
            weakness=0.85,
            signals=["BELOW_MA20", "MA20_SLOPE_DOWN"],
            detail={"distance_to_ma20": close_vs_20, "sma20_slope_5d_pct": slope_20},
        )
    if close_vs_20 is not None and close_vs_20 > 0:
        return _family(
            state=IMPROVING_STATE,
            strength=0.55,
            signals=["ABOVE_MA20"],
            detail={"distance_to_ma20": close_vs_20, "sma20_slope_5d_pct": slope_20},
        )
    return _family(
        state=NEUTRAL_STATE,
        detail={"distance_to_ma20": close_vs_20, "sma20_slope_5d_pct": slope_20},
    )


def _relative_strength(candidate: Dict[str, Any]) -> Dict[str, Any]:
    market = _number(candidate, "rs_market_percentile_20d")
    industry = _number(candidate, "rs_industry_percentile_20d")
    improvement = _number(candidate, "rs_rank_improvement_5d")
    available = any(value is not None for value in (market, industry, improvement))
    if not available:
        return _family(state=INSUFFICIENT_STATE, available=False)

    positive = sum(
        value is not None and value >= threshold
        for value, threshold in ((market, 80.0), (industry, 70.0), (improvement, 0.0))
    )
    negative = sum(
        value is not None and value <= threshold
        for value, threshold in ((market, 40.0), (industry, 40.0), (improvement, -100.0))
    )
    if positive >= 2 and negative == 0:
        return _family(
            state=STRONG_STATE,
            strength=min(1.0, 0.55 + positive * 0.15),
            signals=["RS_MARKET_STRONG"] if market is not None and market >= 80 else [],
            detail={"market_percentile": market, "industry_percentile": industry, "rank_improvement": improvement},
        )
    if negative >= 2:
        return _family(
            state=WEAKENING_STATE,
            weakness=min(1.0, 0.55 + negative * 0.15),
            signals=["RS_DETERIORATING"],
            detail={"market_percentile": market, "industry_percentile": industry, "rank_improvement": improvement},
        )
    if positive:
        return _family(
            state=IMPROVING_STATE,
            strength=0.5,
            signals=["RS_IMPROVING"],
            detail={"market_percentile": market, "industry_percentile": industry, "rank_improvement": improvement},
        )
    return _family(
        state=NEUTRAL_STATE,
        detail={"market_percentile": market, "industry_percentile": industry, "rank_improvement": improvement},
    )


def _volume_price(candidate: Dict[str, Any]) -> Dict[str, Any]:
    up_down = _number(candidate, "up_down_volume_ratio_20d")
    volume_ratio = _number(candidate, "volume_1d_to_20d_avg")
    distribution = "distribution" in (candidate.get("soft_hints") or [])
    available = any(value is not None for value in (up_down, volume_ratio)) or distribution
    if not available:
        return _family(state=INSUFFICIENT_STATE, available=False)
    if distribution or (up_down is not None and up_down < 0.8):
        return _family(
            state=WEAKENING_STATE,
            weakness=0.8 if distribution else 0.65,
            signals=["DISTRIBUTION"] if distribution else ["DOWN_VOLUME_DOMINANT"],
            detail={"up_down_volume_ratio_20d": up_down, "volume_1d_to_20d_avg": volume_ratio},
        )
    if up_down is not None and up_down >= 1.2 and (volume_ratio is None or volume_ratio >= 1.0):
        return _family(
            state=STRONG_STATE,
            strength=0.75,
            signals=["UP_VOLUME_DOMINANT"],
            detail={"up_down_volume_ratio_20d": up_down, "volume_1d_to_20d_avg": volume_ratio},
        )
    return _family(
        state=NEUTRAL_STATE,
        detail={"up_down_volume_ratio_20d": up_down, "volume_1d_to_20d_avg": volume_ratio},
    )


def _momentum(candidate: Dict[str, Any]) -> Dict[str, Any]:
    signals: List[str] = []
    positive = 0
    negative = 0
    for key, positive_values, negative_values in (
        ("kdj_signal", {"golden_cross", "oversold_rebound"}, {"death_cross", "overbought_turn_down"}),
        ("macd_signal", {"bullish_cross", "histogram_rising"}, {"bearish_cross", "histogram_falling"}),
        ("rsi_signal", {"cross_above_50", "oversold_recovery"}, {"cross_below_50", "overbought_turn_down"}),
    ):
        value = str(candidate.get(key) or "").lower()
        if not value:
            continue
        if value in positive_values:
            positive += 1
            signals.append(key.upper() + "_POSITIVE")
        elif value in negative_values:
            negative += 1
            signals.append(key.upper() + "_NEGATIVE")
    if not signals:
        return _family(state=INSUFFICIENT_STATE, available=False)
    if positive >= 2 and negative == 0:
        return _family(state=STRONG_STATE, strength=0.75, signals=signals)
    if negative >= 2 and positive == 0:
        return _family(state=WEAKENING_STATE, weakness=0.75, signals=signals)
    if positive > negative:
        return _family(state=IMPROVING_STATE, strength=0.5, signals=signals)
    if negative > positive:
        return _family(state=WEAKENING_STATE, weakness=0.5, signals=signals)
    return _family(state=CONFLICTED_STATE, signals=signals)


def _risk(candidate: Dict[str, Any]) -> Dict[str, Any]:
    atr = _number(candidate, "atr_pct_14d")
    if atr is None:
        return {"state": INSUFFICIENT_STATE, "available": False, "risk_level": "UNKNOWN", "signals": [], "detail": {}}
    # ATR is deliberately descriptive only.  A high-volatility stock receives
    # a caution label, never an automatic bearish vote.
    risk_level = "HIGH" if atr >= 8.0 else "MEDIUM" if atr >= 4.0 else "LOW"
    return {
        "state": NEUTRAL_STATE,
        "available": True,
        "risk_level": risk_level,
        "signals": ["HIGH_ATR"] if risk_level == "HIGH" else [],
        "detail": {"atr_pct_14d": atr},
    }


def _score(families: Dict[str, Dict[str, Any]], key: str) -> tuple[float, float]:
    numerator = 0.0
    denominator = 0.0
    for name, weight in FAMILY_WEIGHTS.items():
        family = families[name]
        if not family.get("available"):
            continue
        denominator += weight
        numerator += weight * float(family.get(key) or 0.0)
    if denominator <= 0:
        return 0.0, 0.0
    return round(numerator / denominator * 100.0, 1), round(denominator / _DIRECTIONAL_WEIGHT_TOTAL * 100.0, 1)


def _resolve_state(
    *,
    strength_score: float,
    weakness_score: float,
    families: Dict[str, Dict[str, Any]],
    coverage: float,
) -> str:
    if coverage < 40.0:
        return INSUFFICIENT_STATE
    structural_weak = any(
        families[name]["state"] == BROKEN_STATE
        for name in ("price_structure", "trend_ma")
        if families[name].get("available")
    )
    if weakness_score >= 70.0 and structural_weak:
        return BROKEN_STATE
    if strength_score >= 65.0 and weakness_score < 45.0:
        return STRONG_STATE
    if weakness_score >= 55.0 and strength_score < 60.0:
        return WEAKENING_STATE
    if strength_score >= 55.0 and strength_score > weakness_score + 10.0:
        return IMPROVING_STATE
    if strength_score >= 55.0 and weakness_score >= 55.0:
        return CONFLICTED_STATE
    return NEUTRAL_STATE


def build_technical_assessment(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Return the canonical, LLM-safe technical assessment."""
    families = {
        "price_structure": _price_structure(candidate),
        "trend_ma": _trend_ma(candidate),
        "relative_strength": _relative_strength(candidate),
        "volume_price": _volume_price(candidate),
        "momentum": _momentum(candidate),
    }
    strength_score, coverage = _score(families, "strength")
    weakness_score, _ = _score(families, "weakness")
    state = _resolve_state(
        strength_score=strength_score,
        weakness_score=weakness_score,
        families=families,
        coverage=coverage,
    )
    available_count = sum(1 for family in families.values() if family.get("available"))
    non_neutral_count = sum(
        1
        for family in families.values()
        if family.get("available") and family.get("state") not in {NEUTRAL_STATE, INSUFFICIENT_STATE}
    )
    if coverage >= 75.0 and available_count >= 4 and non_neutral_count >= 3:
        confidence = "HIGH"
    elif coverage >= 50.0 and available_count >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    conflicts: List[str] = []
    if strength_score >= 55.0 and weakness_score >= 55.0:
        conflicts.append("STRENGTH_AND_WEAKNESS_BOTH_PRESENT")
    if (
        families["price_structure"].get("state") == STRONG_STATE
        and families["volume_price"].get("state") == WEAKENING_STATE
    ):
        conflicts.append("BREAKOUT_WITH_WEAK_VOLUME")

    actionability = {
        STRONG_STATE: "POSITIVE",
        IMPROVING_STATE: "WATCH_FOR_ENTRY",
        NEUTRAL_STATE: "NEUTRAL",
        WEAKENING_STATE: "CAUTION",
        BROKEN_STATE: "EXIT_CANDIDATE",
        CONFLICTED_STATE: "CAUTION",
        INSUFFICIENT_STATE: "INSUFFICIENT_DATA",
    }[state]
    return {
        "version": TECHNICAL_ASSESSMENT_VERSION,
        "state": state,
        "actionability": actionability,
        "strength_score": strength_score,
        "weakness_score": weakness_score,
        "confidence": confidence,
        "coverage_pct": coverage,
        "families": families,
        "risk": _risk(candidate),
        "conflicts": conflicts,
        "thresholds": {
            "strong_score": 65.0,
            "weakening_score": 55.0,
            "broken_score": 70.0,
            "minimum_exit_confirmation_days": 2,
        },
    }

