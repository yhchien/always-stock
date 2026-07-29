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

from app.models import Base, SignalGenerationJob, SignalSnapshot, SignalWatchHit
from app.signals import candidate_pool, classification, filters, llm_caller
from app.signals import market_snapshot
from app.signals import pipeline as pipeline_mod
from app.signals.pipeline import (
    _build_batches,
    _cap_llm_input,
    _order_llm_input,
    _partition_stage_results,
    _run_parallel_batches,
    run_signal_pipeline_sync,
)


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
    """全部 stage 替換為 noop（happy path）。

    `build_candidate_pool` 必須回非空 list（slice 11 後空 pool 會 raise
    ValueError 跳到 cron exit 1 路徑），所以這裡塞一筆 dummy 讓 happy path
    能繼續往 LLM stage 跑；後面 stage 會用各自的 stub 把 dummy filter 掉。
    """
    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {"target": td})
    monkeypatch.setattr(
        candidate_pool, "compute_rankings", lambda db, td, ing: {"top_industries": [], "top_stocks": []}
    )
    monkeypatch.setattr(
        candidate_pool,
        "build_candidate_pool",
        lambda db, td, ing, rank, **kw: [{"stock_id": "_dummy"}],
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
        llm_caller, "run_watch_reason_batch", lambda watch, ctx: list(watch)
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


def test_pipeline_marks_failed_when_ingest_stage_raises(session_factory, monkeypatch):
    """ingest stage 拋例外 → status=failed + error_message + finished_at 寫入。

    Slice 5 之後 ingest_data 已是實作（不再 NotImplementedError），這裡用 monkeypatch
    模擬 ingest 突發失敗（例如 DB 連線中斷）來驗證最早期的失敗路徑仍被正確記錄。
    """

    def _boom(*args, **kwargs):
        raise NotImplementedError("ingest blew up")

    monkeypatch.setattr(candidate_pool, "ingest_data", _boom)

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
    # 必須回非空 list，否則會在抵達 filter stage 前因空 pool 短路成 ValueError
    monkeypatch.setattr(
        candidate_pool,
        "build_candidate_pool",
        lambda db, td, ing, rank, **kw: [{"stock_id": "_dummy"}],
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


def test_pipeline_raises_value_error_when_candidate_pool_empty(session_factory, monkeypatch):
    """spec slice 11 補丁：build_candidate_pool 回空 list → 短路 raise ValueError。

    用意：cron run_daily_signals.py 的 _classify_exit_code 會把訊息含 "no candidate"
    的 ValueError 映射到 exit 1（no_data），而非 exit 3（db_error）。沒有這個短路，
    pipeline 會繼續送空 batch 給 LLM、最後寫一筆 watchlist=[] 的 done snapshot，
    cron exit 永遠 0，無法區分「真的沒抓到」與「成功但 0 檔」。
    """
    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {"target": td})
    monkeypatch.setattr(
        candidate_pool, "compute_rankings", lambda db, td, ing: {"top_industries": [], "top_stocks": []}
    )
    monkeypatch.setattr(
        candidate_pool, "build_candidate_pool", lambda db, td, ing, rank, **kw: []
    )

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    with pytest.raises(ValueError, match="no candidate"):
        run_signal_pipeline_sync(
            job_id, target_date, session_factory=session_factory
        )

    with session_factory() as db:
        rec = db.get(SignalGenerationJob, job_id)
        assert rec.status == "failed"
        assert "no candidate" in rec.error_message.lower()
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


def test_pipeline_persists_partial_failure_and_processing_summary(
    session_factory, monkeypatch
):
    _stub_all_stages_noop(monkeypatch)
    candidate = {"stock_id": "2330", "prelim_type": "LEADER"}
    monkeypatch.setattr(pipeline_mod, "SIGNALS_PIPELINE_MODE", "legacy")
    monkeypatch.setattr(classification, "classify_stocks", lambda db, td, pool: [candidate])
    monkeypatch.setattr(filters, "apply_hard_exclusions", lambda db, td, rows: list(rows))
    monkeypatch.setattr(filters, "apply_soft_filters", lambda db, td, rows: list(rows))
    monkeypatch.setattr(
        pipeline_mod.det_signals, "attach_deterministic_signals", lambda rows: list(rows)
    )
    monkeypatch.setattr(
        pipeline_mod.market_regime,
        "compute_market_regime",
        lambda db, td: {
            "regime": "BULL_TREND",
            "regime_label": "多頭",
            "reason": "test",
            "metrics": {},
        },
    )
    monkeypatch.setattr(
        pipeline_mod.market_breadth,
        "compute_breadth_from_frame",
        lambda frame, masters: {"breadth_score": 60},
    )
    monkeypatch.setattr(
        pipeline_mod.market_breadth,
        "resolve_regime_detail",
        lambda regime, score: "BROAD_BULL",
    )
    monkeypatch.setattr(filters, "apply_regime_gate", lambda rows, *args, **kwargs: list(rows))
    monkeypatch.setattr(
        llm_caller,
        "run_research_batch",
        lambda batch, ctx: [
            {
                **item,
                "stock": item["stock_id"],
                "_unavailable": True,
                "_unavailable_reason": "timeout",
                "processing_status": "RESEARCH_FAILED",
            }
            for item in batch
        ],
    )

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)
    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    with session_factory() as db:
        job = db.get(SignalGenerationJob, job_id)
        snap = db.query(SignalSnapshot).filter_by(snapshot_date=target_date).one()
        processing = snap.summary["processing_summary"]

        assert job.status == "partial_failure"
        assert job.progress_pct == 100
        assert processing["llm_eligible_count"] == 1
        assert processing["research_requested_count"] == 1
        assert processing["research_completed_count"] == 0
        assert processing["research_failed_count"] == 1
        assert processing["decision_requested_count"] == 0
        assert processing["unprocessed_count"] == 1
        assert processing["capacity_truncated_count"] == 0
        assert processing["momentum_score_mode"] == "applicability_aware"
        assert (
            processing["momentum_score_version"]
            == pipeline_mod.momentum.MOMENTUM_SCORE_VERSION
        )
        assert processing["is_complete"] is False


