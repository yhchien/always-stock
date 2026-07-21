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


def build_phase2_pool(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """從 **raw** `candidate_pool.build_candidate_pool()` 輸出（未經 legacy
    `classification.classify_stocks()` 三選一硬刪除）建立 Phase 2 候選池。

    只套用 §P 定義的「真正定義性」hard exclusion（見
    `regime_gate.is_true_hard_exclusion` docstring：legacy 條件 #2「法人 5 日
    流出且非 ROTATION_LAGGARD」依賴 prelim_type，Phase 2 刻意不搬過來）。

    `pipeline.py` 的 shadow hook 與 `run_phase2_replay.py` 共用這個函式，
    確保 production shadow 與離線 replay 走同一套邏輯。
    """
    with_signals = det_signals.attach_deterministic_signals(pool)
    survivors = []
    for c in with_signals:
        hints = legacy_filters._detect_soft_hints(c)
        c_with_hints = {**c, "soft_hints": hints}
        if regime_mod.is_true_hard_exclusion(c_with_hints):
            continue
        survivors.append(c_with_hints)
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
) -> Dict[str, Any]:
    """
    對已經建好的候選池（legacy candidate_pool 輸出，含 momentum frame 特徵 +
    tracking_status）跑完整 Phase 2 deterministic 決策層。

    回傳：
        {
            "survivors": [...],       # 通過 regime gate 的候選（含 role/conviction/entry_state）
            "explain_traces": {...},  # stock_id -> explain trace
            "funnel_metrics": {...},
            "sector_context": {...},  # stock_id -> sector context（debug 用）
            "sector_clusters": {...},
        }
    """
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

    # Step 8：explain trace（每一檔候選都要有，不論存活與否）
    explain_traces: Dict[str, Any] = {}
    for c in candidates:
        sid = c["stock_id"]
        hard_reason = regime_mod.is_true_hard_exclusion(c)
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
            regime_gate_passed=regime_passed,
            regime=market_regime,
            conviction=next((s.get("conviction") for s in survivors if s["stock_id"] == sid), None),
            sent_to_llm=sid in survivor_ids,
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
        candidate_count=len(candidates),
        momentum_eligible_count=sum(1 for c in candidates if c.get("base_eligible")),
        role_counts=role_counts,
        hard_risk_survivor_count=len(eligible_candidates),
        regime_survivor_count=len(survivors),
        sent_to_llm_count=len(survivors),
        watch_count=len(survivors),
        sector_candidate_counts=sector_candidate_counts,
        sector_role_none_counts=sector_role_none_counts,
    )

    return {
        "survivors": survivors,
        "explain_traces": explain_traces,
        "funnel_metrics": funnel,
        "sector_context": sector_ctx_by_id,
        "sector_clusters": clusters,
        "pipeline_version": PIPELINE_VERSION,
    }
