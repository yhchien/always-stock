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


def test_distribution_is_soft_signal_not_hard_exclusion():
    """2026-07-21 修正（7/20 真實資料 replay 後決定）：distribution 不再 hard
    exclusion（會在大盤系統性下跌日誤殺長上影的強勢股，見台化/台虹/長榮/萬海/
    台塑化案例），改成只降 conviction 一級。"""
    c = _candidate("A", soft_hints=["distribution"], role=rg.roles_mod.ROLE_SECTOR_FOLLOWER,
                    rs_market_percentile_20d=60.0)
    assert rg.is_true_hard_exclusion(c) is None
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_BULL_TREND)
    assert len(survivors) == 1
    without_distribution = _candidate("B", role=rg.roles_mod.ROLE_SECTOR_FOLLOWER,
                                       rs_market_percentile_20d=60.0)
    baseline = rg.apply_regime_gate_v2([without_distribution], rg.REGIME_BULL_TREND)[0]
    assert survivors[0]["conviction"] == rg._CONVICTION_DOWNGRADE[baseline["conviction"]]


def test_risk_off_survives_via_healthy_tracking_state_without_role():
    """漢翔/星宇雛型：已追蹤股在 RISK_OFF 沒有 role（分類權讓給 tracking_state），
    但 tracking_state=HEALTHY_PULLBACK 時應視為 formal leader 的替代存活條件。"""
    c = _candidate("2634", role=None, hit_count=0, rs_market_percentile_20d=99.0,
                    tracking_state=rg.tracking_mod.TRACKING_HEALTHY_PULLBACK)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert len(survivors) == 1
    assert survivors[0]["stock_id"] == "2634"


def test_risk_off_still_requires_market_rs_even_with_healthy_tracking():
    c = _candidate("A", role=None, rs_market_percentile_20d=50.0,
                    tracking_state=rg.tracking_mod.TRACKING_HEALTHY_PULLBACK)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert survivors == []


def test_risk_off_rejects_deteriorating_tracking_state():
    c = _candidate("A", role=None, rs_market_percentile_20d=99.0,
                    tracking_state=rg.tracking_mod.TRACKING_DETERIORATING)
    survivors = rg.apply_regime_gate_v2([c], rg.REGIME_RISK_OFF)
    assert survivors == []


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
