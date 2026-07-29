"""
M23 Pipeline 主流程：cron / BackgroundTasks 共用入口。

對應 spec §5（10 步 pipeline）+ §11.5（背景任務實作）。

設計原則：
  - 每個 stage 結束 commit 一次 job 進度，前端 polling 即時看到進度
  - 非 LLM batch 的 stage 拋例外 → 先 rollback session 清 error state，再寫
    `job.status=failed` + `error_message=traceback`，最後 raise 讓 caller 紀錄
  - LLM 單批失敗會隔離、繼續其餘批次並保存 partial snapshot
  - 不能用 request session（請求結束會 close）；要嘛用預設 SessionLocal、
    要嘛測試傳 in-memory factory（spec §11.5）

骨架（slice 4）：
  - status 流轉、progress update、exception handling、_persist_snapshot UPSERT
    皆已實作完成
  - 各 stage 仍呼叫 stub function（slice 5/6 raise NotImplementedError），
    所以實際 run 會在 stage 1 ingest 即 failed
  - 測試以 monkeypatch 替換 stage function 為 noop 來覆蓋 happy path
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import logging
import os
import traceback
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DailyPrice, SignalGenerationJob, SignalSnapshot
from app.signals import archive as signal_archive
from app.signals import (
    candidate_pool,
    classification,
    filters,
    global_selector,
    llm_caller,
    observation_lifecycle,
    prompt_family,
)
from app.signals import deterministic_signals as det_signals
from app.signals import market_breadth, market_margin, market_regime, market_snapshot, momentum

logger = logging.getLogger(__name__)


# Spec §11.5 / models.py current_stage enum
STAGE_INGEST = "ingest"
STAGE_RANK = "rank"
STAGE_CANDIDATE = "candidate"
STAGE_FILTER = "filter"
STAGE_LLM_RESEARCH = "llm_research"
STAGE_LLM_EXPLAIN = "llm_explain"
STAGE_GLOBAL_SELECTION = "global_selection"
STAGE_TRACKING = "tracking"
STAGE_PERSIST = "persist"
LLM_BATCH_CONCURRENCY = 2


@dataclass
class BatchExecution:
    """One stage's deterministic batch results and audit trail."""

    results: list
    batches: list[Dict[str, Any]]
    failures: list[Dict[str, Any]]

# Phase 2（2026-07-21 shadow 上線 / 2026-07-22 production cutover）：
# SIGNALS_PIPELINE_MODE 控制候選池 + regime gate 要用 legacy 還是 Phase 2 邏輯。
#   - "legacy"：完全不執行 Phase 2 模組，行為與 Phase 2 開工前逐 byte 相同
#   - "phase2_shadow"：legacy 仍是真正回傳給使用者的 watchlist/removed 來源；
#     額外跑一次 Phase 2 deterministic 決策層存進 `signal_shadow_snapshots` 供比對，
#     不影響任何使用者看得到的輸出。Phase 2 任何例外都被吞掉（見 `_run_phase2_shadow`）。
#   - "phase2"：Phase 2 存活者（含 role → prelim_type 映射）**取代** legacy 的
#     after_regime，成為真正送進 LLM、寫進 signal_snapshots / signal_watch_hits 的
#     候選來源。legacy 這條 chain 仍會照跑（成本很低，純 deterministic，無 LLM），
#     只是算出來的 after_regime 不再是最終輸出，改成拿去跟 phase2 結果一起存進
#     shadow snapshot 當作持續監控/回溯比對用。若 Phase 2 pipeline 本身丟例外，
#     fail-safe 退回 legacy 的 after_regime，確保 cron 不會因為新程式碼的 bug 而整包失敗。
# 2026-07-22：預設值由 "legacy" 改為 "phase2"，即為 production cutover 本身
# （不需要另外在 Render / GitHub Actions 設定 env var；改這裡的預設值同時涵蓋
# Render web service 的 BackgroundTasks 路徑與 daily_signals.yml workflow_dispatch
# 路徑）。要臨時退回 legacy，設定 env var `SIGNALS_PIPELINE_MODE=legacy` 即可，
# 不需要改代碼、不需要 revert commit。
SIGNALS_PIPELINE_MODE = os.getenv("SIGNALS_PIPELINE_MODE", "phase2").strip().lower()


def _persist_phase2_shadow_snapshot(
    db: Session,
    target_date: date,
    candidates: list,
    result: Dict[str, Any],
    *,
    legacy_survivor_ids: Optional[list] = None,
) -> None:
    """UPSERT `signal_shadow_snapshots`（不 commit 例外處理——呼叫端決定）。

    `legacy_survivor_ids` 有值時（`SIGNALS_PIPELINE_MODE=="phase2"` 的即時比較）
    寫進 `comparison_summary`，讓 Phase 2 Debug View 在正式切換後仍能持續當作
    「若還在用 legacy 今天會抓到誰」的監控/回溯工具，不是只有 cutover 前才有用。
    """
    from app.models import SignalShadowSnapshot
    from app.signals.phase2 import pipeline_v2

    row = (
        db.query(SignalShadowSnapshot)
        .filter(
            SignalShadowSnapshot.snapshot_date == target_date,
            SignalShadowSnapshot.pipeline_version == pipeline_v2.PIPELINE_VERSION,
        )
        .first()
    )
    if row is None:
        row = SignalShadowSnapshot(
            snapshot_date=target_date,
            pipeline_version=pipeline_v2.PIPELINE_VERSION,
        )
        db.add(row)
    row.funnel_metrics = result["funnel_metrics"]
    row.explain_traces = result["explain_traces"]
    row.candidate_pool_size = len(candidates)
    row.role_survivor_count = sum(
        1 for c in candidates if c.get("role") or c.get("tracking_state")
    )
    row.regime_survivor_count = len(result["survivors"])
    if legacy_survivor_ids is not None:
        phase2_survivor_ids = [c.get("stock_id") for c in result["survivors"]]
        row.comparison_summary = {
            "legacy_survivor_count": len(legacy_survivor_ids),
            "legacy_survivor_ids": legacy_survivor_ids,
            "phase2_survivor_count": len(phase2_survivor_ids),
            "phase2_survivor_ids": phase2_survivor_ids,
            "mode": SIGNALS_PIPELINE_MODE,
        }
    db.commit()
    logger.info(
        "Phase 2 shadow snapshot recorded for %s: candidates=%d survivors=%d mode=%s",
        target_date, len(candidates), len(result["survivors"]), SIGNALS_PIPELINE_MODE,
    )


def _run_phase2_shadow(
    db: Session,
    target_date: date,
    raw_pool: list,
    regime: str,
    *,
    taiex_return_1d_pct: Optional[float] = None,
) -> None:
    """在 `SIGNALS_PIPELINE_MODE=phase2_shadow` 時執行；任何例外都只 log，
    不 raise——shadow mode 對 legacy pipeline 必須是零風險的旁路。

    `raw_pool` 必須是 legacy `classification.classify_stocks()` **之前**的候選池
    （見呼叫端註解）；這裡用 `pipeline_v2.build_phase2_pool()` 套用 Phase 2 自己
    的定義性 hard exclusion，不是拿 legacy 已經三選一硬刪除過的結果。

    只在 "phase2_shadow" 模式呼叫（見呼叫端 if/else 分支）；"phase2" 模式的
    shadow 紀錄改由 `run_signal_pipeline_sync` 直接呼叫
    `_persist_phase2_shadow_snapshot`，因為那裡已經算好 phase2_result 可以重用，
    不需要在這裡重算一次。
    """
    if SIGNALS_PIPELINE_MODE != "phase2_shadow":
        return
    try:
        from app.signals.phase2 import pipeline_v2

        hard_excluded: list = []
        candidates = pipeline_v2.build_phase2_pool(
            raw_pool, taiex_return_1d_pct=taiex_return_1d_pct, excluded_out=hard_excluded
        )
        result = pipeline_v2.run_phase2_pipeline(
            db, candidates, regime, hard_excluded=hard_excluded, taiex_return_1d_pct=taiex_return_1d_pct
        )
        _persist_phase2_shadow_snapshot(db, target_date, candidates, result)
    except Exception:
        logger.exception("Phase 2 shadow pipeline failed for %s (non-fatal, legacy unaffected)", target_date)
        db.rollback()


