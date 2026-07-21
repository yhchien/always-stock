"""Phase 2 pipeline_v2 整合測試（in-memory DB + 手造候選池，驗證端到端行為）。"""
from app.models import SecurityClassification
from app.signals.phase2 import pipeline_v2
from app.signals.phase2 import regime_gate as rg


def _seed_classification(db, stock_id, primary_sector, sub_sector=None, confidence="HIGH"):
    db.add(SecurityClassification(
        stock_id=stock_id,
        asset_type="COMMON_STOCK",
        source_industry="其他",
        primary_sector=primary_sector,
        sub_sector=sub_sector,
        is_financial=False,
        classification_confidence=confidence,
        review_required=(confidence == "LOW"),
    ))


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
        "rs_rank_improvement_5d": 0,
        "distance_to_20d_high": -2.0,
        "atr_pct_14d": 5.0,
        "is_tracked": False,
        "failed_follow_through": False,
        "hit_count": 0,
        "total_institution_flow_3d": 100.0,
    }
    base.update(overrides)
    return base


def test_hanxiang_like_candidate_reaches_independent_leader_not_deleted(db):
    """漢翔雛型端到端：primary_sector 樣本數=1 → sector context UNUSABLE，
    但個股夠強 → INDEPENDENT_LEADER，且能通過 BULL_TREND regime gate。"""
    _seed_classification(db, "2634", "AEROSPACE_DEFENSE", "航空器製造")
    for i in range(5):
        _seed_classification(db, f"SEMI{i}", "SEMICONDUCTOR", "晶圓代工")

    candidates = [
        _candidate(
            "2634", momentum_score=80.0, rs_market_percentile_20d=99.0,
            consecutive_buy_days_3d=2, volume_5d_to_60d_ratio=2.0,
        ),
    ] + [_candidate(f"SEMI{i}", rs_market_percentile_20d=float(i * 10)) for i in range(5)]

    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)

    trace = out["explain_traces"]["2634"]
    assert trace["sector_context"]["sector_context_quality"] == "UNUSABLE"
    assert trace["role"]["type"] == "INDEPENDENT_LEADER"
    survivor_ids = {c["stock_id"] for c in out["survivors"]}
    assert "2634" in survivor_ids


def test_no_output_day_flagged_when_all_excluded(db):
    """funnel metrics 要能在 0 檔存活時明確標記，而不是靜默回空清單。"""
    _seed_classification(db, "A", "WEAK_SECTOR")
    candidates = [_candidate("A", momentum_score=20.0)]  # base 不合格
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)
    assert out["survivors"] == []
    assert out["funnel_metrics"]["no_output_day"] is True
    assert out["explain_traces"]["A"]["final_stage"] == "momentum_eligibility"


def test_tracked_stock_uses_tracking_state_not_new_role_selection(db):
    """台化雛型：is_tracked=True → 走 tracking_state（HEALTHY_PULLBACK），
    不重新參加 role 選秀（role 留 None）。"""
    _seed_classification(db, "1326", "PETROCHEMICAL", "化學纖維原料")
    candidates = [
        _candidate(
            "1326", is_tracked=True, momentum_score=68.0,
            distance_to_20d_high=-9.0, atr_pct_14d=5.0,
            rs_rank_improvement_5d=-7, max_negative_return_pct=-5.0,
            momentum_phase="trending",
        )
    ]
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)
    trace = out["explain_traces"]["1326"]
    assert trace["tracking_state"] == "HEALTHY_PULLBACK"
    assert trace["role"]["type"] == "HEALTHY_PULLBACK"  # role 欄位 fallback 顯示 tracking_state
