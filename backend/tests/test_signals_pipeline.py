"""M23 slice 4：pipeline 主流程 status 流轉與 exception handling 測試。

Slice 4 的 stage 函式都是 stub（NotImplementedError），所以實際 run 會在
ingest 即 failed。本測試以 monkeypatch 替換為 noop 來覆蓋：
  - happy path：所有 stage 走完 → status=done + progress_pct=100 + snapshot 寫入
  - failure path：第一個 stage 拋 NotImplementedError → status=failed + traceback
  - mid-failure：前面幾個 stage 成功，filter stage 拋 RuntimeError → status=failed
                 + 失敗時的 stage / pct 已被 commit 到 DB
  - missing job：job_id 不存在 → ValueError
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, SignalGenerationJob, SignalSnapshot
from app.signals import candidate_pool, classification, filters, llm_caller
from app.signals.pipeline import run_signal_pipeline_sync


@pytest.fixture
def session_factory():
    """每個測試獨立的 in-memory SQLite + Session factory（pipeline 用）。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    yield Session
    Base.metadata.drop_all(bind=engine)


def _seed_pending_job(
    session_factory,
    job_id: str,
    snapshot_date: date = date(2026, 4, 25),
) -> None:
    with session_factory() as db:
        db.add(
            SignalGenerationJob(
                job_id=job_id,
                snapshot_date=snapshot_date,
                triggered_by="cron",
                status="pending",
            )
        )
        db.commit()


def _stub_all_stages_noop(monkeypatch):
    """全部 stage 替換為 noop（happy path）。"""
    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {"target": td})
    monkeypatch.setattr(
        candidate_pool, "compute_rankings", lambda db, td, ing: {"top_industries": [], "top_stocks": []}
    )
    monkeypatch.setattr(
        candidate_pool, "build_candidate_pool", lambda db, td, ing, rank: []
    )
    monkeypatch.setattr(classification, "classify_stocks", lambda db, td, pool: [])
    monkeypatch.setattr(filters, "apply_hard_exclusions", lambda db, td, c: [])
    monkeypatch.setattr(filters, "apply_soft_filters", lambda db, td, c: [])
    monkeypatch.setattr(
        llm_caller, "assemble_market_context", lambda snap: {"market_state": "RANGE"}
    )
    monkeypatch.setattr(
        llm_caller, "run_research_batch", lambda batch, ctx: list(batch)
    )
    monkeypatch.setattr(
        llm_caller, "run_explanation_batch", lambda research, ctx: []
    )
    monkeypatch.setattr(
        llm_caller,
        "assemble_final_output",
        lambda ctx, expl, *, candidate_pool_size: {
            "market_context": ctx,
            "watchlist": [],
            "removed": [],
            "summary": {
                "leader_count": 0,
                "follower_count": 0,
                "laggard_count": 0,
            },
            "candidate_pool_size": candidate_pool_size,
            "final_watchlist_size": 0,
            "llm_model": "test-model",
            "llm_total_tokens": 1234,
        },
    )


# ---------- failure paths ----------


def test_pipeline_marks_failed_when_first_stage_raises_not_implemented(session_factory):
    """slice 4 預設行為：所有 stage 為 stub，第一個 ingest 即 NotImplementedError。"""
    job_id = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id)

    with pytest.raises(NotImplementedError):
        run_signal_pipeline_sync(
            job_id, date(2026, 4, 25), session_factory=session_factory
        )

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "failed"
        assert rec.error_message is not None
        assert "NotImplementedError" in rec.error_message
        assert rec.finished_at is not None


def test_pipeline_marks_failed_when_filter_stage_raises(session_factory, monkeypatch):
    """前面幾個 stage 成功 / filter 拋 RuntimeError → status=failed，
    且失敗發生時的 stage 與 pct 已被 commit 到 DB（前端可看到失敗點）。"""

    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {})
    monkeypatch.setattr(candidate_pool, "compute_rankings", lambda db, td, ing: {})
    monkeypatch.setattr(
        candidate_pool, "build_candidate_pool", lambda db, td, ing, rank: []
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("filter blew up")

    monkeypatch.setattr(classification, "classify_stocks", _boom)

    job_id = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id)

    with pytest.raises(RuntimeError, match="filter blew up"):
        run_signal_pipeline_sync(
            job_id, date(2026, 4, 25), session_factory=session_factory
        )

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "failed"
        assert "filter blew up" in rec.error_message
        # 失敗發生在 filter stage：上一個 _set_progress 已將 stage 標為 filter / pct=30
        assert rec.current_stage == "filter"
        assert rec.progress_pct == 30
        assert rec.finished_at is not None


def test_pipeline_raises_when_job_not_found(session_factory):
    """job_id 不存在 → ValueError（不寫任何 status，因為沒 record 可寫）。"""
    with pytest.raises(ValueError, match="not found"):
        run_signal_pipeline_sync(
            "does-not-exist", date(2026, 4, 25), session_factory=session_factory
        )


# ---------- happy path ----------


def test_pipeline_marks_done_when_all_stages_noop(session_factory, monkeypatch):
    _stub_all_stages_noop(monkeypatch)

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"
        assert rec.progress_pct == 100
        assert rec.current_stage == "persist"
        assert rec.finished_at is not None
        assert rec.error_message is None


def test_pipeline_persists_snapshot_with_payload_fields(session_factory, monkeypatch):
    _stub_all_stages_noop(monkeypatch)

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        snap = (
            db.query(SignalSnapshot)
            .filter(SignalSnapshot.snapshot_date == target_date)
            .one()
        )
        assert snap.market_context["market_state"] == "RANGE"
        assert snap.watchlist == []
        assert snap.removed == []
        assert snap.summary["leader_count"] == 0
        assert snap.candidate_pool_size == 0
        assert snap.final_watchlist_size == 0
        assert snap.llm_model == "test-model"
        assert snap.llm_total_tokens == 1234
        assert snap.job_id == job_id
        assert snap.generated_at is not None


def test_pipeline_upserts_existing_snapshot_on_rerun(session_factory, monkeypatch):
    """同一日重新跑 → UPSERT 覆蓋而非 unique constraint 違反。"""
    _stub_all_stages_noop(monkeypatch)

    target_date = date(2026, 4, 25)

    # 第一次
    job_id_1 = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id_1, snapshot_date=target_date)
    run_signal_pipeline_sync(job_id_1, target_date, session_factory=session_factory)

    # 第二次（同一日）
    job_id_2 = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id_2, snapshot_date=target_date)
    run_signal_pipeline_sync(job_id_2, target_date, session_factory=session_factory)

    with session_factory() as db:
        snaps = (
            db.query(SignalSnapshot)
            .filter(SignalSnapshot.snapshot_date == target_date)
            .all()
        )
        assert len(snaps) == 1, "重跑應 UPSERT 同一筆而非新增"
        assert snaps[0].job_id == job_id_2  # job_id 已更新為最後一次
