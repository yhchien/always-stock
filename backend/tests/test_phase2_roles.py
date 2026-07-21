"""Phase 2 §G~§M：role taxonomy 測試（含 spec §U regression case 雛型）。"""
from app.signals.phase2 import roles


def _candidate(stock_id, **overrides):
    base = {
        "stock_id": stock_id,
        "momentum_score": 60.0,
        "rs_market_percentile_20d": 60.0,
        "momentum_phase": "trending",
        "soft_hints": [],
        "consecutive_buy_days_3d": 0,
        "inst_buy_to_turnover_percentile_2d": None,
        "volume_5d_to_60d_ratio": None,
        "entry_state": None,
        "rs_rank_improvement_5d": 0,
    }
    base.update(overrides)
    return base


def _ctx(primary_sector, sub_sector=None, quality="HIGH", sector_strength=None, peer_rs=None):
    return {
        "primary_sector": primary_sector,
        "sub_sector": sub_sector,
        "sector_context_quality": quality,
        "sector_strength_percentile_20d": sector_strength,
        "peer_rs_percentile_20d": peer_rs,
    }


# ---------- base eligibility ----------


def test_base_eligibility_rejects_low_score():
    assert roles.is_base_momentum_eligible(_candidate("A", momentum_score=30.0)) is False


def test_base_eligibility_rejects_weakening_phase():
    assert roles.is_base_momentum_eligible(_candidate("A", momentum_phase="weakening")) is False


def test_base_eligibility_rejects_distribution_hint():
    assert roles.is_base_momentum_eligible(_candidate("A", soft_hints=["distribution"])) is False


def test_base_ineligible_gets_role_none_not_deleted():
    """§G：base 不合格不代表消失——呼叫端仍拿得到一筆 result，role=None。"""
    candidates = [_candidate("A", momentum_score=10.0)]
    out = roles.classify_roles(candidates, {"A": _ctx("X")})
    assert out["A"]["base_eligible"] is False
    assert out["A"]["role"] is None


# ---------- SECTOR_LEADER / CO_LEADER（evidence count，非 6/6 AND） ----------


def test_full_evidence_is_sector_leader():
    c = _candidate(
        "A", momentum_score=80.0, rs_market_percentile_20d=95.0,
        consecutive_buy_days_3d=3, volume_5d_to_60d_ratio=1.5,
    )
    ctx = _ctx("SEC", sector_strength=80.0, peer_rs=90.0)
    out = roles.classify_roles([c], {"A": ctx})
    assert out["A"]["role"] == roles.ROLE_SECTOR_LEADER
    assert out["A"]["evidence_count"] == 6


def test_four_evidence_is_co_leader():
    c = _candidate(
        "A", momentum_score=75.0, rs_market_percentile_20d=95.0,
        consecutive_buy_days_3d=3, volume_5d_to_60d_ratio=None,  # 量能證據缺席
    )
    ctx = _ctx("SEC", sector_strength=80.0, peer_rs=None)  # peer_rs 證據也缺席 → 剩 4
    out = roles.classify_roles([c], {"A": ctx})
    assert out["A"]["evidence_count"] == 4
    assert out["A"]["role"] == roles.ROLE_CO_LEADER


# ---------- INDEPENDENT_LEADER（解漢翔：sector 樣本不足/UNUSABLE） ----------


def test_independent_leader_when_sector_unusable():
    """漢翔雛型：sector_context_quality=UNUSABLE（樣本數=1），但個股 market RS 極高、
    momentum_score 高、且有法人/量能/價格結構等獨立確認 → INDEPENDENT_LEADER，
    不因「沒有 sector 可用」被判死。"""
    c = _candidate(
        "2634", momentum_score=80.0, rs_market_percentile_20d=99.0,
        consecutive_buy_days_3d=2, volume_5d_to_60d_ratio=2.0,
        entry_state="NORMAL_PULLBACK",
    )
    ctx = _ctx("AEROSPACE_DEFENSE", quality="UNUSABLE", sector_strength=None, peer_rs=None)
    out = roles.classify_roles([c], {"2634": ctx})
    assert out["2634"]["role"] == roles.ROLE_INDEPENDENT_LEADER


