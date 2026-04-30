"""Tests for `run_signal_archive_returns.py` target-date resolution."""

from __future__ import annotations

from datetime import date, datetime

import run_signal_archive_returns as runner


def test_parse_target_trade_date_uses_argv_override():
    assert runner._parse_target_trade_date(["run_signal_archive_returns.py", "2026-04-30"]) == date(
        2026,
        4,
        30,
    )


def test_parse_target_trade_date_before_ready_time_uses_previous_day(monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 1, 0, 7, tzinfo=tz)

    monkeypatch.setattr(runner, "datetime", FakeDatetime)

    assert runner._parse_target_trade_date(["run_signal_archive_returns.py"]) == date(2026, 4, 30)


def test_parse_target_trade_date_after_ready_time_uses_today(monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 1, 20, 1, tzinfo=tz)

    monkeypatch.setattr(runner, "datetime", FakeDatetime)

    assert runner._parse_target_trade_date(["run_signal_archive_returns.py"]) == date(2026, 5, 1)
