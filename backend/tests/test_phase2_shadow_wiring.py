"""Phase 2 shadow mode 接線測試：驗證 SIGNALS_PIPELINE_MODE 預設對 legacy pipeline
零影響，且 shadow 例外絕對不能拖垮 legacy production pipeline。"""
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, SignalGenerationJob, SignalShadowSnapshot, SignalSnapshot
from app.signals import candidate_pool, classification, filters, llm_caller
from app.signals.pipeline import run_signal_pipeline_sync
from tests.test_signals_pipeline import _seed_pending_job, _stub_all_stages_noop


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    yield Session
    Base.metadata.drop_all(bind=engine)


def test_default_mode_never_writes_shadow_snapshot(session_factory, monkeypatch):
    """SIGNALS_PIPELINE_MODE 未設定（預設 "legacy"）→ 完全不執行 phase2 邏輯。"""
    import app.signals.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "SIGNALS_PIPELINE_MODE", "legacy")

    _stub_all_stages_noop(monkeypatch)
    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        assert db.query(SignalShadowSnapshot).count() == 0
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"


def test_shadow_mode_writes_snapshot_without_touching_legacy_output(session_factory, monkeypatch):
    """開啟 phase2_shadow → 額外寫入 signal_shadow_snapshots，
    但 legacy 的 SignalSnapshot（真正給使用者看的）完全不受影響。"""
    import app.signals.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "SIGNALS_PIPELINE_MODE", "phase2_shadow")

    _stub_all_stages_noop(monkeypatch)
    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"

        legacy_snap = db.query(SignalSnapshot).filter(SignalSnapshot.snapshot_date == target_date).first()
        assert legacy_snap is not None
        assert legacy_snap.watchlist == []  # 沿用 stub 的 legacy 輸出，不受 phase2 影響

        shadow = db.query(SignalShadowSnapshot).filter(SignalShadowSnapshot.snapshot_date == target_date).first()
        assert shadow is not None
        assert shadow.pipeline_version == "phase2-v1"
        assert shadow.funnel_metrics is not None


def test_shadow_pipeline_exception_does_not_break_legacy_pipeline(session_factory, monkeypatch):
    """phase2 pipeline 內部拋例外 → legacy pipeline 仍必須成功（shadow 對 production
    必須零風險），只在 log 留下例外紀錄。"""
    import app.signals.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "SIGNALS_PIPELINE_MODE", "phase2_shadow")

    def _boom(db, candidates, regime):
        raise RuntimeError("phase2 exploded")

    monkeypatch.setattr(
        "app.signals.phase2.pipeline_v2.run_phase2_pipeline", _boom
    )

    _stub_all_stages_noop(monkeypatch)
    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    # 不應 raise —— legacy pipeline 完全不受 phase2 例外影響
    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"
        assert db.query(SignalShadowSnapshot).count() == 0  # 例外時不寫入
