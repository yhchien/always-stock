"""
Phase 2 整合入口：把 §D~§S 的模組串成完整 shadow pipeline。

**Candidate Discovery 沿用 legacy**（`candidate_pool.ingest_data/compute_rankings/
build_candidate_pool` + `momentum.compute_market_momentum_frame`）——Phase 2 不
重新定義候選池怎麼來，只改變候選池建好之後「怎麼判斷角色、怎麼過 risk/regime」。
這也是為什麼這個 pipeline 可以在 shadow mode 下對歷史任何一天重跑：候選池本身
是 deterministic 的，只要 `daily_price`/`inst_stock_flow`/… 歷史資料還在 DB 裡，
就可以完整重建。

流程（spec §G）：
    1. Candidate Discovery（legacy candidate_pool，不變）
    2. Canonical Sector Context（sector_context.py）
    3. Base Momentum Eligibility + Entry State（roles.py + entry_state.py）
    4. New vs Tracked 分流（tracking_state.py）
    5. Sector Momentum Cluster（sector_cluster.py）
    6. Role Annotation（roles.py，非 tracked 候選才走這條）
    7. True Hard Exclusion + Regime Gate（regime_gate.py）
    8. Explain Trace + Funnel Metrics（explain_trace.py / funnel_metrics.py）

**這個模組完全不呼叫 LLM**——shadow replay 只驗證 deterministic 決策層，
LLM 解釋層留到 §X（v6 prompt）穩定後才接。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import SecurityClassification
from app.signals import deterministic_signals as det_signals
from app.signals import filters as legacy_filters
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import explain_trace as trace_mod
from app.signals.phase2 import funnel_metrics as funnel_mod
from app.signals.phase2 import regime_gate as regime_mod
from app.signals.phase2 import roles as roles_mod
from app.signals.phase2 import sector_cluster as cluster_mod
from app.signals.phase2 import sector_context as sector_ctx_mod
from app.signals.phase2 import tracking_state as tracking_mod

PIPELINE_VERSION = "phase2-v1"

# §U（2026-07-22）：production cutover 的 LLM 相容層。
#
# Phase 2 的候選（`run_phase2_pipeline` 存活者）沒有 legacy `classification.
# classify_stocks()` 產生的 `prelim_type` 欄位——role 分類是 evidence-count-based
# 七種角色（見 roles.py）+ tracking_state 五種延續狀態（見 tracking_state.py），
# 兩者都不是舊版的 LEADER/FOLLOWER/ROTATION_LAGGARD 三選一。但既有 LLM prompt /
# `llm_caller._normalize_prelim_type()` / 前端 `SignalTypeChip` 都只認
# LEADER/FOLLOWER/LAGGARD 三種 `type`；要讓 Phase 2 候選能真的餵給既有 LLM 管線
# 並產生前端相容的輸出，需要把新角色**映射**回舊三分類，而不是重寫整條契約
# （那是刻意保留給 §X v6 prompt 的範圍，本次不做）。
#
# 映射原則（工程判斷，非 spec 硬性規定，需 replay/production 觀察後再校準）：
#   - 三種 formal leader（SECTOR_LEADER/CO_LEADER/INDEPENDENT_LEADER）→ LEADER
#   - SECTOR_FOLLOWER → FOLLOWER
#   - ROTATION_LAGGARD → "ROTATION_LAGGARD"（`_normalize_prelim_type` 既有邏輯
#     會把這個字串再映射成 LAGGARD，維持與 legacy 完全一致的行為）
#   - EMERGING_MOMENTUM（RS 快速改善但尚未確立）→ FOLLOWER 桶：這類股票不是
#     「產業已有龍頭、我在跟」，而是「自己正在被市場重新定價」，用 FOLLOWER
#     桶只是借用同一組敘事層級（非龍頭），LLM reason 仍會依 evidence 具體描述
#   - UNCLASSIFIED_MOMENTUM（符合基本動能資格但角色不明確）→ LAGGARD 桶：
#     最保守分類，避免灌水成 LEADER/FOLLOWER
#   - 已追蹤股（role=None，改用 tracking_state 代表分類）：
#       ACTIVE_TREND / REACCELERATING → LEADER；HEALTHY_PULLBACK → FOLLOWER；
#       DETERIORATING / INVALIDATED → LAGGARD（理論上這兩態多半已在 regime gate
#       被排除，這裡只是防禦性 fallback）
_ROLE_TO_PRELIM_TYPE = {
    roles_mod.ROLE_SECTOR_LEADER: "LEADER",
    roles_mod.ROLE_CO_LEADER: "LEADER",
    roles_mod.ROLE_INDEPENDENT_LEADER: "LEADER",
    roles_mod.ROLE_SECTOR_FOLLOWER: "FOLLOWER",
    roles_mod.ROLE_ROTATION_LAGGARD: "ROTATION_LAGGARD",
    roles_mod.ROLE_EMERGING_MOMENTUM: "FOLLOWER",
    roles_mod.ROLE_UNCLASSIFIED_MOMENTUM: "LAGGARD",
}
_TRACKING_STATE_TO_PRELIM_TYPE = {
    tracking_mod.TRACKING_ACTIVE_TREND: "LEADER",
    tracking_mod.TRACKING_REACCELERATING: "LEADER",
    tracking_mod.TRACKING_HEALTHY_PULLBACK: "FOLLOWER",
    tracking_mod.TRACKING_DETERIORATING: "LAGGARD",
    tracking_mod.TRACKING_INVALIDATED: "LAGGARD",
}


def role_to_prelim_type(candidate: Dict[str, Any]) -> str:
    """把 Phase 2 的 `role` / `tracking_state` 映射回 legacy LLM 契約需要的
    `prelim_type`（LEADER / FOLLOWER / ROTATION_LAGGARD）。

    給 `pipeline.py` 在 `SIGNALS_PIPELINE_MODE=phase2` 時，把 Phase 2 存活者
    餵進既有 `llm_caller` 之前呼叫一次，設定 `candidate["prelim_type"]`。
    """
    role = candidate.get("role")
    if role in _ROLE_TO_PRELIM_TYPE:
        return _ROLE_TO_PRELIM_TYPE[role]
    tracking = candidate.get("tracking_state")
    return _TRACKING_STATE_TO_PRELIM_TYPE.get(tracking, "LAGGARD")


def build_phase2_pool(
    pool: List[Dict[str, Any]],
    *,
    taiex_return_1d_pct: Optional[float] = None,
    excluded_out: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """從 **raw** `candidate_pool.build_candidate_pool()` 輸出（未經 legacy
    `classification.classify_stocks()` 三選一硬刪除）建立 Phase 2 候選池。

    只套用 §十四定義的 6 種「真正定義性」hard exclusion（見
    `regime_gate.build_hard_exclusion_result` docstring：legacy 條件 #2「法人 5 日
    流出且非 ROTATION_LAGGARD」依賴 prelim_type，Phase 2 刻意不搬過來）。

    `taiex_return_1d_pct`：REVERSAL_FAILURE 判斷需要的大盤當日報酬（用來算個股
    相對大盤的超額報酬）；未提供時 REVERSAL_FAILURE 永遠不會觸發（缺資料不臆測）。

    `excluded_out`：若提供一個 list，被 Hard Exclusion 剔除的候選會以
    `{"stock_id": ..., "hard_exclusion_result": {...}}` 的形式 append 進去
    （2026-07-22 §十九「不要 silent delete」——呼叫端可用這個組完整 funnel 統計 /
    explain trace，不提供則維持原本輕量行為，向後相容）。

    存活的候選會額外帶 `risk_warnings` / `liquidity_state` 欄位（原本會被
    Hard Exclude 的單一條件，如 `EXTENDED_3D`，重構後降級為 warning 而非剔除，
    仍需要讓下游看得到）。

    `pipeline.py` 的 phase2 分支與 `run_phase2_replay.py` 共用這個函式，
    確保 production 與離線 replay 走同一套邏輯。

    **2026-07-22 順手修正的既有 bug**：舊版先呼叫 `attach_deterministic_signals`
    才呼叫 `_detect_soft_hints` 設定 `soft_hints`——但
    `deterministic_signals.build_deterministic_signals()` 內部的
    `chip_trend`/`technical_status`/`risk_flags` 全部依賴 `candidate.get(
    "soft_hints")`，導致這些欄位在計算當下永遠讀到空 list（`soft_hints` 那時
    還不存在），`distribution`/`weakening`/`retail_overheated` 三個 soft hint
    衍生出的 deterministic_signals 從未被正確套用過。這條 bug 直接影響本輪
    COMPOSITE_RISK_EXCLUDE 是否能被正確判斷，屬於 hard exclusion 直接相關範圍，
    順手修正（不是額外新功能，只是把兩行呼叫順序對調）。
    """
    with_hints = []
    for c in pool:
        hints = legacy_filters._detect_soft_hints(c)
        with_hints.append({**c, "soft_hints": hints})
    with_signals = det_signals.attach_deterministic_signals(with_hints)

    survivors = []
    for c in with_signals:
        result = regime_mod.build_hard_exclusion_result(c, taiex_return_1d_pct=taiex_return_1d_pct)
        if result["excluded"]:
            if excluded_out is not None:
                excluded_out.append({"stock_id": c.get("stock_id"), "hard_exclusion_result": result})
            continue
        survivors.append({
            **c,
            "risk_warnings": result["risk_warnings"],
            "liquidity_state": result["liquidity_state"],
        })
    return survivors


def load_classifications(db: Session, stock_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """從 Phase 1 `security_classification` 表載入 stock_id -> {primary_sector,
    sub_sector, confidence}。查無分類（理論上不該發生，backfill 已覆蓋全 universe）
    的股票不會出現在回傳 dict 裡，呼叫端的 sector_context 對缺席股票會自然落到
    MARKET_ONLY（`classifications.get(sid)` 回 None）。"""
    if not stock_ids:
        return {}
    rows = (
        db.query(SecurityClassification)
        .filter(SecurityClassification.stock_id.in_(stock_ids))
        .all()
    )
    return {
        r.stock_id: {
            "primary_sector": r.primary_sector,
            "sub_sector": r.sub_sector,
            "confidence": r.classification_confidence,
        }
        for r in rows
    }


def run_phase2_pipeline(
    db: Session,
    candidates: List[Dict[str, Any]],
    market_regime: str,
    *,
    hard_excluded: Optional[List[Dict[str, Any]]] = None,
    taiex_return_1d_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    對已經建好的候選池（legacy candidate_pool 輸出，含 momentum frame 特徵 +
    tracking_status）跑完整 Phase 2 deterministic 決策層。

    `hard_excluded`：`build_phase2_pool(..., excluded_out=[])` 收集到的、在
    候選池建立階段就被 Hard Exclusion 剔除的候選（MANUAL_BLACKLIST /
    FAILED_FOLLOW_THROUGH_CURRENT_EPISODE / LIQUIDITY_FAILURE /
    COMPOSITE_RISK_EXCLUDE / REVERSAL_FAILURE）。這裡會幫他們也建一份
    explain trace（2026-07-22 §十九「不要 silent delete」），並計入
    `funnel_metrics.hard_exclusion_reason_counts`。
    **`STRUCTURE_DAMAGED` 不會出現在這裡**——它依賴 `entry_state`，只有進到這個
    函式（Step 3 算完 entry_state 之後）才判斷得出來，見下方 Step 8 迴圈。

    回傳：
        {
            "survivors": [...],       # 通過 regime gate 的候選（含 role/conviction/entry_state）
            "explain_traces": {...},  # stock_id -> explain trace（含 hard-excluded）
            "funnel_metrics": {...},  # 含 hard_exclusion_reason_counts / hard_exclusion_version
            "sector_context": {...},  # stock_id -> sector context（debug 用）
            "sector_clusters": {...},
        }
    """
    hard_excluded = hard_excluded or []
    stock_ids = [c["stock_id"] for c in candidates]
    classifications = load_classifications(db, stock_ids)

    # Step 2：sector context 需要全市場 momentum frame 才能算 percentile；
    # 這裡只用候選池本身當 frame 近似——候選池已是全市場排名後的子集，
    # 對「候選池內排名」而言足夠；若要更精確的全市場 percentile，
    # 應由呼叫端傳完整 momentum_frame（見 run_phase2_replay.py 的用法）。
    frame = {c["stock_id"]: c for c in candidates}
    sector_ctx_by_id = sector_ctx_mod.compute_sector_context(frame, classifications)

    # Step 3：entry_state（純函式，逐檔算）
    for c in candidates:
        c["entry_state_result"] = entry_state_mod.compute_entry_state(c)
        c["entry_state"] = c["entry_state_result"]["entry_state"]

    # Step 4：New vs Tracked 分流
    for c in candidates:
        c["tracking_state"] = tracking_mod.compute_tracking_state(c)

    # Step 5：sector cluster（用候選池成員估計；同樣是候選池子集近似）
    clusters = cluster_mod.compute_sector_clusters(candidates, sector_ctx_by_id)

    # Step 3b（base eligibility）+ Step 6（role annotation，僅對「非 tracked」跑，
    # tracked 候選改用 tracking_state 當作最終分類——不重新參加選秀，見 §N）
    new_discovery = [c for c in candidates if c.get("tracking_state") is None]
    role_results = roles_mod.classify_roles(new_discovery, sector_ctx_by_id, clusters)
    for c in candidates:
        sid = c["stock_id"]
        if sid in role_results:
            c["role"] = role_results[sid]["role"]
            c["base_eligible"] = role_results[sid]["base_eligible"]
            c["evidence_count"] = role_results[sid].get("evidence_count")
            c["evidence_detail"] = role_results[sid].get("evidence_detail")
        else:
            # tracked 候選：role 留白，由 tracking_state 代表它的「分類」
            c["role"] = None
            c["base_eligible"] = roles_mod.is_base_momentum_eligible(c)
            c["evidence_count"] = None
            c["evidence_detail"] = None

    # Step 7：true hard exclusion + regime gate（tracked 候選也要過這關；
    # apply_regime_gate_v2 用 `role` 判斷 formal leader——tracked 股沒有 role，
    # 在 RISK_OFF 下會被排除，除非其 tracking_state 本身已經是 ACTIVE_TREND/
    # REACCELERATING 且我們日後決定把它視同 leader；本版先保守處理，只讓
    # base_eligible 的 tracked 股在 BULL/VOLATILE regime 存活，RISK_OFF 下
    # 沒有 role 的 tracked 股一律排除——這是需要 replay 驗證調整的已知簡化）
    eligible_candidates = [c for c in candidates if c.get("base_eligible")]
    survivors = regime_mod.apply_regime_gate_v2(eligible_candidates, market_regime)
    survivor_ids = {c["stock_id"] for c in survivors}

    # Step 8：explain trace（每一檔候選都要有，不論存活與否）。用完整的
    # `build_hard_exclusion_result` 而不是精簡版 `is_true_hard_exclusion`，
    # 這裡是 STRUCTURE_DAMAGED 唯一能被正確判斷的時機點（entry_state 到這裡才有值）。
    explain_traces: Dict[str, Any] = {}
    hard_exclusion_reason_counter: Counter = Counter()
    for c in candidates:
        sid = c["stock_id"]
        hard_result = regime_mod.build_hard_exclusion_result(c, taiex_return_1d_pct=taiex_return_1d_pct)
        hard_reason = hard_result["reason"]
        if hard_reason:
            hard_exclusion_reason_counter[hard_reason] += 1
        regime_passed = sid in survivor_ids if c.get("base_eligible") else None
        explain_traces[sid] = trace_mod.build_explain_trace(
            sid,
            candidate_channels=[
                ch for ch in ("in_top_stocks_3d", "in_price_momentum_pool", "in_acceleration_pool", "in_fundamental_pool")
                if c.get(ch)
            ],
            sector_context=sector_ctx_by_id.get(sid),
            momentum_eligible=c.get("base_eligible"),
            role=c.get("role") or c.get("tracking_state"),
            role_evidence=c.get("evidence_detail"),
            tracking_state=c.get("tracking_state"),
            entry_state=c.get("entry_state"),
            hard_exclusion_reason=hard_reason,
            hard_exclusion_risk_warnings=hard_result["risk_warnings"],
            hard_exclusion_evidence_families=hard_result["evidence_families"],
            hard_exclusion_liquidity_state=hard_result["liquidity_state"],
            regime_gate_passed=regime_passed,
            regime=market_regime,
            conviction=next((s.get("conviction") for s in survivors if s["stock_id"] == sid), None),
            sent_to_llm=sid in survivor_ids,
        )

    # 候選池建立階段（`build_phase2_pool`）就被剔除的候選：MANUAL_BLACKLIST /
    # FAILED_FOLLOW_THROUGH_CURRENT_EPISODE / LIQUIDITY_FAILURE /
    # COMPOSITE_RISK_EXCLUDE / REVERSAL_FAILURE。這裡才第一次幫他們建 explain
    # trace（2026-07-22 §十九「不要 silent delete」，之前完全不會出現在 trace 裡）。
    for rec in hard_excluded:
        sid = rec.get("stock_id")
        if not sid:
            continue
        result = rec.get("hard_exclusion_result") or {}
        hard_exclusion_reason_counter[result.get("reason")] += 1
        explain_traces[sid] = trace_mod.build_explain_trace(
            sid,
            hard_exclusion_reason=result.get("reason"),
            hard_exclusion_risk_warnings=result.get("risk_warnings"),
            hard_exclusion_evidence_families=result.get("evidence_families"),
            hard_exclusion_liquidity_state=result.get("liquidity_state"),
            regime=market_regime,
        )

    role_counts = funnel_mod.role_counts_from_results(role_results)
    sector_candidate_counts: Dict[str, int] = {}
    sector_role_none_counts: Dict[str, int] = {}
    for c in candidates:
        sector = (sector_ctx_by_id.get(c["stock_id"]) or {}).get("primary_sector")
        if not sector:
            continue
        sector_candidate_counts[sector] = sector_candidate_counts.get(sector, 0) + 1
        if not c.get("role") and not c.get("tracking_state"):
            sector_role_none_counts[sector] = sector_role_none_counts.get(sector, 0) + 1

    funnel = funnel_mod.compute_funnel_metrics(
        candidate_count=len(candidates) + len(hard_excluded),
        momentum_eligible_count=sum(1 for c in candidates if c.get("base_eligible")),
        role_counts=role_counts,
        hard_risk_survivor_count=len(eligible_candidates),
        regime_survivor_count=len(survivors),
        sent_to_llm_count=len(survivors),
        watch_count=len(survivors),
        sector_candidate_counts=sector_candidate_counts,
        sector_role_none_counts=sector_role_none_counts,
        hard_exclusion_reason_counts=dict(hard_exclusion_reason_counter),
        hard_exclusion_version=regime_mod.HARD_EXCLUSION_VERSION,
    )

    return {
        "survivors": survivors,
        "explain_traces": explain_traces,
        "funnel_metrics": funnel,
        "sector_context": sector_ctx_by_id,
        "sector_clusters": clusters,
        "pipeline_version": PIPELINE_VERSION,
    }
