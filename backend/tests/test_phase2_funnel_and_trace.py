"""Phase 2 §R/§S：signal_explain_trace + funnel_metrics 測試。"""
from app.signals.phase2 import explain_trace as trace
from app.signals.phase2 import funnel_metrics as fm
from app.signals.phase2 import roles


def test_explain_trace_stops_at_momentum_eligibility_when_not_eligible():
    out = trace.build_explain_trace("A", momentum_eligible=False)
    assert out["final_stage"] == trace.STAGE_MOMENTUM_ELIGIBILITY
    assert out["first_exclusion_reason"] == "base_momentum_not_eligible"


def test_explain_trace_stops_at_hard_exclusion():
    out = trace.build_explain_trace("A", momentum_eligible=True, hard_exclusion_reason="distribution")
    assert out["final_stage"] == trace.STAGE_HARD_EXCLUSION
    assert out["first_exclusion_reason"] == "distribution"


def test_explain_trace_stops_at_regime_gate():
    out = trace.build_explain_trace(
        "A", momentum_eligible=True, hard_exclusion_reason=None,
        regime_gate_passed=False, regime="RISK_OFF",
    )
    assert out["final_stage"] == trace.STAGE_REGIME_GATE
    assert out["first_exclusion_reason"] == "regime_excluded:RISK_OFF"


def test_explain_trace_reaches_llm_when_everything_passes():
    out = trace.build_explain_trace(
        "A", momentum_eligible=True, hard_exclusion_reason=None,
        regime_gate_passed=True, regime="BULL_TREND", role="SECTOR_LEADER",
        sent_to_llm=True,
    )
    assert out["final_stage"] == trace.STAGE_SENT_TO_LLM
    assert out["first_exclusion_reason"] is None


def test_funnel_metrics_flags_no_output_day():
    metrics = fm.compute_funnel_metrics(
        candidate_count=120, momentum_eligible_count=14, role_counts={"NONE": 106},
        hard_risk_survivor_count=1, regime_survivor_count=0, sent_to_llm_count=0, watch_count=0,
    )
    assert metrics["no_output_day"] is True
    assert "no_output_day" in metrics["anomaly_flags"]
    assert "sent_to_llm_zero" in metrics["anomaly_flags"]


def test_funnel_metrics_detects_sector_lockout():
    metrics = fm.compute_funnel_metrics(
        candidate_count=30, momentum_eligible_count=10, role_counts={},
        hard_risk_survivor_count=5, regime_survivor_count=2, sent_to_llm_count=2, watch_count=1,
        sector_candidate_counts={"SHIPPING_CONTAINER": 8},
        sector_role_none_counts={"SHIPPING_CONTAINER": 8},
    )
    assert "SHIPPING_CONTAINER" in metrics["sector_lockout_sectors"]
    assert "sector_lockout_detected" in metrics["anomaly_flags"]


def test_role_counts_from_results():
    role_results = {
        "A": {"role": roles.ROLE_SECTOR_LEADER},
        "B": {"role": roles.ROLE_SECTOR_FOLLOWER},
        "C": {"role": None},
    }
    counts = fm.role_counts_from_results(role_results)
    assert counts == {roles.ROLE_SECTOR_LEADER: 1, roles.ROLE_SECTOR_FOLLOWER: 1, "NONE": 1}