def test_independent_leader_not_granted_without_enough_non_sector_evidence():
    """market RS 高分但只有一項獨立確認 → 門檻不足，不能濫發 INDEPENDENT_LEADER。"""
    c = _candidate("A", momentum_score=76.0, rs_market_percentile_20d=95.0)
    ctx = _ctx("X", quality="UNUSABLE")
    out = roles.classify_roles([c], {"A": ctx})
    assert out["A"]["role"] != roles.ROLE_INDEPENDENT_LEADER


# ---------- SECTOR_FOLLOWER（解航運：cluster ACTIVE 不需要 formal leader） ----------


def test_follower_opens_via_active_cluster_without_formal_leader():
    """航運雛型：sector 內沒有任何一檔湊到 formal leader evidence，
    但 sector_momentum_cluster=ACTIVE 時仍可打開 FOLLOWER 路徑。"""
    candidates = [
        _candidate("SHIP1", momentum_score=59.0, rs_market_percentile_20d=70.0),
        _candidate("SHIP2", momentum_score=61.0, rs_market_percentile_20d=72.0),
    ]
    ctx_by_id = {
        "SHIP1": _ctx("SHIPPING_CONTAINER", sector_strength=82.5, peer_rs=40.0),
        "SHIP2": _ctx("SHIPPING_CONTAINER", sector_strength=82.5, peer_rs=45.0),
    }
    clusters = {"SHIPPING_CONTAINER": {"cluster_state": "ACTIVE"}}
    out = roles.classify_roles(candidates, ctx_by_id, clusters)
    assert out["SHIP1"]["role"] == roles.ROLE_SECTOR_FOLLOWER
    assert out["SHIP2"]["role"] == roles.ROLE_SECTOR_FOLLOWER
    assert out["SHIP1"]["follower_eligible"] is True


def test_follower_blocked_when_no_leader_and_cluster_not_active():
    candidates = [_candidate("A", momentum_score=55.0)]
    ctx_by_id = {"A": _ctx("WEAK_SEC", sector_strength=20.0, peer_rs=30.0)}
    clusters = {"WEAK_SEC": {"cluster_state": "NEUTRAL"}}
    out = roles.classify_roles(candidates, ctx_by_id, clusters)
    assert out["A"]["role"] != roles.ROLE_SECTOR_FOLLOWER
    assert out["A"]["follower_eligible"] is False


# ---------- ROTATION_LAGGARD（弱個股在強產業，RS 改善中） ----------


def test_rotation_laggard_strong_sector_lagging_stock_improving():
    c = _candidate("A", momentum_score=55.0, rs_rank_improvement_5d=20)
    ctx = _ctx("STRONG_SEC", sector_strength=75.0, peer_rs=30.0)
    clusters = {"STRONG_SEC": {"cluster_state": "ACTIVE"}}
    out = roles.classify_roles([c], {"A": ctx}, clusters)
    assert out["A"]["role"] == roles.ROLE_ROTATION_LAGGARD


# ---------- EMERGING_MOMENTUM ----------


def test_emerging_momentum_when_rs_improving_fast_but_score_not_extreme():
    c = _candidate(
        "A", momentum_score=50.0, rs_rank_improvement_5d=50,
        rs_market_percentile_20d=45.0,
    )
    ctx = _ctx("X", quality="UNUSABLE")
    out = roles.classify_roles([c], {"A": ctx})
    assert out["A"]["role"] == roles.ROLE_EMERGING_MOMENTUM


# ---------- UNCLASSIFIED_MOMENTUM（base 合格但沒有明確角色，不等於死亡） ----------


def test_unclassified_when_base_eligible_but_no_role_matches():
    c = _candidate("A", momentum_score=52.0, rs_rank_improvement_5d=-5)
    ctx = _ctx("QUIET_SEC", sector_strength=40.0, peer_rs=40.0)
    clusters = {"QUIET_SEC": {"cluster_state": "NEUTRAL"}}
    out = roles.classify_roles([c], {"A": ctx}, clusters)
    assert out["A"]["role"] == roles.ROLE_UNCLASSIFIED_MOMENTUM
    assert out["A"]["base_eligible"] is True
