"""Phase 2.5 momentum_freshness.py 單元測試。"""
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import momentum_freshness as freshness


def _candidate(**overrides):
    base = {
        "price_change_1d": 0.5,
        "price_change_3d": 2.0,
        "rs_rank_improvement_5d": 50,
        "high_1d": 105.0,
        "low_1d": 100.0,
        "close_1d": 103.0,
        "volume_1d_to_5d_ratio": 1.0,
        "entry_state": entry_state_mod.ENTRY_NORMAL_PULLBACK,
        "momentum_score": 70.0,
        "deterministic_signals": {
            "institution_flow_momentum": "stable",
            "sector_rotation_status": "neutral",
        },
    }
    base.update(overrides)
    return base


def test_fresh_strong_requires_relative_strength_plus_confirmation_plus_strength_evidence():
    c = _candidate(
        price_change_1d=3.0,
        close_1d=104.5,
        high_1d=105.0,
        low_1d=100.0,
        volume_1d_to_5d_ratio=1.5,
        rs_rank_improvement_5d=80,
        deterministic_signals={"institution_flow_momentum": "accelerating", "sector_rotation_status": "inflow"},
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.2)
    assert result["momentum_freshness"] == freshness.FRESH_STRONG


def test_relative_return_uses_excess_not_absolute():
    """大盤跌 5%、個股跌 2% → 相對抗跌（超額報酬 +3%），不是轉弱。"""
    c = _candidate(
        price_change_1d=-2.0,
        close_1d=101.0,
        high_1d=103.0,
        low_1d=100.0,
        volume_1d_to_5d_ratio=0.8,
        rs_rank_improvement_5d=40,
        deterministic_signals={"institution_flow_momentum": "stable", "sector_rotation_status": "inflow"},
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=-5.0)
    assert result["excess_return_vs_market_1d"] == 3.0
    assert result["momentum_freshness"] != freshness.DETERIORATING


def test_absolute_positive_but_underperforms_market_is_relative_weak():
    """大盤 +2%、個股 -2% → 相對報酬 -4%，才是真正需要警戒。"""
    c = _candidate(price_change_1d=-2.0, close_1d=99.0, high_1d=102.0, low_1d=98.0)
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=2.0)
    assert result["excess_return_vs_market_1d"] == -4.0


def test_deteriorating_needs_at_least_two_independent_negative_evidences():
    c = _candidate(
        price_change_1d=-3.0,
        close_1d=100.5,
        high_1d=104.0,
        low_1d=100.0,  # close_location_value 很低 → close_weak
        rs_rank_improvement_5d=-60,  # rs_rank_deteriorating
        volume_1d_to_5d_ratio=1.0,
        deterministic_signals={"institution_flow_momentum": "stable", "sector_rotation_status": "neutral"},
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.0)
    assert result["momentum_freshness"] == freshness.DETERIORATING


def test_single_negative_evidence_does_not_trigger_deteriorating():
    """禁止單一固定門檻直接判死——只有 1 個負面證據時不該是 DETERIORATING。"""
    c = _candidate(rs_rank_improvement_5d=-60)  # 只有 rs_rank_deteriorating 成立
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.0)
    assert result["momentum_freshness"] != freshness.DETERIORATING


def test_healthy_pullback_when_pulling_back_but_relative_return_not_weak():
    c = _candidate(
        entry_state=entry_state_mod.ENTRY_NORMAL_PULLBACK,
        price_change_1d=-1.0,
        close_1d=101.0,
        high_1d=103.0,
        low_1d=100.0,
        rs_rank_improvement_5d=10,
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=-1.0)
    assert result["momentum_freshness"] == freshness.HEALTHY_PULLBACK


def test_stale_when_historically_strong_but_no_fresh_confirmation():
    c = _candidate(
        momentum_score=80.0,
        price_change_1d=0.0,
        close_1d=102.0,
        high_1d=105.0,
        low_1d=100.0,
        rs_rank_improvement_5d=0,
        volume_1d_to_5d_ratio=1.0,
        entry_state=None,
        deterministic_signals={"institution_flow_momentum": "neutral", "sector_rotation_status": "neutral"},
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.0)
    assert result["momentum_freshness"] == freshness.STALE


def test_missing_taiex_return_disables_relative_return_evidence_not_crash():
    c = _candidate()
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=None)
    assert result["excess_return_vs_market_1d"] is None
    assert result["momentum_freshness"] in freshness.ALL_FRESHNESS_STATES


def test_close_location_value_none_when_high_equals_low():
    c = _candidate(high_1d=100.0, low_1d=100.0, close_1d=100.0)
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.0)
    assert result["close_location_value"] is None


def test_entry_reaccelerating_counts_as_fresh_strong_confirmation_evidence():
    c = _candidate(
        entry_state=entry_state_mod.ENTRY_REACCELERATING,
        price_change_1d=2.5,
        close_1d=104.0,
        high_1d=105.0,
        low_1d=100.0,
        volume_1d_to_5d_ratio=1.4,
        rs_rank_improvement_5d=40,
    )
    result = freshness.compute_momentum_freshness(c, taiex_return_1d_pct=0.0)
    assert result["momentum_freshness_evidence"]["entry_reaccelerating"] is True
    assert result["momentum_freshness"] == freshness.FRESH_STRONG


def test_freshness_rank_orders_strong_before_deteriorating():
    assert freshness.freshness_rank(freshness.FRESH_STRONG) < freshness.freshness_rank(freshness.STALE)
    assert freshness.freshness_rank(freshness.STALE) < freshness.freshness_rank(freshness.DETERIORATING)
    assert freshness.freshness_rank("UNKNOWN_STATE") > freshness.freshness_rank(freshness.DETERIORATING)
