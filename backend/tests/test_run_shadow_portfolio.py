"""每日多策略生產入口的邏輯測試（不含真正的 DB 端到端流程，那部分由
`test_shadow_portfolio.py` 涵蓋）：
  1. v1_frozen 與 FORWARD_V1_202609 都從 2026-09-07 起排進目前 production 時間線
  2. 兩套策略預設同時運作；關閉 Forward 開關時仍可只跑 v1_frozen
  3. 某個 strategy_version 執行失敗不影響其他 strategy_version 照跑
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import run_shadow_portfolio as runner  # noqa: E402
from app.signals import shadow_portfolio as sp  # noqa: E402


def test_strategy_versions_for_date_starts_both_strategies_on_2026_09_07():
    before = runner.FORWARD_V1_START_DATE - timedelta(days=1)
    versions = runner._strategy_versions_for_date(before)
    assert versions == []
    assert runner._strategy_versions_for_date(runner.V1_FROZEN_START_DATE) == [
        sp.STRATEGY_VERSION,
        sp.STRATEGY_VERSION_FORWARD_V1,
    ]


def test_strategy_versions_for_date_can_disable_forward_without_changing_date_gate(monkeypatch):
    monkeypatch.setattr(runner, "_FORWARD_V1_ENABLED", False)
    versions_on_start = runner._strategy_versions_for_date(runner.FORWARD_V1_START_DATE)
    assert versions_on_start == [sp.STRATEGY_VERSION]


def test_strategy_versions_for_date_forward_gate_is_still_date_bound(monkeypatch):
    """即使重新開啟 Forward，也不能把 9/7 以前的資料補成 Forward Day。
    """
    monkeypatch.setattr(runner, "_FORWARD_V1_ENABLED", True)

    before = runner.FORWARD_V1_START_DATE - timedelta(days=1)
    assert runner._strategy_versions_for_date(before) == []

    on_start = runner._strategy_versions_for_date(runner.FORWARD_V1_START_DATE)
    assert on_start == [sp.STRATEGY_VERSION, sp.STRATEGY_VERSION_FORWARD_V1]


def test_one_strategy_version_failure_does_not_block_others(monkeypatch):
    """`v1_frozen` 當天執行途中拋例外，`FORWARD_V1_202609` 仍然要照跑且成功——
    不能因為其中一個 strategy_version 出錯就讓整支腳本直接放棄其他版本。"""
    calls = []

    class DummySession:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def commit(self):
            pass

    def fake_session_local():
        return DummySession()

    def fake_execute_pending(db, *, target_date, strategy_version):
        calls.append(("execute_pending", strategy_version))
        if strategy_version == sp.STRATEGY_VERSION:
            raise RuntimeError("simulated v1_frozen failure")
        return {}

    def fake_run_daily(db, *, target_date, strategy_version):
        calls.append(("run_daily", strategy_version))
        return {}

    class FakeSnapshot:
        total_equity = 600000.0
        total_return_pct = 0.0
        position_count = 0

    def fake_snapshot(db, *, target_date, strategy_version):
        calls.append(("snapshot", strategy_version))
        return FakeSnapshot()

    def fake_cycle_reset(db, *, target_date, strategy_version):
        calls.append(("cycle_reset", strategy_version))
        return False

    def fake_winner_tracking(db, *, target_date, strategy_version):
        calls.append(("winner_tracking", strategy_version))
        return 0

    monkeypatch.setattr(sp, "execute_pending_strategy_orders", fake_execute_pending)
    monkeypatch.setattr(sp, "run_daily_trading_strategy", fake_run_daily)
    monkeypatch.setattr(sp, "create_portfolio_daily_snapshot", fake_snapshot)
    monkeypatch.setattr(sp, "check_and_apply_cycle_reset", fake_cycle_reset)
    monkeypatch.setattr(sp, "update_winner_tracking", fake_winner_tracking)

    results = {
        v: runner._run_one_strategy_version(
            fake_session_local, sp, target_date=runner.FORWARD_V1_START_DATE, strategy_version=v
        )
        for v in [sp.STRATEGY_VERSION, sp.STRATEGY_VERSION_FORWARD_V1]
    }

    assert results[sp.STRATEGY_VERSION] is False  # v1_frozen 失敗
    assert results[sp.STRATEGY_VERSION_FORWARD_V1] is True  # FORWARD_V1 不受影響，成功
    assert ("execute_pending", sp.STRATEGY_VERSION_FORWARD_V1) in calls
    assert ("winner_tracking", sp.STRATEGY_VERSION_FORWARD_V1) in calls  # track_winners=True 才會呼叫
    assert ("winner_tracking", sp.STRATEGY_VERSION) not in calls  # v1_frozen 一路失敗，不會走到這步
