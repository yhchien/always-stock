import pytest

from app.signals.selection_policy import (
    EvidenceStatus,
    EvidenceUsage,
    FROZEN_EVIDENCE_POLICY,
    build_persistence_warning,
    can_auto_filter,
    can_emit_warning,
    can_primary_rank,
    can_tie_break,
    can_use_evidence,
)


def test_pass_filter_is_the_only_status_allowed_to_auto_filter():
    filter_usage = {EvidenceUsage.FILTER}
    assert can_auto_filter(EvidenceStatus.PASS, filter_usage) is True
    for status in (
        EvidenceStatus.WEAK,
        EvidenceStatus.SHADOW_ONLY,
        EvidenceStatus.INCOMPLETE,
        EvidenceStatus.REJECT,
    ):
        assert can_auto_filter(status, filter_usage) is False


def test_pass_rank_orders_but_never_auto_filters():
    policy = FROZEN_EVIDENCE_POLICY["P3D_MOMENTUM_RANK_GRADIENT"]
    assert can_primary_rank(policy.status, policy.allowed_usages) is True
    assert can_auto_filter(policy.status, policy.allowed_usages) is False
    assert can_tie_break(policy.status, policy.allowed_usages) is False


def test_weak_source_efficiency_is_tie_break_and_shadow_only():
    policy = FROZEN_EVIDENCE_POLICY["P3B_AC_SOURCE_EFFICIENCY"]
    assert can_tie_break(policy.status, policy.allowed_usages) is True
    assert can_emit_warning(policy.status, policy.allowed_usages) is True
    assert can_auto_filter(policy.status, policy.allowed_usages) is False
    assert can_primary_rank(policy.status, policy.allowed_usages) is False


@pytest.mark.parametrize("usage", list(EvidenceUsage))
def test_reject_evidence_cannot_be_used_for_any_purpose(usage):
    assert can_use_evidence(
        EvidenceStatus.REJECT,
        set(EvidenceUsage),
        usage,
    ) is False


@pytest.mark.parametrize("usage", list(EvidenceUsage))
def test_incomplete_evidence_cannot_be_used_for_any_purpose(usage):
    assert can_use_evidence(
        EvidenceStatus.INCOMPLETE,
        set(EvidenceUsage),
        usage,
    ) is False


@pytest.mark.parametrize("state,count", [("AT_RISK", 3), ("FAILED", 3)])
def test_persistence_states_emit_warning_only(state, count):
    warning = build_persistence_warning(state, count)
    assert warning == {
        "persistence_warning": True,
        "persistence_state": state,
        "persistence_count": count,
        "recommended_action": "MANUAL_REVIEW",
        "evidence_id": "P3A_PERSISTENCE_DIRECT_EXIT",
    }
    assert "decision" not in warning
    assert "backend_max_decision" not in warning
    assert "automatic_action" not in warning

    policy = FROZEN_EVIDENCE_POLICY["P3A_PERSISTENCE_DIRECT_EXIT"]
    assert can_emit_warning(policy.status, policy.allowed_usages) is True
    assert can_auto_filter(policy.status, policy.allowed_usages) is False


def test_non_risk_persistence_state_has_no_annotation():
    assert build_persistence_warning("HEALTHY", 10) is None
