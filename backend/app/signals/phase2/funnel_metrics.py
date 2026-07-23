"""
Phase 2 §S：Funnel Metrics。

7/20 那種「120 → 14 → 1 → 0」的崩潰必須能立即被看到是哪一層造成，不能等到使用者
自己發現「今天怎麼都沒有訊號」才回頭 debug。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

# 異常偵測門檻（工程起始值，待 replay 觀察後調整）
_LOW_SURVIVAL_RATE_THRESHOLD = 0.20
_SECTOR_LOCKOUT_MIN_CANDIDATES = 5  # 產業候選數 >= 此值但角色全被判 None 才算「lockout」


def compute_funnel_metrics(
    candidate_count: int,
    momentum_eligible_count: int,
    role_counts: Dict[str, int],
    hard_risk_survivor_count: int,
    regime_survivor_count: int,
    sent_to_llm_count: int,
    watch_count: int,
    sector_candidate_counts: Optional[Dict[str, int]] = None,
    sector_role_none_counts: Optional[Dict[str, int]] = None,
    hard_exclusion_reason_counts: Optional[Dict[str, int]] = None,
    hard_exclusion_version: Optional[str] = None,
    freshness_counts: Optional[Dict[str, int]] = None,
    watch_quality_counts: Optional[Dict[str, int]] = None,
    watch_quality_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """組出每日 funnel 統計 + 異常偵測旗標。

    2026-07-22 Hard Exclusion 重構（§十九「不要 silent delete」）：
    `hard_exclusion_reason_counts` 讓每個 Hard Exclusion reason 剔除了幾檔可以
    直接被看到（例：`candidate 120 → MANUAL_BLACKLIST 2 → FAILED_FOLLOW_THROUGH 3
    → ... → survivors N`），不必等使用者自己發現「今天怎麼都沒有訊號」。
    `hard_exclusion_version` 標記本次用的是哪一版 Hard Exclusion 方法論
    （見 `regime_gate.HARD_EXCLUSION_VERSION`），讓 historical snapshot 可以
    區分新舊規則產生的結果。
    """
    survival_rate = (
        round(momentum_eligible_count / candidate_count, 3) if candidate_count > 0 else 0.0
    )

    sector_lockouts: List[str] = []
    sector_candidate_counts = sector_candidate_counts or {}
    sector_role_none_counts = sector_role_none_counts or {}
    for sector, count in sector_candidate_counts.items():
        if count >= _SECTOR_LOCKOUT_MIN_CANDIDATES and sector_role_none_counts.get(sector, 0) == count:
            sector_lockouts.append(sector)

    no_output_day = candidate_count > 0 and watch_count == 0

    return {
        "candidate_count": candidate_count,
        "momentum_eligible_count": momentum_eligible_count,
        "role_counts": dict(role_counts),
        "hard_risk_survivor_count": hard_risk_survivor_count,
        "regime_survivor_count": regime_survivor_count,
        # Phase 2.5：regime gate 通過的合格候選數（尚未經 Watch Quality 過濾），
        # 與 `sent_to_llm_count`（quality 過濾後真正送 LLM 的數量）對照可看出
        # Momentum Freshness + Final Watch Quality Layer 這一關擋掉了幾檔。
        "after_regime_count": regime_survivor_count,
        "sent_to_llm_count": sent_to_llm_count,
        "watch_count": watch_count,
        "classification_survival_rate": survival_rate,
        "sector_lockout_count": len(sector_lockouts),
        "sector_lockout_sectors": sector_lockouts,
        "no_output_day": no_output_day,
        "anomaly_flags": _compute_anomaly_flags(survival_rate, sector_lockouts, sent_to_llm_count, no_output_day),
        "hard_exclusion_reason_counts": dict(hard_exclusion_reason_counts or {}),
        "hard_exclusion_version": hard_exclusion_version,
        # Phase 2.5（momentum freshness + watch quality layer）
        "freshness_counts": dict(freshness_counts or {}),
        "watch_quality_counts": dict(watch_quality_counts or {}),
        "watch_quality_mode": watch_quality_mode,
    }


def _compute_anomaly_flags(
    survival_rate: float,
    sector_lockouts: List[str],
    sent_to_llm_count: int,
    no_output_day: bool,
) -> List[str]:
    flags = []
    if survival_rate < _LOW_SURVIVAL_RATE_THRESHOLD:
        flags.append("classification_survival_low")
    if sector_lockouts:
        flags.append("sector_lockout_detected")
    if sent_to_llm_count == 0:
        flags.append("sent_to_llm_zero")
    if no_output_day:
        flags.append("no_output_day")
    return flags


def role_counts_from_results(role_results: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """從 roles.classify_roles() 的輸出算 role 分布統計（None 也算一類）。"""
    counter = Counter(r.get("role") or "NONE" for r in role_results.values())
    return dict(counter)
