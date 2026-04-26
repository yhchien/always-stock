"""
M23 每日異常訊號清單 API（spec §11）

四個 endpoint：
- GET  /api/signals/latest                 公開，取最新一筆 SignalSnapshot（無 → 404）
- GET  /api/signals/snapshot/{date}        公開，指定日期 snapshot（無 → 404）
- GET  /api/signals/jobs/latest            公開，最新 job 給前端進度條 polling
- POST /api/signals/regenerate             需登入，背景重產 + 限頻 + concurrency guard

重要設計（spec §11.4 / §11.5）：
- regenerate 用 BackgroundTasks 觸發 `run_signal_pipeline_sync`，立刻回 202
- 同一個 `snapshot_date` 已有 `running` job → 409 Conflict（讓前端讀進度而非重觸發）
- 同一日 user 限頻 1 次 → 429
- 全站同一日累計 5 次 → 429
- Pipeline 不能用 request session（請求結束會 close）→ 注入 `SessionLocal` factory
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import SessionLocal, get_db
from app.models import SignalGenerationJob, SignalSnapshot, User
from app.signals.pipeline import run_signal_pipeline_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signals", tags=["signals"])


# Spec §11.4：限頻與 concurrency 上限
USER_DAILY_REGENERATE_LIMIT = 1
GLOBAL_DAILY_REGENERATE_LIMIT = 5


# ---------------------------------------------------------------------------
# Pydantic schema（spec §10.3 + §11.3）
# ---------------------------------------------------------------------------


class SnapshotResponse(BaseModel):
    """spec §10.3 對外 API Response Schema。"""

    snapshot_date: date
    generated_at: datetime
    llm_model: Optional[str]
    data: Dict[str, Any]


class JobResponse(BaseModel):
    """spec §11.3 job polling schema。"""

    job_id: str
    snapshot_date: date
    status: str
    current_stage: Optional[str]
    progress_pct: int
    progress_label: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]
    error_message: Optional[str]


class RegenerateAcceptedResponse(BaseModel):
    job_id: str
    snapshot_date: date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_snapshot(snap: SignalSnapshot) -> SnapshotResponse:
    """SignalSnapshot ORM → spec §10.3 schema（meta + data 包裹）。"""
    data: Dict[str, Any] = {
        "market_context": snap.market_context or {},
        "watchlist": snap.watchlist or [],
        "removed": snap.removed or [],
        "summary": snap.summary or {},
        "candidate_pool_size": snap.candidate_pool_size,
        "final_watchlist_size": snap.final_watchlist_size,
    }
    return SnapshotResponse(
        snapshot_date=snap.snapshot_date,
        generated_at=snap.generated_at,
        llm_model=snap.llm_model,
        data=data,
    )


def _serialize_job(job: SignalGenerationJob) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        snapshot_date=job.snapshot_date,
        status=job.status,
        current_stage=job.current_stage,
        progress_pct=job.progress_pct or 0,
        progress_label=job.progress_label,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
    )


def _resolve_target_date(db: Session) -> date:
    """fallback target date 用最新 snapshot；若 DB 完全空則用今天（限頻仍會擋）。"""
    latest = (
        db.query(SignalSnapshot)
        .order_by(SignalSnapshot.snapshot_date.desc())
        .first()
    )
    if latest is not None:
        return latest.snapshot_date
    return date.today()


def _has_running_job_for_date(db: Session, target_date: date) -> bool:
    return (
        db.query(SignalGenerationJob)
        .filter(
            SignalGenerationJob.snapshot_date == target_date,
            SignalGenerationJob.status.in_(("pending", "running")),
        )
        .first()
        is not None
    )


def _user_count_today(db: Session, user_id: int, target_date: date) -> int:
    """同一使用者當日已觸發次數（不論成敗）。"""
    return (
        db.query(SignalGenerationJob)
        .filter(
            SignalGenerationJob.snapshot_date == target_date,
            SignalGenerationJob.triggered_by == f"user:{user_id}",
        )
        .count()
    )


def _global_count_today(db: Session, target_date: date) -> int:
    """全站同日觸發次數（含 cron / user / admin）。"""
    return (
        db.query(SignalGenerationJob)
        .filter(SignalGenerationJob.snapshot_date == target_date)
        .count()
    )


def _run_pipeline_safely(job_id: str, target_date: date) -> None:
    """BackgroundTasks 包裝：吞例外不讓 worker crash（pipeline 自身會把狀態寫成 failed）。"""
    try:
        run_signal_pipeline_sync(
            job_id=job_id,
            target_date=target_date,
            session_factory=SessionLocal,
        )
    except Exception:
        logger.exception(
            "Background signal pipeline raised: job_id=%s target=%s",
            job_id,
            target_date,
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/latest", response_model=SnapshotResponse)
def get_latest_signal(db: Session = Depends(get_db)) -> SnapshotResponse:
    """spec §11.1：取最新一筆 snapshot；DB 無 → 404。"""
    snap = (
        db.query(SignalSnapshot)
        .order_by(SignalSnapshot.snapshot_date.desc())
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No snapshot yet")
    return _serialize_snapshot(snap)


@router.get("/snapshot/{snapshot_date}", response_model=SnapshotResponse)
def get_snapshot_by_date(
    snapshot_date: date,
    db: Session = Depends(get_db),
) -> SnapshotResponse:
    """spec §11.2：指定日期 snapshot；無 → 404。"""
    snap = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == snapshot_date)
        .first()
    )
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No snapshot for {snapshot_date.isoformat()}",
        )
    return _serialize_snapshot(snap)


@router.get("/jobs/latest", response_model=Optional[JobResponse])
def get_latest_job(db: Session = Depends(get_db)) -> Optional[JobResponse]:
    """spec §11.3：最新 job（不論 status）；無任何 job → 回 null（不是 404，前端少處理一個分支）。"""
    job = (
        db.query(SignalGenerationJob)
        .order_by(SignalGenerationJob.started_at.desc())
        .first()
    )
    if job is None:
        return None
    return _serialize_job(job)


@router.post(
    "/regenerate",
    response_model=RegenerateAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_signals(
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RegenerateAcceptedResponse:
    """spec §11.4：觸發背景重產 + 限頻 + concurrency guard。

    錯誤碼：
      - 401 未登入（Depends require_user）
      - 409 同 snapshot_date 已有 running job
      - 429 user 同日已達 1 次 / 全站同日已達 5 次
      - 202 Accepted + { job_id, snapshot_date }
    """
    target_date = _resolve_target_date(db)

    if _has_running_job_for_date(db, target_date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此日期已有產生中的 job，請等候完成",
        )

    if _user_count_today(db, user.id, target_date) >= USER_DAILY_REGENERATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="您今日已觸發過訊號重產，明日再試",
        )

    if _global_count_today(db, target_date) >= GLOBAL_DAILY_REGENERATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="全站今日訊號重產次數已達上限，請稍後",
        )

    job_id = str(uuid.uuid4())
    job = SignalGenerationJob(
        job_id=job_id,
        snapshot_date=target_date,
        triggered_by=f"user:{user.id}",
        status="pending",
        current_stage=None,
        progress_pct=0,
        progress_label="排程中",
        started_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()

    background_tasks.add_task(_run_pipeline_safely, job_id, target_date)
    logger.info(
        "Signal regeneration triggered: user=%s target=%s job=%s",
        user.id,
        target_date,
        job_id,
    )
    return RegenerateAcceptedResponse(job_id=job_id, snapshot_date=target_date)
