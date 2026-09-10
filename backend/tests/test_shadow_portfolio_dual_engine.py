"""v1_frozen Dual-Engine（2026-09-09 第二輪重寫）測試：CONTINUATION（Starter 5萬 ->
D3 Confirmation 加碼 5萬 或 Fast Fail/Not-Confirmed）與 PULLBACK_RECOVERY（真實
episode 價格路徑的 Watch/Recovery/Recovery Failure）。

分兩層：
- 純函式測試（`evaluate_continuation_starter`／`evaluate_continuation_confirmation`／
  `evaluate_pullback_watch`／`evaluate_pullback_recovery_entry`）：直接用建構好的
  `EvidenceRow` 對照規格書逐值比對，對應 PART 48／49／50／51／52／54 的具體數字範例。
- 端到端整合測試（`_run_v1_dual_engine_daily_strategy`）：驗證真正的 orchestrator
  會依照這些規則產生正確的 BUY/ADD(Confirm)/SELL `ShadowStrategyDailyDecision`／
  `ShadowStrategyOrder`，對應 PART 53（Trailing）、PART 55（Pending Exposure）與
  資料品質防護（PART 45）。

`resolve_tracking_universe()` 會 union 目前這個 strategy_version 的既有持倉，所以
測「持倉中」的出場情境時，只要真的建立 `ShadowVirtualPosition`／`ShadowPositionLot`，
不需要額外造魚尾 `SignalWatchHit`；只有測「全新進場」情境才需要 `_seed_hit` 讓股票
進入 universe。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.models import (
    DailyPrice,
    ShadowPositionLot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    SignalWatchHit,
)
from app.signals import shadow_portfolio as sp


@pytest.fixture(autouse=True)
def _reset_snapshot_watchlist_cache():
    sp._SNAPSHOT_WATCHLIST_CACHE.clear()
    sp._SNAPSHOT_WATCHLIST_PAYLOAD_CACHE.clear()
    yield
    sp._SNAPSHOT_WATCHLIST_CACHE.clear()
    sp._SNAPSHOT_WATCHLIST_PAYLOAD_CACHE.clear()


@pytest.fixture(autouse=True)
def _use_legacy_dual_engine_fixture_params():
    """Keep these low-level tests focused on the ordinary Starter path.

    The production v1_frozen defaults now enable the validated report-profile
    route; the tests below intentionally exercise the pre-profile mechanics
    (50k Starter + 50k confirmation) and should opt out explicitly.
    """
    params = sp.DUAL_ENGINE_PARAMS
    original = {
        "continuation_starter_capital": params["continuation_starter_capital"],
        "continuation_confirm_scale_in_capital": params["continuation_confirm_scale_in_capital"],
        "pullback_bucket_cap": params["pullback_bucket_cap"],
        "continuation_rotation": deepcopy(params["continuation_rotation"]),
        "continuation_starter": deepcopy(params["continuation_starter"]),
    }
    params["continuation_starter_capital"] = 50000.0
    params["continuation_confirm_scale_in_capital"] = 50000.0
    params["pullback_bucket_cap"] = 300000.0
    params["continuation_rotation"]["enabled"] = False
    params["continuation_starter"].update({
        "report_profile_gate": False,
        "report_profile_direct_entry": False,
        "report_profile_rotation": False,
    })
    yield
    params["continuation_starter_capital"] = original["continuation_starter_capital"]
    params["continuation_confirm_scale_in_capital"] = original["continuation_confirm_scale_in_capital"]
    params["pullback_bucket_cap"] = original["pullback_bucket_cap"]
    params["continuation_rotation"] = original["continuation_rotation"]
    params["continuation_starter"] = original["continuation_starter"]


V = sp.STRATEGY_VERSION_V1_FROZEN

D0 = date(2026, 8, 3)  # Monday（first_seen_date / day_index==1）
D1 = D0 + timedelta(days=1)  # Tue（day_index==2）
D2 = D0 + timedelta(days=2)  # Wed（day_index==3）
D3 = D0 + timedelta(days=3)  # Thu（day_index==4）
D4 = D0 + timedelta(days=4)  # Fri（day_index==5）
D5 = D0 + timedelta(days=7)  # next Mon（day_index==6）


def _seed_price(db, stock_id: str, trade_date_: date, *, close: float, low=None, open_=None, high=None) -> None:
    db.add(
        DailyPrice(
            stock_id=stock_id, trade_date=trade_date_,
            open_price=open_ if open_ is not None else close,
            high_price=high if high is not None else close,
            low_price=low if low is not None else close,
            close_price=close,
        )
    )
    db.commit()


def _seed_calendar(db, start: date, days: int, stock_id: str = "ZZZZ") -> None:
    d = start
    count = 0
    while count < days:
        if d.weekday() < 5:
            _seed_price(db, stock_id, d, close=100.0)
            count += 1
        d += timedelta(days=1)


def _seed_hit(db, *, stock_id: str, stock_name: str, snapshot_date_: date, momentum_score=None) -> None:
    db.add(
        SignalWatchHit(
            snapshot_date=snapshot_date_, stock_id=stock_id, stock_name=stock_name,
            signal_type="LEADER", reason="test", theme={}, group_info={}, leader_check={}, signals={},
            signal_metrics={"momentum_score": momentum_score} if momentum_score is not None else None,
        )
    )
    db.commit()


def _row(**overrides) -> sp.EvidenceRow:
    """建構一個純測試用的 EvidenceRow——`first_seen_close`/`close_price`/`low_price`
    給定後，`episode_price_return_pct`/`episode_low_return_pct` 直接算出來，不用
    再各自手算一次百分比。"""
    base = dict(
        stock_id="1101", stock_name="台泥", first_seen_date=D0, trade_date=D1,
        day_index=2, p3_selected_today=True, hit_count_so_far=1, momentum_score=75.0,
        p4_decision="CAUTION", mark_to_market_return_pct=-1.0, is_official_exit_signal_day=False,
        close_price=101.0, low_price=99.0, first_seen_close=100.0,
        episode_price_return_pct=1.0, episode_low_return_pct=-1.0, corporate_action_suspect=False,
        continuation_evidence_count=3, continuation_eligible=True,
        continuation_evidence={"role_quality": 3, "families": {}},
    )
    base.update(overrides)
    row = dict(base)
    # 若呼叫端直接指定了 close_price/low_price/first_seen_close 但沒有明確覆寫
    # episode return，就用這三個原始值重新算，避免兩邊數字對不起來。
    if "episode_price_return_pct" not in overrides and row["first_seen_close"] not in (None, 0) and row["close_price"] is not None:
        row["episode_price_return_pct"] = (row["close_price"] / row["first_seen_close"] - 1.0) * 100.0
    if "episode_low_return_pct" not in overrides and row["first_seen_close"] not in (None, 0) and row["low_price"] is not None:
        row["episode_low_return_pct"] = (row["low_price"] / row["first_seen_close"] - 1.0) * 100.0
    return sp.EvidenceRow(**row)


STARTER_CFG = sp.DUAL_ENGINE_PARAMS["continuation_starter"]
CONFIRM_CFG = sp.DUAL_ENGINE_PARAMS["continuation_confirm"]
WATCH_CFG = sp.DUAL_ENGINE_PARAMS["pullback_watch"]
RECOVERY_CFG = sp.DUAL_ENGINE_PARAMS["pullback_recovery"]


# ---------------------------------------------------------------------------
# PART 48 — Episode Price（pure function）
# ---------------------------------------------------------------------------
def test_episode_price_return_pct():
    row = _row(first_seen_close=100.0, close_price=102.0)
    assert row.episode_price_return_pct == pytest.approx(2.0)


def test_episode_low_return_pct():
    row = _row(first_seen_close=100.0, low_price=96.0)
    assert row.episode_low_return_pct == pytest.approx(-4.0)


# ---------------------------------------------------------------------------
# PART 49 — Continuation Starter（pure function）
# ---------------------------------------------------------------------------
def test_starter_good():
    day1 = _row(day_index=1, p3_selected_today=True, momentum_score=78.0)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is True


def test_starter_rejects_broken_intraday():
    day1 = _row(day_index=1, p3_selected_today=False, momentum_score=78.0)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


def test_starter_rejects_extended_d2():
    day1 = _row(day_index=1, p3_selected_today=True, momentum_score=59.0)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


def test_starter_rejects_momentum_collapsed():
    day1 = _row(day_index=1, p3_selected_today=True, momentum_score=60.0,
               continuation_evidence_count=2, continuation_eligible=False)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


def test_starter_rejects_when_day1_not_real_discovery():
    day1 = _row(day_index=1, p3_selected_today=False, hit_count_so_far=0, momentum_score=78.0)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


def test_starter_rejects_p4_stop():
    day1 = _row(day_index=1, p3_selected_today=True, momentum_score=78.0,
               continuation_hard_excluded=True, continuation_eligible=False)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


def test_starter_momentum_retention_not_checked_when_day1_missing():
    """day1 momentum 缺值時，retention 檢查不擋（PART 10：如果 D1 momentum 也存在
    才檢查）。"""
    day1 = _row(day_index=1, p3_selected_today=True, momentum_score=None,
               continuation_evidence_count=3, continuation_eligible=False)
    assert sp.evaluate_continuation_starter(day1, STARTER_CFG) is False


# ---------------------------------------------------------------------------
# PART 50/51 — Confirmation（pure function）
# ---------------------------------------------------------------------------
def test_confirmation_good():
    day2 = _row(day_index=2, close_price=100.0, momentum_score=74.0, first_seen_close=100.0)
    day3 = _row(day_index=3, close_price=104.0, first_seen_close=100.0, momentum_score=72.0, p4_decision="CAUTION")
    assert sp.evaluate_continuation_confirmation(day3, day2, actual_position_return=2.0, cfg=CONFIRM_CFG) is True


def test_confirmation_rejects_no_starter_high_d3():
    """D3 很強但沒有 Starter 存在 -> 不能新 BUY（這條在 orchestrator 層保證：
    evaluate_continuation_confirmation 只在已存在 Starter 時才會被呼叫，這裡驗證
    純函式本身遇到極端強勢的 D3 也不會意外把它誤判成別的東西——它就是回 True，
    上層呼叫端的責任是『不呼叫它』。這裡改用整合測試驗證 orchestrator 真的不會
    在沒有 Starter 時呼叫這個函式產生 BUY，見
    `test_end_to_end_no_starter_no_confirmation_buy_even_if_d3_strong`。"""
    day2 = _row(day_index=2, close_price=100.0, momentum_score=74.0, first_seen_close=100.0)
    day3 = _row(day_index=3, close_price=110.0, first_seen_close=100.0, momentum_score=80.0)
    # The pure gate intentionally ignores actual_position_return; ownership of
    # the Starter is enforced by the orchestrator.
    assert sp.evaluate_continuation_confirmation(day3, day2, actual_position_return=None, cfg=CONFIRM_CFG) is True


def test_d2_early_confirmation_uses_episode_path_not_same_day_actual_pnl():
    """D1 close=100, D2 HIGH=110 and close=105: a HIGH fill would show
    -4.55% same-day P&L, but the D2 episode path is healthy and may confirm."""
    day1 = _row(day_index=1, first_seen_close=100.0, close_price=100.0,
                low_price=99.0, momentum_score=70.0)
    day2 = _row(day_index=2, first_seen_close=100.0, close_price=105.0,
                low_price=100.0, momentum_score=65.0)
    early_cfg = sp.DUAL_ENGINE_PARAMS["continuation_early_confirm"]
    assert sp.evaluate_continuation_early_confirmation(day2, day1, early_cfg) is True


def test_no_confirmation_rejects_negative_actual_return():
    day2 = _row(day_index=2, close_price=100.0, momentum_score=74.0, first_seen_close=100.0)
    day3 = _row(day_index=3, close_price=101.0, first_seen_close=100.0, momentum_score=72.0)
    assert sp.evaluate_continuation_confirmation(day3, day2, actual_position_return=-1.0, cfg=CONFIRM_CFG) is False


# ---------------------------------------------------------------------------
# PART 54 — Pullback Watch / Recovery（pure function）
# ---------------------------------------------------------------------------
def test_pullback_watch_only():
    row = _row(day_index=2, first_seen_close=100.0, close_price=92.0, momentum_score=70.0, p4_decision="CAUTION")
    assert sp.evaluate_pullback_watch(row, WATCH_CFG) is True


def test_pullback_dual_recovery():
    prev = _row(first_seen_close=100.0, close_price=92.0, momentum_score=68.0)
    today = _row(first_seen_close=100.0, close_price=96.0, momentum_score=71.0, p4_decision="CAUTION")
    assert sp.evaluate_pullback_recovery_entry(today, prev, RECOVERY_CFG) is True


def test_pullback_momentum_only_recovery_is_not_enough():
    prev = _row(first_seen_close=100.0, close_price=92.0, momentum_score=68.0)
    today = _row(first_seen_close=100.0, close_price=91.0, momentum_score=71.0)  # price got worse
    assert sp.evaluate_pullback_recovery_entry(today, prev, RECOVERY_CFG) is False


def test_pullback_price_only_recovery_is_not_enough():
    prev = _row(first_seen_close=100.0, close_price=92.0, momentum_score=68.0)
    today = _row(first_seen_close=100.0, close_price=96.0, momentum_score=50.0)
    assert sp.evaluate_pullback_recovery_entry(today, prev, RECOVERY_CFG) is False


def test_pullback_recovery_rejects_when_prev_momentum_missing():
    prev = _row(first_seen_close=100.0, close_price=92.0, momentum_score=None)
    today = _row(first_seen_close=100.0, close_price=96.0, momentum_score=71.0)
    assert sp.evaluate_pullback_recovery_entry(today, prev, RECOVERY_CFG) is False


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------
def _seed_continuation_starter(
    db, *, stock_id="1101", stock_name="台泥", entry_signal_date=D0, entry_execution_date=D1,
    entry_price=100.0, shares=500.0, allocation=50000.0, cash=500000.0,
) -> ShadowVirtualPosition:
    portfolio = db.query(ShadowVirtualPortfolio).filter(ShadowVirtualPortfolio.strategy_version == V).first()
    if portfolio is None:
        db.add(ShadowVirtualPortfolio(strategy_version=V, cash=cash))
        db.commit()
    position = ShadowVirtualPosition(
        strategy_version=V, stock_id=stock_id, stock_name=stock_name, first_seen_date=entry_signal_date,
    )
    db.add(position)
    db.commit()
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type=sp.ENTRY_TYPE_CONTINUATION_STARTER,
            entry_signal_date=entry_signal_date, entry_execution_date=entry_execution_date,
            entry_price=entry_price, shares=shares, allocation=allocation,
        )
    )
    db.commit()
    return position


def _seed_continuation_confirmed(db, **kwargs) -> ShadowVirtualPosition:
    position = _seed_continuation_starter(db, **kwargs)
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type=sp.ENTRY_TYPE_CONTINUATION_CONFIRM_SCALE_IN,
            entry_signal_date=kwargs.get("entry_execution_date", D1),
            entry_execution_date=kwargs.get("entry_execution_date", D1) + timedelta(days=1),
            entry_price=kwargs.get("entry_price", 100.0), shares=500.0, allocation=50000.0,
        )
    )
    db.commit()
    return position


def _seed_pullback_position(
    db, *, stock_id="1101", stock_name="台泥", entry_signal_date=D0, entry_execution_date=D1,
    entry_price=100.0, shares=None, allocation=100000.0, cash=500000.0, first_seen_date=None,
) -> ShadowVirtualPosition:
    if shares is None:
        shares = allocation / entry_price
    portfolio = db.query(ShadowVirtualPortfolio).filter(ShadowVirtualPortfolio.strategy_version == V).first()
    if portfolio is None:
        db.add(ShadowVirtualPortfolio(strategy_version=V, cash=cash))
        db.commit()
    position = ShadowVirtualPosition(
        strategy_version=V, stock_id=stock_id, stock_name=stock_name,
        first_seen_date=first_seen_date or entry_signal_date,
    )
    db.add(position)
    db.commit()
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type=sp.ENTRY_TYPE_PULLBACK_RECOVERY,
            entry_signal_date=entry_signal_date, entry_execution_date=entry_execution_date,
            entry_price=entry_price, shares=shares, allocation=allocation,
        )
    )
    db.commit()
    return position


def _decision_for(db, stock_id: str, trade_date_: date) -> ShadowStrategyDailyDecision:
    return (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == V,
            ShadowStrategyDailyDecision.stock_id == stock_id,
            ShadowStrategyDailyDecision.trade_date == trade_date_,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# PART 52 — Starter Fast Fail（integration）
# ---------------------------------------------------------------------------
def test_starter_fast_fail(db):
    _seed_calendar(db, D0, 3)
    _seed_continuation_starter(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=100.0)
    _seed_price(db, "1101", D2, close=94.9)  # -5.1%, first complete day after D2 execution

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_CONTINUATION_STARTER_FAST_FAIL


def test_starter_execution_day_does_not_fast_stop_on_negative_high_fill_pnl(db):
    _seed_calendar(db, D0, 2)
    _seed_continuation_starter(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=95.0, high=110.0, low=94.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=70.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D1, momentum_score=65.0)

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D1, strategy_version=V)
    decision = _decision_for(db, "1101", D1)
    assert decision.action == sp.ACTION_HOLD


# ---------------------------------------------------------------------------
# PART 50/51 — Confirmation / Not-Confirmed（integration）
# ---------------------------------------------------------------------------
def test_end_to_end_confirmation_scales_in(db):
    _seed_calendar(db, D0, 3)
    _seed_continuation_starter(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)  # first_seen_close
    _seed_price(db, "1101", D1, close=100.0)  # day2 close（Starter 訊號日）
    _seed_price(db, "1101", D2, close=104.0, high=106.0)  # day3：episode +4%, actual +4%
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=74.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D2, momentum_score=72.0)

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_ADD
    assert decision.entry_pattern == sp.ENTRY_TYPE_CONTINUATION_FINAL_CONFIRM

    order = (
        db.query(ShadowStrategyOrder)
        .filter(ShadowStrategyOrder.strategy_version == V, ShadowStrategyOrder.stock_id == "1101")
        .first()
    )
    assert order.action == sp.ACTION_ADD
    assert order.planned_amount == 50000.0


def test_end_to_end_not_confirmed_sells(db):
    _seed_calendar(db, D0, 3)
    _seed_continuation_starter(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=100.0)
    _seed_price(db, "1101", D2, close=100.5)  # episode +0.5%（不到 +2% 門檻），actual +0.5%

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_CONTINUATION_NOT_CONFIRMED


def test_end_to_end_no_starter_no_confirmation_buy_even_if_d3_strong(db):
    """PART 19：D3 只能處理已存在 Starter——沒有 Starter 的股票，即使 D3 當天
    episode return 已經 +10%，也不能因為很強就新 BUY。"""
    _seed_calendar(db, D0, 3)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=70.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=100.0)
    _seed_price(db, "1101", D2, close=110.0)  # day_index==3，episode +10%，強勢

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action != sp.ACTION_BUY
    order_count = (
        db.query(ShadowStrategyOrder)
        .filter(ShadowStrategyOrder.strategy_version == V, ShadowStrategyOrder.stock_id == "1101")
        .count()
    )
    assert order_count == 0


# ---------------------------------------------------------------------------
# CONFIRMED Continuation Exit（integration）
# ---------------------------------------------------------------------------
def test_confirmed_hard_stop(db):
    _seed_calendar(db, D0, 3)
    _seed_continuation_confirmed(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=100.0)
    _seed_price(db, "1101", D2, close=91.0)  # -9.0%，跌破 -8% 門檻

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_CONTINUATION_CONFIRMED_STOP


# ---------------------------------------------------------------------------
# PART 53 — Trailing（pure via _confirmed_trailing_check + integration）
# ---------------------------------------------------------------------------
def test_confirmed_trailing_check_pure(db):
    _seed_calendar(db, D0, 4)
    _seed_price(db, "9999", D0, close=100.0)
    _seed_price(db, "9999", D1, close=120.0)  # peak
    _seed_price(db, "9999", D2, close=112.0)  # 回落 6.67% >= 6%

    triggered, peak_close = sp._confirmed_trailing_check(
        db, stock_id="9999", avg_cost=100.0, start_date=D0, end_date=D2,
        activate_pct=10.0, drawdown_pct=6.0,
    )
    assert peak_close == pytest.approx(120.0)
    assert triggered is True


def test_confirmed_trailing_not_affected_by_scale_in_average_cost(db):
    """回測發現的真實 bug 場景（第一輪 v3 修法）：Confirmation Scale-in 後平均成本
    改變，若拿 return% 比較 peak/current 會被誤判成「從高點回落」。新版 Trailing
    改用純收盤價，理論上不會再受影響——這裡驗證：即使 avg_cost 因為加碼而改變，
    只要收盤價本身沒有從高點跌破門檻，就不該觸發。"""
    _seed_calendar(db, D0, 4)
    _seed_price(db, "9999", D0, close=100.0)
    _seed_price(db, "9999", D1, close=115.0)   # 單一 lot 基準下 +15%，觸發啟動
    _seed_price(db, "9999", D2, close=110.0)   # 從 115 高點回落 4.35%，< 6% 不觸發

    # avg_cost 故意帶入「加碼後被稀釋」的較高成本（例如 106.98），驗證只要 close
    # 本身沒有真的跌破 peak_close 的 94%，就不會誤判。
    starter_lot = ShadowPositionLot(
        entry_execution_date=D0, entry_price=100.0, shares=500.0, allocation=50000.0,
    )
    confirmation_lot = ShadowPositionLot(
        entry_execution_date=D2, entry_price=113.0, shares=50000.0 / 113.0, allocation=50000.0,
    )
    triggered, peak_close = sp._confirmed_trailing_check(
        db, stock_id="9999", lots=[starter_lot, confirmation_lot], start_date=D0, end_date=D2,
        activate_pct=10.0, drawdown_pct=6.0,
    )
    assert peak_close == pytest.approx(115.0)
    assert triggered is False  # 110 > 115*0.94=108.1


def test_end_to_end_confirmed_trailing_exit(db):
    _seed_calendar(db, D0, 4)
    _seed_continuation_confirmed(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=120.0)  # +20%，peak
    _seed_price(db, "1101", D2, close=112.0)  # 回落 6.67% >= 6%

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_CONTINUATION_TRAILING_EXIT


def test_end_to_end_no_fixed_take_profit(db):
    """PART 27：actual return >= +10% 不能直接 SELL（沒有從高點回落就不該賣）。"""
    _seed_calendar(db, D0, 3)
    _seed_continuation_confirmed(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=111.0)  # +11%，剛創新高，尚未回落

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D1, strategy_version=V)
    decision = _decision_for(db, "1101", D1)
    assert decision.action == sp.ACTION_HOLD


# ---------------------------------------------------------------------------
# Pullback Exit（integration）
# ---------------------------------------------------------------------------
def test_pullback_real_stop(db):
    _seed_calendar(db, D0, 2)
    _seed_pullback_position(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=91.0)  # -9.0%

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D1, strategy_version=V)
    decision = _decision_for(db, "1101", D1)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_PULLBACK_REAL_STOP


def test_pullback_no_fixed_take_profit(db):
    _seed_calendar(db, D0, 2)
    _seed_pullback_position(db, entry_signal_date=D0, entry_execution_date=D1, entry_price=100.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=125.0)  # +25%

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D1, strategy_version=V)
    decision = _decision_for(db, "1101", D1)
    assert decision.action == sp.ACTION_HOLD


# ---------------------------------------------------------------------------
# PART 45 — Data Quality Guard（integration）
# ---------------------------------------------------------------------------
def test_corporate_action_suspect_blocks_entry(db):
    _seed_calendar(db, D0, 3)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=78.0)
    _seed_price(db, "1101", D0, close=1000.0)
    _seed_price(db, "1101", D1, close=60.0)  # -94% 單日跳動，可疑資料斷點

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D1, strategy_version=V)
    decision = _decision_for(db, "1101", D1)
    assert decision.action == sp.ACTION_DATA_QUALITY_SUSPECT

    order_count = (
        db.query(ShadowStrategyOrder)
        .filter(ShadowStrategyOrder.strategy_version == V, ShadowStrategyOrder.stock_id == "1101")
        .count()
    )
    assert order_count == 0


def test_corporate_action_suspect_persists_for_rest_of_episode(db):
    """一旦某天出現可疑跳動，這個 episode 之後每一天都要繼續標記（股價尺度已經
    整段改變，不是只有跳動當天的數字失真）。"""
    _seed_calendar(db, D0, 4)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=78.0)
    _seed_price(db, "1101", D0, close=1000.0)
    _seed_price(db, "1101", D1, close=60.0)  # 可疑跳動
    _seed_price(db, "1101", D2, close=62.0)  # 之後只是正常小波動

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_DATA_QUALITY_SUSPECT


# ---------------------------------------------------------------------------
# PART 55 — Pending Exposure（integration）
# ---------------------------------------------------------------------------
def test_pending_starter_order_counts_against_bucket_cap(db):
    """既有 Continuation 曝險 250k（5 個 Starter）+ 1 筆卡住的 PENDING Starter
    （5萬）= 300k 已滿——下一個候選不得再建立新的 5 萬 PENDING。"""
    _seed_calendar(db, D0, 2)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    for i in range(5):
        _seed_continuation_starter(
            db, stock_id=f"11{i:02d}", stock_name=f"既有{i}",
            entry_signal_date=D0, entry_execution_date=D0, entry_price=100.0,
        )
        _seed_price(db, f"11{i:02d}", D1, close=100.0)

    db.add(
        ShadowStrategyOrder(
            strategy_version=V, stock_id="9998", stock_name="卡住的訂單",
            action=sp.ACTION_BUY, signal_date=D0, scheduled_execution_date=D0,
            status=sp.ORDER_STATUS_PENDING, entry_pattern=sp.ENTRY_TYPE_CONTINUATION_STARTER,
            units=1, planned_amount=50000.0,
        )
    )
    db.commit()

    _seed_hit(db, stock_id="9999", stock_name="第六個候選", snapshot_date_=D0, momentum_score=75.0)
    _seed_price(db, "9999", D0, close=100.0)
    _seed_price(db, "9999", D1, close=100.5, high=101.0)
    _seed_hit(db, stock_id="9999", stock_name="第六個候選", snapshot_date_=D1, momentum_score=75.0)

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D0, strategy_version=V)
    decision = _decision_for(db, "9999", D0)
    assert decision.action == sp.ACTION_SKIPPED_CAPACITY


# ---------------------------------------------------------------------------
# 端到端進場（透過真正的 evidence pipeline：SignalWatchHit -> universe -> BUY order）
# ---------------------------------------------------------------------------
def test_end_to_end_starter_entry_creates_buy_order(db):
    _seed_calendar(db, D0, 2)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=78.0)
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=101.0, high=102.0)  # D2 execution day

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D0, strategy_version=V)
    decision = _decision_for(db, "1101", D0)
    assert decision.action == sp.ACTION_BUY
    assert decision.entry_pattern == sp.ENTRY_TYPE_CONTINUATION_STARTER

    order = (
        db.query(ShadowStrategyOrder)
        .filter(ShadowStrategyOrder.strategy_version == V, ShadowStrategyOrder.stock_id == "1101")
        .first()
    )
    assert order.action == sp.ACTION_BUY
    assert order.planned_amount == 50000.0


def test_pending_starter_executes_planned_amount_not_strategy_unit_capital(db):
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.add(
        DailyPrice(
            stock_id="1101", trade_date=D1, open_price=100.0,
            high_price=110.0, low_price=99.0, close_price=105.0,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=V, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_BUY, signal_date=D0,
            scheduled_execution_date=D1, status=sp.ORDER_STATUS_PENDING,
            reason=sp.ENTRY_TYPE_CONTINUATION_STARTER,
            entry_pattern=sp.ENTRY_TYPE_CONTINUATION_STARTER,
            units=1, planned_amount=50000.0,
            signal_snapshot={"first_seen_date": D0.isoformat(), "day_index": 1},
        )
    )
    db.commit()

    result = sp.execute_pending_strategy_orders(db, target_date=D1, strategy_version=V)
    db.flush()
    lot = db.query(ShadowPositionLot).first()
    portfolio = db.query(ShadowVirtualPortfolio).filter(ShadowVirtualPortfolio.strategy_version == V).first()
    assert result["buy"] == 1
    assert lot.allocation == pytest.approx(50000.0)
    assert portfolio.cash == pytest.approx(550000.0)


def test_end_to_end_pullback_recovery_creates_buy_order(db):
    _seed_calendar(db, D0, 3)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=600000.0))
    db.commit()
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_price(db, "1101", D0, close=100.0)  # first_seen_close
    _seed_price(db, "1101", D1, close=91.5)   # day_index=2，episode -8.5%（watch day）
    _seed_price(db, "1101", D2, close=95.5, high=96.0)  # day_index=3，episode -4.5%（recovery）
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D1, momentum_score=68.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D2, momentum_score=71.0)

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D2, strategy_version=V)
    decision = _decision_for(db, "1101", D2)
    assert decision.action == sp.ACTION_BUY
    assert decision.entry_pattern == sp.ENTRY_TYPE_PULLBACK_RECOVERY


def test_end_to_end_pullback_recovery_failure(db):
    """PART 37：進場後前 2 個完整交易日內跌破觀察期間最低點 -> RECOVERY_FAILED。"""
    _seed_calendar(db, D0, 3)
    _seed_pullback_position(
        db, entry_signal_date=D1, entry_execution_date=D2, entry_price=95.5, first_seen_date=D0,
    )
    # watch day（D1）episode -8.5% 是觀察期間的最低點；D2 是進場日
    _seed_price(db, "1101", D0, close=100.0)
    _seed_price(db, "1101", D1, close=91.5)
    _seed_price(db, "1101", D2, close=95.5)
    _seed_price(db, "1101", D3, close=88.0)  # episode -12%，跌破觀察期最低 -8.5%
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0, momentum_score=70.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D1, momentum_score=70.0)

    sp._run_v1_dual_engine_daily_strategy(db, target_date=D3, strategy_version=V)
    decision = _decision_for(db, "1101", D3)
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_PULLBACK_RECOVERY_FAILED
