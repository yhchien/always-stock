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

import logging
import traceback
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SignalGenerationJob, SignalSnapshot
from app.signals import candidate_pool, classification, filters, llm_caller
from app.signals import market_snapshot

logger = logging.getLogger(__name__)


# Spec §11.5 / models.py current_stage enum
STAGE_INGEST = "ingest"
STAGE_RANK = "rank"
STAGE_CANDIDATE = "candidate"
STAGE_FILTER = "filter"
STAGE_LLM_RESEARCH = "llm_research"
STAGE_LLM_EXPLAIN = "llm_explain"
STAGE_PERSIST = "persist"


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
            pool = candidate_pool.build_candidate_pool(
                db, target_date, ingestion, rankings
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
            after_soft = filters.apply_soft_filters(db, target_date, after_hard)

            # Step 5：LLM Research（batch）
            total_for_llm = max(len(after_soft), 1)
            _set_progress(
                db,
                job,
                stage=STAGE_LLM_RESEARCH,
                pct=45,
                label=f"LLM 上網查詢（共 {len(after_soft)} 檔）",
            )
            db_market_snapshot = market_snapshot.build_db_market_snapshot(db, target_date)
            market_context = llm_caller.assemble_market_context(db_market_snapshot)
            research_results: list = []
            research_batch_size = llm_caller.DEFAULT_RESEARCH_BATCH_SIZE
            for i in range(0, len(after_soft), research_batch_size):
                batch = after_soft[i : i + research_batch_size]
                research_results.extend(
                    llm_caller.run_research_batch(batch, market_context)
                )
                done_count = min(i + research_batch_size, len(after_soft))
                pct = 45 + int(30 * done_count / total_for_llm)
                _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_RESEARCH,
                    pct=pct,
                    label=f"研究第 {done_count} / {len(after_soft)} 檔",
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
            explanation: list = []
            for i in range(0, len(research_results), explain_batch_size):
                chunk = research_results[i : i + explain_batch_size]
                explanation.extend(
                    llm_caller.run_explanation_batch(chunk, market_context)
                )
                done_count = min(i + explain_batch_size, len(research_results))
                pct = 75 + int(10 * done_count / total_for_explain)
                _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=pct,
                    label=f"初判第 {done_count} / {len(research_results)} 檔",
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
            enriched_watch: list = []
            for i in range(0, len(watch_candidates), explain_batch_size):
                chunk = watch_candidates[i : i + explain_batch_size]
                enriched_watch.extend(
                    llm_caller.run_watch_reason_batch(chunk, market_context)
                )
                done_count = min(i + explain_batch_size, len(watch_candidates))
                pct = 86 + int(9 * done_count / total_for_watch_reason)
                _set_progress(
                    db,
                    job,
                    stage=STAGE_LLM_EXPLAIN,
                    pct=pct,
                    label=f"長理由第 {done_count} / {len(watch_candidates)} 檔",
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
            _persist_snapshot(db, target_date, final_payload, job_id)

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
        "job_id": job_id,
    }
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.generated_at = datetime.utcnow()
    else:
        db.add(SignalSnapshot(snapshot_date=target_date, **fields))
    db.commit()
