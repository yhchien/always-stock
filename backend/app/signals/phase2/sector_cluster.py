"""
Phase 2 §K：Sector Momentum Cluster。

解決航運全滅案例：一個產業裡有 8 檔 score 59~68、法人持續流入、多檔 RS 改善，
但沒有一檔湊滿 6/6 formal LEADER 條件——舊版 FOLLOWER 因為「同產業必須先有
formal LEADER」而全部連坐剔除。

`sector_momentum_cluster` 是產業層級（不是個股層級）的狀態，讓 FOLLOWER 的
eligibility 改成：

    formal_leader_exists_in_sector OR sector_momentum_cluster == ACTIVE

只依賴 sector_context 已經算好的 `peer_scope_used != MARKET_ONLY` 分組（沒有可信
sector 分組時 cluster 直接是 UNAVAILABLE，不臆測）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

CLUSTER_ACTIVE = "ACTIVE"
CLUSTER_NEUTRAL = "NEUTRAL"
CLUSTER_COOLING = "COOLING"
CLUSTER_FAILED = "FAILED"
CLUSTER_UNAVAILABLE = "UNAVAILABLE"

# ACTIVE 判定門檻（見 compute_sector_clusters docstring）
_ACTIVE_MIN_STRENGTH_PCT = 60.0
_ACTIVE_MIN_STRONG_STOCK_COUNT = 3
_ACTIVE_MIN_STRONG_STOCK_SCORE = 55.0
_ACTIVE_MIN_POSITIVE_FLOW_RATIO = 0.5
_FAILED_MAX_STRENGTH_PCT = 25.0
_FAILED_MAX_POSITIVE_RATIO = 0.3


def compute_sector_clusters(
    candidates: List[Dict[str, Any]],
    sector_ctx_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    對每個出現在候選池的 primary_sector 算一次 cluster 狀態。

    輸入：
        candidates：候選池（已 merge momentum frame + momentum_score）
        sector_ctx_by_id：`sector_context.compute_sector_context()` 的輸出

    輸出：primary_sector -> {
        "cluster_state": ACTIVE/NEUTRAL/COOLING/FAILED/UNAVAILABLE,
        "member_count": int,
        "strong_stock_count": int（momentum_score >= 55 的成員數）,
        "positive_flow_ratio": float | None（近 3 日法人淨買超為正的成員比例）,
        "sector_strength_percentile_20d": float | None（沿用 sector_context 的值，
            取該產業內任一成員的值，理論上同 sector 內一致）,
    }

    規則（工程決策，待 replay 校準，不得為了單一案例 hardcode）：
        - sector_context_quality == UNUSABLE（樣本不足）→ UNAVAILABLE
        - sector_strength_percentile_20d >= 60 且 (強勢股 >= 3 檔 或 法人正流入比例
          >= 0.5) → ACTIVE
        - sector_strength_percentile_20d <= 25 且法人正流入比例 <= 0.3 → FAILED
        - 其餘：sector_strength 從高轉低 → COOLING；否則 NEUTRAL（本版簡化為
          sector_strength_percentile_20d < 50 → COOLING，避免需要「前一日」狀態）
    """
    by_sector: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        sid = c.get("stock_id")
        ctx = sector_ctx_by_id.get(sid)
        if not ctx or not ctx.get("primary_sector"):
            continue
        by_sector.setdefault(ctx["primary_sector"], []).append(c)

    results: Dict[str, Dict[str, Any]] = {}
    for sector, members in by_sector.items():
        sample_ctx = sector_ctx_by_id.get(members[0].get("stock_id"), {})
        quality = sample_ctx.get("sector_context_quality")
        strength_pct = sample_ctx.get("sector_strength_percentile_20d")

        strong_count = sum(1 for c in members if (c.get("momentum_score") or 0) >= _ACTIVE_MIN_STRONG_STOCK_SCORE)

        flow_flags = [c.get("total_institution_flow_3d") for c in members if c.get("total_institution_flow_3d") is not None]
        positive_ratio = (
            sum(1 for f in flow_flags if f > 0) / len(flow_flags) if flow_flags else None
        )

        if quality == "UNUSABLE" or strength_pct is None:
            state = CLUSTER_UNAVAILABLE
        elif strength_pct >= _ACTIVE_MIN_STRENGTH_PCT and (
            strong_count >= _ACTIVE_MIN_STRONG_STOCK_COUNT
            or (positive_ratio is not None and positive_ratio >= _ACTIVE_MIN_POSITIVE_FLOW_RATIO)
        ):
            state = CLUSTER_ACTIVE
        elif strength_pct <= _FAILED_MAX_STRENGTH_PCT and (
            positive_ratio is None or positive_ratio <= _FAILED_MAX_POSITIVE_RATIO
        ):
            state = CLUSTER_FAILED
        elif strength_pct < 50.0:
            state = CLUSTER_COOLING
        else:
            state = CLUSTER_NEUTRAL

        results[sector] = {
            "cluster_state": state,
            "member_count": len(members),
            "strong_stock_count": strong_count,
            "positive_flow_ratio": round(positive_ratio, 2) if positive_ratio is not None else None,
            "sector_strength_percentile_20d": strength_pct,
        }

    return results


def get_cluster_state(sector: Optional[str], clusters: Dict[str, Dict[str, Any]]) -> str:
    if not sector or sector not in clusters:
        return CLUSTER_UNAVAILABLE
    return clusters[sector]["cluster_state"]