def run_signal_pipeline_sync(
    job_id: str,
    target_date: date,
    *,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """同步 pipeline 入口（cron / BackgroundTasks 共用）。

    流程：
      1. 從 DB 讀對應 SignalGenerationJob（找不到 raise ValueError）
      2. 逐 stage 跑、commit 進度
      3. 完整跑完 → status=done；批次技術失敗 → status=partial_failure
      4. 非批次 stage 拋例外 → status=failed + error_message=traceback + finished_at，
         並 re-raise（caller 可決定是否吞）

    參數：
      session_factory：可注入的 Session factory（測試用 in-memory SQLite）。
                       預設使用 `SessionLocal`。
    """
    factory = session_factory or SessionLocal
    db = factory()
    try:
        job = db.get(SignalGenerationJob, job_id)
        if job is None:
            raise ValueError(f"SignalGenerationJob not found: {job_id}")

        try:
            # Resolve once before any production stage. Unknown families fail closed
            # and can never drift into a deprecated executable prompt.
            family_metadata = prompt_family.prompt_metadata()
            # Step 1：DB ingest
            _set_progress(
                db,
                job,
                status="running",
                stage=STAGE_INGEST,
                pct=5,
                label="讀取 DB 資料",
            )
            ingestion = candidate_pool.ingest_data(db, target_date)

            # Step 2：產業 / 個股 ranking
            _set_progress(
                db,
                job,
                stage=STAGE_RANK,
                pct=10,
                label="計算產業 / 個股排行",
            )
            rankings = candidate_pool.compute_rankings(db, target_date, ingestion)

            # Step 3：候選池建立 + 擴散
            _set_progress(
                db,
                job,
                stage=STAGE_CANDIDATE,
                pct=15,
                label="建立候選池",
            )
            # v2.2：momentum frame 先算一次，candidate pool 與 market breadth 共用
            # 同一份全市場資料（避免 daily_price 全市場 query 跑兩次）
            momentum_frame = momentum.compute_market_momentum_frame(
                db, target_date, ingestion.get("stocks_master") or {}
            )
            pool = candidate_pool.build_candidate_pool(
                db, target_date, ingestion, rankings, momentum_frame=momentum_frame
            )
            processing_summary: Dict[str, Any] = {
                "raw_union_count": len(pool),
                "raw_union_count_before_total_cap": len(pool),
                "raw_union_count_after_total_cap": len(pool),
                "raw_union_truncated_count": 0,
                "raw_union_total_cap_applied": False,
                "phase2_pool_count": len(pool),
                "hard_exclusion_count": 0,
                "base_eligibility_survivor_count": 0,
                "regime_survivor_count": 0,
                "llm_eligible_count": 0,
                "research_requested_count": 0,
                "research_completed_count": 0,
                "research_failed_count": 0,
                "decision_requested_count": 0,
                "decision_completed_count": 0,
                "decision_failed_count": 0,
                "global_selection_eligible_count": 0,
                "global_selection_recommended_count": 0,
                "global_selection_not_selected_count": 0,
                "global_selection_status": "NOT_STARTED",
                "selection_complete": False,
                "long_reason_requested_count": 0,
                "long_reason_completed_count": 0,
                "final_watch_count": 0,
                "final_remove_count": 0,
                "unprocessed_count": 0,
                "technical_failure_count": 0,
                "capacity_truncated_count": 0,
                "is_complete": True,
                "momentum_score_version": momentum.current_momentum_score_version(),
                "momentum_score_mode": momentum.resolve_momentum_score_mode(),
                **family_metadata,
                "research_batches": [],
                "decision_batches": [],
                "technical_failures": [],
                "prompt_payload_metrics": {},
            }
            logger.info(
                "Ordered %d raw candidates for Phase 2 processing; capacity truncation disabled",
                len(pool),
            )

            # 沒有任何交易資料仍是 no_data；但「交易日有行情、P3 候選為 0」
            # 不得跳過 P4。Active observations 必須獨立於 A/B/C/D 每日檢查。
            if not pool:
                has_trade_data = (
                    db.query(DailyPrice.id)
                    .filter(DailyPrice.trade_date == target_date)
                    .first()
                    is not None
                )
                if not has_trade_data:
                    raise ValueError(
                        f"no candidate stocks or trade data for target_date={target_date}"
                    )
                _run_p4_tracking_only_day(
                    db,
                    job=job,
                    target_date=target_date,
                    ingestion=ingestion,
                    momentum_frame=momentum_frame,
                    processing_summary=processing_summary,
                    job_id=job_id,
                )
                return

            # Step 4：deterministic filter（含預分類）
            _set_progress(
                db,
                job,
                stage=STAGE_FILTER,
                pct=30,
                label="預分類 + filter",
            )
            classified = classification.classify_stocks(db, target_date, pool)
            after_hard = filters.apply_hard_exclusions(db, target_date, classified)
            llm_input = _order_llm_input(after_hard)
            after_soft = filters.apply_soft_filters(db, target_date, llm_input)

            # v2.2：deterministic_signals（v5 STEP 7.5 Risk Cap 的後端 deterministic 化）
            # chip_trend / technical_status / entry_quality / sector_rotation_status /
            # institution_flow_momentum / risk_gate_action / max_decision / risk_flags
            after_soft = det_signals.attach_deterministic_signals(after_soft)

            # M27 Market Regime Gate（deterministic）：依大盤狀態收斂候選範圍。
            # 震盪 / 退潮盤剔除單次命中 Follower-Laggard、distribution、急拉突破；
            # 存活者標 regime_conviction。regime 從 TAIEX 指數 deterministic 算，LLM 不可改寫。
            # v2.2：疊市場廣度（spec §7.2）→ BULL_TREND 拆 BROAD/NARROW，只作用於
            # deterministic gate；對 LLM 的 market_regime 契約維持 3 態（v5 prompt enum 固定）。
            regime_info = market_regime.compute_market_regime(db, target_date)
            breadth = market_breadth.compute_breadth_from_frame(
                momentum_frame, ingestion.get("stocks_master") or {}
            )
            regime_detail = market_breadth.resolve_regime_detail(
                regime_info["regime"], breadth.get("breadth_score")
            )
            regime_info = {
                **regime_info,
                "regime_detail": regime_detail,
                "breadth_score": breadth.get("breadth_score"),
                "breadth": breadth,
            }
            after_regime = filters.apply_regime_gate(
                after_soft, regime_info["regime"], regime_detail=regime_detail
            )
            processing_summary.update(
                {
                    "hard_exclusion_count": max(len(classified) - len(after_hard), 0),
                    "base_eligibility_survivor_count": len(after_soft),
                    "regime_survivor_count": len(after_regime),
                    "llm_eligible_count": len(after_regime),
                }
            )
            conviction_by_stock = {
                str(c.get("stock_id") or ""): c.get("regime_conviction")
                for c in after_regime
            }
            # v2.1（fishtail momentum upgrade spec §9.2）：每檔候選的動能特徵
            # deterministic 快照，最後蓋回 watchlist item 落進 snapshot + watch hit
            signal_metrics_by_stock = {
                str(c.get("stock_id") or ""): momentum.build_signal_metrics(c, regime_info)
                for c in after_regime
            }

            # Phase 2 production cutover（2026-07-22）：
            # 用 **raw `pool`**（legacy `classification.classify_stocks()` 三選一
            # 硬刪除**之前**的候選池）建 Phase 2 專屬候選池——若改用 `after_soft`
            # 會繼承 legacy 分類已經刪掉漢翔/台虹/航運這類案例的問題。
            #
            # `SIGNALS_PIPELINE_MODE=="phase2"` 時，Phase 2 存活者（映射過
            # prelim_type，依 deterministic priority 排序但不截斷）**取代**上面 legacy
            # 算出來的 after_regime/conviction_by_stock/signal_metrics_by_stock，
            # 成為真正送進 LLM 與寫進 signal_snapshots 的來源；legacy 這幾個變數在
            # 這個模式下只保留給 fail-safe fallback（Phase 2 丟例外時退回使用）與
            # shadow snapshot 的比較基準（`legacy_survivor_ids`），並不會消失。
            #
            # 其他模式（legacy / phase2_shadow）：本段落只寫入
            # `signal_shadow_snapshots` 供比對，不動 after_regime 等變數
            # （`_run_phase2_shadow` 內部依 mode 自我判斷是否執行）。
            if SIGNALS_PIPELINE_MODE == "phase2":
                try:
                    from app.signals.phase2 import pipeline_v2

                    # 2026-07-22 Hard Exclusion 重構：REVERSAL_FAILURE 需要大盤當日
                    # 報酬當比較基準（個股相對大盤的超額報酬）；缺值時 REVERSAL_FAILURE
                    # 永遠不觸發（不臆測），不影響其餘 5 種 hard exclusion reason。
                    taiex_return_1d_pct = (regime_info.get("metrics") or {}).get("return_1d_pct")
                    phase2_hard_excluded: list = []
                    phase2_candidates = pipeline_v2.build_phase2_pool(
                        pool,
                        taiex_return_1d_pct=taiex_return_1d_pct,
                        excluded_out=phase2_hard_excluded,
                    )
                    phase2_result = pipeline_v2.run_phase2_pipeline(
                        db,
                        phase2_candidates,
                        regime_info["regime"],
                        hard_excluded=phase2_hard_excluded,
                        taiex_return_1d_pct=taiex_return_1d_pct,
                    )
                    phase2_survivors = phase2_result["survivors"]
                    for c in phase2_survivors:
                        c["prelim_type"] = pipeline_v2.role_to_prelim_type(c)
                        # phase2 的信心度欄位叫 `conviction`（regime_gate.py），legacy
                        # 是 `regime_conviction`（filters.py）；llm_caller evidence view
                        # 讀的是後者的 key 名，這裡別名一份避免 LLM 看到全 null。
                        c["regime_conviction"] = c.get("conviction")
                    # Phase 2.5（2026-07-23）：真正送 LLM 的是 `llm_eligible`（
                    # WATCH_QUALITY_MODE=production 時只有 READY/SETUP；off/shadow
                    # 時等於 phase2_survivors，行為不變）。同一批 dict 物件參照，
                    # 上面對 phase2_survivors 做的 in-place 欄位更新一併反映在這裡。
                    phase2_llm_eligible = phase2_result.get("llm_eligible", phase2_survivors)
                    phase2_after_regime = _order_llm_input(phase2_llm_eligible)
                    legacy_survivor_ids = [
                        str(c.get("stock_id") or "") for c in after_regime
                    ]

                    _persist_phase2_shadow_snapshot(
                        db,
                        target_date,
                        phase2_candidates,
                        phase2_result,
                        legacy_survivor_ids=legacy_survivor_ids,
                    )

                    after_regime = phase2_after_regime
                    conviction_by_stock = {
                        str(c.get("stock_id") or ""): c.get("conviction")
                        for c in after_regime
                    }
                    signal_metrics_by_stock = {
                        str(c.get("stock_id") or ""): momentum.build_signal_metrics(
                            c, regime_info
                        )
                        for c in after_regime
                    }
                    funnel = phase2_result.get("funnel_metrics") or {}
                    processing_summary.update(
                        {
                            "phase2_pool_count": len(phase2_candidates),
                            "hard_exclusion_count": len(phase2_hard_excluded),
                            "base_eligibility_survivor_count": int(
                                funnel["momentum_eligible_count"]
                                if funnel.get("momentum_eligible_count") is not None
                                else len(phase2_survivors)
                            ),
                            "regime_survivor_count": len(phase2_survivors),
                            "llm_eligible_count": len(after_regime),
                        }
                    )
                    logger.info(
                        "Phase 2 production mode active for %s: candidates=%d "
                        "survivors=%d llm_eligible=%d (watch_quality_mode=%s; all %d "
                        "queued in priority order); legacy would have produced %d survivors",
                        target_date,
                        len(phase2_candidates),
                        len(phase2_survivors),
                        len(phase2_llm_eligible),
                        phase2_result.get("watch_quality_mode"),
                        len(after_regime),
                        len(legacy_survivor_ids),
                    )
                except Exception:
                    logger.exception(
                        "Phase 2 production pipeline failed for %s; "
                        "falling back to legacy output for this run",
                        target_date,
                    )
                    db.rollback()
                    # after_regime / conviction_by_stock / signal_metrics_by_stock
                    # 保留上面 legacy 算好的值，本次 run 以 legacy 輸出為準，
                    # 不讓 Phase 2 的 bug 拖垮整個 cron。
            else:
                _run_phase2_shadow(
                    db,
                    target_date,
                    pool,
                    regime_info["regime"],
                    taiex_return_1d_pct=(regime_info.get("metrics") or {}).get("return_1d_pct"),
                )

            # Step 5：LLM Research（batch）
            total_for_llm = max(len(after_regime), 1)
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_RESEARCH,
                pct=45,
                label=f"LLM 上網查詢（共 {len(after_regime)} 檔）",
            )
            market_context = _build_pipeline_market_context(
                db,
                target_date=target_date,
                regime_info=regime_info,
            )
            research_batch_size = llm_caller.DEFAULT_RESEARCH_BATCH_SIZE
            research_batches = _build_batches(after_regime, research_batch_size)
            processing_summary["research_requested_count"] = len(after_regime)
            logger.info(
                "Queued %d Phase 2 survivors for LLM research in %d batches",
                len(after_regime),
                len(research_batches),
            )
            research_execution = _run_parallel_batches(
                research_batches,
                lambda batch: llm_caller.run_research_batch(batch, market_context),
                stage="research",
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_RESEARCH,
                    pct=45 + int(30 * done_count / total_for_llm),
                    label=f"研究第 {done_count} / {len(after_regime)} 檔",
                ),
            )
            research_results, research_failures = _partition_stage_results(
                research_execution, failure_status="RESEARCH_FAILED"
            )
            processing_summary["research_batches"] = research_execution.batches
            processing_summary["research_completed_count"] = len(research_results)
            processing_summary["research_failed_count"] = len(research_failures)
            processing_summary["prompt_payload_metrics"]["research"] = (
                _collect_prompt_payload_metrics(research_execution.results)
            )
            logger.info(
                "Completed research for %d/%d candidates; %d research failures",
                len(research_results),
                len(after_regime),
                len(research_failures),
            )

            # Step 6a：逐檔 assessment（eligibility / true veto；不是正式推薦）
            backend_pre_removed = []
            assessment_inputs = []
            for research_item in research_results:
                backend_max = str(
                    (
                        research_item.get("deterministic_signals") or {}
                    ).get("max_decision")
                    or ""
                ).upper()
                if backend_max == "REMOVE":
                    backend_pre_removed.append(
                        {
                            **research_item,
                            "backend_max_decision": "REMOVE",
                            "assessment_status": "REMOVE",
                            "decision": "REMOVE",
                            "veto_reason": "BACKEND_MAX_REMOVE",
                            "short_reason": "Backend deterministic max decision is REMOVE.",
                        }
                    )
                else:
                    assessment_inputs.append(research_item)

            total_for_explain = max(len(assessment_inputs), 1)
            explain_batch_size = llm_caller.DEFAULT_EXPLANATION_BATCH_SIZE
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_EXPLAIN,
                pct=75,
                label=f"逐檔驗證（共 {len(research_results)} 檔）",
            )
            explain_batches = _build_batches(assessment_inputs, explain_batch_size)
            processing_summary["decision_requested_count"] = len(research_results)
            processing_summary["backend_pre_removed_count"] = len(
                backend_pre_removed
            )
            decision_execution = _run_parallel_batches(
                explain_batches,
                lambda chunk: llm_caller.run_explanation_batch(chunk, market_context),
                stage="decision",
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=75 + int(10 * done_count / total_for_explain),
                    label=f"驗證第 {done_count} / {len(assessment_inputs)} 檔",
                ),
            )
            assessed_items, decision_failures = _partition_stage_results(
                decision_execution, failure_status="DECISION_FAILED"
            )
            assessed_by_id = {
                _candidate_id(item): item for item in assessed_items
            }
            backend_removed_by_id = {
                _candidate_id(item): item for item in backend_pre_removed
            }
            explanation = []
            for research_item in research_results:
                sid = _candidate_id(research_item)
                if sid in backend_removed_by_id:
                    explanation.append(backend_removed_by_id[sid])
                elif sid in assessed_by_id:
                    explanation.append(assessed_by_id[sid])
            processing_summary["decision_batches"] = decision_execution.batches
            processing_summary["decision_completed_count"] = len(explanation)
            processing_summary["decision_failed_count"] = len(decision_failures)
            processing_summary["prompt_payload_metrics"]["assessment"] = (
                _collect_prompt_payload_metrics(decision_execution.results)
            )
            logger.info(
                "Completed assessments for %d/%d candidates; %d assessment failures",
                len(explanation),
                len(research_results),
                len(decision_failures),
            )

            # Step 6b：P3 全體比較。真實 REMOVE 在 selector 前分離；其餘候選
            # 以同 schema compact cards 一次送入，禁止 chunk/tournament/Top-K。
            selection_eligible, removed_assessments = global_selector.partition_assessments(
                explanation
            )
            selection_cards = global_selector.build_compact_selection_cards(
                selection_eligible,
                selection_date=target_date,
            )
            capacity = global_selector.estimate_selection_capacity(selection_cards)
            processing_summary.update(
                {
                    "global_selection_eligible_count": len(selection_eligible),
                    "global_selection_status": "RUNNING",
                    "selection_candidate_count": capacity.candidate_count,
                    "selection_serialized_bytes": capacity.serialized_bytes,
                    "selection_estimated_input_tokens": capacity.estimated_input_tokens,
                    "selection_output_token_reserve": capacity.output_token_reserve,
                    "selection_model_context_limit_tokens": capacity.model_context_limit_tokens,
                }
            )
            _set_progress(
                db,
                job,
                stage=STAGE_GLOBAL_SELECTION,
                pct=86,
                label=f"全體候選比較（共 {len(selection_eligible)} 檔）",
            )
            try:
                selection_result = global_selector.run_global_selection(
                    selection_cards,
                    market_context,
                    selection_date=target_date,
                )
            except global_selector.GlobalSelectionError as exc:
                selection_failure = exc.as_dict()
                selection_failure_diagnostic = (
                    selection_failure.get("diagnostic") or {}
                )
                if selection_failure_diagnostic.get("payload_metrics"):
                    processing_summary["prompt_payload_metrics"][
                        "global_selector"
                    ] = [
                        selection_failure_diagnostic["payload_metrics"]
                    ]
                technical_failures = [
                    *research_failures,
                    *decision_failures,
                    selection_failure,
                ]
                processing_summary.update(
                    {
                        "global_selection_status": "FAILED",
                        "selection_complete": False,
                        "global_selection_error": selection_failure,
                        "final_watch_count": 0,
                        "final_remove_count": len(removed_assessments),
                        "unprocessed_count": (
                            len(selection_eligible)
                            + len(research_failures)
                            + len(decision_failures)
                        ),
                        "technical_failures": technical_failures,
                        "technical_failure_count": len(technical_failures),
                        "is_complete": False,
                    }
                )
                failed_payload = llm_caller.assemble_final_output(
                    market_context,
                    removed_assessments,
                    candidate_pool_size=len(pool),
                )
                failed_payload["watchlist"] = []
                failed_payload["not_selected"] = []
                failed_payload["final_watchlist_size"] = 0
                failed_summary = failed_payload.setdefault("summary", {})
                failed_summary["not_selected"] = []
                failed_summary["technical_failures"] = technical_failures
                failed_summary["research_results"] = research_results
                failed_summary["compact_selection_cards"] = selection_cards
                failed_summary["selection_summary"] = {
                    "phase2_eligible_count": processing_summary.get(
                        "llm_eligible_count", 0
                    ),
                    "research_completed_count": len(research_results),
                    "veto_removed_count": len(removed_assessments),
                    "global_eligible_count": len(selection_eligible),
                    "recommended_count": 0,
                    "not_selected_count": 0,
                    "technical_failure_count": len(technical_failures),
                    "selection_version": prompt_family.stage_version(
                        "global_selector"
                    ),
                    "selection_complete": False,
                    "status": "FAILED",
                    "error": selection_failure,
                }
                failed_summary["processing_summary"] = processing_summary
                tracking_result = _run_p4_tracking(
                    db,
                    job=job,
                    target_date=target_date,
                    market_context=market_context,
                    ingestion=ingestion,
                    momentum_frame=momentum_frame,
                    current_candidates=pool,
                    watchlist=[],
                    processing_summary=processing_summary,
                )
                tracking_failures = [
                    *tracking_result.get("technical_failures", []),
                    *tracking_result.get("conflicts", []),
                ]
                technical_failures.extend(tracking_failures)
                processing_summary.update(
                    {
                        "technical_failures": technical_failures,
                        "technical_failure_count": len(technical_failures),
                        "tracking_review_count": len(
                            tracking_result.get("reviews", [])
                        ),
                        "tracking_review_failed_count": (
                            tracking_result.get("tracking_summary", {}).get(
                                "review_failed_count", 0
                            )
                        ),
                        "tracking_conflict_count": (
                            tracking_result.get("tracking_summary", {}).get(
                                "conflict_count", 0
                            )
                        ),
                    }
                )
                processing_summary["prompt_payload_metrics"]["tracking"] = (
                    tracking_result.get("tracking_summary", {}).get(
                        "prompt_payload_metrics", []
                    )
                )
                failed_summary["technical_failures"] = technical_failures
                failed_summary["selection_summary"][
                    "technical_failure_count"
                ] = len(technical_failures)
                failed_summary["tracking_summary"] = tracking_result.get(
                    "tracking_summary", {}
                )
                failed_summary["processing_summary"] = processing_summary
                _set_progress(
                    db,
                    job,
                    stage=STAGE_PERSIST,
                    pct=98,
                    label="保存 P3 與 P4 結果",
                )
                _persist_snapshot(db, target_date, failed_payload, job_id)
                signal_archive.clear_signal_watch_hits_for_date(db, target_date)
                _mark_partial_failure(
                    db,
                    job,
                    (
                        "Global recommendation selection failed atomically; "
                        f"{len(selection_eligible)} eligible candidates were not selected."
                    ),
                )
                return

            selected_items = global_selector.merge_selection_items(
                selection_eligible,
                selection_result,
            )
            recommend_candidates = [
                item
                for item in selected_items
                if str(item.get("decision") or "").upper() == "RECOMMEND"
            ]
            processing_summary.update(
                {
                    "global_selection_status": "COMPLETED",
                    "selection_complete": True,
                    "global_selection_recommended_count": len(recommend_candidates),
                    "global_selection_not_selected_count": (
                        len(selected_items) - len(recommend_candidates)
                    ),
                    "long_reason_requested_count": len(recommend_candidates),
                }
            )
            selection_diagnostic = selection_result.get("llm_diagnostic") or {}
            if selection_diagnostic.get("payload_metrics"):
                processing_summary["prompt_payload_metrics"][
                    "global_selector"
                ] = [selection_diagnostic["payload_metrics"]]

            # Step 6c：只有正式 RECOMMEND 產生長理由。
            total_for_watch_reason = max(len(recommend_candidates), 1)
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_EXPLAIN,
                pct=90,
                label=f"推薦理由（共 {len(recommend_candidates)} 檔）",
            )
            watch_batches = _build_batches(recommend_candidates, explain_batch_size)
            watch_reason_execution = _run_parallel_batches(
                watch_batches,
                lambda chunk: llm_caller.run_watch_reason_batch(chunk, market_context),
                stage="reason_generation",
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=90 + int(5 * done_count / total_for_watch_reason),
                    label=f"推薦理由第 {done_count} / {len(recommend_candidates)} 檔",
                ),
            )
            enriched_watch, watch_reason_failures = _partition_stage_results(
                watch_reason_execution,
                failure_status="REASON_GENERATION_FAILED",
            )
            processing_summary["long_reason_completed_count"] = len(enriched_watch)
            processing_summary["prompt_payload_metrics"]["reason"] = (
                _collect_prompt_payload_metrics(watch_reason_execution.results)
            )

            if enriched_watch:
                watch_by_id = {
                    str(item.get("stock") or item.get("stock_id") or ""): item
                    for item in enriched_watch
                }
                merged_selected: list = []
                for item in selected_items:
                    sid = str(item.get("stock") or item.get("stock_id") or "")
                    if sid and sid in watch_by_id:
                        merged_selected.append({**item, **watch_by_id[sid]})
                    else:
                        merged_selected.append(item)
                selected_items = merged_selected

            explanation = [*selected_items, *removed_assessments]

            # Step 7：Persist Snapshot
            _set_progress(
                db,
                job,
                stage=STAGE_PERSIST,
                pct=95,
                label="寫入 snapshot",
            )
            final_payload = llm_caller.assemble_final_output(
                market_context, explanation, candidate_pool_size=len(pool)
            )
            technical_failures = [
                *research_failures,
                *decision_failures,
                *watch_reason_failures,
            ]
            failed_stock_ids = {
                str(item.get("stock_id") or "")
                for item in technical_failures
                if item.get("stock_id")
            }
            processing_summary.update(
                {
                    "final_watch_count": len(final_payload.get("watchlist", [])),
                    "final_remove_count": len(final_payload.get("removed", [])),
                    "unprocessed_count": len(failed_stock_ids),
                    "technical_failures": technical_failures,
                    "technical_failure_count": len(technical_failures),
                    "is_complete": not technical_failures,
                }
            )
            final_summary = final_payload.setdefault("summary", {})
            final_summary["not_selected"] = final_payload.get("not_selected", [])
            final_summary["technical_failures"] = technical_failures
            final_summary["selection_summary"] = {
                "phase2_eligible_count": processing_summary.get(
                    "llm_eligible_count", 0
                ),
                "research_completed_count": len(research_results),
                "veto_removed_count": len(removed_assessments),
                "global_eligible_count": len(selection_eligible),
                "recommended_count": len(final_payload.get("watchlist", [])),
                "not_selected_count": len(final_payload.get("not_selected", [])),
                "technical_failure_count": len(technical_failures),
                "selection_rationale": (
                    selection_result.get("summary", {}).get(
                        "selection_rationale", ""
                    )
                ),
                "selection_version": selection_result.get("selection_version"),
                "selection_complete": True,
                "status": "COMPLETED",
                "capacity": selection_result.get("capacity"),
            }
            final_summary["processing_summary"] = processing_summary
            # M27：把 deterministic conviction / watch_intensity 蓋回每筆 watchlist item
            # （不依賴 LLM；regime 為全市場一致）
            for item in final_payload.get("watchlist", []):
                sid = str(item.get("stock") or "")
                conv = conviction_by_stock.get(sid)
                item["regime"] = regime_info["regime"]
                item["conviction"] = conv
                item["watch_intensity"] = filters.regime_watch_intensity(
                    regime_info["regime"], conv
                )
                # v2.1：動能特徵 deterministic 蓋回（不依賴 LLM 回傳）
                item["signal_metrics"] = {
                    **(signal_metrics_by_stock.get(sid) or {}),
                    "selection_status": "RECOMMEND",
                    "selection_version": item.get("selection_version"),
                    "recommendation_rank": item.get("recommendation_rank"),
                    "backend_priority_rank": item.get("backend_priority_rank"),
                    "backend_priority_total": item.get("backend_priority_total"),
                    "backend_priority_percentile": item.get(
                        "backend_priority_percentile"
                    ),
                    "initial_recommendation_date": target_date.isoformat(),
                    "initial_recommendation_rank": item.get(
                        "recommendation_rank"
                    ),
                    "initial_backend_priority_rank": item.get(
                        "backend_priority_rank"
                    ),
                    "initial_phase2_role": item.get("phase2_role"),
                    "initial_entry_state": item.get("phase2_entry_state"),
                    "initial_momentum_freshness": item.get(
                        "phase2_momentum_freshness"
                    ),
                    "initial_watch_quality_state": item.get(
                        "phase2_watch_quality_state"
                    ),
                    "initial_quality_evidence": item.get("quality_evidence"),
                    "initial_theme_cluster": item.get("theme_cluster"),
                    "initial_recommendation_thesis": item.get(
                        "recommendation_thesis"
                    ),
                    "initial_relative_advantage": item.get(
                        "relative_advantage"
                    ),
                    "initial_instrument_validation": item.get(
                        "business_validation"
                    ),
                    "initial_theme_validation": item.get("theme_validation"),
                    "initial_catalyst_summary": item.get("catalyst_summary"),
                    "initial_research_confidence": item.get(
                        "research_confidence"
                    ),
                    "initial_selection_version": item.get(
                        "selection_version"
                    ),
                    "initial_prompt_versions": {
                        "prompt_family_version": processing_summary.get(
                            "prompt_family_version"
                        ),
                        "shared_policy_version": processing_summary.get(
                            "shared_policy_version"
                        ),
                        "research_prompt_version": processing_summary.get(
                            "research_prompt_version"
                        ),
                        "assessment_prompt_version": processing_summary.get(
                            "assessment_prompt_version"
                        ),
                        "global_selector_version": processing_summary.get(
                            "global_selector_version"
                        ),
                        "reason_prompt_version": processing_summary.get(
                            "reason_prompt_version"
                        ),
                        "tracking_prompt_version": processing_summary.get(
                            "tracking_prompt_version"
                        ),
                        "tracking_state_machine_version": processing_summary.get(
                            "tracking_state_machine_version"
                        ),
                        "prompt_sha256": processing_summary.get(
                            "prompt_sha256"
                        ),
                    },
                    "prompt_version": item.get("prompt_version"),
                    "momentum_score_version": momentum.current_momentum_score_version(),
                }
            tracking_result = _run_p4_tracking(
                db,
                job=job,
                target_date=target_date,
                market_context=market_context,
                ingestion=ingestion,
                momentum_frame=momentum_frame,
                current_candidates=pool,
                watchlist=final_payload.get("watchlist", []),
                processing_summary=processing_summary,
            )
            tracking_failures = [
                *tracking_result.get("technical_failures", []),
                *tracking_result.get("conflicts", []),
            ]
            technical_failures.extend(tracking_failures)
            failed_stock_ids.update(
                str(item.get("stock") or item.get("stock_id") or "")
                for item in tracking_failures
                if item.get("stock") or item.get("stock_id")
            )
            processing_summary.update(
                {
                    "technical_failures": technical_failures,
                    "technical_failure_count": len(technical_failures),
                    "tracking_review_count": len(
                        tracking_result.get("reviews", [])
                    ),
                    "tracking_review_failed_count": (
                        tracking_result.get("tracking_summary", {}).get(
                            "review_failed_count", 0
                        )
                    ),
                    "tracking_conflict_count": (
                        tracking_result.get("tracking_summary", {}).get(
                            "conflict_count", 0
                        )
                    ),
                    "is_complete": not technical_failures,
                }
            )
            processing_summary["prompt_payload_metrics"]["tracking"] = (
                tracking_result.get("tracking_summary", {}).get(
                    "prompt_payload_metrics", []
                )
            )
            final_summary["technical_failures"] = technical_failures
            final_summary["selection_summary"][
                "technical_failure_count"
            ] = len(technical_failures)
            final_summary["tracking_summary"] = tracking_result.get(
                "tracking_summary", {}
            )
            final_summary["processing_summary"] = processing_summary
            _set_progress(
                db,
                job,
                stage=STAGE_PERSIST,
                pct=98,
                label="保存 P3 與 P4 結果",
            )
            _persist_snapshot(db, target_date, final_payload, job_id)
            signal_archive.persist_signal_watch_hits(db, target_date, final_payload, job_id)

            if technical_failures:
                _mark_partial_failure(
                    db,
                    job,
                    (
                        f"{len(failed_stock_ids)} candidates were not fully processed; "
                        f"research_failed={len(research_failures)}, "
                        f"decision_failed={len(decision_failures)}, "
                        "tracking_failed="
                        f"{processing_summary.get('tracking_review_failed_count', 0)}, "
                        "tracking_conflicts="
                        f"{processing_summary.get('tracking_conflict_count', 0)}"
                    ),
                )
            else:
                _mark_done(db, job)
        except Exception:
            tb = traceback.format_exc()
            logger.exception(
                "Signal pipeline failed: job_id=%s target_date=%s",
                job_id,
                target_date,
            )
            _mark_failed(db, job, tb)
            raise
    finally:
        db.close()


