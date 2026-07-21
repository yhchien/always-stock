"""
Phase 2 §N：New Discovery 與 Existing Tracking 分流。

解決台化案例：已追蹤 30 個交易日內的強勢股，不該每天重新參加「今天是不是新
LEADER」的選秀——應該問「原本的強勢邏輯還在嗎」。

沿用既有 M23 tracking_status（candidate_pool._load_tracking_status，2026-05-26）
算好的 `is_tracked` / `max_positive_return_pct` / `max_negative_return_pct` /
`failed_follow_through`，不重新查 DB；這裡只負責「continuation state」判斷。

`compute_tracking_state()` 對非追蹤股（新股）回傳 None——呼叫端用 `is None` 判斷
「這檔該走 New Discovery（roles.py）還是 Tracked Continuation（這裡）」。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.signals.phase2 import entry_state as entry_state_mod

TRACKING_ACTIVE_TREND = "ACTIVE_TREND"
TRACKING_HEALTHY_PULLBACK = "HEALTHY_PULLBACK"
TRACKING_REACCELERATING = "REACCELERATING"
TRACKING_DETERIORATING = "DETERIORATING"
TRACKING_INVALIDATED = "INVALIDATED"

# HEALTHY_PULLBACK 的最大可接受回檔（超過視為體質已受損，改判 DETERIORATING）
_HEALTHY_PULLBACK_MAX_NEGATIVE_PCT = -15.0
_SEVERE_RS_DETERIORATION = -50


def compute_tracking_state(candidate: Dict[str, Any]) -> Optional[str]:
    """
    輸入候選池 dict，需要（皆由既有 M23 tracking_status + entry_state 模組提供）：
        is_tracked, failed_follow_through, max_positive_return_pct,
        max_negative_return_pct, momentum_phase, rs_rank_improvement_5d,
        entry_state（entry_state.compute_entry_state 的輸出，非 raw distance）

    非追蹤股（新股）→ None，交給 roles.py 走 New Discovery 路徑。
    """
    if not candidate.get("is_tracked"):
        return None

    if candidate.get("failed_follow_through"):
        return TRACKING_INVALIDATED

    phase = candidate.get("momentum_phase")
    entry = candidate.get("entry_state")
    rs_improvement = candidate.get("rs_rank_improvement_5d")
    max_neg = candidate.get("max_negative_return_pct")

    structure_damaged = entry == entry_state_mod.ENTRY_STRUCTURE_DAMAGED
    severe_rs_drop_in_pullback = (
        rs_improvement is not None
        and rs_improvement <= _SEVERE_RS_DETERIORATION
        and entry in (entry_state_mod.ENTRY_DEEP_PULLBACK, entry_state_mod.ENTRY_STRUCTURE_DAMAGED)
    )
    if phase == "weakening" or structure_damaged or severe_rs_drop_in_pullback:
        return TRACKING_DETERIORATING

    if entry == entry_state_mod.ENTRY_REACCELERATING:
        return TRACKING_REACCELERATING

    if entry in (entry_state_mod.ENTRY_NORMAL_PULLBACK, entry_state_mod.ENTRY_DEEP_PULLBACK):
        if max_neg is None or max_neg > _HEALTHY_PULLBACK_MAX_NEGATIVE_PCT:
            return TRACKING_HEALTHY_PULLBACK
        return TRACKING_DETERIORATING

    return TRACKING_ACTIVE_TREND
