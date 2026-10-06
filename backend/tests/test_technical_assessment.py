from app.signals.technical_assessment import build_technical_assessment


def _candidate(**overrides):
    base = {
        "distance_to_ma20": 3.0,
        "distance_to_sma60_pct": 8.0,
        "sma20_slope_5d_pct": 1.5,
        "ma_alignment": "BULLISH",
        "breakout_20d_confirmed": True,
        "breakdown_20d_confirmed": False,
        "support_20d_hold": True,
        "resistance_retest_hold": True,
        "distance_to_resistance_20d": 1.0,
        "distance_to_support_20d": 8.0,
        "rs_market_percentile_20d": 92.0,
        "rs_industry_percentile_20d": 88.0,
        "rs_rank_improvement_5d": 120.0,
        "up_down_volume_ratio_20d": 1.5,
        "volume_1d_to_20d_avg": 1.3,
        "kdj_signal": "golden_cross",
        "macd_signal": "histogram_rising",
        "rsi_signal": "neutral",
        "atr_pct_14d": 3.0,
        "soft_hints": [],
    }
    base.update(overrides)
    return base


def test_canonical_assessment_requires_multiple_families_for_strong():
    out = build_technical_assessment(_candidate())

    assert out["version"] == "technical_v1"
    assert out["state"] == "STRONG"
    assert out["strength_score"] >= 65.0
    assert out["weakness_score"] < 45.0
    assert out["confidence"] == "HIGH"
    assert out["families"]["price_structure"]["state"] == "STRONG"
    assert out["families"]["trend_ma"]["state"] == "STRONG"


def test_single_momentum_signal_does_not_create_technical_weakness():
    out = build_technical_assessment(
        _candidate(
            breakout_20d_confirmed=None,
            support_20d_hold=None,
            resistance_retest_hold=None,
            kdj_signal="death_cross",
            macd_signal="neutral",
            rsi_signal="neutral",
        )
    )

    assert out["families"]["momentum"]["state"] == "WEAKENING"
    assert out["state"] != "BROKEN"
    assert out["weakness_score"] < 55.0


def test_structural_break_with_confirmation_is_broken():
    out = build_technical_assessment(
        _candidate(
            distance_to_ma20=-4.0,
            distance_to_sma60_pct=-7.0,
            sma20_slope_5d_pct=-1.5,
            ma_alignment="BEARISH",
            breakout_20d_confirmed=False,
            breakdown_20d_confirmed=True,
            support_20d_hold=False,
            resistance_retest_hold=False,
            rs_market_percentile_20d=25.0,
            rs_industry_percentile_20d=30.0,
            rs_rank_improvement_5d=-150.0,
            up_down_volume_ratio_20d=0.6,
            kdj_signal="death_cross",
            macd_signal="bearish_cross",
            rsi_signal="cross_below_50",
        )
    )

    assert out["state"] == "BROKEN"
    assert out["actionability"] == "EXIT_CANDIDATE"
    assert out["weakness_score"] >= 70.0


def test_missing_indicators_are_not_treated_as_neutral_votes():
    out = build_technical_assessment({"atr_pct_14d": 3.0})

    assert out["state"] == "INSUFFICIENT_DATA"
    assert out["coverage_pct"] == 0.0
    assert out["confidence"] == "LOW"

