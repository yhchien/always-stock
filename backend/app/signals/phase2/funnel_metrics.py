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
) -> Dict[str, Any]:
    """組出每日 funnel 統計 + 異常偵測旗標。"""
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
        "sent_to_llm_count": sent_to_llm_count,
        "watch_count": watch_count,
        "classification_survival_rate": survival_rate,
        "sector_lockout_count": len(sector_lockouts),
        "sector_lockout_sectors": sector_lockouts,
        "no_output_day": no_output_day,
        "anomaly_flags": _compute_anomaly_flags(survival_rate, sector_lockouts, sent_to_llm_count, no_output_day),
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
