"""M23 slice 7 — `run_daily_signals.py` cron entrypoint 測試。"""

from __future__ import annotations

import importlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def runner_module():
    """確保 import 出來的是 backend/run_daily_signals.py（與 backend/app/* 共存於 sys.path）。"""
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    if "run_daily_signals" in sys.modules:
        return importlib.reload(sys.modules["run_daily_signals"])
    return importlib.import_module("run_daily_signals")


def test_parse_target_date_from_argv_explicit(runner_module):
    assert runner_module._parse_target_date_from_argv(
        ["run_daily_signals.py", "2026-04-25"]
    ) == date(2026, 4, 25)


def test_parse_target_date_from_argv_missing_before_ready_time_uses_yesterday(
    runner_module, monkeypatch
):
    """argv 沒帶日期且台北未到 19:00 → 預設使用昨天。"""
    fixed_now = datetime(2026, 4, 25, 18, 30, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(runner_module, "datetime", FakeDateTime)
    assert runner_module._parse_target_date_from_argv(["run_daily_signals.py"]) == date(
        2026, 4, 24
    )


def test_parse_target_date_from_argv_missing_after_ready_time_uses_today(
    runner_module, monkeypatch
):
    """argv 沒帶日期且台北已到 19:00 → 預設使用今天。"""
    fixed_now = datetime(2026, 4, 25, 19, 30, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(runner_module, "datetime", FakeDateTime)
    assert runner_module._parse_target_date_from_argv(["run_daily_signals.py"]) == date(
        2026, 4, 25
    )


def test_classify_exit_code_no_data(runner_module):
    assert runner_module._classify_exit_code(ValueError("no candidate stocks for date")) == 1
    assert runner_module._classify_exit_code(ValueError("no data available")) == 1
    assert runner_module._classify_exit_code(ValueError("No trade dates in DB")) == 1


def test_classify_exit_code_llm_error(runner_module):
    assert runner_module._classify_exit_code(RuntimeError("OpenAI API call failed")) == 2
    assert runner_module._classify_exit_code(RuntimeError("LLM batch returned 4xx")) == 2
    assert runner_module._classify_exit_code(FileNotFoundError("prompt file missing")) == 2


def test_classify_exit_code_db_error_default(runner_module):
    assert runner_module._classify_exit_code(RuntimeError("connection refused")) == 3
    assert runner_module._classify_exit_code(Exception("snapshot UPSERT failed")) == 3


def test_classify_exit_code_value_error_unrelated_falls_back_to_db(runner_module):
    """ValueError 但訊息不含 no_data 關鍵字 → 走 db_error fallback。"""
    assert runner_module._classify_exit_code(ValueError("invalid input shape")) == 3


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("done", 0),
        ("partial_failure", 4),
        ("running", 3),
        ("pending", 3),
    ],
)
def test_terminal_job_status_maps_to_process_exit(runner_module, status, expected):
    assert runner_module._terminal_job_exit_code(status) == expected


def test_terminal_failed_job_reuses_error_classification(runner_module):
    assert (
        runner_module._terminal_job_exit_code(
            "failed", "OpenAI research batch failed"
        )
        == 2
    )
    assert (
        runner_module._terminal_job_exit_code(
            "failed", "database connection refused"
        )
        == 3
    )


def test_daily_signals_workflow_runs_daily_and_fails_partial_results():
    """2026-08-14：daily_signals 從固定 cron 改成接在 margin_trade_backfill 完成後
    觸發（workflow_run），理由是 GitHub Actions 排定的 cron 實際觸發時間普遍比
    表訂時間晚 40~80 分鐘（gh run list 實測），固定時間 + 猜緩衝仍可能撞回融資
    融券資料還沒同步完就跑的問題。margin_trade_backfill 本身已改成每天跑
    （見 margin_trade_backfill.yml），所以這裡改用事件觸發仍然涵蓋「每天」。"""
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (repo_root / ".github/workflows/daily_signals.yml").read_text(
        encoding="utf-8"
    )
    assert 'workflows: ["Margin Trade Backfill"]' in workflow
    assert "SIGNALS_PIPELINE_MODE: phase2" in workflow
    assert "4) echo \"Status: partial_failure (FAIL; snapshot is incomplete)\"" in workflow


@pytest.fixture
def in_memory_session_factory():
    """建一個乾淨的 in-memory sqlite session factory，掛上全部 models（含 DailyPrice）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401  註冊全部 model 到 Base.metadata
    from app.database import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_main_skips_non_trading_day_without_creating_job(
    runner_module, monkeypatch, in_memory_session_factory
):
    """target_date 在 daily_price 完全沒有資料 → 視為非交易日，快速回 EXIT_NO_DATA，
    且不建立 SignalGenerationJob、不呼叫任何後續 pipeline 步驟。"""
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", in_memory_session_factory)

    def _boom(*args, **kwargs):
        raise AssertionError("non-trading day 不應該再往下呼叫 pipeline")

    monkeypatch.setattr(
        "app.signals.pipeline.run_signal_pipeline_sync", _boom, raising=False
    )

    exit_code = runner_module.main(["run_daily_signals.py", "2026-08-01"])
    assert exit_code == runner_module.EXIT_NO_DATA

    from app.models import SignalGenerationJob

    with in_memory_session_factory() as db:
        assert db.query(SignalGenerationJob).count() == 0


def test_main_proceeds_when_target_date_has_trade_data(
    runner_module, monkeypatch, in_memory_session_factory
):
    """target_date 確實有 daily_price 資料 → 通過交易日檢查，正常建立 job 並呼叫 pipeline。"""
    import app.database
    from app.models import DailyPrice

    monkeypatch.setattr(app.database, "SessionLocal", in_memory_session_factory)

    with in_memory_session_factory() as db:
        db.add(
            DailyPrice(
                trade_date=date(2026, 8, 3),
                stock_id="2330",
                open_price=100.0,
                high_price=101.0,
                low_price=99.0,
                close_price=100.5,
                volume=1000.0,
                turnover=100500.0,
            )
        )
        db.commit()

    def _fake_run_pipeline(*, job_id, target_date, session_factory=None):
        from app.models import SignalGenerationJob

        factory = session_factory or in_memory_session_factory
        with factory() as db:
            job = db.get(SignalGenerationJob, job_id)
            job.status = "done"
            job.progress_pct = 100
            db.commit()

    monkeypatch.setattr(runner_module, "SessionLocal", in_memory_session_factory, raising=False)
    monkeypatch.setattr(
        "app.signals.pipeline.run_signal_pipeline_sync",
        _fake_run_pipeline,
        raising=False,
    )
    monkeypatch.setattr(
        "app.observation_schema.ensure_observation_tables",
        lambda engine: None,
        raising=False,
    )
    monkeypatch.setattr(
        "app.outcome_schema.ensure_outcome_tables",
        lambda engine: None,
        raising=False,
    )
    monkeypatch.setattr(
        "app.signals.outcome_metrics.refresh_incremental_outcomes",
        lambda db: None,
        raising=False,
    )

    exit_code = runner_module.main(["run_daily_signals.py", "2026-08-03"])
    assert exit_code == runner_module.EXIT_OK

    from app.models import SignalGenerationJob

    with in_memory_session_factory() as db:
        jobs = db.query(SignalGenerationJob).all()
        assert len(jobs) == 1
        assert jobs[0].snapshot_date == date(2026, 8, 3)
