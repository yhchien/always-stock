"""魚尾 v2.2：deterministic_signals.py 測試（v5 STEP 6/7/7.5 的後端 deterministic 化）。"""
from app.signals.deterministic_signals import (
    attach_deterministic_signals,
    build_deterministic_signals,
)
from app.signals.filters import (
    HINT_DISTRIBUTION,
    HINT_RANGE_BOUND,
    HINT_RETAIL_OVERHEATED,
    HINT_WEAKENING,
)


def _candidate(**overrides):
    """乾淨候選（全部 neutral / PASS）。"""
    base = {
        "stock_id": "2330",
        "soft_hints": [],
        "consecutive_buy_days_3d": 0,
        "volume_5d_to_60d_ratio": 1.0,
        "price_change_1d": 0.5,
        "price_change_5d": 1.0,
        "total_institution_flow_1d": 1.0e7,
        "total_institution_flow_3d": 3.0e7,
        "industry_flow_1d": 1.0e8,
        "industry_flow_3d": 3.0e8,
        "distance_to_20d_high": -5.0,
        "distance_to_ma20": 1.0,
        "trend_efficiency_20d": 0.2,
        "return_20d": 3.0,
        "close_1d": 100.0,
        "ma_10d": 101.0,
        "rs_rank_improvement_5d": 0,
        "volume_1d_to_5d_ratio": 1.0,
        "volume_1d_to_20d_avg": 1.0,
        "momentum_phase": "trending",
        "industry_rs_percentile_20d": 70.0,
    }
    base.update(overrides)
    return base


# ---------- chip_trend ----------


def test_chip_weakening_from_hint():
    out = build_deterministic_signals(_candidate(soft_hints=[HINT_WEAKENING]))
    assert out["chip_trend"] == "weakening"


def test_chip_retail_overheated_from_hint():
    out = build_deterministic_signals(_candidate(soft_hints=[HINT_RETAIL_OVERHEATED]))
    assert out["chip_trend"] == "retail_overheated"


def test_chip_short_squeeze_potential():
    out = build_deterministic_signals(
        _candidate(margin_change_shares=-500, short_change_shares=100, price_change_1d=1.0)
    )
    assert out["chip_trend"] == "short_squeeze_potential"


def test_chip_accumulating():
    out = build_deterministic_signals(
        _candidate(consecutive_buy_days_3d=3, price_change_5d=4.0, volume_5d_to_60d_ratio=1.5)
    )
    assert out["chip_trend"] == "accumulating"


def test_chip_neutral_default():
    assert build_deterministic_signals(_candidate())["chip_trend"] == "neutral"


# ---------- technical_status ----------


def test_technical_distribution_from_hint():
    out = build_deterministic_signals(_candidate(soft_hints=[HINT_DISTRIBUTION]))
    assert out["technical_status"] == "distribution"


def test_technical_breakout_on_new_high():
    out = build_deterministic_signals(_candidate(distance_to_20d_high=0.0))
    assert out["technical_status"] == "breakout"


def test_technical_steady_uptrend():
    out = build_deterministic_signals(
        _candidate(distance_to_ma20=3.0, trend_efficiency_20d=0.6, return_20d=8.0)
    )
    assert out["technical_status"] == "steady_uptrend"


def test_technical_early_turn():
    out = build_deterministic_signals(
        _candidate(close_1d=102.0, ma_10d=100.0, rs_rank_improvement_5d=80)
    )
    assert out["technical_status"] == "early_turn"


def test_technical_range_bound_from_hint():
    out = build_deterministic_signals(_candidate(soft_hints=[HINT_RANGE_BOUND]))
    assert out["technical_status"] == "range_bound"


def test_technical_weak_fallback():
    assert build_deterministic_signals(_candidate())["technical_status"] == "weak"


# ---------- entry_quality ----------


def test_entry_extended_chase_on_spike():
    out = build_deterministic_signals(
        _candidate(volume_1d_to_5d_ratio=2.5, price_change_1d=6.0)
    )
    assert out["entry_quality"] == "extended_chase"
    assert "extended_chase" in out["risk_flags"]
    assert out["risk_gate_action"] == "MAX_B"


def test_entry_extended_chase_on_extended_phase():
    out = build_deterministic_signals(_candidate(momentum_phase="extended"))
    assert out["entry_quality"] == "extended_chase"


