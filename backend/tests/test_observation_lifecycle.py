from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import time
import tracemalloc

import pytest

from app.models import (
    DailyPrice,
    SignalObservation,
    SignalObservationArchive,
    SignalObservationReview,
    SignalWatchCompletedArchive,
    SignalWatchHit,
    SignalWatchStoppedObservation,
)
from app.signals import archive as archive_module
from app.signals import observation_lifecycle as lifecycle


DAY_0 = date(2026, 7, 20)
DAY_1 = date(2026, 7, 21)
DAY_2 = date(2026, 7, 22)


def _observation(
    db,
    stock: str = "2330",
    *,
    started: date = DAY_0,
    asset_type: str = "COMMON_STOCK",
    baseline_quality: str = "P3_COMPLETE",
) -> SignalObservation:
    row = SignalObservation(
        stock_id=stock,
        stock_name=f"Stock-{stock}",
        asset_type=asset_type,
        episode_id=f"episode-{stock}-{started}",
        status="OBSERVING",
        started_signal_date=started,
        baseline_quality=baseline_quality,
        initial_snapshot_json={
            "recommendation_date": started.isoformat(),
            "recommendation_thesis": "原始 thesis",
            "relative_advantage": "相對優勢",
            "instrument_validation": "VERIFIED",
            "theme_validation": "VERIFIED",
        },
        consecutive_caution_count=0,
    )
    db.add(row)
    db.commit()
    return row


def _healthy_evidence(stock: str = "2330"):
    return {
        "stock": stock,
        "tracking_state": "ACTIVE_TREND",
        "momentum_freshness": "FRESH_STABLE",
        "momentum_phase": "trending",
        "watch_quality_state": "READY",
        "quality_evidence": {
            "PARTICIPATION": True,
            "INSTITUTION_CONFIRMATION": True,
        },
        "deterministic_signals": {
            "institution_flow_momentum": "stable",
            "chip_trend": "accumulating",
            "sector_rotation_status": "inflow",
        },
        "hard_exclusion": {"excluded": False, "reason": None},
        "persistence_warning": {"warning": False},
        "market_regime": "BULL_TREND",
        "data_quality": {
            "price_available": True,
            "baseline_quality": "P3_COMPLETE",
        },
    }


def _external(
    stock: str = "2330",
    *,
    assessment: str = "THESIS_INTACT",
    catalyst: str = "ACTIVE",
):
    return {
        "stock": stock,
        "assessment": assessment,
        "invalidation_reason_code": None,
        "instrument_validation": "VERIFIED",
        "theme_validation": "VERIFIED",
        "supply_chain_validation": "VERIFIED",
        "catalyst_status": catalyst,
        "thesis_dimensions": {
            "business_or_exposure": "INTACT",
            "theme": "INTACT",
            "catalyst": "INTACT",
        },
        "assessment_reason": "原始 thesis 仍成立。",
        "material_evidence": [],
    }


def _recommend(stock: str = "2330", *, asset_type: str = "COMMON_STOCK"):
    return {
        "stock": stock,
        "name": f"Stock-{stock}",
        "asset_type": asset_type,
        "decision": "RECOMMEND",
        "selection_status": "RECOMMEND",
        "selection_version": "p3_global_v1",
        "recommendation_rank": 1,
        "backend_priority_rank": 1,
        "recommendation_thesis": "原始 thesis",
        "relative_advantage": "相對優勢",
        "business_validation": "VERIFIED",
        "theme_validation": "VERIFIED",
        "theme_cluster": "AI",
        "catalyst_summary": "需求延續",
        "research_confidence": "HIGH",
        "phase2_role": "SECTOR_LEADER",
        "phase2_entry_state": "NEAR_HIGH",
        "phase2_momentum_freshness": "FRESH_STRONG",
        "phase2_watch_quality_state": "READY",
        "quality_evidence": {"PARTICIPATION": True},
        "signal_metrics": {"momentum_score_version": "v3_applicability_aware"},
    }


def _patch_evidence(monkeypatch, mapping):
    monkeypatch.setattr(
        lifecycle,
        "build_current_tracking_evidence",
        lambda db, **kwargs: mapping,
    )


def _runner_for(mapping, failures=None):
    return lambda payloads: (mapping, failures or [])


def test_daily_review_does_not_require_candidate_rehit(db, monkeypatch):
    observation = _observation(db)
    _patch_evidence(monkeypatch, {observation.id: _healthy_evidence()})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )

    assert result["tracking_summary"]["continue_count"] == 1
    assert db.get(SignalObservation, observation.id).status == "OBSERVING"


def test_daily_review_persists_momentum_score_from_evidence(db, monkeypatch):
    """2026-08-13：P4 每日複核算出的 momentum_score（跟 P3 同一套公式／同一份
    momentum_frame）要被存進 SignalObservationReview.momentum_score，讓動能分數
    折線圖在 P3 沒有再次選中該股的那幾天也有資料點可用。"""
    observation = _observation(db)
    evidence = _healthy_evidence()
    evidence["momentum_score"] = 72.5
    _patch_evidence(monkeypatch, {observation.id: evidence})

    lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )

    review = (
        db.query(SignalObservationReview)
        .filter_by(observation_id=observation.id, review_date=DAY_1)
        .one()
    )
    assert review.momentum_score == 72.5


def test_daily_review_reports_token_usage_from_assessment_diagnostics(db, monkeypatch):
    """2026-08-12（成本追蹤）：P4 每日複核也是 LLM stage 之一，tracking_summary
    要能反映實際的 token 用量（供 pipeline.py 併入整次 run 的總量）。"""
    observation = _observation(db)
    _patch_evidence(monkeypatch, {observation.id: _healthy_evidence()})
    external = _external()
    external["_llm_diagnostic"] = {
        "response_id": "resp-tracking-1",
        "usage": {"total_tokens": 250},
    }

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": external}),
        persist=True,
    )

    assert result["tracking_summary"]["token_usage"] == {
        "call_count": 1,
        "total_tokens": 250,
    }


def test_daily_review_token_usage_is_zero_when_diagnostics_absent(db, monkeypatch):
    observation = _observation(db)
    _patch_evidence(monkeypatch, {observation.id: _healthy_evidence()})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )

    assert result["tracking_summary"]["token_usage"] == {
        "call_count": 0,
        "total_tokens": 0,
    }


def test_same_day_new_recommendation_creates_observation_but_skips_review(db):
    sync = lifecycle.sync_recommendations(
        db,
        signal_date=DAY_1,
        watchlist=[_recommend()],
    )
    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for({}),
        persist=True,
    )

    assert sync["created"] == ["2330"]
    assert result["tracking_summary"]["excluded_same_day_count"] == 1
    assert db.query(SignalObservationReview).count() == 0


def test_only_formal_p3_recommendation_starts_observation(db):
    watch = _recommend()
    watch["decision"] = "WATCH"
    watch["selection_status"] = "WATCH"
    result = lifecycle.sync_recommendations(
        db,
        signal_date=DAY_1,
        watchlist=[watch],
    )
    assert result["created"] == []
    assert db.query(SignalObservation).count() == 0


@pytest.mark.parametrize(
    "reason",
    [
        "MANUAL_BLACKLIST",
        "FAILED_FOLLOW_THROUGH_CURRENT_EPISODE",
        "STRUCTURE_DAMAGED",
        "LIQUIDITY_FAILURE",
        # COMPOSITE_RISK_EXCLUDE 2026-08-18 起不再是 immediate stop——見
        # test_composite_risk_exclude_creates_pending_instead_of_immediate_stop
        # 與其他 test_composite_risk_* 案例。
        "REVERSAL_FAILURE",
    ],
)
def test_immediate_hard_invalidation_stops_without_sell(reason):
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": True, "reason": reason}
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=None,
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [reason]
    assert "SELL" not in decision.reason.upper()


# ---------------------------------------------------------------------------
# P4 Observation Lifecycle v2（2026-08-18）：假突破防誤殺
# COMPOSITE_RISK_EXCLUDE pending 狀態機
# ---------------------------------------------------------------------------


