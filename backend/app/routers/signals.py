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
- 全站同一日累計 10 次 → 429
- Pipeline 不能用 request session（請求結束會 close）→ 注入 `SessionLocal` factory
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import SessionLocal, get_db
from app.industry_flow_service import get_latest_industry_trade_date
from app.models import (
    SignalExpectationPrice,
    SignalGenerationJob,
    SignalSnapshot,
    SignalWatchHit,
    User,
)
from app.signals import archive as signal_archive
from app.signals import expectation_price as expectation_price_service
from app.signals.pipeline import run_signal_pipeline_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/signals", tags=["signals"])
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SIGNALS_SAME_DAY_READY_TIME = time(hour=19, minute=0)


# Spec §11.4：限頻與 concurrency 上限
# 每帳號每日最多可手動重產 3 次。
# 只計入 pending / running / done；failed 不算，避免使用者因系統錯誤白白耗掉額度。
USER_DAILY_REGENERATE_LIMIT = 3
GLOBAL_DAILY_REGENERATE_LIMIT = 15
_COUNTED_JOB_STATUSES = ("pending", "running", "done")

# Expectation price 手動重產（個股級別）：使用者一天 30 次、全站 100 次
USER_DAILY_EXPECTATION_LIMIT = 30
GLOBAL_DAILY_EXPECTATION_LIMIT = 100


# ---------------------------------------------------------------------------
# Pydantic schema（spec §10.3 + §11.3）
# ---------------------------------------------------------------------------


class SnapshotResponse(BaseModel):
    """spec §10.3 對外 API Response Schema。"""

    snapshot_date: date
    generated_at: datetime
    llm_model: Optional[str]
    prompt_version: str = "v1"
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


class RegenerateQuotaResponse(BaseModel):
    snapshot_date: date
    daily_limit: int
    used_count: int
    remaining_count: int
    disabled: bool


