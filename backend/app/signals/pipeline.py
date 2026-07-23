"""
M23 Pipeline 主流程：cron / BackgroundTasks 共用入口。

對應 spec §5（10 步 pipeline）+ §11.5（背景任務實作）。

設計原則：
  - 每個 stage 結束 commit 一次 job 進度，前端 polling 即時看到進度
  - 任何 stage 拋例外 → 先 rollback session 清 error state，再寫
    `job.status=failed` + `error_message=traceback`，最後 raise 讓 caller 紀錄
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
import logging
import os
import traceback
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SignalGenerationJob, SignalSnapshot
from app.signals import archive as signal_archive
from app.signals import candidate_pool, classification, filters, llm_caller
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
STAGE_PERSIST = "persist"
LLM_BATCH_CONCURRENCY = 2
LLM_INPUT_HARD_LIMIT = 50

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
      3. 跑完 → status=done + progress_pct=100 + finished_at
      4. 任何 stage 拋例外 → status=failed + error_message=traceback + finished_at，
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

            # 短路：候選池空 → raise ValueError 讓 cron 分類為 exit 1 (no_data)
            # 觸發情境：週末 / 假日跑、target_date DB 無交易資料、或當天市場太冷沒檔股票
            # 通過篩選。沒短路會導致 LLM 跑空 batch、最後寫一筆全空的 snapshot 並 status=done，
            # 看起來像「成功但 0 檔」很難跟「真的沒抓到」區分。
            if not pool:
                raise ValueError(
                    f"no candidate stocks for target_date={target_date}"
                )

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
            llm_input = _cap_llm_input(after_hard, limit=LLM_INPUT_HARD_LIMIT)
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
            # prelim_type，套用同一個 LLM_INPUT_HARD_LIMIT 上限）**取代**上面 legacy
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
                    phase2_after_regime = _cap_llm_input(
                        phase2_llm_eligible, limit=LLM_INPUT_HARD_LIMIT
                    )
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
                    logger.info(
                        "Phase 2 production mode active for %s: candidates=%d "
                        "survivors=%d llm_eligible=%d (watch_quality_mode=%s, capped to "
                        "%d for LLM); legacy would have produced %d survivors",
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
            db_market_snapshot = market_snapshot.build_db_market_snapshot(db, target_date)
            market_context = llm_caller.assemble_market_context(db_market_snapshot)
            # M27：把 deterministic regime 掛進 market_context（backend authoritative，全市場一個）。
            # 攤平成 string + label + reason，避免 LLM 把巢狀 object 當每檔不同的 regime。
            market_context["market_regime"] = regime_info["regime"]
            market_context["market_regime_label"] = regime_info["regime_label"]
            market_context["market_regime_reason"] = regime_info["reason"]
            # v2.2 市場廣度（觀察欄位；LLM 契約的 market_regime 仍是 3 態）
            market_context["breadth_score"] = regime_info.get("breadth_score")
            market_context["market_regime_detail"] = regime_info.get("regime_detail")
            # 2026-05-25：把大盤融資融券盤勢塞進 market_context，供 explanation /
            # watch_reason 兩 stage 共用（cache 4h，多檔 batch 不重算）
            try:
                market_context["margin_climate"] = market_margin.compute_market_margin_snapshot(
                    db, target_date
                )
            except Exception:
                logger.exception("compute_market_margin_snapshot failed; continuing without it")
                market_context["margin_climate"] = {
                    "target_date": target_date.isoformat(),
                    "data_available": False,
                    "climate_label": "unknown",
                    "climate_reason": "大盤融資融券資料聚合失敗。",
                }
            research_batch_size = llm_caller.DEFAULT_RESEARCH_BATCH_SIZE
            research_batches = [
                after_regime[i : i + research_batch_size]
                for i in range(0, len(after_regime), research_batch_size)
            ]
            research_results = _run_parallel_batches(
                research_batches,
                lambda batch: llm_caller.run_research_batch(batch, market_context),
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_RESEARCH,
                    pct=45 + int(30 * done_count / total_for_llm),
                    label=f"研究第 {done_count} / {len(after_regime)} 檔",
                ),
            )

            # Step 6a：LLM 短 decision（全候選）
            total_for_explain = max(len(research_results), 1)
            explain_batch_size = llm_caller.DEFAULT_EXPLANATION_BATCH_SIZE
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_EXPLAIN,
                pct=75,
                label=f"LLM 初判（共 {len(research_results)} 檔）",
            )
            explain_batches = [
                research_results[i : i + explain_batch_size]
                for i in range(0, len(research_results), explain_batch_size)
            ]
            explanation = _run_parallel_batches(
                explain_batches,
                lambda chunk: llm_caller.run_explanation_batch(chunk, market_context),
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=75 + int(10 * done_count / total_for_explain),
                    label=f"初判第 {done_count} / {len(research_results)} 檔",
                ),
            )

            # Step 6b：只對 WATCH 名單補長理由
            watch_candidates = [
                item for item in explanation
                if str(item.get("decision") or "").upper() == "WATCH"
            ]
            total_for_watch_reason = max(len(watch_candidates), 1)
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_EXPLAIN,
                pct=86,
                label=f"補長理由（共 {len(watch_candidates)} 檔）",
            )
            watch_batches = [
                watch_candidates[i : i + explain_batch_size]
                for i in range(0, len(watch_candidates), explain_batch_size)
            ]
            enriched_watch = _run_parallel_batches(
                watch_batches,
                lambda chunk: llm_caller.run_watch_reason_batch(chunk, market_context),
                concurrency=LLM_BATCH_CONCURRENCY,
                on_batch_done=lambda done_count: _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=86 + int(9 * done_count / total_for_watch_reason),
                    label=f"長理由第 {done_count} / {len(watch_candidates)} 檔",
                ),
            )

            if enriched_watch:
                watch_by_id = {
                    str(item.get("stock") or item.get("stock_id") or ""): item
                    for item in enriched_watch
                }
                merged_explanation: list = []
                for item in explanation:
                    sid = str(item.get("stock") or item.get("stock_id") or "")
                    if sid and sid in watch_by_id:
                        merged_explanation.append({**item, **watch_by_id[sid]})
                    else:
                        merged_explanation.append(item)
                explanation = merged_explanation

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
                item["signal_metrics"] = signal_metrics_by_stock.get(sid)
            _persist_snapshot(db, target_date, final_payload, job_id)
            signal_archive.persist_signal_watch_hits(db, target_date, final_payload, job_id)

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


def _mark_done(db: Session, job: SignalGenerationJob) -> None:
    job.status = "done"
    job.progress_pct = 100
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
    concurrency: int,
    on_batch_done: Optional[Callable[[int], None]] = None,
) -> list:
    if not batches:
        return []
    if concurrency <= 1 or len(batches) == 1:
        out: list = []
        done_count = 0
        for batch in batches:
            result = runner(batch)
            out.extend(result)
            done_count += len(batch)
            if on_batch_done is not None:
                on_batch_done(done_count)
        return out

    results_by_index: dict[int, list] = {}
    done_count = 0
    with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as executor:
        future_to_meta = {
            executor.submit(runner, batch): (idx, len(batch))
            for idx, batch in enumerate(batches)
        }
        for future in as_completed(future_to_meta):
            idx, batch_size = future_to_meta[future]
            results_by_index[idx] = future.result()
            done_count += batch_size
            if on_batch_done is not None:
                on_batch_done(done_count)

    flattened: list = []
    for idx in range(len(batches)):
        flattened.extend(results_by_index[idx])
    return flattened


def _cap_llm_input(
    candidates: list[Dict[str, Any]],
    *,
    limit: int,
) -> list[Dict[str, Any]]:
    if limit <= 0 or len(candidates) <= limit:
        return list(candidates)

    ordered = sorted(candidates, key=_llm_input_sort_key)
    return ordered[:limit]


def _llm_input_sort_key(candidate: Dict[str, Any]) -> tuple:
    """LLM_INPUT_HARD_LIMIT 截斷排序。Phase 2 候選（有 `role`/`tracking_state`
    欄位，即使值為 None）改走 `_phase2_llm_priority_key`（見 2026-07-22 LLM v6
    contract 對齊 §29-31）；legacy 候選（兩個欄位都不存在）維持原本
    prelim_type-based 排序不變。
    """
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
    """LLM v6 contract §29-31（2026-07-22）：Phase 2 候選超過
    `LLM_INPUT_HARD_LIMIT` 時，不能再用 `prelim_type`（display_type 映射後的
    LEADER/FOLLOWER/LAGGARD 三桶）當主排序——`EMERGING_MOMENTUM` /
    `UNCLASSIFIED_MOMENTUM` 這類角色會被映射進 FOLLOWER/LAGGARD 桶，用桶排序
    截斷等於系統性把它們排到後面優先被砍掉，重新帶入本來要拿掉的 legacy bias。

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
