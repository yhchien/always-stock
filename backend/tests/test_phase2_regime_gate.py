"""Phase 2 §O/§P/§Q：regime gate 測試（含統一案例：hit_count<3 不可單獨造成 REMOVE）。"""
from app.signals.phase2 import regime_gate as rg


def _candidate(stock_id, **overrides):
    base = {
        "stock_id": stock_id,
        "role": rg.roles_mod.ROLE_SECTOR_LEADER,
        "rs_market_percentile_20d": 91.5,
        "hit_count": 2,
        "failed_follow_through": False,
        "soft_hints": [],
        "entry_state": None,
        "risk_gate_action": None,
    }
    base.update(overrides)
    return base


def test_hit_count_below_3_does_not_remove_leader_in_risk_off():
    """統一雛型：RS 91.5、formal LEADER、hit_count=2 < legacy 3 門檻，
    Phase 2 不可再因 hit_count 單獨剔除。"""
    c = _candidate("2912", hit_count=2)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert len(survivors) == 1
    assert survivors[0]["stock_id"] == "2912"


def test_risk_off_still_requires_formal_leader_role():
    c = _candidate("A", role=rg.roles_mod.ROLE_UNCLASSIFIED_MOMENTUM, hit_count=5)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert survivors == []


def test_risk_off_still_requires_high_market_rs():
    c = _candidate("A", rs_market_percentile_20d=50.0)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert survivors == []


def test_true_hard_exclusion_distribution_applies_regardless_of_regime():
    c = _candidate("A", soft_hints=["distribution"])
    assert rg.is_true_hard_exclusion(c) == "distribution"
    assert rg.apply_regime_gate_v2([c], rg.REGIME_BULL_TREND) == []


def test_structure_damaged_entry_state_is_hard_excluded():
    c = _candidate("A", entry_state="STRUCTURE_DAMAGED")
    assert rg.apply_regime_gate_v2([c], rg.REGIME_BULL_TREND) == []


def test_conviction_reflects_hit_count_without_gating():
    """hit_count 只影響 conviction 高低，不影響是否存活。"""
    low_hit = _candidate("A", hit_count=1, role=rg.roles_mod.ROLE_UNCLASSIFIED_MOMENTUM,
                          rs_market_percentile_20d=50.0)
    high_hit = _candidate("B", hit_count=5, role=rg.roles_mod.ROLE_UNCLASSIFIED_MOMENTUM,
                           rs_market_percentile_20d=50.0)
    survivors = rg.apply_regime_gate_v2([low_hit, high_hit], rg.REGIME_BULL_TREND)
    convictions = {c["stock_id"]: c["conviction"] for c in survivors}
    assert convictions["A"] == rg.CONVICTION_LOW
    assert convictions["B"] == rg.CONVICTION_MEDIUM  # hit_count>=2 但非 leader


def test_bull_trend_allows_non_leader_roles_through():
    c = _candidate("A", role=rg.roles_mod.ROLE_SECTOR_FOLLOWER, rs_market_percentile_20d=60.0)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_BULL_TREND)
    assert len(survivors) == 1
