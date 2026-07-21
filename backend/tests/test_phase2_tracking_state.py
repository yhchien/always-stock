"""Phase 2 §N：New vs Tracked 分流測試（含台化案例雛型）。"""
from app.signals.phase2 import tracking_state as ts


def _tracked(**overrides):
    base = {
        "is_tracked": True,
        "failed_follow_through": False,
        "momentum_phase": "trending",
        "rs_rank_improvement_5d": 0,
        "entry_state": None,
        "max_positive_return_pct": 20.0,
        "max_negative_return_pct": -5.0,
    }
    base.update(overrides)
    return base


def test_new_stock_returns_none():
    assert ts.compute_tracking_state({"is_tracked": False}) is None
    assert ts.compute_tracking_state({}) is None


def test_failed_follow_through_is_invalidated():
    c = _tracked(failed_follow_through=True)
    assert ts.compute_tracking_state(c) == ts.TRACKING_INVALIDATED


def test_weakening_phase_is_deteriorating():
    c = _tracked(momentum_phase="weakening")
    assert ts.compute_tracking_state(c) == ts.TRACKING_DETERIORATING


def test_structure_damaged_entry_is_deteriorating():
    c = _tracked(entry_state="STRUCTURE_DAMAGED")
    assert ts.compute_tracking_state(c) == ts.TRACKING_DETERIORATING


def test_reaccelerating_entry_state_carries_through():
    c = _tracked(entry_state="REACCELERATING")
    assert ts.compute_tracking_state(c) == ts.TRACKING_REACCELERATING


def test_taihua_like_case_normal_pullback_with_moderate_drawdown_is_healthy():
    """台化雛型：7/20 距高點回落、RS 排名略退，但歷史最大回撤沒有很深（-5%），
    momentum_phase 未到 weakening → 應判 HEALTHY_PULLBACK，不是每天重新選秀。"""
    c = _tracked(
        momentum_phase="trending",
        rs_rank_improvement_5d=-7,
        entry_state="NORMAL_PULLBACK",
        max_negative_return_pct=-5.0,
    )
    assert ts.compute_tracking_state(c) == ts.TRACKING_HEALTHY_PULLBACK


def test_deep_pullback_with_severe_existing_drawdown_is_deteriorating():
    c = _tracked(entry_state="DEEP_PULLBACK", max_negative_return_pct=-20.0)
    assert ts.compute_tracking_state(c) == ts.TRACKING_DETERIORATING


def test_severe_rs_collapse_during_pullback_is_deteriorating_even_without_weakening_label():
    c = _tracked(
        momentum_phase="trending",  # 尚未被標 weakening
        entry_state="DEEP_PULLBACK",
        rs_rank_improvement_5d=-80,
        max_negative_return_pct=-5.0,  # 回撤本身不深，但 RS 排名暴跌是更早的警訊
    )
    assert ts.compute_tracking_state(c) == ts.TRACKING_DETERIORATING


def test_near_high_or_trending_without_pullback_is_active_trend():
    c = _tracked(entry_state="NEAR_HIGH")
    assert ts.compute_tracking_state(c) == ts.TRACKING_ACTIVE_TREND
