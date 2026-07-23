"""Phase 2.5 watch_quality.py 單元測試。"""
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import momentum_freshness as freshness
from app.signals.phase2 import roles as roles_mod
from app.signals.phase2 import sector_cluster as cluster_mod
from app.signals.phase2 import tracking_state as tracking_mod
from app.signals.phase2 import watch_quality as quality


def _candidate(**overrides):
    base = {
        "stock_id": "TEST",
        "role": roles_mod.ROLE_SECTOR_LEADER,
        "tracking_state": None,
        "momentum_score": 80.0,
        "rs_market_percentile_20d": 92.0,
        "consecutive_buy_days_3d": 3,
        "volume_5d_to_60d_ratio": 1.6,
        "entry_state": entry_state_mod.ENTRY_NEAR_HIGH,
        "risk_warnings": [],
        "deterministic_signals": {
            "institution_flow_momentum": "accelerating",
            "sector_rotation_status": "inflow",
        },
    }
    base.update(overrides)
    return base


def _fresh(state):
    return {"momentum_freshness": state, "momentum_freshness_evidence": {}}


def test_formal_leader_with_full_evidence_and_fresh_strong_is_ready():
    c = _candidate()
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STRONG), sector_ctx={"peer_rs_percentile_20d": 85.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_READY
    assert result["watch_quality_score"] > 50


def test_role_alone_does_not_guarantee_ready_without_evidence():
    """§19：role 只是證據之一，formal leader 若缺乏其餘證據仍不該自動 READY。"""
    c = _candidate(
        momentum_score=55.0,
        rs_market_percentile_20d=60.0,
        consecutive_buy_days_3d=0,
        volume_5d_to_60d_ratio=1.0,
        entry_state=entry_state_mod.ENTRY_DEEP_PULLBACK,
        deterministic_signals={"institution_flow_momentum": "neutral", "sector_rotation_status": "neutral"},
    )
    result = quality.compute_watch_quality(c, _fresh(freshness.STALE), sector_ctx=None, cluster_state=None)
    assert result["watch_quality_state"] != quality.WATCH_QUALITY_READY


def test_emerging_momentum_defaults_to_reserve_without_strong_confirmation():
    c = _candidate(
        role=roles_mod.ROLE_EMERGING_MOMENTUM,
        momentum_score=48.0,
        rs_market_percentile_20d=55.0,
        consecutive_buy_days_3d=0,
        volume_5d_to_60d_ratio=1.0,
        deterministic_signals={"institution_flow_momentum": "neutral", "sector_rotation_status": "neutral"},
    )
    result = quality.compute_watch_quality(c, _fresh(freshness.FRESH_STABLE), sector_ctx=None, cluster_state=None)
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_RESERVE
    assert "EMERGING_INSUFFICIENT_CONFIRMATION" in result["quality_reasons"]


def test_emerging_momentum_upgrades_to_setup_with_strong_confirmation():
    c = _candidate(role=roles_mod.ROLE_EMERGING_MOMENTUM)
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STABLE),
        sector_ctx={"peer_rs_percentile_20d": 85.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] in (quality.WATCH_QUALITY_SETUP, quality.WATCH_QUALITY_READY)
    assert "EMERGING_HIGH_QUALITY_CONFIRMED" in result["quality_reasons"]


def test_unclassified_momentum_defaults_to_reserve():
    c = _candidate(
        role=roles_mod.ROLE_UNCLASSIFIED_MOMENTUM,
        momentum_score=52.0,
        rs_market_percentile_20d=45.0,
        consecutive_buy_days_3d=0,
        volume_5d_to_60d_ratio=1.0,
        deterministic_signals={"institution_flow_momentum": "neutral", "sector_rotation_status": "neutral"},
    )
    result = quality.compute_watch_quality(c, _fresh(freshness.FRESH_STABLE), sector_ctx=None, cluster_state=None)
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_RESERVE


def test_extended_3d_warning_does_not_auto_downgrade_from_ready():
    """§24：EXTENDED != FAILED，不因 EXTENDED_3D 自動降級。"""
    c = _candidate(risk_warnings=["EXTENDED_3D"])
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STRONG),
        sector_ctx={"peer_rs_percentile_20d": 90.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_READY
    assert "EXTENDED_ENTRY_RISK" in result["quality_reasons"]


def test_severe_risk_combo_caps_at_setup_not_ready():
    c = _candidate(risk_warnings=["INSTITUTION_REVERSAL_WARNING", "EXTENDED_PROFIT_TAKING_WARNING"])
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STRONG),
        sector_ctx={"peer_rs_percentile_20d": 90.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] != quality.WATCH_QUALITY_READY


def test_tracked_deteriorating_forces_reserve():
    c = _candidate(role=None, tracking_state=tracking_mod.TRACKING_DETERIORATING)
    result = quality.compute_watch_quality(c, _fresh(freshness.STALE), sector_ctx=None, cluster_state=None)
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_RESERVE
    assert "TRACKED_DETERIORATING" in result["quality_reasons"]


def test_tracked_healthy_pullback_maps_to_setup_when_evidence_sufficient():
    c = _candidate(role=None, tracking_state=tracking_mod.TRACKING_HEALTHY_PULLBACK)
    result = quality.compute_watch_quality(
        c, _fresh(freshness.HEALTHY_PULLBACK),
        sector_ctx={"peer_rs_percentile_20d": 80.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_SETUP


def test_tracked_active_trend_can_reach_ready():
    c = _candidate(role=None, tracking_state=tracking_mod.TRACKING_ACTIVE_TREND)
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STRONG),
        sector_ctx={"peer_rs_percentile_20d": 90.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] == quality.WATCH_QUALITY_READY


def test_sector_context_unusable_falls_back_to_independent_market_rs():
    c = _candidate(rs_market_percentile_20d=95.0)
    result = quality.compute_watch_quality(
        c, _fresh(freshness.FRESH_STRONG), sector_ctx=None, cluster_state=None,
    )
    assert result["quality_evidence"]["RELATIVE_STRENGTH"] is True


def test_deteriorating_freshness_never_reaches_ready():
    c = _candidate()
    result = quality.compute_watch_quality(
        c, _fresh(freshness.DETERIORATING),
        sector_ctx={"peer_rs_percentile_20d": 90.0, "sector_context_quality": "HIGH"},
        cluster_state=cluster_mod.CLUSTER_ACTIVE,
    )
    assert result["watch_quality_state"] != quality.WATCH_QUALITY_READY
    assert "DETERIORATING_MOMENTUM" in result["quality_reasons"]


def test_all_states_are_valid_enum_values():
    for state in (quality.WATCH_QUALITY_READY, quality.WATCH_QUALITY_SETUP, quality.WATCH_QUALITY_RESERVE):
        assert state in quality.ALL_WATCH_QUALITY_STATES
