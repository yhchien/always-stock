"""
Phase 2 §R：Signal Explain Trace。

漢翔 debug 過程中最有用的東西：不能再讓「今天 0 檔」變成一個黑盒子。每檔候選都要
能回答「這檔股票到底死在哪一關？」

`build_explain_trace()` 是純組裝函式，把 pipeline_v2 各 stage 已經算好的欄位收斂
成一份完整、可序列化（JSON-safe）的追蹤記錄。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STAGE_CANDIDATE_DISCOVERY = "candidate_discovery"
STAGE_SECTOR_CONTEXT = "sector_context"
STAGE_MOMENTUM_ELIGIBILITY = "momentum_eligibility"
STAGE_ROLE_ANNOTATION = "role_annotation"
STAGE_TRACKING_STATE = "tracking_state"
STAGE_ENTRY_STATE = "entry_state"
STAGE_HARD_EXCLUSION = "hard_exclusion"
STAGE_REGIME_GATE = "regime_gate"
STAGE_SENT_TO_LLM = "sent_to_llm"


def build_explain_trace(
    stock_id: str,
    *,
    candidate_channels: Optional[List[str]] = None,
    sector_context: Optional[Dict[str, Any]] = None,
    momentum_eligible: Optional[bool] = None,
    role: Optional[str] = None,
    role_evidence: Optional[Dict[str, Any]] = None,
    tracking_state: Optional[str] = None,
    entry_state: Optional[str] = None,
    hard_exclusion_reason: Optional[str] = None,
    regime_gate_passed: Optional[bool] = None,
    regime: Optional[str] = None,
    conviction: Optional[str] = None,
    sent_to_llm: bool = False,
) -> Dict[str, Any]:
    """組出單一 candidate 的完整決策追蹤。`final_stage` / `first_exclusion_reason`
    由最先發生的排除點決定——一旦某一關把它擋下，後面關卡就不會再跑，這裡忠實
    反映「跑到哪一關為止」，而不是假裝跑完全部關卡。"""
    final_stage = STAGE_CANDIDATE_DISCOVERY
    first_exclusion_reason: Optional[str] = None

    if momentum_eligible is False:
        final_stage = STAGE_MOMENTUM_ELIGIBILITY
        first_exclusion_reason = "base_momentum_not_eligible"
    elif hard_exclusion_reason:
        final_stage = STAGE_HARD_EXCLUSION
        first_exclusion_reason = hard_exclusion_reason
    elif regime_gate_passed is False:
        final_stage = STAGE_REGIME_GATE
        first_exclusion_reason = f"regime_excluded:{regime}"
    elif sent_to_llm:
        final_stage = STAGE_SENT_TO_LLM
    elif role is not None:
        final_stage = STAGE_ROLE_ANNOTATION

    return {
        "stock_id": stock_id,
        "candidate_channels": candidate_channels or [],
        "sector_context": sector_context,
        "momentum_eligibility": {"pass": momentum_eligible},
        "role": {"type": role, "evidence": role_evidence},
        "tracking_state": tracking_state,
        "entry_state": entry_state,
        "hard_exclusion_result": {"pass": hard_exclusion_reason is None, "reason": hard_exclusion_reason},
        "regime_gate_result": {"pass": regime_gate_passed, "regime": regime, "conviction": conviction},
        "final_stage": final_stage,
        "first_exclusion_reason": first_exclusion_reason,
    }