def _composite_evidence(stock: str = "2330", **overrides):
    evidence = _healthy_evidence(stock)
    evidence["hard_exclusion"] = {"excluded": True, "reason": "COMPOSITE_RISK_EXCLUDE"}
    evidence["risk_flags"] = ["distribution", "institution_flow_reversal"]
    evidence["open_1d"] = 274.0
    evidence["high_1d"] = 286.5
    evidence["low_1d"] = 253.0
    evidence["close_1d"] = 259.0
    evidence["price_change_1d"] = -4.95
    evidence["excess_return_vs_market"] = -3.0
    evidence["institution_flow"] = {"day_1": -943577922.0, "day_3": 1399536588.5}
    evidence["reversal_failure_check"] = {
        "triggered": False,
        "institution_reversal_ratio": 0.403,
        "excess_return_vs_market": -3.0,
    }
    evidence.update(overrides)
    return evidence


def test_composite_risk_exclude_creates_pending_instead_of_immediate_stop():
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=_composite_evidence(),
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"
    assert "COMPOSITE_RISK_PENDING" in decision.reason_codes
    assert decision.pending_stop_update == "SET"
    assert decision.pending_stop_reason == "COMPOSITE_RISK_EXCLUDE"
    snapshot = decision.pending_stop_trigger_snapshot
    assert snapshot["high"] == 286.5
    assert snapshot["low"] == 253.0
    assert snapshot["institution_reversal_ratio"] == 0.403
    assert snapshot["distribution"] is True


def test_risk_off_composite_risk_skips_pending_and_accelerates_stop():
    """2026-08-27 方法 A 延伸：同一份 composite risk 證據，regime 若是 RISK_OFF，
    不該走「先觀察一天」的 pending 緩衝，應直接視為 MOMENTUM_STRUCTURE +
    PARTICIPATION 同時失效，透過方法 A 立即 STOP——對照上一個測試（BULL_TREND
    下同樣證據只會建立 pending，維持 CAUTION）。"""
    evidence = _composite_evidence(market_regime="RISK_OFF")
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [
        "RISK_OFF_ACCELERATED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    ]
    assert decision.pending_stop_update is None


def test_risk_off_bypasses_existing_composite_pending_and_accelerates_stop():
    """既有的 pending（例如大盤在 BULL_TREND 時觸發、隔天market_regime 轉為
    RISK_OFF）也應該直接繞過 pending 確認邏輯，走加速停止判斷。"""
    evidence = _composite_evidence(market_regime="RISK_OFF")
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={
            "baseline_quality": "P3_COMPLETE",
            "pending_stop_status": "ACTIVE",
            "pending_stop_reason": "COMPOSITE_RISK_EXCLUDE",
            "pending_stop_review_count": 0,
            "pending_stop_trigger_snapshot": {"close": 259.0, "high": 286.5, "low": 253.0},
        },
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [
        "RISK_OFF_ACCELERATED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    ]


def test_composite_risk_exclude_masked_reversal_failure_is_still_immediate_stop():
    """P4 v2 spec §7：若同一天也獨立符合更嚴格的 REVERSAL_FAILURE，不能因為
    `build_hard_exclusion_result` 優先序把它標成 COMPOSITE_RISK_EXCLUDE 就被
    composite 的 pending 規則繞過。"""
    evidence = _composite_evidence(
        reversal_failure_check={
            "triggered": True,
            "institution_reversal_ratio": 0.65,
            "excess_return_vs_market": -2.1,
        }
    )
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == ["REVERSAL_FAILURE"]
    assert decision.pending_stop_update == "CLEAR"


def test_composite_risk_pending_recovers_when_price_reclaims_and_momentum_returns():
    trigger_snapshot = {
        "trigger_date": "2026-08-17",
        "high": 286.5,
        "low": 253.0,
    }
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": False, "reason": None}
    evidence["close_1d"] = 275.0  # >= (286.5+253.0)/2 = 269.75
    evidence["price_change_1d"] = 6.2  # >= 2.0
    evidence["excess_return_vs_market"] = 4.0
    evidence["tracking_state"] = "REACCELERATING"
    evidence["deterministic_signals"]["institution_flow_momentum"] = "stable"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={
            "baseline_quality": "P3_COMPLETE",
            "pending_stop_status": "ACTIVE",
            "pending_stop_reason": "COMPOSITE_RISK_EXCLUDE",
            "pending_stop_review_count": 1,
            "pending_stop_trigger_snapshot": trigger_snapshot,
        },
    )
    assert decision.decision != "STOP_OBSERVING"
    assert decision.pending_stop_update == "CLEAR"


def test_composite_risk_pending_confirms_stop_when_participation_and_momentum_both_fail():
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": False, "reason": None}
    evidence["tracking_state"] = "DETERIORATING"
    evidence["deterministic_signals"] = {
        "institution_flow_momentum": "reversal",
        "chip_trend": "weakening",
        "sector_rotation_status": "failed_rotation",
    }
    evidence["quality_evidence"] = {
        "PARTICIPATION": False,
        "INSTITUTION_CONFIRMATION": False,
    }
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={
            "baseline_quality": "P3_COMPLETE",
            "pending_stop_status": "ACTIVE",
            "pending_stop_reason": "COMPOSITE_RISK_EXCLUDE",
            "pending_stop_review_count": 1,
            "pending_stop_trigger_snapshot": {"high": 286.5, "low": 253.0},
        },
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == ["COMPOSITE_RISK_CONFIRMED"]
    assert decision.pending_stop_update == "CLEAR"


def test_composite_risk_pending_keeps_waiting_when_neither_recovered_nor_confirmed():
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": False, "reason": None}
    # 中性：不觸發 recovery（沒有 reaccelerating/healthy_pullback/fresh_strong 或
    # stable/accelerating 資金），也不觸發 confirm（PARTICIPATION 沒有兩個負面訊號）。
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={
            "baseline_quality": "P3_COMPLETE",
            "pending_stop_status": "ACTIVE",
            "pending_stop_reason": "COMPOSITE_RISK_EXCLUDE",
            "pending_stop_review_count": 1,
            "pending_stop_trigger_snapshot": {"high": 286.5, "low": 253.0},
        },
    )
    assert decision.decision == "CAUTION"
    assert "COMPOSITE_RISK_PENDING" in decision.reason_codes
    assert decision.pending_stop_update == "KEEP"


def test_composite_risk_pending_expires_after_max_reviews_without_forcing_stop():
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": False, "reason": None}
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={
            "baseline_quality": "P3_COMPLETE",
            "pending_stop_status": "ACTIVE",
            "pending_stop_reason": "COMPOSITE_RISK_EXCLUDE",
            "pending_stop_review_count": lifecycle.COMPOSITE_PENDING_MAX_REVIEWS,
            "pending_stop_trigger_snapshot": {"high": 286.5, "low": 253.0},
        },
    )
    assert decision.decision != "STOP_OBSERVING"
    assert decision.pending_stop_update == "CLEAR"


def test_ac7_8039_20260817_composite_risk_becomes_pending_not_immediate_stop(
    db, monkeypatch
):
    """P4 Observation Lifecycle v2 AC7：8039（台虹）2026-08-17 的真實 production
    證據（盤中創高後尾盤翻黑收在接近全日低點形成 distribution K 棒，同時 3 日累計
    法人買超轉為單日大幅賣超形成 institution_flow_reversal，但
    institution_reversal_ratio≈0.403 < 0.5 不足以構成獨立的 REVERSAL_FAILURE）套進
    新邏輯，必須是 CAUTION + 建立 pending，不能是立即 STOP_OBSERVING、也不能當下就
    archive/結算魚尾。"""
    observation = _observation(db, stock="8039", started=DAY_0)
    evidence = _composite_evidence(stock="8039")
    _patch_evidence(monkeypatch, {observation.id: evidence})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"8039": _external(stock="8039")}),
        persist=True,
    )

    assert result["tracking_summary"]["caution_count"] == 1
    assert result["tracking_summary"]["stopped_count"] == 0
    row = db.get(SignalObservation, observation.id)
    assert row.status == "CAUTION"
    assert row.pending_stop_status == "ACTIVE"
    assert row.pending_stop_reason == "COMPOSITE_RISK_EXCLUDE"
    assert row.pending_stop_review_count == 1
    assert row.pending_stop_since == DAY_1
    assert (
        db.query(SignalObservationArchive)
        .filter_by(observation_id=observation.id)
        .count()
        == 0
    )


