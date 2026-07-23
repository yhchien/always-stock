"""
Phase 2.5 §14~§28：Final Watch Quality Layer。

核心區分（§三，不可混在一起）：
    Hard Exclusion        = 真正失效（`regime_gate.py` 六大類，本次不動）
    Base Momentum Eligible = 值得繼續研究（`roles.is_base_momentum_eligible`，本次不動）
    Watch Quality          = 值得進「正式」魚尾 WATCH（本模組新增）

`compute_watch_quality()` 在 Regime Gate 存活者身上再疊一層：不是每個「合格候選」
都該變成「正式 WATCH」，用 7 個獨立 evidence family（§25）+ freshness state + role/
tracking_state 作為輔助調整（不是唯一決定因素，§19）判斷 READY / SETUP / RESERVE。

RESERVE **不是失敗**（§17/§37）：只代表「今天證據不足以進正式 WATCH」，明天證據
改善可以重新升級（§38，逐日重算，天然支援 re-entry，無需額外狀態）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import momentum_freshness as freshness_mod
from app.signals.phase2 import roles as roles_mod
from app.signals.phase2 import sector_cluster as cluster_mod
from app.signals.phase2 import tracking_state as tracking_mod

WATCH_QUALITY_READY = "READY"
WATCH_QUALITY_SETUP = "SETUP"
WATCH_QUALITY_RESERVE = "RESERVE"

ALL_WATCH_QUALITY_STATES = (WATCH_QUALITY_READY, WATCH_QUALITY_SETUP, WATCH_QUALITY_RESERVE)

# Evidence family 門檻（工程起始值，待 replay 校準；沿用既有 roles.py leader 門檻
# 維持跨模組一致性，非本模組獨創新 cliff）
_MOMENTUM_SCORE_STRONG = 70.0
_RS_MARKET_STRONG = 85.0
_PEER_RS_STRONG = 70.0
_INDEPENDENT_RS_STRONG = 90.0  # sector context 不可用時的替代門檻（同 roles._INDEPENDENT_MARKET_RS_MIN）
_VOLUME_PARTICIPATION_MIN = 1.3  # 同 roles._LEADER_VOLUME_RATIO_MIN
_INSTITUTION_BUY_DAYS_MIN = 2

_READY_MIN_EVIDENCE = 4
_SETUP_MIN_EVIDENCE = 2
_EMERGING_HIGH_QUALITY_MIN_EVIDENCE = 3
_UNCLASSIFIED_STRONG_MIN_EVIDENCE = 5

_FRESHNESS_READY_STATES = (freshness_mod.FRESH_STRONG, freshness_mod.FRESH_STABLE)
_FRESHNESS_SETUP_STATES = (
    freshness_mod.FRESH_STRONG,
    freshness_mod.FRESH_STABLE,
    freshness_mod.HEALTHY_PULLBACK,
)


def _quality_evidence(
    candidate: Dict[str, Any],
    freshness_result: Dict[str, Any],
    sector_ctx: Optional[Dict[str, Any]],
    cluster_state: Optional[str],
) -> Dict[str, bool]:
    """§25：7 個獨立 evidence family，避免同源重複（同一份底層資料只計一次票）。"""
    ctx = sector_ctx or {}
    momentum_score = candidate.get("momentum_score")
    rs_market = candidate.get("rs_market_percentile_20d")
    peer_rs = ctx.get("peer_rs_percentile_20d")
    sector_quality = ctx.get("sector_context_quality")
    det_signals = candidate.get("deterministic_signals") or {}
    inst_momentum = det_signals.get("institution_flow_momentum")
    sector_status = det_signals.get("sector_rotation_status")
    buy_days = candidate.get("consecutive_buy_days_3d") or 0
    vol_ratio = candidate.get("volume_5d_to_60d_ratio")
    entry_state = candidate.get("entry_state")
    fresh_evidence = freshness_result.get("momentum_freshness_evidence") or {}

    # RELATIVE_STRENGTH：sector context 可用時用 peer_rs（同產業內排名）；
    # 不可用時 fallback 用極強的 market RS 當獨立確認（呼應 INDEPENDENT_LEADER 邏輯，
    # 不是重新發明門檻，只是複用同一把尺）
    if sector_quality not in (None, "UNUSABLE") and peer_rs is not None:
        relative_strength = peer_rs >= _PEER_RS_STRONG
    else:
        relative_strength = rs_market is not None and rs_market >= _INDEPENDENT_RS_STRONG

    return {
        "MOMENTUM_STRENGTH": (
            (momentum_score is not None and momentum_score >= _MOMENTUM_SCORE_STRONG)
            or (rs_market is not None and rs_market >= _RS_MARKET_STRONG)
        ),
        "FRESHNESS": freshness_result.get("momentum_freshness") in _FRESHNESS_SETUP_STATES,
        "RELATIVE_STRENGTH": relative_strength,
        "PARTICIPATION": (
            (vol_ratio is not None and vol_ratio >= _VOLUME_PARTICIPATION_MIN)
            or bool(fresh_evidence.get("volume_confirms_strength"))
        ),
        "SECTOR_CONFIRMATION": (
            sector_status == "inflow" or cluster_state == cluster_mod.CLUSTER_ACTIVE
        ),
        "INSTITUTION_CONFIRMATION": (
            inst_momentum in ("accelerating", "stable") or buy_days >= _INSTITUTION_BUY_DAYS_MIN
        ),
        "PRICE_STRUCTURE": (
            entry_state
            in (
                entry_state_mod.ENTRY_NEAR_HIGH,
                entry_state_mod.ENTRY_NORMAL_PULLBACK,
                entry_state_mod.ENTRY_REACCELERATING,
            )
            and not fresh_evidence.get("close_weak")
        ),
    }


def _has_severe_risk_combo(risk_warnings: List[str]) -> bool:
    """§28：risk warning 只降級品質，不直接排除；但兩個獨立訊號同時出現時，
    視為足夠嚴重的組合，限制不可到 READY（仍可 SETUP）。"""
    warnings = set(risk_warnings or [])
    severe_pairs = (
        {"INSTITUTION_REVERSAL_WARNING", "EXTENDED_PROFIT_TAKING_WARNING"},
        {"INSTITUTION_REVERSAL_WARNING", "LOW_RAW_VOLUME"},
    )
    return any(pair.issubset(warnings) for pair in severe_pairs)


def compute_watch_quality(
    candidate: Dict[str, Any],
    freshness_result: Dict[str, Any],
    *,
    sector_ctx: Optional[Dict[str, Any]] = None,
    cluster_state: Optional[str] = None,
) -> Dict[str, Any]:
    """回傳 `{"watch_quality_state": READY|SETUP|RESERVE, "watch_quality_score": 0~100,
    "quality_evidence": {...}, "quality_reasons": [...]}`。

    `watch_quality_score` 只用於排序 / debug / LLM priority（§26），不是唯一 eligibility
    依據——真正決定 READY/SETUP/RESERVE 的是 state machine（§27），score 只是附帶輸出。
    """
    evidence = _quality_evidence(candidate, freshness_result, sector_ctx, cluster_state)
    evidence_count = sum(1 for v in evidence.values() if v)
    score = round(100.0 * evidence_count / len(evidence), 1)

    freshness = freshness_result.get("momentum_freshness")
    role = candidate.get("role")
    tracking = candidate.get("tracking_state")
    risk_warnings = candidate.get("risk_warnings") or []
    severe_combo = _has_severe_risk_combo(risk_warnings)

    reasons: List[str] = []

    # §23：Tracked stock 走 tracking_state，優先於一般 role 邏輯（DETERIORATING 已在
    # regime gate 前一輪多半被排除，這裡仍防禦性處理，避免漏網之魚被誤判 READY）
    if tracking == tracking_mod.TRACKING_DETERIORATING:
        state = WATCH_QUALITY_RESERVE
        reasons.append("TRACKED_DETERIORATING")
    elif tracking == tracking_mod.TRACKING_HEALTHY_PULLBACK:
        state = WATCH_QUALITY_SETUP if evidence_count >= _SETUP_MIN_EVIDENCE else WATCH_QUALITY_RESERVE
        reasons.append("TRACKED_HEALTHY_PULLBACK")
    elif tracking in (tracking_mod.TRACKING_ACTIVE_TREND, tracking_mod.TRACKING_REACCELERATING):
        if freshness in _FRESHNESS_READY_STATES and evidence_count >= _READY_MIN_EVIDENCE and not severe_combo:
            state = WATCH_QUALITY_READY
        elif evidence_count >= _SETUP_MIN_EVIDENCE:
            state = WATCH_QUALITY_SETUP
        else:
            state = WATCH_QUALITY_RESERVE
        reasons.append(f"TRACKED_{tracking}")
    # §21：EMERGING_MOMENTUM 預設 RESERVE，只有證據足夠強才升級（不可只因排名進步）
    elif role == roles_mod.ROLE_EMERGING_MOMENTUM:
        if evidence_count >= _EMERGING_HIGH_QUALITY_MIN_EVIDENCE and freshness in _FRESHNESS_SETUP_STATES:
            state = WATCH_QUALITY_SETUP if evidence_count < _READY_MIN_EVIDENCE else WATCH_QUALITY_READY
            reasons.append("EMERGING_HIGH_QUALITY_CONFIRMED")
        else:
            state = WATCH_QUALITY_RESERVE
            reasons.append("EMERGING_INSUFFICIENT_CONFIRMATION")
    # §20：UNCLASSIFIED_MOMENTUM 預設 RESERVE，只有非常強的獨立證據才升級
    elif role == roles_mod.ROLE_UNCLASSIFIED_MOMENTUM:
        if evidence_count >= _UNCLASSIFIED_STRONG_MIN_EVIDENCE and freshness in _FRESHNESS_READY_STATES:
            state = WATCH_QUALITY_READY if not severe_combo else WATCH_QUALITY_SETUP
            reasons.append("UNCLASSIFIED_STRONG_INDEPENDENT_EVIDENCE")
        elif evidence_count >= _EMERGING_HIGH_QUALITY_MIN_EVIDENCE:
            state = WATCH_QUALITY_SETUP
            reasons.append("UNCLASSIFIED_MODERATE_EVIDENCE")
        else:
            state = WATCH_QUALITY_RESERVE
            reasons.append("UNCLASSIFIED_INSUFFICIENT_EVIDENCE")
    # §22：SECTOR_FOLLOWER 不得只因 sector cluster ACTIVE 就 READY，仍要看
    # freshness/participation，避免只是 sector beta 的弱 follower
    elif role == roles_mod.ROLE_SECTOR_FOLLOWER:
        if freshness in _FRESHNESS_READY_STATES and evidence_count >= _READY_MIN_EVIDENCE and not severe_combo:
            state = WATCH_QUALITY_READY
        elif freshness in _FRESHNESS_SETUP_STATES and evidence_count >= _SETUP_MIN_EVIDENCE:
            state = WATCH_QUALITY_SETUP
        else:
            state = WATCH_QUALITY_RESERVE
            reasons.append("FOLLOWER_WEAK_PARTICIPATION")
    # formal leader（SECTOR_LEADER/CO_LEADER/INDEPENDENT_LEADER）與 ROTATION_LAGGARD：
    # 一般 evidence + freshness state machine（role 本身已通過較嚴格的資格條件，
    # 但仍不是 READY 的自動保證——§19 禁止 role → automatic READY）
    else:
        if freshness in _FRESHNESS_READY_STATES and evidence_count >= _READY_MIN_EVIDENCE and not severe_combo:
            state = WATCH_QUALITY_READY
        elif freshness in _FRESHNESS_SETUP_STATES and evidence_count >= _SETUP_MIN_EVIDENCE:
            state = WATCH_QUALITY_SETUP
        else:
            state = WATCH_QUALITY_RESERVE

    # §24：EXTENDED（近 3 日漲多）不等於失敗——不因 EXTENDED_3D 自動降級，只在
    # reasons 附註 entry risk 供 debug / LLM 理解，state 仍由上面 evidence 決定
    if "EXTENDED_3D" in risk_warnings and state in (WATCH_QUALITY_READY, WATCH_QUALITY_SETUP):
        reasons.append("EXTENDED_ENTRY_RISK")
    if freshness == freshness_mod.FRESH_STRONG and "STRONG_MOMENTUM" not in reasons:
        reasons.insert(0, "STRONG_MOMENTUM")
    elif freshness == freshness_mod.STALE:
        reasons.append("STALE_MOMENTUM")
    elif freshness == freshness_mod.DETERIORATING:
        reasons.append("DETERIORATING_MOMENTUM")

    return {
        "watch_quality_state": state,
        "watch_quality_score": score,
        "quality_evidence": evidence,
        "quality_reasons": reasons,
    }