def test_entry_breakout_confirmed():
    out = build_deterministic_signals(
        _candidate(distance_to_20d_high=0.0, volume_1d_to_20d_avg=1.5)
    )
    assert out["entry_quality"] == "breakout_confirmed"


def test_entry_pullback_setup():
    out = build_deterministic_signals(
        _candidate(
            distance_to_ma20=2.0,
            distance_to_20d_high=-5.0,
            return_20d=10.0,
            volume_1d_to_5d_ratio=0.7,
        )
    )
    assert out["entry_quality"] == "pullback_setup"


def test_entry_failed_rotation():
    out = build_deterministic_signals(
        _candidate(
            industry_flow_3d=-1.0e8,
            industry_flow_1d=-5.0e7,
            industry_rs_percentile_20d=30.0,
            price_change_5d=-2.0,
        )
    )
    assert out["entry_quality"] == "failed_rotation"
    assert out["sector_rotation_status"] == "failed_rotation"
    assert "failed_rotation" in out["risk_flags"]


# ---------- sector_rotation_status ----------


def test_sector_inflow():
    out = build_deterministic_signals(_candidate())
    assert out["sector_rotation_status"] == "inflow"


def test_sector_cooling():
    out = build_deterministic_signals(_candidate(industry_flow_1d=-1.0e7))
    assert out["sector_rotation_status"] == "cooling"


def test_sector_neutral_when_flows_missing():
    out = build_deterministic_signals(
        _candidate(industry_flow_1d=None, industry_flow_3d=None)
    )
    assert out["sector_rotation_status"] == "neutral"


# ---------- institution_flow_momentum ----------


def test_flow_reversal():
    out = build_deterministic_signals(
        _candidate(total_institution_flow_3d=3.0e7, total_institution_flow_1d=-1.0e7)
    )
    assert out["institution_flow_momentum"] == "reversal"
    assert "institution_flow_reversal" in out["risk_flags"]
    assert out["risk_gate_action"] == "DOWNGRADE_ONE_LEVEL"


def test_flow_accelerating():
    # 3 日均 = 1e7；今日 2e7 > 1.5e7 → accelerating
    out = build_deterministic_signals(
        _candidate(total_institution_flow_3d=3.0e7, total_institution_flow_1d=2.0e7)
    )
    assert out["institution_flow_momentum"] == "accelerating"


def test_flow_decelerating():
    # 3 日均 = 1e7；今日 0.3e7 < 0.5e7 → decelerating
    out = build_deterministic_signals(
        _candidate(total_institution_flow_3d=3.0e7, total_institution_flow_1d=0.3e7)
    )
    assert out["institution_flow_momentum"] == "decelerating"


def test_flow_stable():
    out = build_deterministic_signals(
        _candidate(total_institution_flow_3d=3.0e7, total_institution_flow_1d=1.0e7)
    )
    assert out["institution_flow_momentum"] == "stable"


# ---------- risk_gate_action / max_decision ----------


def test_gate_pass_when_clean():
    out = build_deterministic_signals(_candidate())
    assert out["risk_flags"] == []
    assert out["risk_gate_action"] == "PASS"
    assert out["max_decision"] == "WATCH"


def test_gate_exclude_on_distribution_plus_reversal():
    out = build_deterministic_signals(
        _candidate(
            soft_hints=[HINT_DISTRIBUTION],
            total_institution_flow_3d=3.0e7,
            total_institution_flow_1d=-1.0e7,
        )
    )
    assert out["risk_gate_action"] == "EXCLUDE"
    assert out["max_decision"] == "REMOVE"


def test_gate_exclude_on_failed_rotation_plus_weakening_phase():
    out = build_deterministic_signals(
        _candidate(
            industry_flow_3d=-1.0e8,
            industry_flow_1d=-5.0e7,
            industry_rs_percentile_20d=30.0,
            momentum_phase="weakening",
        )
    )
    assert out["risk_gate_action"] == "EXCLUDE"


def test_gate_rs_deterioration_flag():
    out = build_deterministic_signals(_candidate(rs_rank_improvement_5d=-150))
    assert "rs_deterioration" in out["risk_flags"]
    assert out["risk_gate_action"] == "DOWNGRADE_ONE_LEVEL"


def test_attach_does_not_mutate_input():
    c = _candidate()
    out = attach_deterministic_signals([c])
    assert "deterministic_signals" not in c
    assert out[0]["deterministic_signals"]["risk_gate_action"] == "PASS"