def test_case_a_composite_pending_confirms_stop_on_second_day(db, monkeypatch):
    """P4 v2 spec §19 Case A：Day 1 composite risk → pending/CAUTION；Day 2
    participation 與 momentum structure 都仍然失效、也沒有恢復訊號 → STOP，reason
    為 COMPOSITE_RISK_CONFIRMED（不是原本的 COMPOSITE_RISK_EXCLUDE）。"""
    observation = _observation(db, started=DAY_0)
    _patch_evidence(monkeypatch, {observation.id: _composite_evidence()})
    lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )
    assert db.get(SignalObservation, observation.id).status == "CAUTION"

    day2_evidence = _healthy_evidence()
    day2_evidence["hard_exclusion"] = {"excluded": False, "reason": None}
    day2_evidence["tracking_state"] = "DETERIORATING"
    day2_evidence["deterministic_signals"] = {
        "institution_flow_momentum": "reversal",
        "chip_trend": "weakening",
        "sector_rotation_status": "failed_rotation",
    }
    day2_evidence["quality_evidence"] = {
        "PARTICIPATION": False,
        "INSTITUTION_CONFIRMATION": False,
    }
    _patch_evidence(monkeypatch, {observation.id: day2_evidence})
    result_day2 = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_2,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )

    assert result_day2["tracking_summary"]["stopped_count"] == 1
    row = db.get(SignalObservation, observation.id)
    assert row.status == "STOPPED"
    assert row.stop_reason_code == "COMPOSITE_RISK_CONFIRMED"
    assert row.pending_stop_status is None


def test_case_b_structure_damaged_on_pending_day2_bypasses_composite_confirmation(
    db, monkeypatch
):
    """P4 v2 spec §19 Case B：Day 1 composite risk → pending/CAUTION；Day 2 若
    出現真正的 STRUCTURE_DAMAGED（不同、更嚴重的 hard reason），要直接 immediate
    STOP，不需要等待 composite risk 自己的 confirmation 判斷。"""
    observation = _observation(db, started=DAY_0)
    _patch_evidence(monkeypatch, {observation.id: _composite_evidence()})
    lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )
    assert db.get(SignalObservation, observation.id).status == "CAUTION"

    day2_evidence = _healthy_evidence()
    day2_evidence["hard_exclusion"] = {
        "excluded": True,
        "reason": "STRUCTURE_DAMAGED",
    }
    _patch_evidence(monkeypatch, {observation.id: day2_evidence})
    result_day2 = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_2,
        market_context={},
        current_candidates=[],
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )

    assert result_day2["tracking_summary"]["stopped_count"] == 1
    row = db.get(SignalObservation, observation.id)
    assert row.status == "STOPPED"
    assert row.stop_reason_code == "STRUCTURE_DAMAGED"
    assert row.pending_stop_status is None


def test_composite_pending_skips_llm_research_batch_when_composite_only(
    db, monkeypatch
):
    """2026-08-18 修正：composite risk（不再是 immediate hard reason）不該讓 LLM
    tracking-review research 被跳過——這輪觀察可能還要繼續好幾天，外部事實查證跟一般
    觀察一樣需要照常執行。"""
    observation = _observation(db, started=DAY_0)
    _patch_evidence(monkeypatch, {observation.id: _composite_evidence()})

    captured_batches: list = []

    def _capturing_runner(payloads):
        captured_batches.append(list(payloads))
        return {"2330": _external()}, []

    lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        current_candidates=[],
        assessment_runner=_capturing_runner,
        persist=True,
    )

    assert len(captured_batches) == 1
    assert len(captured_batches[0]) == 1
    assert captured_batches[0][0]["stock"] == "2330"


def test_tracking_invalidated_stops():
    evidence = _healthy_evidence()
    evidence["tracking_state"] = "INVALIDATED"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.reason_codes == ["TRACKING_INVALIDATED"]


@pytest.mark.parametrize(
    "reason",
    [
        "BUSINESS_MISMATCH",
        "THEME_MISMATCH",
        "FALSE_SUPPLY_CHAIN_LINK",
        "MATERIAL_NEGATIVE_EVENT",
        "DATA_CONTRADICTION",
    ],
)
def test_external_thesis_invalidation_stops(reason):
    external = _external(assessment="THESIS_INVALIDATED")
    external["invalidation_reason_code"] = reason
    external["thesis_dimensions"]["theme"] = "INVALIDATED"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=_healthy_evidence(),
        external_thesis_assessment=external,
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [reason]


def test_unconfirmed_and_material_without_source_cannot_be_validated():
    raw = _external(assessment="THESIS_INVALIDATED")
    raw.update(
        {
            "invalidation_reason_code": "MATERIAL_NEGATIVE_EVENT",
            "material_evidence": [],
        }
    )
    raw["thesis_dimensions"]["theme"] = "INVALIDATED"
    with pytest.raises(ValueError, match="traceable evidence"):
        lifecycle._validate_external_assessment(raw, review_date=DAY_1)

    unconfirmed = _external(assessment="THESIS_WEAKENING", catalyst="UNCONFIRMED")
    validated = lifecycle._validate_external_assessment(
        unconfirmed,
        review_date=DAY_1,
    )
    assert validated["assessment"] == "THESIS_WEAKENING"


def test_external_mismatch_requires_consistent_invalidated_contract():
    inconsistent = _external()
    inconsistent["instrument_validation"] = "MISMATCH"
    with pytest.raises(ValueError, match="THESIS_INVALIDATED"):
        lifecycle._validate_external_assessment(
            inconsistent,
            review_date=DAY_1,
        )

    invalidated = _external(assessment="THESIS_INVALIDATED")
    invalidated["invalidation_reason_code"] = "BUSINESS_MISMATCH"
    invalidated["thesis_dimensions"]["business_or_exposure"] = "INVALIDATED"
    with pytest.raises(ValueError, match="instrument_validation=MISMATCH"):
        lifecycle._validate_external_assessment(
            invalidated,
            review_date=DAY_1,
        )


def test_material_evidence_is_date_bounded_and_records_retrieved_date():
    raw = _external(assessment="THESIS_INVALIDATED")
    raw.update(
        {
            "invalidation_reason_code": "DATA_CONTRADICTION",
            "material_evidence": [
                {
                    "summary": "公告與 thesis 矛盾",
                    "url": "https://example.com/filing",
                    "published_date": DAY_0.isoformat(),
                }
            ],
        }
    )
    raw["thesis_dimensions"]["business_or_exposure"] = "INVALIDATED"
    validated = lifecycle._validate_external_assessment(raw, review_date=DAY_1)
    assert validated["material_evidence"][0]["retrieved_date"] == DAY_1.isoformat()


def test_persistence_or_market_warning_never_stops():
    for mutator in (
        lambda evidence: evidence["persistence_warning"].update(
            {"warning": True, "state": "FAILED", "count": 4}
        ),
        lambda evidence: evidence.update({"market_regime": "RISK_OFF"}),
    ):
        evidence = _healthy_evidence()
        mutator(evidence)
        decision = lifecycle.decide_observation_action(
            current_backend_evidence=evidence,
            external_thesis_assessment=_external(),
            latest_valid_reviews=[
                {
                    "decision": "CAUTION",
                    "failed_dimensions": [],
                }
            ],
            current_observation={"baseline_quality": "P3_COMPLETE"},
        )
        assert decision.decision == "CAUTION"


def test_one_day_institution_reversal_is_caution_not_stop():
    evidence = _healthy_evidence()
    evidence["deterministic_signals"]["institution_flow_momentum"] = "reversal"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"
    assert decision.failed_dimensions == []