def _set_progress(
    db: Session,
    job: SignalGenerationJob,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    pct: Optional[int] = None,
    label: Optional[str] = None,
) -> None:
    """更新 job 進度欄位並 commit。所有參數皆 optional，只 set 提供的欄位。"""
    if status is not None:
        job.status = status
    if stage is not None:
        job.current_stage = stage
    if pct is not None:
        job.progress_pct = pct
    if label is not None:
        job.progress_label = label
    db.commit()


def _build_pipeline_market_context(
    db: Session,
    *,
    target_date: date,
    regime_info: Dict[str, Any],
) -> Dict[str, Any]:
    market_context = llm_caller.assemble_market_context(
        market_snapshot.build_db_market_snapshot(db, target_date)
    )
    market_context["target_date"] = target_date.isoformat()
    market_context["market_regime"] = regime_info["regime"]
    market_context["market_regime_label"] = regime_info["regime_label"]
    market_context["market_regime_reason"] = regime_info["reason"]
    market_context["breadth_score"] = regime_info.get("breadth_score")
    market_context["market_regime_detail"] = regime_info.get("regime_detail")
    try:
        market_context["margin_climate"] = (
            market_margin.compute_market_margin_snapshot(db, target_date)
        )
    except Exception:
        logger.exception(
            "compute_market_margin_snapshot failed; continuing without it"
        )
        market_context["margin_climate"] = {
            "target_date": target_date.isoformat(),
            "data_available": False,
            "climate_label": "unknown",
            "climate_reason": "大盤融資融券資料聚合失敗。",
        }
    return market_context


