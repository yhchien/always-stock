"""Phase 2.5：WATCH_QUALITY_MODE (off/shadow/production) 接線行為測試。

`pipeline_v2.WATCH_QUALITY_MODE` 是 import 期讀入的 module-level 常數（沿用專案既有
`momentum._AVAILABLE_WEIGHT_NORMALIZATION_ENABLED` 的 monkeypatch 慣例），測試直接
monkeypatch 該常數觸發三種模式，不依賴環境變數 reload。
"""
from app.models import SecurityClassification
from app.signals.phase2 import pipeline_v2
from app.signals.phase2 import regime_gate as rg
from app.signals.phase2 import watch_quality as quality


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
    """一檔證據充分的強勢候選（應該落在 READY）。"""
    base = {
        "stock_id": stock_id,
        "momentum_score": 85.0,
        "rs_market_percentile_20d": 95.0,
        "momentum_phase": "trending",
        "soft_hints": [],
        "consecutive_buy_days_3d": 3,
        "inst_buy_to_turnover_percentile_2d": 90.0,
        "volume_5d_to_60d_ratio": 1.6,
        "rs_rank_improvement_5d": 60,
        "distance_to_20d_high": -1.0,
        "atr_pct_14d": 5.0,
        "is_tracked": False,
        "failed_follow_through": False,
        "hit_count": 0,
        "total_institution_flow_3d": 5_000_000.0,
        "price_change_1d": 2.0,
        "high_1d": 105.0,
        "low_1d": 101.0,
        "close_1d": 104.5,
        "volume_1d_to_5d_ratio": 1.5,
        "deterministic_signals": {
            "institution_flow_momentum": "accelerating",
            "sector_rotation_status": "inflow",
        },
    }
    base.update(overrides)
    return base


def _weak_candidate(stock_id, **overrides):
    """一檔僅剛好通過 base eligibility、但幾乎無額外證據的候選（應該落在 RESERVE）。"""
    base = {
        "stock_id": stock_id,
        "momentum_score": 51.0,
        "rs_market_percentile_20d": 41.0,
        "momentum_phase": "emerging",
        "soft_hints": [],
        "consecutive_buy_days_3d": 0,
        "inst_buy_to_turnover_percentile_2d": None,
        "volume_5d_to_60d_ratio": 1.0,
        "rs_rank_improvement_5d": 0,
        "distance_to_20d_high": -15.0,
        "atr_pct_14d": 5.0,
        "is_tracked": False,
        "failed_follow_through": False,
        "hit_count": 0,
        "total_institution_flow_3d": 0.0,
        "price_change_1d": 0.0,
        "high_1d": 100.0,
        "low_1d": 98.0,
        "close_1d": 98.5,
        "volume_1d_to_5d_ratio": 1.0,
        "deterministic_signals": {
            "institution_flow_momentum": "neutral",
            "sector_rotation_status": "neutral",
        },
    }
    base.update(overrides)
    return base


def _setup_candidates(db):
    _seed_classification(db, "STRONG", "SEMICONDUCTOR", "IC設計")
    _seed_classification(db, "WEAK", "SEMICONDUCTOR", "IC設計")
    for i in range(5):
        _seed_classification(db, f"PEER{i}", "SEMICONDUCTOR", "IC設計")
    candidates = [_candidate("STRONG"), _weak_candidate("WEAK")] + [
        _candidate(f"PEER{i}", rs_market_percentile_20d=50.0 + i) for i in range(5)
    ]
    return candidates


def test_off_mode_computes_nothing(db, monkeypatch):
    monkeypatch.setattr(pipeline_v2, "WATCH_QUALITY_MODE", "off")
    candidates = _setup_candidates(db)
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)

    assert out["watch_quality_mode"] == "off"
    strong = next(c for c in out["survivors"] if c["stock_id"] == "STRONG")
    assert "momentum_freshness" not in strong
    assert "watch_quality_state" not in strong
    # off 模式下 llm_eligible 等於全部 survivors（行為與加這層之前一致）
    assert {c["stock_id"] for c in out["llm_eligible"]} == {c["stock_id"] for c in out["survivors"]}


def test_shadow_mode_computes_but_does_not_filter(db, monkeypatch):
    monkeypatch.setattr(pipeline_v2, "WATCH_QUALITY_MODE", "shadow")
    candidates = _setup_candidates(db)
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)

    assert out["watch_quality_mode"] == "shadow"
    survivor_ids = {c["stock_id"] for c in out["survivors"]}
    weak_survived = "WEAK" in survivor_ids
    for c in out["survivors"]:
        assert c.get("watch_quality_state") in quality.ALL_WATCH_QUALITY_STATES
    # shadow：即使某些候選是 RESERVE，仍全部視為 llm_eligible（不過濾）
    assert {c["stock_id"] for c in out["llm_eligible"]} == survivor_ids
    if weak_survived:
        weak = next(c for c in out["survivors"] if c["stock_id"] == "WEAK")
        assert weak["watch_quality_state"] == quality.WATCH_QUALITY_RESERVE


def test_production_mode_filters_reserve_out_of_llm_eligible(db, monkeypatch):
    monkeypatch.setattr(pipeline_v2, "WATCH_QUALITY_MODE", "production")
    candidates = _setup_candidates(db)
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)

    assert out["watch_quality_mode"] == "production"
    survivor_ids = {c["stock_id"] for c in out["survivors"]}
    llm_eligible_ids = {c["stock_id"] for c in out["llm_eligible"]}

    if "WEAK" in survivor_ids:
        weak = next(c for c in out["survivors"] if c["stock_id"] == "WEAK")
        if weak["watch_quality_state"] == quality.WATCH_QUALITY_RESERVE:
            assert "WEAK" not in llm_eligible_ids
            # RESERVE 不是排除：仍在 survivors，仍有 explain trace，且 hard_exclusion 未被觸發
            trace = out["explain_traces"]["WEAK"]
            assert trace["hard_exclusion_result"]["excluded"] is False
            assert trace["llm_eligible"] is False
            assert trace["final_stage"] == "watch_quality"

    # llm_eligible 永遠是 survivors 的子集
    assert llm_eligible_ids <= survivor_ids


def test_funnel_metrics_includes_freshness_and_quality_counts(db, monkeypatch):
    monkeypatch.setattr(pipeline_v2, "WATCH_QUALITY_MODE", "production")
    candidates = _setup_candidates(db)
    out = pipeline_v2.run_phase2_pipeline(db, candidates, market_regime=rg.REGIME_BULL_TREND)

    funnel = out["funnel_metrics"]
    assert funnel["watch_quality_mode"] == "production"
    assert isinstance(funnel["freshness_counts"], dict)
    assert isinstance(funnel["watch_quality_counts"], dict)
    assert funnel["after_regime_count"] == len(out["survivors"])
    assert funnel["sent_to_llm_count"] == len(out["llm_eligible"])