def test_second_successful_review_can_stop_sustained_two_dimension_failure():
    evidence = _healthy_evidence()
    evidence["tracking_state"] = "DETERIORATING"
    evidence["deterministic_signals"].update(
        {
            "institution_flow_momentum": "reversal",
            "chip_trend": "distribution",
        }
    )
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[
            {
                "decision": "CAUTION",
                "failed_dimensions": [
                    "MOMENTUM_STRUCTURE",
                    "PARTICIPATION",
                ],
            }
        ],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [
        "SUSTAINED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    ]


def test_risk_off_regime_accelerates_stop_without_waiting_second_day():
    """2026-08-27 方法 A：RISK_OFF 當天若核心維度已同時出現 >=2 個失效（交集含
    MOMENTUM_STRUCTURE 或 PARTICIPATION），不必等隔天複核確認即可直接 STOP——
    對照既有 `test_second_successful_review_can_stop_sustained_two_dimension_failure`
    需要兩次 review 才會 STOP，這裡第一次 review（`latest_valid_reviews=[]`）就要 STOP。"""
    evidence = _healthy_evidence()
    evidence["market_regime"] = "RISK_OFF"
    evidence["tracking_state"] = "DETERIORATING"
    evidence["deterministic_signals"].update(
        {
            "institution_flow_momentum": "reversal",
            "chip_trend": "distribution",
        }
    )
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "STOP_OBSERVING"
    assert decision.reason_codes == [
        "RISK_OFF_ACCELERATED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    ]


def test_bull_trend_regime_does_not_accelerate_stop_with_same_evidence():
    """同一份「2 個核心維度失效」證據，regime 若不是 RISK_OFF（這裡用預設的
    BULL_TREND）就不該加速——加速只限 RISK_OFF，範圍刻意窄。"""
    evidence = _healthy_evidence()
    evidence["tracking_state"] = "DETERIORATING"
    evidence["deterministic_signals"].update(
        {
            "institution_flow_momentum": "reversal",
            "chip_trend": "distribution",
        }
    )
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"


def test_risk_off_with_only_one_core_dimension_failing_does_not_accelerate():
    """RISK_OFF 但只有 1 個核心維度失效（未達 >=2 門檻）——維持 CAUTION，不加速。"""
    evidence = _healthy_evidence()
    evidence["market_regime"] = "RISK_OFF"
    evidence["tracking_state"] = "DETERIORATING"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"
    assert decision.failed_dimensions == ["MOMENTUM_STRUCTURE"]


def test_recovery_evidence_overrides_risk_off_acceleration():
    """就算 RISK_OFF 且今天技術面一度出現 2 個核心維度失效訊號，只要同時滿足既有
    `_has_recovery_evidence` 的恢復條件，仍應維持既有「恢復優先」的行為——加速停止
    不能繞過既有的 recovery 保護機制。"""
    evidence = _healthy_evidence()
    evidence["market_regime"] = "RISK_OFF"
    evidence["momentum_phase"] = "weakening"
    evidence["momentum_freshness"] = "FRESH_STRONG"
    evidence["deterministic_signals"]["chip_trend"] = "distribution"
    evidence["quality_evidence"]["PARTICIPATION"] = False
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"
    assert decision.failed_dimensions == []
    assert "RISK_OFF_ACCELERATED_MOMENTUM_AND_PARTICIPATION_FAILURE" not in decision.reason_codes


def test_participation_still_requires_two_flags_to_fail_even_in_risk_off():
    """2026-08-27：曾經試過 RISK_OFF 期間把 PARTICIPATION 的 failed 門檻降到
    1 個旗標，但用真實 7/16 資料驗證後發現 `institution_flow_momentum==reversal`
    在系統性重挫當天幾乎是全市場現象，降到 1 旗標會讓大多數候選同一天一起被判定
    失效、鑑別力太低，已回收。這裡鎖住回收後的行為：只給 1 個 participation 旗標
    + 動能轉弱，不論 RISK_OFF 或 BULL_TREND，都只停在 CAUTION（PARTICIPATION 不算
    failed），不會觸發方法 A 加速停止。"""
    for regime in ("RISK_OFF", "BULL_TREND"):
        evidence = _healthy_evidence()
        evidence["market_regime"] = regime
        evidence["tracking_state"] = "DETERIORATING"
        evidence["deterministic_signals"]["institution_flow_momentum"] = "reversal"
        decision = lifecycle.decide_observation_action(
            current_backend_evidence=evidence,
            external_thesis_assessment=_external(),
            latest_valid_reviews=[],
            current_observation={"baseline_quality": "P3_COMPLETE"},
        )
        assert decision.decision == "CAUTION", regime
        assert decision.failed_dimensions == ["MOMENTUM_STRUCTURE"], regime


def test_market_and_persistence_do_not_count_as_core_dimensions():
    evidence = _healthy_evidence()
    evidence["market_regime"] = "RISK_OFF"
    evidence["persistence_warning"] = {
        "warning": True,
        "state": "FAILED",
        "count": 5,
    }
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[
            {
                "decision": "CAUTION",
                "failed_dimensions": [
                    "MARKET_CONTEXT",
                    "PERSISTENCE_WARNING",
                ],
            }
        ],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CAUTION"


def test_reacceleration_recovers_caution_to_continue():
    evidence = _healthy_evidence()
    evidence["tracking_state"] = "REACCELERATING"
    evidence["momentum_freshness"] = "FRESH_STRONG"
    decision = lifecycle.decide_observation_action(
        current_backend_evidence=evidence,
        external_thesis_assessment=_external(),
        latest_valid_reviews=[
            {
                "decision": "CAUTION",
                "failed_dimensions": ["MOMENTUM_STRUCTURE"],
            }
        ],
        current_observation={"baseline_quality": "P3_COMPLETE"},
    )
    assert decision.decision == "CONTINUE"
    assert decision.reason_codes == ["RECOVERED_FROM_CAUTION"]


def test_technical_failure_preserves_status_and_caution_count(db, monkeypatch):
    observation = _observation(db)
    observation.status = "CAUTION"
    observation.consecutive_caution_count = 2
    db.commit()
    _patch_evidence(monkeypatch, {observation.id: _healthy_evidence()})
    failure = {
        "stock": "2330",
        "status": "TRACKING_RESEARCH_FAILED",
        "error_summary": "timeout",
    }

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for({}, [failure]),
        persist=True,
    )

    refreshed = db.get(SignalObservation, observation.id)
    assert result["tracking_summary"]["review_failed_count"] == 1
    assert refreshed.status == "CAUTION"
    assert refreshed.consecutive_caution_count == 2
    assert db.query(SignalObservationReview).one().decision == "REVIEW_FAILED"


def test_tracking_batch_exception_becomes_per_stock_review_failure(monkeypatch):
    monkeypatch.setattr(
        lifecycle.llm_caller,
        "_call_llm_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    successful, failures = lifecycle.run_tracking_assessments(
        [
            {"date": DAY_1.isoformat(), "stock": "2330"},
            {"date": DAY_1.isoformat(), "stock": "2454"},
        ],
        batch_size=2,
    )
    assert successful == {}
    assert {item["stock"] for item in failures} == {"2330", "2454"}
    assert all(item["processing_status"] == "REVIEW_FAILED" for item in failures)


def test_v7_tracking_call_uses_strict_output_schema(monkeypatch):
    captured = {}

    def fake_call(*args, **kwargs):
        captured.update(kwargs)
        return {
            "review_date": DAY_1.isoformat(),
            "items": [_external()],
        }, {"status": "ok"}

    monkeypatch.setattr(lifecycle.llm_caller, "_call_llm_json", fake_call)
    successful, failures = lifecycle.run_tracking_assessments(
        [{"date": DAY_1.isoformat(), "stock": "2330"}],
        batch_size=1,
    )
    assert failures == []
    assert successful["2330"]["assessment"] == "THESIS_INTACT"
    assert captured["response_format_name"] == "fishtail_v7_tracking"
    stock_schema = captured["response_schema"]["properties"]["items"]["items"][
        "properties"
    ]["stock"]
    assert stock_schema["enum"] == ["2330"]


def test_v7_tracking_semantic_contract_retries_single_stock(monkeypatch):
    invalid = _external(assessment="THESIS_INVALIDATED")
    invalid.update(
        {
            "invalidation_reason_code": "MATERIAL_NEGATIVE_EVENT",
            "thesis_dimensions": {
                "business_or_exposure": "INTACT",
                "theme": "INVALIDATED",
                "catalyst": "INVALIDATED",
            },
            "material_evidence": [],
        }
    )
    calls = []

    def fake_call(_system, user_msg, **kwargs):
        payload = json.loads(user_msg)
        calls.append(payload)
        item = invalid if len(calls) == 1 else _external()
        return {
            "review_date": DAY_1.isoformat(),
            "items": [item],
        }, {"status": "ok"}

    monkeypatch.setattr(lifecycle.llm_caller, "_call_llm_json", fake_call)
    successful, failures = lifecycle.run_tracking_assessments(
        [{"date": DAY_1.isoformat(), "stock": "2330"}],
        batch_size=1,
    )

    assert failures == []
    assert successful["2330"]["assessment"] == "THESIS_INTACT"
    assert len(calls) == 2
    retry = calls[1]["contract_retry"]
    assert "traceable evidence" in retry["previous_rejection"]
    assert "不可捏造來源" in retry["required_correction"]
    assert (
        successful["2330"]["_llm_diagnostic"]["contract_retry_attempt"]
        == 1
    )


def test_v7_tracking_alignment_failure_retries_omitted_stock(monkeypatch):
    """2026-08-22 regression：批次回應格式正確（合法 dict／items list／review_date
    對齊），但 LLM 直接漏答其中一檔（不是輸出格式錯誤，是根本沒把那一檔放進
    items 陣列）——原本這種情況完全沒有重試（只有 ValueError 契約驗證失敗才有單檔
    重試），單一股票被漏答就讓整批（乃至整天 pipeline）判定失敗。真實案例：
    2026-08-21 排程 00738U 被漏答，導致 partial_failure 整包 fail。"""
    calls = []

    def fake_call(_system, user_msg, **kwargs):
        payload = json.loads(user_msg)
        calls.append(payload)
        if len(payload["items"]) == 2:
            # 第一次批次呼叫：LLM 只回了 2330，完全漏掉 2454。
            return {
                "review_date": DAY_1.isoformat(),
                "items": [_external(stock="2330")],
            }, {"status": "ok"}
        # 針對漏答股票的單檔重試：這次正確回答。
        return {
            "review_date": DAY_1.isoformat(),
            "items": [_external(stock="2454")],
        }, {"status": "ok"}

    monkeypatch.setattr(lifecycle.llm_caller, "_call_llm_json", fake_call)
    successful, failures = lifecycle.run_tracking_assessments(
        [
            {"date": DAY_1.isoformat(), "stock": "2330"},
            {"date": DAY_1.isoformat(), "stock": "2454"},
        ],
        batch_size=2,
    )

    assert failures == []
    assert set(successful.keys()) == {"2330", "2454"}
    assert len(calls) == 2
    retry_body = calls[1]
    assert retry_body["items"] == [{"date": DAY_1.isoformat(), "stock": "2454"}]
    retry = retry_body["contract_retry"]
    assert retry["previous_rejection"] == "Tracking assessment omitted the stock."
    assert "省略" in retry["required_correction"]
    assert (
        successful["2454"]["_llm_diagnostic"]["contract_retry_attempt"] == 1
    )


def test_v7_tracking_alignment_failure_gives_up_after_retries_exhausted(monkeypatch):
    """漏答重試仍然漏答（或持續格式不對）→ 用盡重試次數後才真的判
    TRACKING_OUTPUT_ALIGNMENT_FAILED，不能無限重試。"""
    calls = []

    def fake_call(_system, user_msg, **kwargs):
        payload = json.loads(user_msg)
        calls.append(payload)
        if len(payload["items"]) == 2:
            return {
                "review_date": DAY_1.isoformat(),
                "items": [_external(stock="2330")],
            }, {"status": "ok"}
        # 單檔重試每次都繼續漏答同一檔（回傳空 items）。
        return {"review_date": DAY_1.isoformat(), "items": []}, {"status": "ok"}

    monkeypatch.setattr(lifecycle.llm_caller, "_call_llm_json", fake_call)
    successful, failures = lifecycle.run_tracking_assessments(
        [
            {"date": DAY_1.isoformat(), "stock": "2330"},
            {"date": DAY_1.isoformat(), "stock": "2454"},
        ],
        batch_size=2,
    )

    assert set(successful.keys()) == {"2330"}
    assert len(failures) == 1
    assert failures[0]["stock"] == "2454"
    assert failures[0]["status"] == "TRACKING_OUTPUT_ALIGNMENT_FAILED"
    # 1 次原始批次呼叫 + max_contract_retries(2) 次單檔重試 = 3 次總呼叫
    assert len(calls) == 3


def test_same_date_rerun_is_idempotent(db, monkeypatch):
    observation = _observation(db)
    evidence = _healthy_evidence()
    evidence["market_regime"] = "RISK_OFF"
    _patch_evidence(monkeypatch, {observation.id: evidence})

    for _ in range(2):
        lifecycle.run_daily_observation_reviews(
            db,
            review_date=DAY_1,
            market_context={},
            assessment_runner=_runner_for({"2330": _external()}),
            persist=True,
        )

    refreshed = db.get(SignalObservation, observation.id)
    assert db.query(SignalObservationReview).count() == 1
    assert refreshed.consecutive_caution_count == 1


def test_legacy_baseline_is_marked_and_never_stops_from_missing_fields(db, monkeypatch):
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="台積電",
            signal_type="LEADER",
            reason="legacy reason",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            signal_metrics={},
            prompt_version="v1",
        )
    )
    db.commit()
    assert lifecycle.bootstrap_legacy_observations(db) == 1
    observation = db.query(SignalObservation).one()
    assert observation.baseline_quality == "LEGACY_INCOMPLETE"
    _patch_evidence(monkeypatch, {observation.id: _healthy_evidence()})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for({"2330": _external()}),
        persist=True,
    )
    assert result["tracking_summary"]["stopped_count"] == 0
    assert db.get(SignalObservation, observation.id).status == "CAUTION"


