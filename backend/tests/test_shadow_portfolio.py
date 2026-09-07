"""魚尾每日模擬交易（Shadow Portfolio）Phase 1 測試：no-lookahead 不變量、v1 進出場門檻
與沙盒 fishtail_backtest/backtest/signals.py 逐值比對、-8% 真實停損優先序、容量/現金分配、
idempotent 決策紀錄、訂單 self-healing 執行。
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.models import (
    DailyPrice,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    SignalObservation,
    SignalObservationReview,
    SignalWatchHit,
)
from app.signals import shadow_portfolio as sp


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _seed_price(db, stock_id: str, trade_date_: date, *, open_=100.0, high=105.0, low=95.0, close=100.0) -> None:
    db.add(
        DailyPrice(
            stock_id=stock_id, trade_date=trade_date_,
            open_price=open_, high_price=high, low_price=low, close_price=close,
        )
    )
    db.commit()


def _seed_trading_calendar(db, start: date, days: int, stock_id: str = "ZZZZ") -> None:
    """建立一段連續交易日曆（用一檔跟主測試無關的股票寫 daily_price，讓
    day_index 的「全市場交易日數」query 有資料可數）。跳過週末，模擬真實交易日曆。"""
    d = start
    count = 0
    while count < days:
        if d.weekday() < 5:
            _seed_price(db, stock_id, d)
            count += 1
        d += timedelta(days=1)


def _seed_observation(
    db, *, stock_id: str, stock_name: str, first_seen_date: date,
    status: str = "OBSERVING", asset_type: str = "COMMON_STOCK",
) -> SignalObservation:
    obs = SignalObservation(
        stock_id=stock_id, stock_name=stock_name, episode_id=str(uuid.uuid4()),
        status=status, started_signal_date=first_seen_date, initial_snapshot_json={},
        asset_type=asset_type,
    )
    db.add(obs)
    db.commit()
    return obs


def _seed_review(db, observation: SignalObservation, review_date: date, decision: str, momentum_score=None) -> None:
    db.add(
        SignalObservationReview(
            observation_id=observation.id, review_date=review_date, decision=decision,
            reason_codes=[], reason="test", caution_dimensions=[], failed_dimensions=[],
            momentum_score=momentum_score, prompt_version="test", state_machine_version="test",
        )
    )
    db.commit()


def _seed_hit(
    db, *, stock_id: str, stock_name: str, snapshot_date_: date,
    momentum_score=None, baseline_trade_date=None, baseline_price=None, return_pct=None,
) -> None:
    db.add(
        SignalWatchHit(
            snapshot_date=snapshot_date_, stock_id=stock_id, stock_name=stock_name,
            signal_type="LEADER", reason="test", theme={}, group_info={}, leader_check={}, signals={},
            signal_metrics={"momentum_score": momentum_score} if momentum_score is not None else None,
            baseline_trade_date=baseline_trade_date, baseline_price=baseline_price, return_pct=return_pct,
        )
    )
    db.commit()


D0 = date(2026, 8, 3)  # Monday
D1 = D0 + timedelta(days=1)  # Tue
D2 = D0 + timedelta(days=2)  # Wed
D3 = D0 + timedelta(days=3)  # Thu
D4 = D0 + timedelta(days=6)  # next Mon


# ---------------------------------------------------------------------------
# no-lookahead
# ---------------------------------------------------------------------------
def test_evidence_builder_ignores_future_hits_beyond_target_date(db):
    _seed_trading_calendar(db, D0, 10)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    # 未來（D2）的重選紀錄不該被算進 D1 的 hit_count_so_far
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D2)

    evidence = sp.build_daily_evidence(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1
    )
    assert evidence.hit_count_so_far == 1
    assert evidence.p3_selected_today is False  # D1 本身沒有 hit


def test_tracking_universe_uses_review_history_not_current_mutable_status(db):
    """Regression test：曾經真實觸發過的 bug——`SignalObservation.status` 是「現在」的
    最新狀態，回放（backfill）歷史某一天時若直接濾這個欄位，會把「當時仍在追蹤、後來才
    被停止」的股票整段歷史都排除掉（用了未來才會發生的停止事實），等同 no-lookahead
    違規。`resolve_tracking_universe` 必須改用 review 歷史（是否在 target_date 之前已經
    出現過 STOP_OBSERVING）判斷，不能看目前的 `.status` 欄位。"""
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, status="STOPPED")
    _seed_review(db, obs, D1, "CAUTION")
    _seed_review(db, obs, D2, "STOP_OBSERVING")  # 真正停止是在 D2

    universe_before_stop = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D1)
    universe_after_stop = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D2)

    assert any(t[0] == "1101" for t in universe_before_stop), (
        "D1 當時這檔股票還在追蹤中（尚未觸發 STOP_OBSERVING），即使『現在』的 "
        ".status 已經是 STOPPED，回放 D1 時仍應該把它納入評估"
    )
    assert not any(t[0] == "1101" for t in universe_after_stop)


def test_tracking_universe_excludes_etf(db):
    """v1 凍結參數 `exclude_etf=True`：真實 production 資料查證發現 00738U/00994A/00947
    這類 ETF 若不排除，會被誤判進 v1 候選（v1 從未設計來處理 ETF 的動能/回檔行為）。"""
    _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, asset_type="COMMON_STOCK")
    _seed_observation(db, stock_id="00738U", stock_name="ETF", first_seen_date=D0, asset_type="ETF")

    universe = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D0)
    stock_ids = {t[0] for t in universe}
    assert "1101" in stock_ids
    assert "00738U" not in stock_ids


def test_evidence_builder_ignores_future_daily_price_for_day_index(db):
    _seed_trading_calendar(db, D0, 3)
    # D3/D4 的市場交易日不該被算進 D1 的 day_index
    evidence = sp.build_daily_evidence(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1
    )
    assert evidence.day_index == 2  # D0, D1 兩天


# ---------------------------------------------------------------------------
# Evidence builder correctness
# ---------------------------------------------------------------------------
def test_evidence_momentum_prefers_p3_hit_over_p4_review(db):
    _seed_trading_calendar(db, D0, 5)
    _seed_price(db, "1101", D0, open_=100.0, close=100.0)
    _seed_price(db, "1101", D1, open_=100.0, close=100.0)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D1, "CAUTION", momentum_score=50.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D1, momentum_score=70.0)

    evidence = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1)
    assert evidence.momentum_score == 70.0  # P3 優先於 P4
    assert evidence.p4_decision == "CAUTION"
    # D1 是第 2 個交易日 = baseline 當天，固定 0%（比照 archive.py 既有慣例）
    assert evidence.mark_to_market_return_pct == 0.0


def test_evidence_falls_back_to_p4_review_momentum_when_not_reselected(db):
    _seed_trading_calendar(db, D0, 5)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D1, "CAUTION", momentum_score=55.0)
    # D1 沒有 SignalWatchHit（沒被 P3 重選）

    evidence = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1)
    assert evidence.p3_selected_today is False
    assert evidence.momentum_score == 55.0


def test_evidence_computes_mark_to_market_return_from_daily_price_without_any_hit(db):
    """關鍵 regression test：查證 production 真實資料發現多數 P4 追蹤股票從未有
    `signal_watch_hits` 列，報酬率必須完全靠 `daily_price` 算得出來，不依賴任何 hit。"""
    _seed_price(db, "1101", D0, open_=100.0, close=100.0)  # day_index=1
    _seed_price(db, "1101", D1, open_=100.0, close=100.0)  # day_index=2 -> baseline=(100+100)/2=100
    _seed_price(db, "1101", D2, open_=94.0, close=95.0)    # day_index=3 -> target

    evidence = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D2)
    assert evidence.day_index == 3
    assert evidence.p3_selected_today is False
    # 從未有任何 signal_watch_hits 列，但 first_seen_date 本身就是 P3 第一次選中，
    # hit_count_so_far 恆為至少 1（不會是 0）
    assert evidence.hit_count_so_far == 1
    assert evidence.mark_to_market_return_pct == -5.0  # (95-100)/100*100


def test_evidence_baseline_day_return_is_forced_zero(db):
    _seed_price(db, "1101", D0, open_=100.0, close=100.0)
    _seed_price(db, "1101", D1, open_=90.0, close=110.0)  # (open+close)/2 = 100，但收盤本身是 110

    evidence = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1)
    assert evidence.mark_to_market_return_pct == 0.0  # 比照 archive.py：baseline 當天固定 0%，不用收盤價算


def test_evidence_official_exit_day_at_30th_trading_day(db):
    _seed_trading_calendar(db, D0, 40)
    # 直接找第 30 個交易日
    from app.models import DailyPrice as DP
    trade_dates = sorted({row.trade_date for row in db.query(DP).all()})
    day30 = trade_dates[29]
    day29 = trade_dates[28]

    ev29 = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=day29)
    ev30 = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=day30)
    assert ev29.is_official_exit_signal_day is False
    assert ev30.is_official_exit_signal_day is True


# ---------------------------------------------------------------------------
# 進出場門檻 —— 與沙盒 fishtail_backtest/backtest/signals.py 逐值比對
# ---------------------------------------------------------------------------
def _row(**overrides):
    base = dict(
        stock_id="1101", stock_name="台泥", first_seen_date=D0, trade_date=D1,
        day_index=2, p3_selected_today=False, hit_count_so_far=1,
        momentum_score=75.0, p4_decision="CAUTION", mark_to_market_return_pct=-1.5,
        is_official_exit_signal_day=False,
    )
    base.update(overrides)
    return sp.EvidenceRow(**base)


def test_setup_a_matches_at_frozen_boundary_values(db):
    row = _row(day_index=2, hit_count_so_far=1, momentum_score=68, mark_to_market_return_pct=-2.5, p4_decision="CAUTION")
    sig = sp.generate_entry_signal(row, sp.V1_STRATEGY_PARAMS)
    assert sig is not None
    assert sig.entry_type == sp.ENTRY_TYPE_EARLY_HEALTHY_PULLBACK


def test_setup_a_rejects_just_outside_momentum_band(db):
    row = _row(day_index=2, hit_count_so_far=1, momentum_score=67.99, mark_to_market_return_pct=-1.0)
    sig = sp.generate_entry_signal(row, sp.V1_STRATEGY_PARAMS)
    assert sig is None


def test_setup_b_matches_deep_pullback_band(db):
    row = _row(day_index=3, hit_count_so_far=2, momentum_score=80, mark_to_market_return_pct=-9.0)
    sig = sp.generate_entry_signal(row, sp.V1_STRATEGY_PARAMS)
    assert sig is not None
    assert sig.entry_type == sp.ENTRY_TYPE_DEEP_PULLBACK


def test_setup_b_rejects_wrong_p4_decision(db):
    row = _row(day_index=3, momentum_score=80, mark_to_market_return_pct=-9.0, p4_decision="CONTINUE")
    assert sp.generate_entry_signal(row, sp.V1_STRATEGY_PARAMS) is None


def test_exit_priority_p4_stop_over_take_profit(db):
    row = _row(p4_decision="STOP_OBSERVING", mark_to_market_return_pct=15.0, is_official_exit_signal_day=False)
    sig = sp.generate_exit_signal(row, sp.V1_STRATEGY_PARAMS)
    assert sig.reason == sp.EXIT_REASON_P4_STOP


def test_exit_take_profit_at_10_pct(db):
    row = _row(p4_decision="CAUTION", mark_to_market_return_pct=10.0, is_official_exit_signal_day=False)
    sig = sp.generate_exit_signal(row, sp.V1_STRATEGY_PARAMS)
    assert sig.reason == sp.EXIT_REASON_TAKE_PROFIT


def test_exit_no_signal_when_nothing_triggers(db):
    row = _row(p4_decision="CAUTION", mark_to_market_return_pct=5.0, is_official_exit_signal_day=False)
    assert sp.generate_exit_signal(row, sp.V1_STRATEGY_PARAMS) is None


# ---------------------------------------------------------------------------
# -8% 真實停損優先序（run_daily_trading_strategy 整合測試，因為這條檢查不在
# generate_exit_signal 內部，是呼叫端先做的短路判斷）
# ---------------------------------------------------------------------------
def test_real_stop_loss_overrides_p4_stop(db):
    _seed_trading_calendar(db, D0, 5)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D1, "STOP_OBSERVING")

    portfolio = ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=500000.0)
    db.add(portfolio)
    position = ShadowVirtualPosition(
        strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥", first_seen_date=D0,
    )
    db.add(position)
    db.commit()
    from app.models import ShadowPositionLot
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type="DEEP_PULLBACK", entry_signal_date=D0,
            entry_execution_date=D0, entry_price=100.0, shares=1000.0, allocation=100000.0,
        )
    )
    db.commit()
    _seed_price(db, "1101", D1, close=90.0)  # -10% 部位報酬，跌破 -8% 真實停損

    sp.run_daily_trading_strategy(db, target_date=D1)
    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.stock_id == "1101", ShadowStrategyDailyDecision.trade_date == D1)
        .first()
    )
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_REAL_STOP_LOSS


# ---------------------------------------------------------------------------
# 容量/現金分配
# ---------------------------------------------------------------------------
def test_capacity_allocation_skips_lower_ranked_candidate_when_cash_insufficient(db):
    _seed_trading_calendar(db, D0, 5)
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=150000.0))
    db.commit()

    # 兩檔都符合 setup_a（day_index=3, hit_count_so_far=1，只在 D2 有一筆 hit；
    # momentum 68~80、return -2.5~0 band 內，靠各自 D2 的 daily_price 收盤價相對 D1
    # baseline 算出來）。台泥 momentum 更貼近 75（甜蜜點）+ return 更貼近 -1.5（甜蜜點）
    # -> entry_score 較高，應該優先勝出。
    _seed_price(db, "1101", D0, open_=100.0, close=100.0)
    _seed_price(db, "1101", D1, open_=100.0, close=100.0)  # baseline=100
    _seed_price(db, "1101", D2, open_=98.0, close=99.0)    # return=(99-100)/100*100=-1.0
    _seed_price(db, "2330", D0, open_=100.0, close=100.0)
    _seed_price(db, "2330", D1, open_=100.0, close=100.0)  # baseline=100
    _seed_price(db, "2330", D2, open_=97.0, close=98.0)    # return=(98-100)/100*100=-2.0

    # 刻意不呼叫 _seed_hit：first_seen_date 本身已是隱含的第 1 次選中
    # （hit_count_so_far==1），若在 D2 額外插入一筆 signal_watch_hits 會變成第 2 次
    # 選中，跳出 setup_a 的 hit_count==1 門檻
    obs_a = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs_a, D2, "CAUTION", momentum_score=78.0)

    obs_b = _seed_observation(db, stock_id="2330", stock_name="台積電", first_seen_date=D0)
    _seed_review(db, obs_b, D2, "CAUTION", momentum_score=70.0)

    sp.run_daily_trading_strategy(db, target_date=D2)
    decisions = {
        d.stock_id: d.action
        for d in db.query(ShadowStrategyDailyDecision).filter(ShadowStrategyDailyDecision.trade_date == D2)
    }
    # 只有 150,000 現金，只夠買 1 個 100,000 單位
    accepted = [sid for sid, action in decisions.items() if action in ("BUY", "ADD")]
    skipped = [sid for sid, action in decisions.items() if action == sp.ACTION_SKIPPED_CAPACITY]
    assert accepted == ["1101"]  # entry_score 較高者勝出
    assert skipped == ["2330"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_daily_decision_idempotent_on_rerun_same_day(db):
    _seed_trading_calendar(db, D0, 5)
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=600000.0))
    db.commit()
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D1, "CAUTION")
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D1, momentum_score=75.0, return_pct=-1.5)

    sp.run_daily_trading_strategy(db, target_date=D1)
    first_count = db.query(ShadowStrategyDailyDecision).count()
    first_order_count = db.query(ShadowStrategyOrder).count()

    sp.run_daily_trading_strategy(db, target_date=D1)  # 重跑同一天
    second_count = db.query(ShadowStrategyDailyDecision).count()
    second_order_count = db.query(ShadowStrategyOrder).count()

    assert first_count == second_count
    assert first_order_count == second_order_count


# ---------------------------------------------------------------------------
# 訂單 self-healing 執行
# ---------------------------------------------------------------------------
def test_execute_pending_orders_self_heals_when_price_missing_then_available(db):
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=600000.0))
    db.commit()
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_BUY, signal_date=D0, scheduled_execution_date=D1,
            status=sp.ORDER_STATUS_PENDING, units=1, planned_amount=100000.0,
            signal_snapshot={"first_seen_date": D0.isoformat()},
        )
    )
    db.commit()

    # D1 還沒有 daily_price -> 應該仍是 PENDING
    result = sp.execute_pending_strategy_orders(db, target_date=D1)
    assert result["skipped_no_price"] == 1
    order = db.query(ShadowStrategyOrder).first()
    assert order.status == sp.ORDER_STATUS_PENDING

    # D1 補上 daily_price -> 下次呼叫應該正確成交
    _seed_price(db, "1101", D1, high=101.0)
    result2 = sp.execute_pending_strategy_orders(db, target_date=D1)
    assert result2["buy"] == 1
    db.commit()  # 比照 run_shadow_portfolio.py 的真實用法：每次呼叫後立即 commit
    # db.refresh() 若在 commit 之前呼叫，會用尚未 flush 的舊 DB 值覆蓋掉記憶體中剛做的
    # 修改（SQLAlchemy 的已知行為：refresh 不會先 autoflush 這個物件自己的 pending 變更）——
    # 這裡先 commit 再重新查詢，才是這個 session 真正被使用的方式
    order = db.query(ShadowStrategyOrder).first()
    assert order.status == sp.ORDER_STATUS_EXECUTED
    assert order.execution_price == 101.0

    position = (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.stock_id == "1101", ShadowVirtualPosition.strategy_version == sp.STRATEGY_VERSION)
        .first()
    )
    assert position is not None


def test_execute_pending_orders_sells_before_buys_same_day(db):
    """spec §6：SELL 先於 BUY/ADD，讓賣出釋放的現金當天就能用於買進。"""
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=50000.0))
    db.commit()
    position = ShadowVirtualPosition(
        strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥", first_seen_date=D0,
    )
    db.add(position)
    db.commit()
    from app.models import ShadowPositionLot
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=D0,
            entry_execution_date=D0, entry_price=100.0, shares=1000.0, allocation=100000.0,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_SELL, signal_date=D0, scheduled_execution_date=D1,
            status=sp.ORDER_STATUS_PENDING, units=1,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="2330", stock_name="台積電",
            action=sp.ACTION_BUY, signal_date=D0, scheduled_execution_date=D1,
            status=sp.ORDER_STATUS_PENDING, units=1, planned_amount=100000.0,
            signal_snapshot={"first_seen_date": D0.isoformat()},
        )
    )
    db.commit()
    _seed_price(db, "1101", D1, low=95.0)
    _seed_price(db, "2330", D1, high=100.0)

    result = sp.execute_pending_strategy_orders(db, target_date=D1)
    assert result["sell"] == 1
    assert result["buy"] == 1
    portfolio = db.query(ShadowVirtualPortfolio).first()
    # 50,000 起始現金 + 賣出台泥 95,000 - 買進台積電 100,000 = 45,000（若買在賣之前會現金不足失敗）
    assert portfolio.cash == 45000.0
