"""Frozen Phase 3 evidence policy used by production selection guardrails.

This module deliberately contains only the experiments that production code or
its policy tests need to reference.  Research CSV/Markdown artifacts remain
offline inputs and are not runtime dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Collection, Dict, FrozenSet, Mapping, Optional


class EvidenceStatus(str, Enum):
    PASS = "PASS"
    WEAK = "WEAK"
    SHADOW_ONLY = "SHADOW_ONLY"
    REJECT = "REJECT"
    INCOMPLETE = "INCOMPLETE"


class EvidenceUsage(str, Enum):
    FILTER = "FILTER"
    RANK = "RANK"
    TIE_BREAK = "TIE_BREAK"
    SHADOW_INFORMATION = "SHADOW_INFORMATION"
    UI_WARNING = "UI_WARNING"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NONE = "NONE"


@dataclass(frozen=True)
class FrozenEvidencePolicy:
    status: EvidenceStatus
    allowed_usages: FrozenSet[EvidenceUsage]


FROZEN_EVIDENCE_POLICY: Mapping[str, FrozenEvidencePolicy] = MappingProxyType({
    "P3D_MOMENTUM_RANK_GRADIENT": FrozenEvidencePolicy(
        status=EvidenceStatus.PASS,
        allowed_usages=frozenset({EvidenceUsage.RANK}),
    ),
    "P3B_SOURCE_GROUP_AUTO_REMOVAL": FrozenEvidencePolicy(
        status=EvidenceStatus.REJECT,
        allowed_usages=frozenset(),
    ),
    "P3B_AC_SOURCE_EFFICIENCY": FrozenEvidencePolicy(
        status=EvidenceStatus.WEAK,
        allowed_usages=frozenset({
            EvidenceUsage.TIE_BREAK,
            EvidenceUsage.SHADOW_INFORMATION,
        }),
    ),
    "P3A_PERSISTENCE_DIRECT_EXIT": FrozenEvidencePolicy(
        status=EvidenceStatus.SHADOW_ONLY,
        allowed_usages=frozenset({
            EvidenceUsage.SHADOW_INFORMATION,
            EvidenceUsage.UI_WARNING,
            EvidenceUsage.MANUAL_REVIEW,
        }),
    ),
})


def can_use_evidence(
    status: EvidenceStatus,
    allowed_usages: Collection[EvidenceUsage],
    usage: EvidenceUsage,
) -> bool:
    """Return whether frozen evidence may be used for one explicit purpose."""
    usages = frozenset(allowed_usages)
    if status in {EvidenceStatus.REJECT, EvidenceStatus.INCOMPLETE}:
        return False
    if usage is EvidenceUsage.NONE or usage not in usages:
        return False
    if usage in {EvidenceUsage.FILTER, EvidenceUsage.RANK}:
        return status is EvidenceStatus.PASS
    if usage is EvidenceUsage.TIE_BREAK:
        return status in {EvidenceStatus.PASS, EvidenceStatus.WEAK}
    return status in {
        EvidenceStatus.PASS,
        EvidenceStatus.WEAK,
        EvidenceStatus.SHADOW_ONLY,
    }


def can_auto_filter(
    status: EvidenceStatus,
    allowed_usages: Collection[EvidenceUsage],
) -> bool:
    return can_use_evidence(status, allowed_usages, EvidenceUsage.FILTER)


def can_primary_rank(
    status: EvidenceStatus,
    allowed_usages: Collection[EvidenceUsage],
) -> bool:
    return can_use_evidence(status, allowed_usages, EvidenceUsage.RANK)


def can_tie_break(
    status: EvidenceStatus,
    allowed_usages: Collection[EvidenceUsage],
) -> bool:
    return can_use_evidence(status, allowed_usages, EvidenceUsage.TIE_BREAK)


def can_emit_warning(
    status: EvidenceStatus,
    allowed_usages: Collection[EvidenceUsage],
) -> bool:
    return (
        can_use_evidence(status, allowed_usages, EvidenceUsage.SHADOW_INFORMATION)
        or can_use_evidence(status, allowed_usages, EvidenceUsage.UI_WARNING)
    )


_PERSISTENCE_RISK_STATES = frozenset({"AT_RISK", "FAILED"})
_PERSISTENCE_EVIDENCE_ID = "P3A_PERSISTENCE_DIRECT_EXIT"


def build_persistence_warning(
    state: Optional[str],
    count: int,
) -> Optional[Dict[str, Any]]:
    """Build a warning-only annotation; never return an automatic decision."""
    normalized_state = str(state or "").strip().upper()
    policy = FROZEN_EVIDENCE_POLICY[_PERSISTENCE_EVIDENCE_ID]
    if normalized_state not in _PERSISTENCE_RISK_STATES:
        return None
    if not can_emit_warning(policy.status, policy.allowed_usages):
        return None
    return {
        "persistence_warning": True,
        "persistence_state": normalized_state,
        "persistence_count": max(0, int(count)),
        "recommended_action": EvidenceUsage.MANUAL_REVIEW.value,
        "evidence_id": _PERSISTENCE_EVIDENCE_ID,
    }