def test_bootstrap_preserves_complete_p3_initial_evidence(db):
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2454",
            stock_name="聯發科",
            signal_type="LEADER",
            reason="complete P3 reason",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            signal_metrics={
                "initial_recommendation_thesis": "完整 thesis",
                "initial_relative_advantage": "相對優勢",
                "initial_instrument_validation": "VERIFIED",
                "initial_theme_validation": "VERIFIED",
                "initial_catalyst_summary": "催化仍在",
            },
            prompt_version="v6.1",
        )
    )
    db.commit()
    lifecycle.bootstrap_legacy_observations(db)
    observation = db.query(SignalObservation).one()
    assert observation.baseline_quality == "P3_COMPLETE"
    assert observation.initial_snapshot_json["missing_fields"] == []


def test_p3_recommend_and_p4_stop_creates_explicit_conflict(db, monkeypatch):
    observation = _observation(db)
    evidence = _healthy_evidence()
    evidence["tracking_state"] = "INVALIDATED"
    _patch_evidence(monkeypatch, {observation.id: evidence})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        p3_recommended_stock_ids=["2330"],
        assessment_runner=_runner_for({}),
        persist=True,
    )
    assert result["tracking_summary"]["conflict_count"] == 1
    assert result["conflicts"][0]["error_code"] == "TRACKING_SELECTION_CONFLICT"
    assert db.get(SignalObservation, observation.id).status == "STOPPED"


def test_stopped_stock_can_restart_after_existing_five_day_gap(db):
    observation = _observation(db)
    observation.status = "STOPPED"
    observation.stopped_at = datetime.utcnow()
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="台積電",
            signal_type="LEADER",
            reason="old",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            prompt_version="v6.1",
        )
    )
    for offset in range(7):
        trade_date = DAY_0 + timedelta(days=offset)
        db.add(
            DailyPrice(
                stock_id="2330",
                trade_date=trade_date,
                close_price=100 + offset,
            )
        )
    db.commit()

    sync = lifecycle.sync_recommendations(
        db,
        signal_date=DAY_0 + timedelta(days=6),
        watchlist=[_recommend()],
    )
    assert sync["created"] == ["2330"]
    assert db.query(SignalObservation).count() == 2
    assert db.query(SignalObservation).filter(
        SignalObservation.status == "STOPPED"
    ).count() == 1


def test_stopped_stock_restarts_immediately_without_waiting_for_gap(db):
    """2026-08-11 regression: P3 recommending a stock again the very next day
    after its only P4 observation was stopped must reopen observation right
    away — no minimum-gap cooldown. (Root cause of the 2026-08-10 bug: stocks
    stopped by stop_legacy_incomplete_observations.py that P3 kept
    recommending stayed stuck showing a stale STOPPED badge, because the old
    gap check used signal_watch_hits continuity — which never has a gap for a
    stock P3 recommends every day — to defer the restart indefinitely.)"""
    observation = _observation(db, started=DAY_0)
    observation.status = "STOPPED"
    observation.stopped_at = datetime.utcnow()
    db.commit()

    sync = lifecycle.sync_recommendations(
        db,
        signal_date=DAY_1,
        watchlist=[_recommend()],
    )
    assert sync["created"] == ["2330"]
    assert db.query(SignalObservation).count() == 2
    fresh = (
        db.query(SignalObservation)
        .filter(SignalObservation.status == "OBSERVING")
        .one()
    )
    assert fresh.started_signal_date == DAY_1


