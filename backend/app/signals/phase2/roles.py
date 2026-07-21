"""
Phase 2 §G/§H/§I/§L/§M：Role Annotation（取代舊版「三選一否則 DELETE」）。

核心改變：
    - candidate qualification（base momentum eligibility）與 role annotation 分離
      （§G）——role 判定不到不代表死亡，落到 UNCLASSIFIED_MOMENTUM 繼續往下走
      risk / regime
    - LEADER 不再是 6/6 ALL-AND，改成 evidence-count（§I）
    - 新增 INDEPENDENT_LEADER（§L）：sector context 不可用或 sector 本身不強，
      但個股 market-relative momentum 極強時的路徑（解漢翔）
    - 新增 EMERGING_MOMENTUM（§M）：RS 排名快速改善但尚未到頂的路徑（解台虹一類
      「還沒被市場定價完」的股票）

**門檻是工程起始值，非最終校準值**：所有 threshold 都要在 shadow replay
（60~120 個交易日）驗證後才能視為穩定，不得只為了讓特定案例過關而硬調
（spec §Y.16「Tune only if supported by replay」）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.phase2 import sector_cluster as cluster_mod
from app.signals.phase2 import entry_state as entry_state_mod

ROLE_SECTOR_LEADER = "SECTOR_LEADER"
ROLE_CO_LEADER = "CO_LEADER"
ROLE_INDEPENDENT_LEADER = "INDEPENDENT_LEADER"
ROLE_SECTOR_FOLLOWER = "SECTOR_FOLLOWER"
ROLE_ROTATION_LAGGARD = "ROTATION_LAGGARD"
ROLE_EMERGING_MOMENTUM = "EMERGING_MOMENTUM"
ROLE_UNCLASSIFIED_MOMENTUM = "UNCLASSIFIED_MOMENTUM"

_FORMAL_LEADER_ROLES = (ROLE_SECTOR_LEADER, ROLE_CO_LEADER, ROLE_INDEPENDENT_LEADER)

# Base eligibility（§G）：比 legacy 六條件寬鬆很多，只濾掉明確弱勢/派發的股票
_BASE_SCORE_MIN = 50.0
_BASE_MARKET_RS_MIN = 40.0

# Leader evidence 門檻
_LEADER_SECTOR_STRENGTH_MIN = 70.0
_LEADER_PEER_RS_MIN = 80.0
_LEADER_MOMENTUM_SCORE_MIN = 70.0
_LEADER_BUY_DAYS_MIN = 2
_LEADER_INST_PCT_MIN = 80.0
_LEADER_VOLUME_RATIO_MIN = 1.3
_ENTRY_STATE_INTACT = (None, entry_state_mod.ENTRY_NEAR_HIGH, entry_state_mod.ENTRY_NORMAL_PULLBACK, entry_state_mod.ENTRY_REACCELERATING)

_SECTOR_LEADER_EVIDENCE_MIN = 5
_CO_LEADER_EVIDENCE_MIN = 4

# Independent leader（sector 不可用時的替代路徑）
_INDEPENDENT_MARKET_RS_MIN = 90.0
_INDEPENDENT_MOMENTUM_SCORE_MIN = 75.0
_INDEPENDENT_EVIDENCE_MIN = 3
_INDEPENDENT_EVIDENCE_KEYS = (
    "peer_rs_strong", "momentum_score_high", "institution_confirmed",
    "volume_confirmed", "price_structure_intact",
)

# Follower / Laggard / Emerging
_FOLLOWER_SCORE_MIN = 40.0
_LAGGARD_SECTOR_STRENGTH_MIN = 70.0
_LAGGARD_PEER_RS_MAX = 50.0
_EMERGING_RS_IMPROVEMENT_MIN = 30
_EMERGING_MIN_SCORE = 45.0


def is_base_momentum_eligible(candidate: Dict[str, Any]) -> bool:
    """§G Base Momentum Eligibility：足夠資格繼續往下走 role/risk/regime，
    不代表任何角色資格。

    **不檢查 `distribution` soft hint**（2026-07-21 起，跟 `regime_gate.py` 的
    決定保持一致）：那個訊號在大盤系統性下跌日雜訊很大，會誤殺長上影的強勢股
    （見台化/台虹/長榮/萬海/台塑化 7/20 replay 案例）。降級為只影響
    `regime_gate.compute_conviction()` 的信心度，不在這裡當資格門檻——否則
    等於繞過 regime_gate 那邊已經做的降級決定，從另一個入口把它變回硬剔除。
    """
    score = candidate.get("momentum_score")
    market_rs = candidate.get("rs_market_percentile_20d")
    phase = candidate.get("momentum_phase")

    if score is None or score < _BASE_SCORE_MIN:
        return False
    if market_rs is None or market_rs < _BASE_MARKET_RS_MIN:
        return False
    if phase == "weakening":
        return False
    return True


def compute_leader_evidence(candidate: Dict[str, Any], sector_ctx: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """§I：六項 leader evidence，回傳 count + 明細（不是 AND，是計數）。"""
    ctx = sector_ctx or {}
    sector_strength = ctx.get("sector_strength_percentile_20d")
    peer_rs = ctx.get("peer_rs_percentile_20d")

    buy_days = candidate.get("consecutive_buy_days_3d")
    inst_pct = candidate.get("inst_buy_to_turnover_percentile_2d")
    vol_ratio = candidate.get("volume_5d_to_60d_ratio")
    entry_state = candidate.get("entry_state")

    detail = {
        "sector_strength_strong": sector_strength is not None and sector_strength >= _LEADER_SECTOR_STRENGTH_MIN,
        "peer_rs_strong": peer_rs is not None and peer_rs >= _LEADER_PEER_RS_MIN,
        "momentum_score_high": (candidate.get("momentum_score") or 0) >= _LEADER_MOMENTUM_SCORE_MIN,
        "institution_confirmed": (
            (buy_days is not None and buy_days >= _LEADER_BUY_DAYS_MIN)
            or (inst_pct is not None and inst_pct >= _LEADER_INST_PCT_MIN)
        ),
        "volume_confirmed": vol_ratio is not None and vol_ratio >= _LEADER_VOLUME_RATIO_MIN,
        "price_structure_intact": entry_state in _ENTRY_STATE_INTACT,
    }
    return {"count": sum(1 for v in detail.values() if v), "detail": detail}


def _classify_leader_tier(
    candidate: Dict[str, Any],
    sector_ctx: Optional[Dict[str, Any]],
    evidence: Dict[str, Any],
) -> Optional[str]:
    ctx = sector_ctx or {}
    sector_usable = ctx.get("sector_context_quality") not in (None, "UNUSABLE")
    market_rs = candidate.get("rs_market_percentile_20d") or 0
    score = candidate.get("momentum_score") or 0

    if sector_usable and evidence["count"] >= _SECTOR_LEADER_EVIDENCE_MIN:
        return ROLE_SECTOR_LEADER
    if sector_usable and evidence["count"] >= _CO_LEADER_EVIDENCE_MIN:
        return ROLE_CO_LEADER

    # §L INDEPENDENT_LEADER：sector 不可用、或 sector 本身不強，
    # 但個股 market-relative momentum 極強 + 足夠獨立確認
    sector_blocks_formal_leader = (not sector_usable) or not evidence["detail"].get("sector_strength_strong")
    if sector_blocks_formal_leader and market_rs >= _INDEPENDENT_MARKET_RS_MIN and score >= _INDEPENDENT_MOMENTUM_SCORE_MIN:
        non_sector_count = sum(1 for k in _INDEPENDENT_EVIDENCE_KEYS if evidence["detail"].get(k))
        if non_sector_count >= _INDEPENDENT_EVIDENCE_MIN:
            return ROLE_INDEPENDENT_LEADER

    return None


def classify_roles(
    candidates: List[Dict[str, Any]],
    sector_ctx_by_id: Dict[str, Dict[str, Any]],
    clusters: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    兩階段分類：
        Pass 1：base eligibility + leader evidence → 找出 SECTOR_LEADER / CO_LEADER /
                INDEPENDENT_LEADER
        Pass 2：其餘 base-eligible 候選 → SECTOR_FOLLOWER / ROTATION_LAGGARD /
                EMERGING_MOMENTUM / UNCLASSIFIED_MOMENTUM
                （§K：FOLLOWER 不再要求「同 sector 一定要有 formal leader」，
                sector_momentum_cluster == ACTIVE 也可以打開這條路）

    base_eligible=False 的候選 **不在這裡被剔除**——呼叫端（pipeline_v2）決定
    是否要在更早的 filter 階段就不送進 role classification；本函式仍會回傳
    role=None 讓 explain trace 記錄「為何沒有角色」。
    """
    clusters = clusters or {}
    results: Dict[str, Dict[str, Any]] = {}
    leader_by_sector: Dict[str, List[str]] = {}

    for c in candidates:
        sid = c["stock_id"]
        ctx = sector_ctx_by_id.get(sid)
        base_eligible = is_base_momentum_eligible(c)
        if not base_eligible:
            results[sid] = {
                "role": None,
                "base_eligible": False,
                "evidence_count": None,
                "evidence_detail": None,
            }
            continue

        evidence = compute_leader_evidence(c, ctx)
        role = _classify_leader_tier(c, ctx, evidence)
        results[sid] = {
            "role": role,
            "base_eligible": True,
            "evidence_count": evidence["count"],
            "evidence_detail": evidence["detail"],
        }
        if role in _FORMAL_LEADER_ROLES:
            sector = (ctx or {}).get("primary_sector")
            if sector:
                leader_by_sector.setdefault(sector, []).append(sid)

    for c in candidates:
        sid = c["stock_id"]
        r = results[sid]
        if r["role"] is not None or not r["base_eligible"]:
            continue

        ctx = sector_ctx_by_id.get(sid) or {}
        sector = ctx.get("primary_sector")
        cluster_state = cluster_mod.get_cluster_state(sector, clusters)
        has_formal_leader = bool(leader_by_sector.get(sector))
        follower_eligible = has_formal_leader or cluster_state == cluster_mod.CLUSTER_ACTIVE

        rs_improvement = c.get("rs_rank_improvement_5d")
        score = c.get("momentum_score") or 0
        peer_rs = ctx.get("peer_rs_percentile_20d")
        sector_strength = ctx.get("sector_strength_percentile_20d")

        is_lagging_strong_sector = (
            follower_eligible
            and sector_strength is not None and sector_strength >= _LAGGARD_SECTOR_STRENGTH_MIN
            and peer_rs is not None and peer_rs < _LAGGARD_PEER_RS_MAX
            and rs_improvement is not None and rs_improvement > 0
        )
        is_follower = (
            follower_eligible
            and _FOLLOWER_SCORE_MIN <= score < _LEADER_MOMENTUM_SCORE_MIN
            and (rs_improvement is None or rs_improvement >= 0)
        )
        is_emerging = (
            rs_improvement is not None and rs_improvement >= _EMERGING_RS_IMPROVEMENT_MIN
            and score >= _EMERGING_MIN_SCORE
        )

        # ROTATION_LAGGARD 檢查優先於 FOLLOWER：兩者條件可能重疊（score 落在
        # follower 區間同時 peer_rs 落後），LAGGARD 是更明確的次型態
        # （強產業 + 個股落後 + RS 改善中），應該先辨識出來
        if is_lagging_strong_sector:
            role = ROLE_ROTATION_LAGGARD
        elif is_follower:
            role = ROLE_SECTOR_FOLLOWER
        elif is_emerging:
            role = ROLE_EMERGING_MOMENTUM
        else:
            role = ROLE_UNCLASSIFIED_MOMENTUM

        r["role"] = role
        r["follower_eligible"] = follower_eligible
        r["cluster_state"] = cluster_state

    return results