class SignalArchiveSummaryItemResponse(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    sub_industry: Optional[str]
    first_seen_date: date
    latest_hit_date: date
    tracking_day_index: int
    hit_count: int
    latest_signal_type: str
    baseline_trade_date: Optional[date]
    baseline_price: Optional[float]
    latest_eval_trade_date: Optional[date]
    latest_eval_price: Optional[float]
    return_pct: Optional[float]
    max_positive_return_pct: Optional[float]
    max_positive_return_trade_date: Optional[date]
    max_negative_return_pct: Optional[float]
    max_negative_return_trade_date: Optional[date]
    prompt_version: str = "v1"
    # M26：對應 (stock_id, first_seen_date) 的 SignalExpectationPrice 預測；舊資料 = None
    conservative_price: Optional[float] = None
    dream_price: Optional[float] = None
    # 2026-07-13：卡片極簡化 UI 用的 as_of 收盤價 + 當日漲跌幅（個股當日停牌 = None）
    latest_close_price: Optional[float] = None
    daily_change_pct: Optional[float] = None


class SignalArchiveSummaryResponse(BaseModel):
    as_of_trade_date: Optional[date]
    retention_trade_days: int
    items: List[SignalArchiveSummaryItemResponse]


class SignalArchiveCompletedItemResponse(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    sub_industry: Optional[str]
    first_seen_date: date
    latest_hit_date: date
    hit_count: int
    latest_signal_type: str
    baseline_trade_date: Optional[date]
    baseline_price: Optional[float]
    return_day_10_pct: Optional[float]
    return_day_20_pct: Optional[float]
    return_day_30_pct: Optional[float]
    max_positive_return_pct: Optional[float]
    max_positive_return_trade_date: Optional[date]
    max_negative_return_pct: Optional[float]
    max_negative_return_trade_date: Optional[date]
    completed_trade_date: date
    closure_reason: str = "completed_30_days"
    prompt_version: str = "v1"
    # M26：對應 (stock_id, first_seen_date) 的 SignalExpectationPrice 預測;舊資料 = None
    conservative_price: Optional[float] = None
    dream_price: Optional[float] = None


class SignalArchiveCompletedPeriodMeta(BaseModel):
    period_start: date
    period_end: date
    count: int


class SignalArchiveCompletedResponse(BaseModel):
    items: List[SignalArchiveCompletedItemResponse]
    periods: List[SignalArchiveCompletedPeriodMeta] = []
    selected_period_start: Optional[date] = None


class ExpectationPriceItemResponse(BaseModel):
    """個股「資金行情可期待價格區間」單筆 row。"""
    stock_id: str
    stock_name: str
    first_detected_date: date
    latest_detected_date: Optional[date]
    detected_type: Optional[str]
    industry_name: Optional[str]
    sub_industry: Optional[str]
    conservative_price: Optional[float]
    dream_price: Optional[float]
    valuation_mode: Optional[str]
    valuation_basis: Optional[str]
    current_price_position: Optional[str]
    chase_risk: Optional[str]
    confidence: Optional[str]
    detected_day_high: Optional[float]
    detected_day_close: Optional[float]
    current_price: Optional[float]
    hit_conservative_at: Optional[date]
    hit_dream_at: Optional[date]
    scorecard: Optional[Dict[str, Any]]
    classification: Optional[Dict[str, Any]]
    valuation_detail: Optional[Dict[str, Any]]
    reason_50_words: Optional[str]
    risk_note_30_words: Optional[str]
    source: str
    status: str
    error_message: Optional[str]
    generated_at: datetime
    updated_at: datetime


class ExpectationPriceListResponse(BaseModel):
    snapshot_date: Optional[date]  # 對應 query 的 snapshot_date（為 None 代表回全部）
    items: List[ExpectationPriceItemResponse]


class ExpectationRegenerateRequest(BaseModel):
    stock_id: str


class ExpectationRegenerateAcceptedResponse(BaseModel):
    stock_id: str
    status: str


class ExpectationQuotaResponse(BaseModel):
    daily_limit: int
    used_count: int
    remaining_count: int
    disabled: bool


class SignalArchiveReportResponse(BaseModel):
    snapshot_date: date
    signal_type: str
    reason: str
    business_summary: Optional[str]
    snapshot_generated_at: Optional[datetime]


class SignalArchiveDetailResponse(SignalArchiveSummaryItemResponse):
    reports: List[SignalArchiveReportResponse]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attach_canonical_classification(db: Session, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Phase 1 Canonical Classification（additive，2026-07-21）：對 watchlist/removed 每筆
    item 加一個 `canonical` key（display-only，選股決策已在 pipeline 完成，這裡純粹補顯示
    資訊）。查無分類（理論上不該發生，backfill 已覆蓋全 universe）時該 item 的 `canonical`
    為 None，不影響既有欄位。
    """
    from app.routers.classification import get_classification_for_stock

    # watchlist/removed item 的股票代號欄位是 `stock`（LLM output schema 既有命名），
    # 不是 `stock_id`——沿用既有契約，這裡只負責讀取後查表，不改動既有欄位語意
    enriched = []
    for it in items:
        sid = it.get("stock")
        canonical = get_classification_for_stock(db, sid) if sid else None
        enriched.append({**it, "canonical": canonical.model_dump() if canonical else None})
    return enriched


def _serialize_snapshot(snap: SignalSnapshot, db: Optional[Session] = None) -> SnapshotResponse:
    """SignalSnapshot ORM → spec §10.3 schema（meta + data 包裹）。"""
    watchlist = snap.watchlist or []
    removed = snap.removed or []
    if db is not None:
        watchlist = _attach_canonical_classification(db, watchlist)
        removed = _attach_canonical_classification(db, removed)
    data: Dict[str, Any] = {
        "market_context": snap.market_context or {},
        "watchlist": watchlist,
        "removed": removed,
        "summary": snap.summary or {},
        "candidate_pool_size": snap.candidate_pool_size,
        "final_watchlist_size": snap.final_watchlist_size,
    }
    return SnapshotResponse(
        snapshot_date=snap.snapshot_date,
        generated_at=snap.generated_at,
        llm_model=snap.llm_model,
        prompt_version=snap.prompt_version or "v1",
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


def _get_taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def _resolve_target_date(db: Session) -> date:
    """依台北時間決定 signals 應使用的最新可用交易日。

    規則：
      - 19:00 後：允許使用「今天」資料
      - 19:00 前：只使用「昨天以前」資料
      - 非交易日 / 假日：自然回退到 `<= ceiling` 的最近交易日

    若交易資料完全空，才退回既有最新 snapshot；再沒有才回今天。
    """
    now = _get_taipei_now()
    ceiling = now.date() if now.time() >= SIGNALS_SAME_DAY_READY_TIME else now.date() - timedelta(days=1)

    latest_trade_date = get_latest_industry_trade_date(db, ceiling)
    if latest_trade_date is not None:
        return latest_trade_date

    latest_snapshot = (
        db.query(SignalSnapshot)
        .order_by(SignalSnapshot.snapshot_date.desc())
        .first()
    )
    if latest_snapshot is not None:
        return latest_snapshot.snapshot_date
    return now.date()


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
    """同一使用者當日已觸發且應計次數（failed 不算）。"""
    return (
        db.query(SignalGenerationJob)
        .filter(
            SignalGenerationJob.snapshot_date == target_date,
            SignalGenerationJob.triggered_by == f"user:{user_id}",
            SignalGenerationJob.status.in_(_COUNTED_JOB_STATUSES),
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
        db = SessionLocal()
        try:
            signal_archive.update_signal_watch_returns(
                db,
                as_of_trade_date=target_date,
            )
        finally:
            db.close()
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
    return _serialize_snapshot(snap, db)


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
    return _serialize_snapshot(snap, db)


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


@router.get("/archive", response_model=SignalArchiveSummaryResponse)
def get_signal_archive(
    sort_by: str = Query(default="tracking_days_desc"),
    signal_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(
        default=0,
        ge=0,
        le=5000,
        description="0 = 不限筆數（魚尾追蹤期內全部回傳並留存）",
    ),
    db: Session = Depends(get_db),
) -> SignalArchiveSummaryResponse:
    payload = signal_archive.list_archive_summary(
        db,
        sort_by=sort_by,
        signal_type=signal_type,
        limit=limit,
    )
    return SignalArchiveSummaryResponse(**payload)


@router.get("/archive/completed", response_model=SignalArchiveCompletedResponse)
def get_completed_signal_archive(
    limit: int = Query(
        default=0,
        ge=0,
        le=5000,
        description="0 = 不限筆數（封存紀錄全部回傳並留存）",
    ),
    period_start: Optional[date] = Query(
        default=None,
        description=(
            "半年區間起始日（YYYY-MM-DD）；必須對齊到 2026-05-01 起算的半年區間"
            "（例：2026-05-01 / 2026-11-01 / 2027-05-01）。"
            "未指定時回所有 row（受 limit）。"
        ),
    ),
    db: Session = Depends(get_db),
) -> SignalArchiveCompletedResponse:
    if period_start is not None:
        # 後端 normalize 到正確的 anchor（防使用者傳奇怪日期）
        period_start = signal_archive.half_year_period_start(period_start)
    payload = signal_archive.list_completed_archive_summary(
        db,
        limit=limit,
        period_start=period_start,
    )
    return SignalArchiveCompletedResponse(**payload)


@router.get("/archive/{stock_id}", response_model=SignalArchiveDetailResponse)
def get_signal_archive_detail(
    stock_id: str,
    db: Session = Depends(get_db),
) -> SignalArchiveDetailResponse:
    payload = signal_archive.get_archive_detail(db, stock_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No archived signal records for {stock_id}",
        )
    return SignalArchiveDetailResponse(**payload)


@router.get("/quota", response_model=RegenerateQuotaResponse)
def get_regenerate_quota(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RegenerateQuotaResponse:
    target_date = _resolve_target_date(db)
    daily_limit = USER_DAILY_REGENERATE_LIMIT
    used_count = _user_count_today(db, user.id, target_date)
    remaining_count = max(daily_limit - used_count, 0)
    return RegenerateQuotaResponse(
        snapshot_date=target_date,
        daily_limit=daily_limit,
        used_count=used_count,
        remaining_count=remaining_count,
        disabled=remaining_count <= 0,
    )


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
      - 429 user 同日已達 10 次 / 全站同日已達 10 次
      - 202 Accepted + { job_id, snapshot_date }
    """
    target_date = _resolve_target_date(db)

    if _has_running_job_for_date(db, target_date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此日期已有產生中的 job，請等候完成",
        )

    user_daily_limit = USER_DAILY_REGENERATE_LIMIT
    if _user_count_today(db, user.id, target_date) >= user_daily_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"您今日重新產生次數已達上限（{user_daily_limit} 次），明日再試",
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


# ---------------------------------------------------------------------------
# Expectation Price endpoints（價格區間預測）
# ---------------------------------------------------------------------------


def _serialize_expectation_row(row: SignalExpectationPrice) -> ExpectationPriceItemResponse:
    return ExpectationPriceItemResponse(
        stock_id=row.stock_id,
        stock_name=row.stock_name,
        first_detected_date=row.first_detected_date,
        latest_detected_date=row.latest_detected_date,
        detected_type=row.detected_type,
        industry_name=row.industry_name,
        sub_industry=row.sub_industry,
        conservative_price=row.conservative_price,
        dream_price=row.dream_price,
        valuation_mode=row.valuation_mode,
        valuation_basis=row.valuation_basis,
        current_price_position=row.current_price_position,
        chase_risk=row.chase_risk,
        confidence=row.confidence,
        detected_day_high=row.detected_day_high,
        detected_day_close=row.detected_day_close,
        current_price=row.current_price,
        hit_conservative_at=row.hit_conservative_at,
        hit_dream_at=row.hit_dream_at,
        scorecard=row.scorecard,
        classification=row.classification,
        valuation_detail=row.valuation_detail,
        reason_50_words=row.reason_50_words,
        risk_note_30_words=row.risk_note_30_words,
        source=row.source,
        status=row.status,
        error_message=row.error_message,
        generated_at=row.generated_at,
        updated_at=row.updated_at,
    )


def _user_expectation_count_today(db: Session, user_id: int) -> int:
    """以 updated_at 當日為基準計次（含 ok / failed），避免使用者瘋狂 retry。"""
    today_utc_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    # 我們沒有 triggered_by 欄位區別 user，純粹用 updated_at 全站累積（個人 vs 全站合一處理）
    # 為了區分 user，使用「manual」source 當代理（不算 cron）+ 同日 updated_at >= 今天 UTC 開始
    return (
        db.query(SignalExpectationPrice)
        .filter(
            SignalExpectationPrice.source == "manual",
            SignalExpectationPrice.updated_at >= today_utc_start,
        )
        .count()
    )


def _global_expectation_count_today(db: Session) -> int:
    today_utc_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(SignalExpectationPrice)
        .filter(SignalExpectationPrice.updated_at >= today_utc_start)
        .count()
    )


@router.get("/expectation-prices", response_model=ExpectationPriceListResponse)
def list_expectation_prices(
    snapshot_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
) -> ExpectationPriceListResponse:
    """公開：列出指定 snapshot_date 對應的 expectation price 預測。

    若帶 `snapshot_date` → 回該日 SignalWatchHit watchlist 對應的所有 expectation row
    （以 stock_id 對應，不論該 row 的 first_detected_date 是不是 snapshot_date）。

    若不帶 → 回近 30 天 watchlist 涵蓋的所有 row（給 prod debug 用，正常前端應該帶日期）。
    """
    query = db.query(SignalExpectationPrice).filter(
        SignalExpectationPrice.status == "ok",
    )

    if snapshot_date is not None:
        # 找該日 watchlist 的 stock_id 集合
        stock_ids_q = (
            db.query(SignalWatchHit.stock_id)
            .filter(SignalWatchHit.snapshot_date == snapshot_date)
            .distinct()
        )
        stock_ids = [row[0] for row in stock_ids_q.all()]
        if not stock_ids:
            return ExpectationPriceListResponse(
                snapshot_date=snapshot_date,
                items=[],
            )
        query = query.filter(SignalExpectationPrice.stock_id.in_(stock_ids))

    rows = query.order_by(SignalExpectationPrice.updated_at.desc()).limit(200).all()
    return ExpectationPriceListResponse(
        snapshot_date=snapshot_date,
        items=[_serialize_expectation_row(r) for r in rows],
    )


@router.get("/expectation-prices/quota", response_model=ExpectationQuotaResponse)
def get_expectation_quota(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ExpectationQuotaResponse:
    used = _user_expectation_count_today(db, user.id)
    remaining = max(USER_DAILY_EXPECTATION_LIMIT - used, 0)
    return ExpectationQuotaResponse(
        daily_limit=USER_DAILY_EXPECTATION_LIMIT,
        used_count=used,
        remaining_count=remaining,
        disabled=remaining <= 0,
    )


@router.get("/expectation-prices/{stock_id}", response_model=ExpectationPriceItemResponse)
def get_expectation_price(
    stock_id: str,
    db: Session = Depends(get_db),
) -> ExpectationPriceItemResponse:
    """公開：取某檔股票最新一筆 expectation price（依 updated_at desc）。"""
    row = (
        db.query(SignalExpectationPrice)
        .filter(SignalExpectationPrice.stock_id == stock_id)
        .order_by(SignalExpectationPrice.updated_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No expectation price for {stock_id}",
        )
    return _serialize_expectation_row(row)


def _run_expectation_safely(stock_id: str) -> None:
    """BackgroundTask 包裝：用獨立 session 跑單檔 expectation price。"""
    db = SessionLocal()
    try:
        expectation_price_service.generate_for_stock(
            db,
            stock_id,
            source="manual",
        )
    except Exception:
        logger.exception("expectation price regenerate failed stock=%s", stock_id)
    finally:
        db.close()


@router.post(
    "/expectation-prices/regenerate",
    response_model=ExpectationRegenerateAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_expectation_price(
    payload: ExpectationRegenerateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ExpectationRegenerateAcceptedResponse:
    """需登入：背景重產指定股票的 expectation price。

    限額：每帳號每日 30 次、全站每日 100 次（依 SignalExpectationPrice.updated_at 計）。

    Pre-flight 檢查：股票必須存在 signal_watch_hits（否則沒有 first_detected_date 可用）。
    """
    stock_id = payload.stock_id.strip()
    if not stock_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stock_id 不可為空",
        )

    # Pre-flight：必須有 signal_watch_hits 紀錄
    has_hit = (
        db.query(SignalWatchHit)
        .filter(SignalWatchHit.stock_id == stock_id)
        .first()
        is not None
    )
    if not has_hit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{stock_id} 尚未被魚尾系統抓到，無法預測",
        )

    if _user_expectation_count_today(db, user.id) >= USER_DAILY_EXPECTATION_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"您今日預測次數已達上限（{USER_DAILY_EXPECTATION_LIMIT} 次），明日再試",
        )
    if _global_expectation_count_today(db) >= GLOBAL_DAILY_EXPECTATION_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="全站今日預測次數已達上限，請稍後",
        )

    background_tasks.add_task(_run_expectation_safely, stock_id)
    logger.info(
        "Expectation price regenerate triggered: user=%s stock=%s",
        user.id,
        stock_id,
    )
    return ExpectationRegenerateAcceptedResponse(stock_id=stock_id, status="accepted")