def test_sync_recommendations_revives_observation_stopped_on_prior_trading_day(db):
    """2026-08-24：使用者要求——一檔股票在「上一個交易日」才剛被判定停止觀察，
    今天又被 P3 重新選中時，直接復活同一輪觀察（回到 OBSERVING、刪掉那筆停止
    封存紀錄），不建立新一輪、也不曾在追蹤中顯示「已停止觀察」。真實案例：5608／
    3443 都是這個情境（8/20 停止、8/21 又被重新選中），修復前會卡成孤兒魚尾週期
    （見 test_settle_pending_p4_fishtail_stops_settles_orphaned_cycle_even_
    after_new_episode_starts）。"""
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_0, close_price=100.0))
    observation = _observation(db, started=DAY_0)
    observation.status = "STOPPED"
    observation.stopped_at = datetime.utcnow()
    observation.stop_reason_code = "REVERSAL_FAILURE"
    observation.stop_reason = "test"
    observation.stop_confirm_count = 1
    archive = SignalObservationArchive(
        observation_id=observation.id,
        episode_id=observation.episode_id,
        stock_id="2330",
        stock_name="Stock-2330",
        started_signal_date=DAY_0,
        first_stop_date=DAY_0,
        archived_date=DAY_0,
        stop_reason_code="REVERSAL_FAILURE",
        stop_reason="test",
        entry_price=100.0,
    )
    db.add(archive)
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_0,
            baseline_price=100.0,
            latest_eval_trade_date=DAY_0,
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    db.commit()
    observation_id = observation.id

    sync = lifecycle.sync_recommendations(
        db, signal_date=DAY_1, watchlist=[_recommend()]
    )
    db.commit()

    assert sync == {"created": [], "continued": [], "revived": ["2330"]}
    assert db.query(SignalObservation).count() == 1
    revived = db.get(SignalObservation, observation_id)
    assert revived.status == "OBSERVING"
    assert revived.started_signal_date == DAY_0  # 沿用同一輪，不重算開始日
    assert revived.stopped_at is None
    assert revived.stop_reason_code is None
    assert revived.stop_confirm_count == 0
    assert revived.consecutive_caution_count == 0
    assert (
        db.query(SignalObservationArchive)
        .filter_by(observation_id=observation_id)
        .count()
        == 0
    )
    # 魚尾週期完全沒被動過——同一輪繼續，不是誤觸發結算。
    assert db.query(SignalWatchHit).filter_by(stock_id="2330").count() == 1


def test_sync_recommendations_does_not_revive_when_gap_is_more_than_one_trading_day(
    db,
):
    """邊界：間隔超過一個交易日（不是「昨天才停、今天立刻重選」）一律視為全新一輪，
    不能誤觸發復活——這正是既有
    `test_stopped_stock_can_restart_after_existing_five_day_gap` 要保護的行為，
    這裡額外用『真的有 archive 紀錄』的情境再驗證一次邊界精確性。"""
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_0, close_price=100.0))
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_1, close_price=101.0))
    observation = _observation(db, started=DAY_0)
    observation.status = "STOPPED"
    observation.stopped_at = datetime.utcnow()
    db.add(
        SignalObservationArchive(
            observation_id=observation.id,
            episode_id=observation.episode_id,
            stock_id="2330",
            stock_name="Stock-2330",
            started_signal_date=DAY_0,
            first_stop_date=DAY_0,
            archived_date=DAY_0,
            stop_reason_code="REVERSAL_FAILURE",
            stop_reason="test",
            entry_price=100.0,
        )
    )
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_0,
            baseline_price=100.0,
            latest_eval_trade_date=DAY_0,
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    db.commit()

    # 重新選中發生在 DAY_2（archived_date=DAY_0，但 DAY_2 的上一個交易日是
    # DAY_1，不是 DAY_0）——間隔超過一個交易日，不該復活。
    sync = lifecycle.sync_recommendations(
        db, signal_date=DAY_2, watchlist=[_recommend()]
    )

    assert sync["created"] == ["2330"]
    assert sync["revived"] == []
    assert db.query(SignalObservation).count() == 2


def test_sync_recommendations_does_not_revive_when_fishtail_already_settled(db):
    """archived_date 剛好是上一個交易日，但魚尾週期已經正常結算過（無 active
    SignalWatchHit）——代表這輪已經走完正常流程進了紀錄區，不該被復活。"""
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_0, close_price=100.0))
    observation = _observation(db, started=DAY_0)
    observation.status = "STOPPED"
    observation.stopped_at = datetime.utcnow()
    db.add(
        SignalObservationArchive(
            observation_id=observation.id,
            episode_id=observation.episode_id,
            stock_id="2330",
            stock_name="Stock-2330",
            started_signal_date=DAY_0,
            first_stop_date=DAY_0,
            archived_date=DAY_0,
            stop_reason_code="REVERSAL_FAILURE",
            stop_reason="test",
            entry_price=100.0,
        )
    )
    db.commit()

    sync = lifecycle.sync_recommendations(
        db, signal_date=DAY_1, watchlist=[_recommend()]
    )

    assert sync["created"] == ["2330"]
    assert sync["revived"] == []


def test_asset_types_share_identical_state_machine():
    decisions = []
    for asset_type in ("COMMON_STOCK", "FINANCIAL", "ETF"):
        evidence = _healthy_evidence()
        evidence["asset_type"] = asset_type
        decisions.append(
            lifecycle.decide_observation_action(
                current_backend_evidence=evidence,
                external_thesis_assessment=_external(),
                latest_valid_reviews=[],
                current_observation={"baseline_quality": "P3_COMPLETE"},
            ).decision
        )
    assert decisions == ["CONTINUE", "CONTINUE", "CONTINUE"]


def test_tracking_prompt_preserves_lifecycle_authority_and_asset_parity():
    prompt = lifecycle._PROMPT_PATH.read_text(encoding="utf-8")
    assert "不決定 CONTINUE、CAUTION 或 STOP_OBSERVING" in prompt
    assert "不提供 BUY/SELL" in prompt
    assert "COMMON_STOCK、FINANCIAL、ETF 的 lifecycle 地位一致" in prompt
    assert "review_date 當日或之前" in prompt


def test_replay_is_chronological_point_in_time_and_read_only(db, monkeypatch):
    observation = _observation(db)
    for trade_date in (DAY_1, DAY_2):
        db.add(
            DailyPrice(
                stock_id="2330",
                trade_date=trade_date,
                close_price=100,
            )
        )
    db.commit()
    monkeypatch.setattr(
        lifecycle.candidate_pool,
        "ingest_data",
        lambda _db, replay_date: {"stocks_master": {}},
    )
    monkeypatch.setattr(
        lifecycle.momentum,
        "compute_market_momentum_frame",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        lifecycle.market_regime,
        "compute_market_regime",
        lambda *_args: {
            "regime": "BULL_TREND",
            "regime_label": "多頭",
            "reason": "point in time",
        },
    )
    monkeypatch.setattr(
        lifecycle.market_breadth,
        "compute_breadth_from_frame",
        lambda *_args: {"breadth_score": 60},
    )
    monkeypatch.setattr(
        lifecycle.market_snapshot,
        "build_db_market_snapshot",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        lifecycle.llm_caller,
        "assemble_market_context",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        lifecycle,
        "build_current_tracking_evidence",
        lambda _db, observations, review_date, **_kwargs: {
            row.id: {
                **_healthy_evidence(row.stock_id),
                "review_date": review_date.isoformat(),
            }
            for row in observations
        },
    )
    prompted_dates = []

    def runner(payloads):
        prompted_dates.extend(item["date"] for item in payloads)
        return (
            {
                item["stock"]: _external(item["stock"])
                for item in payloads
            },
            [],
        )

    result = lifecycle.replay_observation_lifecycle(
        db,
        start_date=DAY_1,
        end_date=DAY_2,
        observation_ids=[observation.id],
        assessment_runner=runner,
    )

    assert prompted_dates == [DAY_1.isoformat(), DAY_2.isoformat()]
    assert [row["review_date"] for row in result["rows"]] == prompted_dates
    assert all(row["decision"] == "CONTINUE" for row in result["rows"])
    assert db.query(SignalObservationReview).count() == 0
    assert db.get(SignalObservation, observation.id).status == "OBSERVING"