def _run_p4_tracking_only_day(
    db: Session,
    *,
    job: SignalGenerationJob,
    target_date: date,
    ingestion: Dict[str, Any],
    momentum_frame: Dict[str, Dict[str, Any]],
    processing_summary: Dict[str, Any],
    job_id: str,
) -> None:
    """Finish a valid trading day with zero P3 candidates without skipping P4."""

    regime_info = market_regime.compute_market_regime(db, target_date)
    breadth = market_breadth.compute_breadth_from_frame(
        momentum_frame,
        ingestion.get("stocks_master") or {},
    )
    regime_detail = market_breadth.resolve_regime_detail(
        regime_info["regime"],
        breadth.get("breadth_score"),
    )
    regime_info = {
        **regime_info,
        "regime_detail": regime_detail,
        "breadth_score": breadth.get("breadth_score"),
    }
    market_context = _build_pipeline_market_context(
        db,
        target_date=target_date,
        regime_info=regime_info,
    )
    processing_summary.update(
        {
            "global_selection_status": "COMPLETED",
            "selection_complete": True,
            "global_selection_eligible_count": 0,
            "global_selection_recommended_count": 0,
            "global_selection_not_selected_count": 0,
            "final_watch_count": 0,
            "final_remove_count": 0,
        }
    )
    tracking_result = _run_p4_tracking(
        db,
        job=job,
        target_date=target_date,
        market_context=market_context,
        ingestion=ingestion,
        momentum_frame=momentum_frame,
        current_candidates=[],
        watchlist=[],
        processing_summary=processing_summary,
    )
    technical_failures = [
        *tracking_result.get("technical_failures", []),
        *tracking_result.get("conflicts", []),
    ]
    processing_summary.update(
        {
            "technical_failures": technical_failures,
            "technical_failure_count": len(technical_failures),
            "tracking_review_count": len(tracking_result.get("reviews", [])),
            "tracking_review_failed_count": (
                tracking_result.get("tracking_summary", {}).get(
                    "review_failed_count", 0
                )
            ),
            "tracking_conflict_count": (
                tracking_result.get("tracking_summary", {}).get(
                    "conflict_count", 0
                )
            ),
            "is_complete": not technical_failures,
        }
    )
    processing_summary.setdefault("prompt_payload_metrics", {})[
        "tracking"
    ] = tracking_result.get("tracking_summary", {}).get(
        "prompt_payload_metrics", []
    )
    payload = llm_caller.assemble_final_output(
        market_context,
        [],
        candidate_pool_size=0,
    )
    payload["watchlist"] = []
    payload["not_selected"] = []
    payload["removed"] = []
    payload["final_watchlist_size"] = 0
    summary = payload.setdefault("summary", {})
    summary["technical_failures"] = technical_failures
    summary["tracking_summary"] = tracking_result.get("tracking_summary", {})
    summary["selection_summary"] = {
        "phase2_eligible_count": 0,
        "research_completed_count": 0,
        "veto_removed_count": 0,
        "global_eligible_count": 0,
        "recommended_count": 0,
        "not_selected_count": 0,
        "technical_failure_count": len(technical_failures),
        "selection_version": prompt_family.stage_version("global_selector"),
        "selection_complete": True,
        "status": "COMPLETED",
        "selection_rationale": "Valid trading day with no P3 candidates.",
    }
    summary["processing_summary"] = processing_summary
    _set_progress(
        db,
        job,
        stage=STAGE_PERSIST,
        pct=98,
        label="保存 P4 追蹤結果",
    )
    _persist_snapshot(db, target_date, payload, job_id)
    signal_archive.clear_signal_watch_hits_for_date(db, target_date)
    if technical_failures:
        _mark_partial_failure(
            db,
            job,
            f"P4 tracking had {len(technical_failures)} technical failures.",
        )
    else:
        _mark_done(db, job)