def test_pipeline_passes_db_market_snapshot_into_step_zero(session_factory, monkeypatch):
    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {"target": td})
    monkeypatch.setattr(candidate_pool, "compute_rankings", lambda db, td, ing: {})
    monkeypatch.setattr(
        candidate_pool,
        "build_candidate_pool",
        lambda db, td, ing, rank, **kw: [{"stock_id": "_dummy"}],
    )
    monkeypatch.setattr(classification, "classify_stocks", lambda db, td, pool: [])
    monkeypatch.setattr(filters, "apply_hard_exclusions", lambda db, td, c: [])
    monkeypatch.setattr(filters, "apply_soft_filters", lambda db, td, c: [])
    monkeypatch.setattr(
        market_snapshot,
        "build_db_market_snapshot",
        lambda db, td: {"taiex": {"change_pct_1d": 1.23}, "otc": None},
    )

    captured = {}

    def _assemble_market_context(snapshot):
        captured["snapshot"] = snapshot
        return {"market_state": "RANGE"}

    monkeypatch.setattr(llm_caller, "assemble_market_context", _assemble_market_context)
    monkeypatch.setattr(llm_caller, "run_research_batch", lambda batch, ctx: list(batch))
    monkeypatch.setattr(llm_caller, "run_explanation_batch", lambda research, ctx: [])
    monkeypatch.setattr(llm_caller, "run_watch_reason_batch", lambda watch, ctx: list(watch))
    monkeypatch.setattr(
        llm_caller,
        "assemble_final_output",
        lambda ctx, expl, *, candidate_pool_size: {
            "market_context": ctx,
            "watchlist": [],
            "removed": [],
            "summary": {},
            "candidate_pool_size": candidate_pool_size,
            "final_watchlist_size": 0,
            "llm_model": "test-model",
            "llm_total_tokens": 0,
        },
    )

    job_id = str(uuid.uuid4())
    target_date = date(2026, 4, 25)
    _seed_pending_job(session_factory, job_id, snapshot_date=target_date)

    run_signal_pipeline_sync(job_id, target_date, session_factory=session_factory)

    assert captured["snapshot"] == {"taiex": {"change_pct_1d": 1.23}, "otc": None}


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
        # candidate_pool_size = len(pool)，noop stub 給 1 筆 dummy（slice 11 補丁後不可空 list）
        assert snap.candidate_pool_size == 1
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