@pytest.mark.parametrize("count", [25, 50, 100, 200])
def test_tracking_scale_state_persistence_and_api_serialization(
    db,
    monkeypatch,
    count,
):
    observations = [
        _observation(db, str(1000 + index))
        for index in range(count)
    ]
    mapping = {
        row.id: _healthy_evidence(row.stock_id) for row in observations
    }
    _patch_evidence(monkeypatch, mapping)
    external = {
        row.stock_id: _external(row.stock_id) for row in observations
    }

    tracemalloc.start()
    started = time.perf_counter()
    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for(external),
        persist=True,
    )
    response = lifecycle.list_observations(db, limit=1000)
    serialized = json.dumps(response, default=str, separators=(",", ":"))
    duration = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result["tracking_summary"]["continue_count"] == count
    assert db.query(SignalObservationReview).count() == count
    assert len(response["observations"]) == count
    assert len(serialized.encode()) > 0
    assert duration < 2
    assert peak < 32 * 1024 * 1024


def _hard_excluded_evidence(reason: str = "STRUCTURE_DAMAGED"):
    evidence = _healthy_evidence()
    evidence["hard_exclusion"] = {"excluded": True, "reason": reason}
    return evidence


def test_first_stop_immediately_archives(db, monkeypatch):
    """2026-08-12：使用者要求 STOP 不再等多日複核確認，第一次 STOP 當下（隔天使用者
    看到網站時）就要歸檔並從追蹤中移除——STOP_CONFIRM_THRESHOLD 改成 1 後，第一次
    STOP 的 stop_confirm_count(1) 立刻滿足門檻，同一次 review 就完成歸檔。"""
    observation = _observation(db)
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_0, close_price=100.0))
    db.commit()
    _patch_evidence(monkeypatch, {observation.id: _hard_excluded_evidence()})

    result = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for({}),
        persist=True,
    )

    row = db.get(SignalObservation, observation.id)
    assert row.status == "STOPPED"
    assert row.stop_confirm_count == 1
    assert result["tracking_summary"]["stopped_count"] == 1
    archive = db.query(SignalObservationArchive).one()
    assert archive.observation_id == observation.id
    assert archive.first_stop_date == DAY_1
    assert archive.archived_date == DAY_1
    assert archive.entry_price == 100.0
    assert archive.exit_price is None
    assert archive.return_pct is None

    # Once archived, the observation is excluded from the daily review query
    # entirely (STOPPED + stop_confirm_count >= threshold) -- a later call
    # must not touch it, error, or create a duplicate archive row.
    lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_2,
        market_context={},
        assessment_runner=_runner_for({}),
        persist=True,
    )
    assert db.query(SignalObservationArchive).count() == 1
    assert db.get(SignalObservation, observation.id).stop_confirm_count == 1


def test_first_stop_settles_fishtail_immediately(db, monkeypatch):
    """2026-08-28：撤銷 2026-08-14 那次「延後一天結算」的設計——魚尾頁現在有獨立
    的「今天停止觀察」區塊專門呈現這個資訊，不再需要靠「追蹤中」多留一天 rose
    底色卡片來補這個缺口；同一天兩處都顯示反而讓「追蹤中」看起來不乾淨。所以
    STOP 判定當下（DAY_1）魚尾追蹤週期就該立即結算移出，不必等到 DAY_2。"""
    observation = _observation(db)
    db.add(DailyPrice(stock_id="2330", trade_date=DAY_0, close_price=100.0))
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_0,
            baseline_price=100.0,
            latest_eval_trade_date=DAY_0,
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    db.commit()
    _patch_evidence(monkeypatch, {observation.id: _hard_excluded_evidence()})

    result_day1 = lifecycle.run_daily_observation_reviews(
        db,
        review_date=DAY_1,
        market_context={},
        assessment_runner=_runner_for({}),
        persist=True,
    )

    row = db.get(SignalObservation, observation.id)
    assert row.status == "STOPPED"
    assert row.stop_confirm_count == 1
    # P4 觀察本身當下就定案歸檔（archive row 已存在）……
    assert db.query(SignalObservationArchive).filter_by(observation_id=observation.id).count() == 1
    # ……魚尾追蹤週期也同一晚立即結算移出，不再等到隔天。
    assert db.query(SignalWatchHit).filter_by(stock_id="2330").count() == 0
    completed = (
        db.query(SignalWatchCompletedArchive).filter_by(stock_id="2330").one()
    )
    assert completed.closure_reason == archive_module.CLOSURE_REASON_P4_STOPPED
    assert completed.completed_trade_date == DAY_1
    stopped_record = (
        db.query(SignalWatchStoppedObservation).filter_by(stock_id="2330").one()
    )
    assert stopped_record.closure_reason == archive_module.CLOSURE_REASON_P4_STOPPED
    assert stopped_record.completed_trade_date == DAY_1
    # _settle_pending_p4_fishtail_stops 這次沒有東西可結算（沒有 archived_date <
    # review_date 的殘留），純防禦性 self-healing 沒被觸發。
    assert result_day1["tracking_summary"]["fishtail_stop_settled_count"] == 0


def test_settle_pending_p4_fishtail_stops_is_self_healing_across_gaps(db):
    """跟 `_settle_pending_archive_exits` 同一種 self-healing 設計：即使中間漏跑
    一次複核，之後任何一次呼叫都能抓到「archived_date 早於今天」但魚尾還沒結算的
    股票，不會漏掉、也不會重複結算。"""
    observation = _observation(db)
    observation.status = "STOPPED"
    db.add(
        SignalObservationArchive(
            observation_id=observation.id,
            episode_id=observation.episode_id,
            stock_id="2330",
            stock_name="Stock-2330",
            started_signal_date=DAY_0,
            first_stop_date=DAY_1,
            archived_date=DAY_1,
            stop_reason_code="STRUCTURE_DAMAGED",
            stop_reason="test",
            entry_price=100.0,
        )
    )
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_0,
            baseline_price=100.0,
            latest_eval_trade_date=DAY_0,
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    db.commit()

    # 假設中間漏跑好幾天，直接跳到很後面才複核——一樣要能抓到。
    later = DAY_2 + timedelta(days=5)
    settled = lifecycle._settle_pending_p4_fishtail_stops(db, review_date=later)
    assert settled == 1
    db.commit()

    completed = (
        db.query(SignalWatchCompletedArchive).filter_by(stock_id="2330").one()
    )
    assert completed.completed_trade_date == later

    # 再呼叫一次不應該重複結算（已經沒有進行中的魚尾週期了）。
    settled_again = lifecycle._settle_pending_p4_fishtail_stops(db, review_date=later)
    assert settled_again == 0


def test_settle_pending_p4_fishtail_stops_ignores_stale_episode_from_earlier_stopped_cycle(
    db,
):
    """Regression（2026-08-16）：一檔股票很久以前有一輪觀察被 STOP 並封存
    （archived_date 早於今天），但之後又重新進入一輪全新的追蹤（仍在
    OBSERVING/CAUTION，沒被判定失效）。舊版只用 stock_id + archived_date 判斷，
    會誤把「現在這輪根本沒被 STOP」的魚尾週期強制結算掉——這正是 2026-08-14 上線
    當天誤殺台積電/聯發科/宏致/聯茂等 9 檔股票的真實 bug。修復後必須確認：只有當
    archive 對應的觀察就是該股票「目前最新一輪」且該輪 status==STOPPED，才會結算；
    舊一輪的 archive 紀錄不該影響仍在追蹤中的新一輪。"""
    old_observation = _observation(db, started=DAY_0)
    old_observation.status = "STOPPED"
    db.add(
        SignalObservationArchive(
            observation_id=old_observation.id,
            episode_id=old_observation.episode_id,
            stock_id="2330",
            stock_name="Stock-2330",
            started_signal_date=DAY_0,
            first_stop_date=DAY_1,
            archived_date=DAY_1,
            stop_reason_code="STRUCTURE_DAMAGED",
            stop_reason="test",
            entry_price=100.0,
        )
    )
    # 一段時間後，同一檔股票重新被 P3 選中，開了全新一輪觀察——目前仍在追蹤中，
    # 從未被判定 STOP。
    new_observation = _observation(db, started=DAY_2 + timedelta(days=10))
    new_observation.status = "CAUTION"
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_2 + timedelta(days=10),
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_2 + timedelta(days=10),
            baseline_price=100.0,
            latest_eval_trade_date=DAY_2 + timedelta(days=10),
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    db.commit()

    settled = lifecycle._settle_pending_p4_fishtail_stops(
        db, review_date=DAY_2 + timedelta(days=11)
    )
    assert settled == 0

    active = db.query(SignalWatchHit).filter_by(stock_id="2330").all()
    assert len(active) == 1
    assert (
        db.query(SignalWatchCompletedArchive).filter_by(stock_id="2330").count() == 0
    )