def _run_p4_tracking(
    db: Session,
    *,
    job: SignalGenerationJob,
    target_date: date,
    market_context: Dict[str, Any],
    ingestion: Dict[str, Any],
    momentum_frame: Dict[str, Dict[str, Any]],
    current_candidates: list[Dict[str, Any]],
    watchlist: list[Dict[str, Any]],
    processing_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Run P4 after P3 without allowing tracking failure to erase P3 output."""

    _set_progress(
        db,
        job,
        stage=STAGE_TRACKING,
        pct=96,
        label="每日觀察生命週期檢查",
    )
    prompt_versions = {
        key: processing_summary.get(key)
        for key in (
            "prompt_family_version",
            "shared_policy_version",
            "research_prompt_version",
            "assessment_prompt_version",
            "global_selector_version",
            "reason_prompt_version",
            "tracking_prompt_version",
            "tracking_state_machine_version",
            "prompt_sha256",
        )
    }
    try:
        with db.begin_nested():
            observation_lifecycle.sync_recommendations(
                db,
                signal_date=target_date,
                watchlist=watchlist,
                prompt_versions=prompt_versions,
            )
            return observation_lifecycle.run_daily_observation_reviews(
                db,
                review_date=target_date,
                market_context=market_context,
                p3_recommended_stock_ids=[
                    str(item.get("stock") or "")
                    for item in watchlist
                    if item.get("stock")
                ],
                ingestion=ingestion,
                momentum_frame=momentum_frame,
                current_candidates=current_candidates,
                persist=False,
            )
    except Exception as exc:
        logger.exception("P4 daily observation lifecycle failed")
        return {
            "tracking_summary": {
                "review_date": target_date.isoformat(),
                "active_before_review": 0,
                "continue_count": 0,
                "caution_count": 0,
                "stopped_count": 0,
                "review_failed_count": 0,
                "conflict_count": 0,
                "review_complete": False,
                "tracking_prompt_version": (
                    observation_lifecycle.current_tracking_prompt_version()
                ),
                "tracking_state_machine_version": observation_lifecycle.STATE_MACHINE_VERSION,
            },
            "reviews": [],
            "technical_failures": [
                {
                    "stock": None,
                    "stage": "TRACKING",
                    "status": "TRACKING_BATCH_FAILED",
                    "processing_status": "REVIEW_FAILED",
                    "error_code": "TRACKING_BATCH_FAILED",
                    "error_summary": str(exc)[:500],
                }
            ],
            "conflicts": [],
        }


def _mark_done(db: Session, job: SignalGenerationJob) -> None:
    job.status = "done"
    job.progress_pct = 100
    job.finished_at = datetime.utcnow()
    db.commit()


def _mark_partial_failure(
    db: Session,
    job: SignalGenerationJob,
    error_summary: str,
) -> None:
    """Persist a terminal incomplete state without discarding the usable snapshot."""
    job.status = "partial_failure"
    job.progress_pct = 100
    job.progress_label = "部分候選處理失敗"
    job.error_message = error_summary[:2000]
    job.finished_at = datetime.utcnow()
    db.commit()


def _mark_failed(
    db: Session,
    job: SignalGenerationJob,
    traceback_text: str,
) -> None:
    """異常時更新 job 狀態。先 rollback 清 session error state，再 re-fetch 後寫狀態。"""
    db.rollback()
    job_again = db.get(SignalGenerationJob, job.job_id)
    if job_again is None:
        logger.error("Cannot mark failed: job_id=%s no longer exists", job.job_id)
        return
    job_again.status = "failed"
    job_again.error_message = traceback_text[:2000]
    job_again.finished_at = datetime.utcnow()
    db.commit()


def _persist_snapshot(
    db: Session,
    target_date: date,
    payload: Dict[str, Any],
    job_id: str,
) -> None:
    """UPSERT signal_snapshots（spec §5 Step 9）。

    payload 應由 `llm_caller.assemble_final_output()` 產出，含：
      - market_context / watchlist / removed / summary
      - candidate_pool_size / final_watchlist_size
      - llm_model / llm_total_tokens
    """
    existing = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == target_date)
        .one_or_none()
    )
    fields: Dict[str, Any] = {
        "market_context": payload.get("market_context", {}),
        "watchlist": payload.get("watchlist", []),
        "removed": payload.get("removed", []),
        "summary": payload.get("summary", {}),
        "candidate_pool_size": payload.get("candidate_pool_size"),
        "final_watchlist_size": payload.get("final_watchlist_size"),
        "llm_model": payload.get("llm_model"),
        "llm_total_tokens": payload.get("llm_total_tokens"),
        "prompt_version": payload.get("prompt_version") or "v1",
        "job_id": job_id,
    }
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.generated_at = datetime.utcnow()
    else:
        db.add(SignalSnapshot(snapshot_date=target_date, **fields))
    db.commit()


def _run_parallel_batches(
    batches: list[list],
    runner: Callable[[list], list],
    *,
    stage: str,
    concurrency: int,
    on_batch_done: Optional[Callable[[int], None]] = None,
) -> BatchExecution:
    """Run every batch, isolate failures, and preserve deterministic result order."""
    if not batches:
        return BatchExecution(results=[], batches=[], failures=[])

    total_batches = len(batches)
    batch_runs = [
        {
            "batch_index": idx + 1,
            "total_batches": total_batches,
            "candidate_count": len(batch),
            "candidate_ids": [_candidate_id(item) for item in batch],
            "started_at": None,
            "finished_at": None,
            "status": "PENDING",
            "retry_count": 0,
            "error_summary": None,
        }
        for idx, batch in enumerate(batches)
    ]
    results_by_index: dict[int, list] = {}
    failures: list[Dict[str, Any]] = []
    done_count = 0

    def record_failure(idx: int, batch: list, exc: Exception) -> None:
        error_summary = f"{type(exc).__name__}: {exc}"[:500]
        meta = batch_runs[idx]
        meta["status"] = "FAILED"
        meta["finished_at"] = datetime.utcnow().isoformat()
        meta["error_summary"] = error_summary
        failure_status = f"{stage.upper()}_FAILED"
        for item in batch:
            failures.append(
                {
                    "stock_id": _candidate_id(item),
                    "stock": _candidate_id(item),
                    "stage": stage.upper(),
                    "status": failure_status,
                    "processing_status": failure_status,
                    "batch_index": idx + 1,
                    "error_summary": error_summary,
                }
            )
        logger.exception(
            "%s batch %d/%d failed for candidates=%s",
            stage,
            idx + 1,
            total_batches,
            meta["candidate_ids"],
            exc_info=exc,
        )

    if concurrency <= 1 or len(batches) == 1:
        for idx, batch in enumerate(batches):
            meta = batch_runs[idx]
            meta["status"] = "RUNNING"
            meta["started_at"] = datetime.utcnow().isoformat()
            try:
                batch_result = runner(batch)
                if not isinstance(batch_result, list):
                    raise TypeError("batch runner must return a list")
                results_by_index[idx] = batch_result
                meta["status"] = "COMPLETED"
                meta["finished_at"] = datetime.utcnow().isoformat()
            except Exception as exc:
                record_failure(idx, batch, exc)
            done_count += len(batch)
            if on_batch_done is not None:
                on_batch_done(done_count)
    else:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
            future_to_meta = {}
            for idx, batch in enumerate(batches):
                meta = batch_runs[idx]
                meta["status"] = "RUNNING"
                meta["started_at"] = datetime.utcnow().isoformat()
                future_to_meta[executor.submit(runner, batch)] = (idx, batch)
            for future in as_completed(future_to_meta):
                idx, batch = future_to_meta[future]
                meta = batch_runs[idx]
                try:
                    batch_result = future.result()
                    if not isinstance(batch_result, list):
                        raise TypeError("batch runner must return a list")
                    results_by_index[idx] = batch_result
                    meta["status"] = "COMPLETED"
                    meta["finished_at"] = datetime.utcnow().isoformat()
                except Exception as exc:
                    record_failure(idx, batch, exc)
                done_count += len(batch)
                if on_batch_done is not None:
                    on_batch_done(done_count)

    flattened: list = []
    for idx in range(len(batches)):
        flattened.extend(results_by_index.get(idx, []))
    return BatchExecution(results=flattened, batches=batch_runs, failures=failures)


def _build_batches(items: list, batch_size: int) -> list[list]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _candidate_id(item: Dict[str, Any]) -> str:
    return str(item.get("stock_id") or item.get("stock") or "")


def _partition_stage_results(
    execution: BatchExecution,
    *,
    failure_status: str,
) -> tuple[list, list[Dict[str, Any]]]:
    """Separate LLM technical fallbacks from genuine stage outputs."""
    successful: list = []
    failures = list(execution.failures)
    failed_ids = {item.get("stock_id") for item in failures}

    for item in execution.results:
        processing_status = str(item.get("processing_status") or "").upper()
        if item.get("_unavailable") or processing_status.endswith("_FAILED"):
            stock_id = _candidate_id(item)
            if stock_id not in failed_ids:
                diagnostic = item.get("llm_diagnostic") or {}
                error_summary = (
                    item.get("_unavailable_reason")
                    or item.get("short_reason")
                    or diagnostic.get("message")
                    or failure_status
                )
                failures.append(
                    {
                        "stock_id": stock_id,
                        "stock": stock_id,
                        "stage": failure_status.rsplit("_FAILED", 1)[0],
                        "status": failure_status,
                        "processing_status": failure_status,
                        "batch_index": _batch_index_for_stock(execution.batches, stock_id),
                        "error_summary": str(error_summary)[:500],
                    }
                )
                failed_ids.add(stock_id)
            continue
        successful.append(item)

    failure_by_batch: dict[int, list[str]] = {}
    for failure in failures:
        batch_index = failure.get("batch_index")
        if isinstance(batch_index, int):
            failure_by_batch.setdefault(batch_index, []).append(
                str(failure.get("error_summary") or failure_status)
            )
    for meta in execution.batches:
        errors = failure_by_batch.get(meta["batch_index"])
        if errors:
            meta["status"] = "FAILED"
            meta["error_summary"] = "; ".join(dict.fromkeys(errors))[:500]

    return successful, failures


def _collect_prompt_payload_metrics(
    items: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Collect one bounded metrics record per LLM batch, never full payload text."""
    output: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        diagnostic = item.get("llm_diagnostic")
        metrics = (
            diagnostic.get("payload_metrics")
            if isinstance(diagnostic, dict)
            else None
        )
        if not isinstance(metrics, dict):
            continue
        key = repr(sorted(metrics.items()))
        if key in seen:
            continue
        seen.add(key)
        output.append(dict(metrics))
    return output


def _batch_index_for_stock(batch_runs: list[Dict[str, Any]], stock_id: str) -> Optional[int]:
    for meta in batch_runs:
        if stock_id in meta.get("candidate_ids", []):
            return int(meta["batch_index"])
    return None


def _order_llm_input(
    candidates: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Return every eligible candidate in deterministic processing priority order."""
    return sorted(candidates, key=_llm_input_sort_key)


def _cap_llm_input(
    candidates: list[Dict[str, Any]],
    *,
    limit: Optional[int] = None,
) -> list[Dict[str, Any]]:
    """Compatibility wrapper retained for callers; P1 intentionally ignores ``limit``."""
    del limit
    return _order_llm_input(candidates)


def _llm_input_sort_key(candidate: Dict[str, Any]) -> tuple:
    """Choose the deterministic processing order for Phase 2 or legacy candidates."""
    if "role" in candidate or "tracking_state" in candidate:
        return _phase2_llm_priority_key(candidate)

    prelim_type = str(candidate.get("prelim_type") or "").upper()
    priority = {
        "LEADER": 0,
        "FOLLOWER": 1,
        "ROTATION_LAGGARD": 2,
        "LAGGARD_CANDIDATE": 2,  # 舊命名相容（v2.1 改名 ROTATION_LAGGARD）
    }.get(prelim_type, 3)
    flow_3d = float(candidate.get("total_institution_flow_3d") or 0.0)
    flow_1d = float(candidate.get("total_institution_flow_1d") or 0.0)
    price_5d = float(candidate.get("price_change_5d") or 0.0)
    top_stock_bonus = 1 if candidate.get("in_top_stocks_3d") else 0
    top_industry_bonus = 1 if candidate.get("in_top_industries_3d") else 0

    return (
        priority,
        -top_stock_bonus,
        -top_industry_bonus,
        -flow_3d,
        -flow_1d,
        -price_5d,
        str(candidate.get("stock_id") or ""),
    )


def _phase2_llm_priority_key(candidate: Dict[str, Any]) -> tuple:
    """LLM v6 contract §29-31（2026-07-22）：Phase 2 processing order 不用
    `prelim_type`（display_type 映射後的
    LEADER/FOLLOWER/LAGGARD 三桶）當主排序——`EMERGING_MOMENTUM` /
    `UNCLASSIFIED_MOMENTUM` 這類角色會被映射進 FOLLOWER/LAGGARD 桶，用桶排序
    排序會系統性把它們排到後面，重新帶入 legacy bias。

    改用 deterministic 數字排序：conviction（backend 信心度）> momentum_score >
    rs_market_percentile_20d > risk_warnings 數量（越少越優先）。`internal_role`
    本身**不參與排序**，只是候選攜帶的描述性資訊——spec §31 明確要求「不可因為
    UNCLASSIFIED → automatic low quality，實際仍以 numeric momentum/conviction/
    risk 為主」。
    """
    conviction_rank = {"high": 0, "medium": 1, "low": 2}.get(
        candidate.get("conviction"), 3
    )
    momentum_score = float(candidate.get("momentum_score") or 0.0)
    rs_market = float(candidate.get("rs_market_percentile_20d") or 0.0)
    risk_warning_count = len(candidate.get("risk_warnings") or [])

    return (
        conviction_rank,
        -momentum_score,
        -rs_market,
        risk_warning_count,
        str(candidate.get("stock_id") or ""),
    )
