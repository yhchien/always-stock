"""Phase 2 §K：sector momentum cluster 測試（解航運全滅案例）。"""
from app.signals.phase2 import sector_cluster as scl


def _ctx(sector, quality="HIGH", strength=70.0):
    return {"primary_sector": sector, "sector_context_quality": quality, "sector_strength_percentile_20d": strength}


def test_shipping_sector_without_formal_leader_can_be_active():
    """航運雛型：8 檔 score 59~68（無一達 formal LEADER 70 分），法人多數正流入
    → cluster 應為 ACTIVE，讓 FOLLOWER 路徑打得開。"""
    candidates = []
    ctx_by_id = {}
    scores = [59, 61, 63, 65, 62, 68, 60, 64]
    for i, score in enumerate(scores):
        sid = f"SHIP{i}"
        candidates.append({"stock_id": sid, "momentum_score": float(score), "total_institution_flow_3d": 1000.0})
        ctx_by_id[sid] = _ctx("SHIPPING_CONTAINER", strength=82.5)

    clusters = scl.compute_sector_clusters(candidates, ctx_by_id)
    assert clusters["SHIPPING_CONTAINER"]["cluster_state"] == scl.CLUSTER_ACTIVE
    assert clusters["SHIPPING_CONTAINER"]["strong_stock_count"] >= 3


def test_unusable_sector_context_yields_unavailable_cluster():
    candidates = [{"stock_id": "X1", "momentum_score": 80.0, "total_institution_flow_3d": 100.0}]
    ctx_by_id = {"X1": _ctx("AEROSPACE_DEFENSE", quality="UNUSABLE", strength=None)}
    clusters = scl.compute_sector_clusters(candidates, ctx_by_id)
    assert clusters["AEROSPACE_DEFENSE"]["cluster_state"] == scl.CLUSTER_UNAVAILABLE


def test_weak_sector_with_negative_flow_is_failed():
    candidates = [
        {"stock_id": f"W{i}", "momentum_score": 30.0, "total_institution_flow_3d": -500.0}
        for i in range(5)
    ]
    ctx_by_id = {f"W{i}": _ctx("WEAK_SECTOR", strength=10.0) for i in range(5)}
    clusters = scl.compute_sector_clusters(candidates, ctx_by_id)
    assert clusters["WEAK_SECTOR"]["cluster_state"] == scl.CLUSTER_FAILED


def test_get_cluster_state_missing_sector_returns_unavailable():
    assert scl.get_cluster_state(None, {}) == scl.CLUSTER_UNAVAILABLE
    assert scl.get_cluster_state("NOPE", {}) == scl.CLUSTER_UNAVAILABLE