def test_settle_pending_p4_fishtail_stops_settles_orphaned_cycle_even_after_new_episode_starts(
    db,
):
    """Regression（2026-08-24）：真實案例 5608——股票在 DAY_1 被判定 STOP
    （archived_date=DAY_1），但延後結算排定要執行的那一天（DAY_2），該股票剛好又被
    P3 重新推薦、`sync_recommendations()` 已經先建立了一輪全新 episode（此時最新一輪
    已經不是 STOPPED）。舊版邏輯用「archive 是否對應目前最新一輪」判斷，這種情況下
    永遠對不上，導致 DAY_0 那筆魚尾命中卡在「追蹤中」永遠結算不到，跟目前健康的新
    episode 混在一起顯示，造成卡片底色（跟著新 episode 顯示 OBSERVING）跟動能分數圖
    的「停止觀察」標記（屬於舊 episode）互相矛盾。修復後：只要魚尾週期的命中都發生在
    archived_date（含）之前，不論當下 P4 是否已經有更新的 episode，都要正確結算。"""
    old_observation = _observation(db, started=DAY_0)
    old_observation.status = "STOPPED"
    db.add(
        SignalObservationArchive(
            observation_id=old_observation.id,
            episode_id=old_observation.episode_id,
            stock_id="2330",
            stock_name="Stock-2330",
            started_signal_date=DAY_0,
            first_stop_date=DAY_1,
            archived_date=DAY_1,
            stop_reason_code="REVERSAL_FAILURE",
            stop_reason="test",
            entry_price=100.0,
        )
    )
    db.add(
        SignalWatchHit(
            snapshot_date=DAY_0,
            stock_id="2330",
            stock_name="Stock-2330",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry="x",
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            baseline_trade_date=DAY_0,
            baseline_price=100.0,
            latest_eval_trade_date=DAY_0,
            latest_eval_price=100.0,
            return_pct=0.0,
        )
    )
    # DAY_2（延後結算排定要執行的日子）當天，sync_recommendations 已經先建立了
    # 一輪全新 episode（P3 重新推薦、此時最新一輪已是 STOPPED，視為沒有進行中觀察）。
    new_observation = _observation(db, stock="2330", started=DAY_2)
    new_observation.status = "OBSERVING"
    db.commit()

    settled = lifecycle._settle_pending_p4_fishtail_stops(db, review_date=DAY_2)

    assert settled == 1
    completed = (
        db.query(SignalWatchCompletedArchive).filter_by(stock_id="2330").one()
    )
    assert completed.completed_trade_date == DAY_2
    # 舊週期的魚尾命中已結算移除，但新 episode（OBSERVING）本身不受影響。
    assert db.query(SignalWatchHit).filter_by(stock_id="2330").count() == 0
    refreshed_new_observation = db.get(SignalObservation, new_observation.id)
    assert refreshed_new_observation.status == "OBSERVING"


def test_settle_pending_archive_exits_uses_next_available_open_close_average(db):
    observation = _observation(db)
    archive = SignalObservationArchive(
        observation_id=observation.id,
        episode_id=observation.episode_id,
        stock_id="2330",
        stock_name="Stock-2330",
        started_signal_date=DAY_0,
        first_stop_date=DAY_1,
        archived_date=DAY_1,
        stop_reason_code="STRUCTURE_DAMAGED",
        stop_reason="test",
        entry_price=100.0,
    )
    db.add(archive)
    db.commit()

    # No daily_price for DAY_2 yet -- must stay pending, not raise.
    settled = lifecycle._settle_pending_archive_exits(db, review_date=DAY_2)
    assert settled == 0
    db.flush()
    db.refresh(archive)
    assert archive.exit_price is None

    # A day is skipped in the price feed; settlement self-heals on whichever
    # later day first has a usable daily_price row.
    later = DAY_2 + timedelta(days=2)
    db.add(
        DailyPrice(
            stock_id="2330", trade_date=later, open_price=108.0, close_price=112.0
        )
    )
    db.commit()
    settled = lifecycle._settle_pending_archive_exits(db, review_date=later)
    assert settled == 1
    db.flush()
    db.refresh(archive)
    assert archive.exit_trade_date == later
    assert archive.exit_price == 110.0
    assert archive.return_pct == pytest.approx(10.0)


def test_ensure_observation_tables_backfills_stop_confirm_count_column(db):
    from sqlalchemy import inspect, text

    from app.observation_schema import ensure_observation_tables

    engine = db.get_bind()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE signal_observations DROP COLUMN stop_confirm_count"))
    inspector = inspect(engine)
    assert "stop_confirm_count" not in {
        col["name"] for col in inspector.get_columns("signal_observations")
    }

    ensure_observation_tables(engine)

    inspector = inspect(engine)
    assert "stop_confirm_count" in {
        col["name"] for col in inspector.get_columns("signal_observations")
    }
    # Idempotent second call must not raise (column already present).
    ensure_observation_tables(engine)


def test_build_current_tracking_evidence_runs_without_mocking(db):
    """2026-08-19 regression：`build_current_tracking_evidence()` 本身從未被真的呼叫過
    （既有測試全部用 `_patch_evidence` monkeypatch 掉），導致 2026-08-18 那次 canonical
    industry label 重構在 industry_flow 查表那行留了一個未重新命名的區域變數
    （`industry_name` → 應為 `raw_industry_name`），直接讓 production 每日排程的 P4
    複核階段以 `NameError` 整批失敗（job 標記 partial_failure）。本測試不 mock，直接呼叫
    真正的函式本體，確保這類「只在真的執行到那行程式碼時才會噴」的錯誤能被測試抓到。"""
    from app.models import DailyPrice, InstStockFlow, IndustryDailyFlow, StockMaster
    from app.signals import observation_lifecycle as lifecycle_mod

    stock = "2330"
    industry = "半導體業"
    dates = [date(2026, 7, d) for d in range(6, 21)]  # 10+ 個交易日
    db.add(
        StockMaster(
            stock_id=stock,
            stock_name="台積電",
            industry_name=industry,
            is_active=True,
        )
    )
    for d in dates:
        db.add(
            DailyPrice(
                trade_date=d,
                stock_id=stock,
                open_price=100.0,
                high_price=101.0,
                low_price=99.0,
                close_price=100.0,
                volume=1000.0,
                turnover=1.0e8,
            )
        )
        db.add(
            InstStockFlow(
                trade_date=d,
                stock_id=stock,
                inst_type="foreign",
                buy_shares=0,
                sell_shares=0,
                net_shares=0,
                buy_amount_est=1.0e8,
                sell_amount_est=0.0,
                net_amount_est=1.0e8,
            )
        )
    for d in dates[-3:]:
        db.add(
            IndustryDailyFlow(
                trade_date=d,
                industry_name=industry,
                total_buy_amount=5.0e9,
                total_sell_amount=0.0,
                total_net_amount=5.0e9,
                foreign_net_amount=5.0e9,
                trust_net_amount=0,
                dealer_net_amount=0,
            )
        )
    observation = _observation(db, stock, started=dates[0])
    db.commit()

    evidence_by_id = lifecycle_mod.build_current_tracking_evidence(
        db,
        observations=[observation],
        review_date=dates[-1],
        market_context={},
    )

    assert observation.id in evidence_by_id
    evidence = evidence_by_id[observation.id]
    assert evidence["stock"] == stock
    assert "hard_exclusion" in evidence
    assert "tracking_state" in evidence
    assert "deterministic_signals" in evidence