def test_pipeline_persists_signal_watch_hits_and_replaces_same_day(session_factory, monkeypatch):
    monkeypatch.setattr(candidate_pool, "ingest_data", lambda db, td: {"target": td})
    monkeypatch.setattr(candidate_pool, "compute_rankings", lambda db, td, ing: {})
    monkeypatch.setattr(
        candidate_pool,
        "build_candidate_pool",
        lambda db, td, ing, rank, **kw: [{"stock_id": "_dummy"}],
    )
    monkeypatch.setattr(classification, "classify_stocks", lambda db, td, pool: [])
    monkeypatch.setattr(filters, "apply_hard_exclusions", lambda db, td, c: [])
    monkeypatch.setattr(filters, "apply_soft_filters", lambda db, td, c: [])
    monkeypatch.setattr(llm_caller, "assemble_market_context", lambda snap: {"market_state": "RANGE"})
    monkeypatch.setattr(llm_caller, "run_research_batch", lambda batch, ctx: list(batch))
    monkeypatch.setattr(llm_caller, "run_explanation_batch", lambda research, ctx: [])
    monkeypatch.setattr(llm_caller, "run_watch_reason_batch", lambda watch, ctx: list(watch))

    payloads = [
        {
            "market_context": {"market_state": "RANGE"},
            "watchlist": [
                {
                    "stock": "2330",
                    "name": "台積電",
                    "type": "LEADER",
                    "industry": "半導體業",
                    "sub_industry": "晶圓代工",
                    "business_summary": "晶圓代工龍頭",
                    "theme": {},
                    "group_info": {},
                    "leader_check": {},
                    "signals": {"capital_flow": "strong"},
                    "reason": "第一次命中",
                }
            ],
            "removed": [],
            "summary": {},
            "candidate_pool_size": 1,
            "final_watchlist_size": 1,
            "llm_model": "test-model",
            "llm_total_tokens": 0,
        },
        {
            "market_context": {"market_state": "RANGE"},
            "watchlist": [
                {
                    "stock": "2454",
                    "name": "聯發科",
                    "type": "FOLLOWER",
                    "industry": "半導體業",
                    "sub_industry": "IC 設計",
                    "business_summary": "IC 設計",
                    "theme": {},
                    "group_info": {},
                    "leader_check": {},
                    "signals": {"capital_flow": "moderate"},
                    "reason": "第二次重產覆蓋同日",
                }
            ],
            "removed": [],
            "summary": {},
            "candidate_pool_size": 1,
            "final_watchlist_size": 1,
            "llm_model": "test-model",
            "llm_total_tokens": 0,
        },
    ]

    monkeypatch.setattr(
        llm_caller,
        "assemble_final_output",
        lambda ctx, expl, *, candidate_pool_size: payloads.pop(0),
    )

    target_date = date(2026, 4, 25)

    job_id_1 = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id_1, snapshot_date=target_date)
    run_signal_pipeline_sync(job_id_1, target_date, session_factory=session_factory)

    job_id_2 = str(uuid.uuid4())
    _seed_pending_job(session_factory, job_id_2, snapshot_date=target_date)
    run_signal_pipeline_sync(job_id_2, target_date, session_factory=session_factory)

    with session_factory() as db:
        rows = db.query(SignalWatchHit).filter(SignalWatchHit.snapshot_date == target_date).all()
        assert len(rows) == 1
        assert rows[0].stock_id == "2454"
        assert rows[0].reason == "第二次重產覆蓋同日"


