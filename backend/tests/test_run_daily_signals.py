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
