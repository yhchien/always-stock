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


def _phase2_survivable_pool(db, td, ing, rank, **kw):
    """建一筆足以通過 Phase 2 base eligibility + regime gate 的候選（VOLATILE_RANGE
    下 apply_regime_gate_v2 對非 RISK_OFF regime 不看 role，只要不是 true hard
    exclusion 就存活）；legacy 端的 classify/hard/soft filters 在
    `_stub_all_stages_noop` 裡被硬 stub 成永遠回傳 `[]`，所以只要這筆存活到
    phase2 的 after_regime，就足以證明「phase2 模式真的用了 phase2 的候選池」。"""
    return [{
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體業",
        "momentum_score": 85.0,
        "rs_market_percentile_20d": 95.0,
        "momentum_phase": "trending",
        "soft_hints": [],
        "is_tracked": False,
        "failed_follow_through": False,
        "hit_count": 0,
        "price_change_3d": 2.0,
        "price_change_10d": 5.0,
        "price_change_1d": 1.0,
        "total_institution_flow_1d": 100.0,
        "total_institution_flow_3d": 300.0,
        "avg_turnover_5d": 1_000_000_000.0,
    }]


def test_production_mode_uses_phase2_survivors_as_llm_input(session_factory, monkeypatch):
    """SIGNALS_PIPELINE_MODE=phase2：真正送進 LLM 的候選來自 Phase 2 pipeline，
    不是 legacy（legacy 三段 filter 被 stub 成永遠回傳 `[]`，若送進 LLM 的還是
    空清單，代表分支沒生效）。同時驗證 shadow snapshot 仍會被寫入（作為切換後
    持續監控用），且 comparison_summary 記到 legacy 這天等於 0 檔存活。"""
    import app.signals.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "SIGNALS_PIPELINE_MODE", "phase2")

    _stub_all_stages_noop(monkeypatch)
    # 覆寫掉 _stub_all_stages_noop 裡對 build_candidate_pool 的通用 dummy stub
    monkeypatch.setattr(candidate_pool, "build_candidate_pool", _phase2_survivable_pool)

    received_batches = []
    monkeypatch.setattr(
        llm_caller,
        "run_research_batch",
        lambda batch, ctx: received_batches.append(list(batch)) or list(batch),
    )

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"

        shadow = db.query(SignalShadowSnapshot).filter(SignalShadowSnapshot.snapshot_date == target_date).first()
        assert shadow is not None
        assert shadow.comparison_summary is not None
        assert shadow.comparison_summary["legacy_survivor_count"] == 0
        assert shadow.comparison_summary["mode"] == "phase2"

    assert len(received_batches) == 1
    llm_input = received_batches[0]
    assert len(llm_input) == 1
    assert llm_input[0]["stock_id"] == "2330"
    # role_to_prelim_type 已映射過，且 phase2 conviction 已別名成 regime_conviction
    assert llm_input[0]["prelim_type"] in ("LEADER", "FOLLOWER", "ROTATION_LAGGARD", "LAGGARD")
    assert llm_input[0]["regime_conviction"] == llm_input[0]["conviction"]


def test_production_mode_falls_back_to_legacy_when_phase2_pipeline_raises(session_factory, monkeypatch):
    """phase2 模式下，Phase 2 pipeline 本身丟例外 → fail-safe 退回 legacy 的
    after_regime（此測試中被 stub 成 `[]`），cron 仍必須成功完成，不能整包失敗。"""
    import app.signals.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "SIGNALS_PIPELINE_MODE", "phase2")

    _stub_all_stages_noop(monkeypatch)
    monkeypatch.setattr(candidate_pool, "build_candidate_pool", _phase2_survivable_pool)

    def _boom(db, candidates, regime):
        raise RuntimeError("phase2 production pipeline exploded")

    monkeypatch.setattr("app.signals.phase2.pipeline_v2.run_phase2_pipeline", _boom)

    received_batches = []
    monkeypatch.setattr(
        llm_caller,
        "run_research_batch",
        lambda batch, ctx: received_batches.append(list(batch)) or list(batch),
    )

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    # 不應 raise —— fail-safe 退回 legacy 輸出，cron 仍要跑完
    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "done"
        # 例外時不寫 shadow snapshot（跟 phase2_shadow 模式的例外行為一致）
        assert db.query(SignalShadowSnapshot).count() == 0

    # legacy after_regime 為空（本測試 stub 成 []）→ 沒有任何 batch 需要跑，
    # runner 完全不會被呼叫（`_run_parallel_batches([], ...)` 直接短路回傳 []）
    assert received_batches == []