def test_legacy_cap_wrapper_only_orders_and_keeps_every_candidate():
    candidates = [
        {
            "stock_id": "L1",
            "prelim_type": "LEADER",
            "total_institution_flow_3d": 10.0,
            "total_institution_flow_1d": 1.0,
            "price_change_5d": 3.0,
            "in_top_stocks_3d": False,
            "in_top_industries_3d": True,
        },
        {
            "stock_id": "F1",
            "prelim_type": "FOLLOWER",
            "total_institution_flow_3d": 999.0,
            "total_institution_flow_1d": 9.0,
            "price_change_5d": 9.0,
            "in_top_stocks_3d": True,
            "in_top_industries_3d": True,
        },
        {
            "stock_id": "L2",
            "prelim_type": "LEADER",
            "total_institution_flow_3d": 20.0,
            "total_institution_flow_1d": 2.0,
            "price_change_5d": 4.0,
            "in_top_stocks_3d": True,
            "in_top_industries_3d": True,
        },
        {
            "stock_id": "G1",
            "prelim_type": "LAGGARD_CANDIDATE",
            "total_institution_flow_3d": 500.0,
            "total_institution_flow_1d": 5.0,
            "price_change_5d": 2.0,
            "in_top_stocks_3d": True,
            "in_top_industries_3d": False,
        },
    ]

    out = _cap_llm_input(candidates, limit=2)
    assert [item["stock_id"] for item in out] == ["L2", "L1", "F1", "G1"]


def test_phase2_llm_input_orders_all_73_without_truncation():
    candidates = [
        {
            "stock_id": f"S{i:03d}",
            "role": "SECTOR_FOLLOWER",
            "tracking_state": None,
            "conviction": ("high", "medium", "low")[i % 3],
            "momentum_score": float(73 - i),
            "rs_market_percentile_20d": float(i),
            "risk_warnings": [],
        }
        for i in range(73)
    ]

    out = _order_llm_input(list(reversed(candidates)))

    assert len(out) == 73
    assert {item["stock_id"] for item in out} == {
        item["stock_id"] for item in candidates
    }


@pytest.mark.parametrize(
    ("candidate_count", "batch_size", "expected_batches", "last_batch_size"),
    [(73, 8, 10, 1), (73, 4, 19, 1)],
)
def test_build_batches_has_no_duplicates_or_omissions(
    candidate_count, batch_size, expected_batches, last_batch_size
):
    candidates = [{"stock_id": f"S{i:03d}"} for i in range(candidate_count)]

    batches = _build_batches(candidates, batch_size)

    assert len(batches) == expected_batches
    assert len(batches[-1]) == last_batch_size
    flattened = [item["stock_id"] for batch in batches for item in batch]
    assert flattened == [item["stock_id"] for item in candidates]


@pytest.mark.parametrize("candidate_count", [50, 120, 180, 250])
def test_synthetic_candidate_sizes_keep_all_items_and_expected_batch_counts(
    candidate_count,
):
    candidates = [
        {
            "stock_id": f"S{i:03d}",
            "role": "SECTOR_FOLLOWER",
            "tracking_state": None,
            "conviction": "medium",
            "momentum_score": float(candidate_count - i),
            "rs_market_percentile_20d": float(i % 100),
            "risk_warnings": [],
        }
        for i in range(candidate_count)
    ]

    ordered = _order_llm_input(candidates)
    research_batches = _build_batches(ordered, 8)
    decision_batches = _build_batches(ordered, 4)

    assert len(ordered) == candidate_count
    assert len(research_batches) == (candidate_count + 7) // 8
    assert len(decision_batches) == (candidate_count + 3) // 4
    assert sum(map(len, research_batches)) == candidate_count
    assert sum(map(len, decision_batches)) == candidate_count


def test_parallel_batch_failure_isolated_and_not_mapped_to_remove():
    candidates = [{"stock_id": f"S{i:02d}"} for i in range(12)]
    batches = _build_batches(candidates, 4)
    seen = []

    def runner(batch):
        seen.extend(item["stock_id"] for item in batch)
        if batch[0]["stock_id"] == "S04":
            raise TimeoutError("middle batch timed out")
        return [{**item, "decision": "WATCH"} for item in batch]

    execution = _run_parallel_batches(
        batches, runner, stage="decision", concurrency=1
    )
    successful, failures = _partition_stage_results(
        execution, failure_status="DECISION_FAILED"
    )

    assert seen == [item["stock_id"] for item in candidates]
    assert [item["stock_id"] for item in successful] == [
        "S00", "S01", "S02", "S03", "S08", "S09", "S10", "S11"
    ]
    assert {item["stock_id"] for item in failures} == {
        "S04", "S05", "S06", "S07"
    }
    assert all(item["processing_status"] == "DECISION_FAILED" for item in failures)
    assert all("decision" not in item for item in failures)
    assert [batch["status"] for batch in execution.batches] == [
        "COMPLETED", "FAILED", "COMPLETED"
    ]
