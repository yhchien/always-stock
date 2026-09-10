"""魚尾每日模擬交易（Shadow Portfolio）Phase 1 測試：no-lookahead 不變量、v1 進出場門檻
與沙盒 fishtail_backtest/backtest/signals.py 逐值比對、-8% 真實停損優先序、容量/現金分配、
idempotent 決策紀錄、訂單 self-healing 執行。
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models import (
    DailyPrice,
    EtfClassification,
    ShadowCompletedTrade,
    ShadowMissedCandidate,
    ShadowPositionLot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    ShadowWinnerTracking,
    SignalObservation,
    SignalObservationReview,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
)
from app.signals import shadow_portfolio as sp


@pytest.fixture(autouse=True)
def _reset_snapshot_watchlist_cache():
    """`sp._SNAPSHOT_WATCHLIST_CACHE` 是 process-local 快取（見該常數 docstring），跨測試
    共用同一份 in-memory dict；不同測試的 `db` fixture 各自是獨立的 SQLite 資料庫，同一個
    日期在不同測試可能對應完全不同的 `SignalSnapshot` 內容，快取沒有 per-db 隔離的概念，
    每個測試前都要清空，避免讀到別的測試留下的舊值。"""
    sp._SNAPSHOT_WATCHLIST_CACHE.clear()
    yield
    sp._SNAPSHOT_WATCHLIST_CACHE.clear()


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


def _seed_completed_archive(
    db, *, stock_id: str, stock_name: str, first_seen_date: date, completed_trade_date: date,
    hit_count: int = 1, closure_reason: str = "p4_stopped",
) -> None:
    """已封存的魚尾追蹤週期（signal_watch_hits 已被硬刪除）——backfill/replay 對已經
    結束的歷史週期，以及 is_official_exit_signal_day 判斷都要靠這張表。"""
    db.add(
        SignalWatchCompletedArchive(
            stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date,
            latest_hit_date=completed_trade_date, hit_count=hit_count,
            latest_signal_type="LEADER", completed_trade_date=completed_trade_date,
            closure_reason=closure_reason,
        )
    )
    db.commit()


def _seed_snapshot(db, *, snapshot_date_: date, watchlist: list) -> None:
    """永久保留的每日快照——已封存週期的 hit_count/p3_selected_today/momentum_score
    重建都靠這張表，取代已被硬刪除的 signal_watch_hits。"""
    db.add(
        SignalSnapshot(
            snapshot_date=snapshot_date_, market_context={}, watchlist=watchlist,
            removed=[], summary={},
        )
    )
    db.commit()


def _seed_etf(db, stock_id: str) -> None:
    db.add(
        EtfClassification(
            stock_id=stock_id, asset_type="ETF", asset_class="EQUITY", region="TAIWAN", strategy="PASSIVE",
            classification_confidence="HIGH",
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


def test_resolve_relevant_observation_uses_review_history_not_current_mutable_status(db):
    """Regression test：曾經真實觸發過的 bug——`SignalObservation.status` 是「現在」的
    最新狀態，回放（backfill）歷史某一天時若直接濾這個欄位，會把「當時仍在追蹤、後來才
    被停止」的股票整段歷史都排除掉（用了未來才會發生的停止事實），等同 no-lookahead
    違規。`_resolve_relevant_observation`（`build_daily_evidence` 找 p4_decision 對應
    episode 用）必須改用 review 歷史判斷，不能看目前的 `.status` 欄位；且「已經停止」
    的判斷要用嚴格小於（STOP_OBSERVING 判定當天，仍要能讀到當天自己的這個決策，出場
    判斷才有機會在正確的那天觸發），從隔天開始才真正視為不在追蹤中。"""
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, status="STOPPED")
    _seed_review(db, obs, D1, "CAUTION")
    _seed_review(db, obs, D2, "STOP_OBSERVING")  # 真正停止是在 D2

    before_stop = sp._resolve_relevant_observation(db, stock_id="1101", target_date=D1)
    on_stop_day = sp._resolve_relevant_observation(db, stock_id="1101", target_date=D2)
    after_stop = sp._resolve_relevant_observation(db, stock_id="1101", target_date=D3)

    assert before_stop is not None, (
        "D1 當時這檔股票還在追蹤中（尚未觸發 STOP_OBSERVING），即使『現在』的 "
        ".status 已經是 STOPPED，回放 D1 時仍應該找得到這個 episode"
    )
    assert on_stop_day is not None, (
        "D2 當天剛判定 STOP_OBSERVING，仍要能讀到『今天』的這個決策本身"
    )
    assert after_stop is None, "D3（STOP_OBSERVING 隔天）才真正視為已經不在追蹤中"


def test_tracking_universe_includes_currently_active_fishtail_cohort(db):
    """`resolve_tracking_universe` 的主要來源是魚尾（`signal_watch_hits`），不是
    P4 `SignalObservation`——即使沒有任何 P4 觀察，只要魚尾有活躍週期就該被納入。"""
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)

    universe = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D1)
    assert dict((t[0], t[2]) for t in universe)["1101"] == D0


def test_tracking_universe_uses_fishtail_first_seen_date_not_p4_started_signal_date(db):
    """Regression test：真實 production 事故——2026-08-11 的一次性強制結算（`manual_reset`）
    只重置了魚尾側，P4 `SignalObservation.started_signal_date` 完全沒被動到，導致同一檔股票
    兩套系統的追蹤起點永久錯位（真實案例 2383 台光電：P4 認為 2026-08-06 開始，魚尾在
    manual_reset 後於 2026-08-11 重新起算）。`resolve_tracking_universe` 必須回傳魚尾的
    first_seen_date，不是 P4 的 started_signal_date，否則 `day_index`／`hit_count_so_far`
    全部會用錯誤的起點算，產生沙盒驗證資料裡從未出現過的「幽靈交易」。"""
    _seed_observation(db, stock_id="2383", stock_name="台光電", first_seen_date=D0 - timedelta(days=5))
    _seed_hit(db, stock_id="2383", stock_name="台光電", snapshot_date_=D1)  # 魚尾在 D1 才重新起算

    universe = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D2)
    first_seen_by_stock = {t[0]: t[2] for t in universe}
    assert first_seen_by_stock["2383"] == D1, (
        "first_seen_date 必須是魚尾的起點（D1），不能是 P4 SignalObservation 更早的 "
        "started_signal_date（D0-5天）"
    )


def test_tracking_universe_includes_archived_cohort_covering_target_date(db):
    """backfill/replay 對「已經是過去」的日期重播時，`signal_watch_hits` 可能已經被硬
    刪除（週期已經結算/封存），必須額外查 `signal_watch_completed_archives`／
    `signal_watch_stopped_observations` 才能拿到正確的歷史 universe。"""
    _seed_completed_archive(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, completed_trade_date=D2,
    )
    # 目前完全沒有活躍的 signal_watch_hits（已經硬刪除）

    universe_within_window = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D1)
    universe_after_window = sp.resolve_tracking_universe(db, strategy_version=sp.STRATEGY_VERSION, target_date=D3)

    assert dict((t[0], t[2]) for t in universe_within_window)["1101"] == D0
    assert "1101" not in {t[0] for t in universe_after_window}


def test_tracking_universe_excludes_etf(db):
    """v1 凍結參數 `exclude_etf=True`：真實 production 資料查證發現 `EtfClassification`
    才是正確辨識來源（`SignalObservation.asset_type` 對槓桿/反向 ETF 如 `00753L` 分類
    不準確）。"""
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_hit(db, stock_id="00738U", stock_name="ETF", snapshot_date_=D0)
    _seed_etf(db, "00738U")

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
    # D0 的 hit 代表這個魚尾週期的第 1 次選中（讓 _fishtail_cohort_is_active 判定為
    # 活躍週期），D1 的 hit 才是這個測試真正要驗證的「今天又被 P3 重選」情境
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
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


def test_evidence_official_exit_day_from_fishtail_archive_completion(db):
    """Regression test：`is_official_exit_signal_day` 曾經被誤實作成
    `day_index >= 30`（docstring 誤寫「30 個交易日期滿」，程式碼直接照抄），但真實比對
    沙盒 `daily_data.csv` 後發現這個門檻在 21 個交易日的驗證視窗裡從未被觸發過
    （day_index 最高只到 17），而 CSV 裡這個欄位其實有 174 筆 True——真正代表的是
    「魚尾判定這個追蹤週期在這一天正式結束」（`signal_watch_completed_archives`／
    `signal_watch_stopped_observations` 的 completed_trade_date），不論結束原因為何。"""
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_completed_archive(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, completed_trade_date=D2,
    )

    ev_before = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D1)
    ev_on_completion_day = sp.build_daily_evidence(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D2
    )
    assert ev_before.is_official_exit_signal_day is False
    assert ev_on_completion_day.is_official_exit_signal_day is True


def test_evidence_uses_snapshot_reconstruction_when_cohort_already_archived(db):
    """backfill/replay 重播「已經是過去、且已經封存」的歷史日期時，`signal_watch_hits`
    已被硬刪除，`hit_count_so_far`／`p3_selected_today`／`momentum_score` 必須改從永久
    保留的 `SignalSnapshot.watchlist` 逐日重建，不能因為 hits 表已清空就整段變成
    第 1 次選中／從未被選中。"""
    _seed_trading_calendar(db, D0, 5)
    _seed_completed_archive(
        db, stock_id="1101", stock_name="台泥", first_seen_date=D0, completed_trade_date=D3, hit_count=2,
    )
    # 這個週期已經封存，目前完全沒有 signal_watch_hits，只能靠 SignalSnapshot 重建
    _seed_snapshot(db, snapshot_date_=D2, watchlist=[{"stock": "1101", "signal_metrics": {"momentum_score": 82.0}}])

    evidence = sp.build_daily_evidence(db, stock_id="1101", stock_name="台泥", first_seen_date=D0, target_date=D2)
    assert evidence.p3_selected_today is True
    assert evidence.momentum_score == 82.0
    assert evidence.hit_count_so_far == 2  # 第 1 次（first_seen_date 本身）+ D2 這次重選


def test_evidence_p4_decision_resolves_even_when_started_signal_date_differs_from_fishtail_first_seen_date(db):
    """Regression test：真實案例 2383 台光電——P4 `SignalObservation.started_signal_date`
    與魚尾 first_seen_date 不一致時（見 `_resolve_relevant_observation` docstring），
    `p4_decision` 仍然要能正確找到對應的 P4 episode，不能因為日期對不上就永遠回 None
    （這是原本 exact-match 查詢的 bug：對不上就靜默拿到 p4_decision=None，讓
    `generate_exit_signal` 的 P4_STOP 分支永遠不會觸發）。"""
    _seed_hit(db, stock_id="2383", stock_name="台光電", snapshot_date_=D1)  # 魚尾週期從 D1 起算
    obs = _seed_observation(db, stock_id="2383", stock_name="台光電", first_seen_date=D0 - timedelta(days=5))
    _seed_review(db, obs, D1, "STOP_OBSERVING")

    evidence = sp.build_daily_evidence(db, stock_id="2383", stock_name="台光電", first_seen_date=D1, target_date=D1)
    assert evidence.p4_decision == "STOP_OBSERVING", (
        "first_seen_date（D1，魚尾）跟 P4 episode 的 started_signal_date（D0-5天）不同，"
        "仍然要能正確找到對應的 P4 觀察並讀出 STOP_OBSERVING"
    )


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
    """這條測的是 `run_daily_trading_strategy` 對『非 v1_frozen』策略版本共用的通用
    出場優先序框架（-8% 真實停損短路其他判斷），跟 v1_frozen 本身的門檻無關——2026-09-09
    起 v1_frozen 已改版為 Dual-Engine（見 shadow_portfolio.py 檔頭說明），真實停損改成
    Fast Stop -5%／Real Stop -8% 兩種、且優先序不再共用這條路徑，所以改用同樣繼承
    `V1_STRATEGY_PARAMS`（setup_a/setup_b/-8%/+10%）但完全不受這次改版影響的
    `CLEAN_NO_FIXED_TP` 驗證通用框架本身仍然正確。"""
    V = sp.STRATEGY_VERSION_CLEAN_NO_FIXED_TP
    _seed_trading_calendar(db, D0, 5)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D1, "STOP_OBSERVING")

    portfolio = ShadowVirtualPortfolio(strategy_version=V, cash=500000.0)
    db.add(portfolio)
    position = ShadowVirtualPosition(
        strategy_version=V, stock_id="1101", stock_name="台泥", first_seen_date=D0,
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

    sp.run_daily_trading_strategy(db, target_date=D1, strategy_version=V)
    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == V,
            ShadowStrategyDailyDecision.stock_id == "1101",
            ShadowStrategyDailyDecision.trade_date == D1,
        )
        .first()
    )
    assert decision.action == sp.ACTION_SELL
    assert decision.action_reason == sp.EXIT_REASON_REAL_STOP_LOSS


# ---------------------------------------------------------------------------
# 容量/現金分配
# ---------------------------------------------------------------------------
def test_capacity_allocation_skips_lower_ranked_candidate_when_cash_insufficient(db):
    """通用容量/現金分配框架測試——同 `test_real_stop_loss_overrides_p4_stop` 的理由，
    改用 `CLEAN_NO_FIXED_TP`（2026-09-09 起 v1_frozen 本身已改版為 Dual-Engine，
    entry_score/setup_a 這套排序機制不再是 v1_frozen 實際使用的邏輯）。"""
    V = sp.STRATEGY_VERSION_CLEAN_NO_FIXED_TP
    _seed_trading_calendar(db, D0, 5)
    db.add(ShadowVirtualPortfolio(strategy_version=V, cash=150000.0))
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

    # D0 各自的 hit 代表魚尾週期在 D0 第 1 次選中（讓 resolve_tracking_universe 找得到
    # 這兩檔、first_seen_date 正確落在 D0），D2 刻意不額外插入 hit，讓 hit_count_so_far
    # 在 D2 仍維持 1（不跳出 setup_a 的 hit_count==1 門檻）
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_hit(db, stock_id="2330", stock_name="台積電", snapshot_date_=D0)
    obs_a = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs_a, D2, "CAUTION", momentum_score=78.0)

    obs_b = _seed_observation(db, stock_id="2330", stock_name="台積電", first_seen_date=D0)
    _seed_review(db, obs_b, D2, "CAUTION", momentum_score=70.0)

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=V)
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


# ---------------------------------------------------------------------------
# ShadowCompletedTrade 永久交易紀錄
# ---------------------------------------------------------------------------
def _seed_position_with_lot(db, *, stock_id, stock_name, entry_price, shares, allocation, entry_type="EARLY_HEALTHY_PULLBACK", entry_date=D0):
    position = ShadowVirtualPosition(
        strategy_version=sp.STRATEGY_VERSION, stock_id=stock_id, stock_name=stock_name, first_seen_date=entry_date,
    )
    db.add(position)
    db.commit()
    lot = ShadowPositionLot(
        position_id=position.id, entry_type=entry_type, entry_signal_date=entry_date,
        entry_execution_date=entry_date, entry_price=entry_price, shares=shares, allocation=allocation,
        entry_day_index=2, entry_hit_count=1, entry_momentum=75.0, entry_p4_decision="CAUTION",
        entry_mark_to_market_return=-1.5,
    )
    db.add(lot)
    db.commit()
    return position, lot


def test_sell_records_completed_trade_with_full_entry_exit_fields(db):
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=500000.0))
    db.commit()
    _seed_position_with_lot(db, stock_id="1101", stock_name="台泥", entry_price=100.0, shares=1000.0, allocation=100000.0, entry_date=D0)
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_SELL, signal_date=D1, scheduled_execution_date=D2,
            status=sp.ORDER_STATUS_PENDING, units=1, reason=sp.EXIT_REASON_TAKE_PROFIT,
        )
    )
    db.commit()
    _seed_price(db, "1101", D2, low=108.0)

    sp.execute_pending_strategy_orders(db, target_date=D2)
    db.commit()

    trade = db.query(ShadowCompletedTrade).filter(ShadowCompletedTrade.stock_id == "1101").first()
    assert trade is not None
    assert trade.entry_price == 100.0
    assert trade.entry_day_index == 2
    assert trade.entry_hit_count == 1
    assert trade.entry_momentum == 75.0
    assert trade.entry_p4_decision == "CAUTION"
    assert trade.exit_reason == sp.EXIT_REASON_TAKE_PROFIT
    assert trade.exit_price == 108.0
    assert trade.exit_execution_date == D2
    assert trade.shares == 1000.0
    assert trade.allocation == 100000.0
    assert trade.realized_pnl == 8000.0
    assert trade.realized_return_pct == 8.0
    assert trade.followed_by_rotation is False
    # 平倉後 lot/position 應該被清掉（既有行為，沒有因為新增交易紀錄而改變）
    assert db.query(ShadowVirtualPosition).count() == 0


def test_sell_marks_followed_by_rotation_when_buy_happens_same_day(db):
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=200000.0))
    db.commit()
    _seed_position_with_lot(db, stock_id="1101", stock_name="台泥", entry_price=100.0, shares=1000.0, allocation=100000.0, entry_date=D0)
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_SELL, signal_date=D1, scheduled_execution_date=D2,
            status=sp.ORDER_STATUS_PENDING, units=1, reason=sp.EXIT_REASON_P4_STOP,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="2330", stock_name="台積電",
            action=sp.ACTION_BUY, signal_date=D1, scheduled_execution_date=D2,
            status=sp.ORDER_STATUS_PENDING, units=1, planned_amount=100000.0,
            signal_snapshot={"first_seen_date": D1.isoformat()},
        )
    )
    db.commit()
    _seed_price(db, "1101", D2, low=90.0)
    _seed_price(db, "2330", D2, high=500.0)

    sp.execute_pending_strategy_orders(db, target_date=D2)
    db.commit()

    trade = db.query(ShadowCompletedTrade).filter(ShadowCompletedTrade.stock_id == "1101").first()
    assert trade.followed_by_rotation is True


def test_sell_without_same_day_buy_is_not_marked_as_rotation(db):
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION, cash=200000.0))
    db.commit()
    _seed_position_with_lot(db, stock_id="1101", stock_name="台泥", entry_price=100.0, shares=1000.0, allocation=100000.0, entry_date=D0)
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_SELL, signal_date=D1, scheduled_execution_date=D2,
            status=sp.ORDER_STATUS_PENDING, units=1, reason=sp.EXIT_REASON_P4_STOP,
        )
    )
    db.commit()
    _seed_price(db, "1101", D2, low=90.0)

    sp.execute_pending_strategy_orders(db, target_date=D2)
    db.commit()

    trade = db.query(ShadowCompletedTrade).filter(ShadowCompletedTrade.stock_id == "1101").first()
    assert trade.followed_by_rotation is False


# ---------------------------------------------------------------------------
# 35 交易日循環強制重置
# ---------------------------------------------------------------------------
def test_cycle_reset_not_triggered_before_35_trading_days(db):
    _seed_trading_calendar(db, D0, 40)
    trade_dates = sorted({row.trade_date for row in db.query(DailyPrice).all()})
    cycle_start = trade_dates[0]
    db.add(
        ShadowVirtualPortfolio(
            strategy_version=sp.STRATEGY_VERSION, cash=500000.0, cycle_start_trade_date=cycle_start,
        )
    )
    db.commit()

    for d in trade_dates[:34]:  # 只跑 34 個交易日，還沒滿 35
        sp.create_portfolio_daily_snapshot(db, target_date=d)
        db.commit()
        assert sp.check_and_apply_cycle_reset(db, target_date=d) is False
        db.commit()

    portfolio = db.query(ShadowVirtualPortfolio).first()
    assert portfolio.cycle_number == 1
    assert portfolio.cycle_start_trade_date == cycle_start


def test_cycle_reset_triggers_at_35th_trading_day_and_force_liquidates(db):
    _seed_trading_calendar(db, D0, 40)
    trade_dates = sorted({row.trade_date for row in db.query(DailyPrice).all()})
    cycle_start = trade_dates[0]
    day35 = trade_dates[34]

    db.add(
        ShadowVirtualPortfolio(
            strategy_version=sp.STRATEGY_VERSION, cash=500000.0, cycle_start_trade_date=cycle_start,
        )
    )
    db.commit()
    _seed_position_with_lot(db, stock_id="1101", stock_name="台泥", entry_price=100.0, shares=1000.0, allocation=100000.0, entry_date=cycle_start)
    _seed_price(db, "1101", day35, close=120.0)

    for d in trade_dates[:34]:
        sp.create_portfolio_daily_snapshot(db, target_date=d)
        db.commit()

    sp.create_portfolio_daily_snapshot(db, target_date=day35)
    db.commit()
    reset_triggered = sp.check_and_apply_cycle_reset(db, target_date=day35)
    db.commit()

    assert reset_triggered is True
    portfolio = db.query(ShadowVirtualPortfolio).first()
    assert portfolio.cash == sp.V1_STRATEGY_PARAMS["initial_capital"]
    assert portfolio.realized_pnl_cumulative == 0.0
    assert portfolio.cycle_number == 2
    assert portfolio.cycle_start_trade_date is None

    assert db.query(ShadowVirtualPosition).count() == 0
    assert db.query(ShadowPositionLot).count() == 0

    trade = db.query(ShadowCompletedTrade).filter(ShadowCompletedTrade.stock_id == "1101").first()
    assert trade is not None
    assert trade.exit_reason == sp.EXIT_REASON_CYCLE_RESET
    assert trade.exit_price == 120.0
    assert trade.exit_execution_date == day35
    assert trade.cycle_number == 1  # 屬於被結束的那個循環，不是新循環


def test_cycle_reset_cancels_pending_orders(db):
    _seed_trading_calendar(db, D0, 40)
    trade_dates = sorted({row.trade_date for row in db.query(DailyPrice).all()})
    cycle_start = trade_dates[0]
    day35 = trade_dates[34]

    db.add(
        ShadowVirtualPortfolio(
            strategy_version=sp.STRATEGY_VERSION, cash=500000.0, cycle_start_trade_date=cycle_start,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=sp.STRATEGY_VERSION, stock_id="2330", stock_name="台積電",
            action=sp.ACTION_BUY, signal_date=day35, scheduled_execution_date=trade_dates[35],
            status=sp.ORDER_STATUS_PENDING, units=1, planned_amount=100000.0,
        )
    )
    db.commit()

    for d in trade_dates[:34]:
        sp.create_portfolio_daily_snapshot(db, target_date=d)
        db.commit()
    sp.create_portfolio_daily_snapshot(db, target_date=day35)
    db.commit()
    sp.check_and_apply_cycle_reset(db, target_date=day35)
    db.commit()

    order = db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.stock_id == "2330").first()
    assert order.status == "CANCELLED"


def test_cycle_reset_does_not_touch_daily_decisions_or_completed_trades(db):
    """完成交易的永久紀錄跟 append-only 決策紀錄，不因循環重置而消失。"""
    _seed_trading_calendar(db, D0, 40)
    trade_dates = sorted({row.trade_date for row in db.query(DailyPrice).all()})
    cycle_start = trade_dates[0]
    day35 = trade_dates[34]

    db.add(
        ShadowVirtualPortfolio(
            strategy_version=sp.STRATEGY_VERSION, cash=500000.0, cycle_start_trade_date=cycle_start,
        )
    )
    db.add(
        ShadowStrategyDailyDecision(
            strategy_version=sp.STRATEGY_VERSION, trade_date=cycle_start, stock_id="2330",
            stock_name="台積電", action=sp.ACTION_WATCH,
        )
    )
    db.commit()
    _seed_position_with_lot(db, stock_id="1101", stock_name="台泥", entry_price=100.0, shares=1000.0, allocation=100000.0, entry_date=cycle_start)
    _seed_price(db, "1101", day35, close=120.0)

    for d in trade_dates[:34]:
        sp.create_portfolio_daily_snapshot(db, target_date=d)
        db.commit()
    sp.create_portfolio_daily_snapshot(db, target_date=day35)
    db.commit()
    sp.check_and_apply_cycle_reset(db, target_date=day35)
    db.commit()

    assert db.query(ShadowStrategyDailyDecision).count() == 1  # 沒被清掉
    assert db.query(ShadowCompletedTrade).count() == 1  # 平倉紀錄保留


# ---------------------------------------------------------------------------
# 2026-09：Shadow Portfolio Forward Freeze —— STRATEGY_PARAMS_BY_VERSION 登記表 +
# FORWARD_V1_202609（no unit caps / 50% 曝險上限 / 加碼須先獲利 / 無固定停利 /
# 無 Rotation）。v1_frozen 既有全部測試（本檔案上方）維持逐一通過即代表這次重構對
# v1_frozen 零行為變更；這裡只補新增/修正的行為。
# ---------------------------------------------------------------------------


def _make_evidence_row(**overrides) -> sp.EvidenceRow:
    base = dict(
        stock_id="1101", stock_name="台泥", first_seen_date=D0, trade_date=D2,
        day_index=3, p3_selected_today=False, hit_count_so_far=1, momentum_score=75.0,
        p4_decision="CAUTION", mark_to_market_return_pct=-1.5, is_official_exit_signal_day=False,
    )
    base.update(overrides)
    return sp.EvidenceRow(**base)


def _seed_position_with_lots(db, *, strategy_version, stock_id, stock_name, first_seen_date, lots):
    """`lots`：list[dict]，每個至少含 entry_price/shares/allocation。跟既有
    `_seed_position_with_lot`（單數、硬編碼 v1_frozen）平行，差別是可指定
    `strategy_version` 且一次可建多個 lot（測 FORWARD_V1 no-unit-cap 用）。"""
    position = ShadowVirtualPosition(
        strategy_version=strategy_version, stock_id=stock_id, stock_name=stock_name,
        first_seen_date=first_seen_date,
    )
    db.add(position)
    db.commit()
    for lot_kwargs in lots:
        db.add(
            ShadowPositionLot(
                position_id=position.id,
                entry_type=lot_kwargs.get("entry_type", "EARLY_HEALTHY_PULLBACK"),
                entry_signal_date=lot_kwargs.get("entry_signal_date", first_seen_date),
                entry_execution_date=lot_kwargs.get("entry_execution_date", first_seen_date),
                entry_price=lot_kwargs["entry_price"], shares=lot_kwargs["shares"],
                allocation=lot_kwargs["allocation"],
            )
        )
    db.commit()
    return position


# ---- generate_exit_signal：take_profit_basis 三態 ----
def test_generate_exit_signal_actual_position_basis_ignores_mark_to_market():
    params = {"take_profit_signal_pct": 10.0, "take_profit_basis": "actual_position"}
    row = _make_evidence_row(mark_to_market_return_pct=50.0)  # tracking return 很高
    # 真正部位報酬只有 2% -> 不該觸發固定停利
    assert sp.generate_exit_signal(row, params, actual_position_return=2.0) is None
    # 真正部位報酬達標 -> 觸發
    sig = sp.generate_exit_signal(row, params, actual_position_return=12.0)
    assert sig is not None and sig.reason == sp.EXIT_REASON_TAKE_PROFIT


def test_generate_exit_signal_no_take_profit_when_basis_none():
    params = {"take_profit_signal_pct": None, "take_profit_basis": None}
    row = _make_evidence_row(mark_to_market_return_pct=99.0)
    assert sp.generate_exit_signal(row, params, actual_position_return=99.0) is None


def test_generate_exit_signal_mark_to_market_basis_unchanged_for_clean_fixed_tp():
    """`CLEAN_FIXED_TP` 仍然 spread 自 `V1_STRATEGY_PARAMS`（`take_profit_basis=
    "actual_position"`——原測試名稱裡的『for_v1_frozen』已經不成立：2026-09-09 起
    v1_frozen 本身改版為 Dual-Engine，`generate_exit_signal` 的固定停利機制完全不再是
    v1_frozen 實際使用的邏輯（Dual-Engine 用 Fast Stop/Prove-it/Trailing 取代），
    這裡改驗證仍然共用這套通用機制的 `CLEAN_FIXED_TP`。"""
    params = sp.STRATEGY_PARAMS_BY_VERSION[sp.STRATEGY_VERSION_CLEAN_FIXED_TP]
    row = _make_evidence_row(mark_to_market_return_pct=99.0)
    # actual_position_return 才是這個 basis 真正判斷依據，不是 mark_to_market
    assert sp.generate_exit_signal(row, params, actual_position_return=2.0) is None
    sig = sp.generate_exit_signal(row, params, actual_position_return=12.0)
    assert sig is not None and sig.reason == sp.EXIT_REASON_TAKE_PROFIT


def test_generate_exit_signal_unknown_basis_raises():
    with pytest.raises(ValueError):
        sp.generate_exit_signal(
            _make_evidence_row(), {"take_profit_signal_pct": 10.0, "take_profit_basis": "bogus"}
        )


# ---- execute_pending_strategy_orders 迴歸：不可再硬編碼 V1 的 unit_capital ----
def test_execute_pending_orders_sizes_allocation_by_strategy_version_not_v1_hardcode(db, monkeypatch):
    """2026-09 之前的 bug：無論 `strategy_version` 是誰，`execute_pending_strategy_
    orders` 一律用模組層級 `V1_STRATEGY_PARAMS['unit_capital']`（100,000）算配置金額。
    這裡故意登記一個 unit_capital 明顯不同（50,000）的假策略版本，驗證真的用它自己的
    參數，不是被靜默套用 v1 的值。"""
    fake_version = "TEST_UNIT_CAPITAL_50K"
    monkeypatch.setitem(
        sp.STRATEGY_PARAMS_BY_VERSION, fake_version,
        {**sp.STRATEGY_PARAMS_BY_VERSION[sp.STRATEGY_VERSION_FORWARD_V1], "unit_capital": 50000.0},
    )
    db.add(ShadowVirtualPortfolio(strategy_version=fake_version, cash=200000.0))
    db.commit()
    db.add(
        ShadowStrategyOrder(
            strategy_version=fake_version, stock_id="1101", stock_name="台泥",
            action=sp.ACTION_BUY, signal_date=D0, scheduled_execution_date=D1,
            status=sp.ORDER_STATUS_PENDING, units=1, planned_amount=50000.0,
            signal_snapshot={"first_seen_date": D0.isoformat()},
        )
    )
    db.commit()
    _seed_price(db, "1101", D1, high=100.0)

    result = sp.execute_pending_strategy_orders(db, target_date=D1, strategy_version=fake_version)
    db.commit()

    assert result["buy"] == 1
    portfolio = (
        db.query(ShadowVirtualPortfolio)
        .filter(ShadowVirtualPortfolio.strategy_version == fake_version)
        .first()
    )
    assert portfolio.cash == 150000.0  # 200,000 - 50,000（不是被誤用 v1 的 100,000）
    lot = (
        db.query(ShadowPositionLot)
        .join(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == fake_version)
        .first()
    )
    assert lot.allocation == 50000.0
    assert lot.shares == 500.0  # 50,000 / 100


# ---- check_and_apply_cycle_reset：FORWARD_V1 / Clean Baselines 無強制循環重置 ----
def test_cycle_reset_is_noop_for_forward_v1(db):
    db.add(ShadowVirtualPortfolio(strategy_version=sp.STRATEGY_VERSION_FORWARD_V1, cash=600000.0))
    db.commit()
    _seed_position_with_lots(
        db, strategy_version=sp.STRATEGY_VERSION_FORWARD_V1, stock_id="1101", stock_name="台泥",
        first_seen_date=D0, lots=[{"entry_price": 100.0, "shares": 1000.0, "allocation": 100000.0}],
    )
    triggered = sp.check_and_apply_cycle_reset(db, target_date=D0, strategy_version=sp.STRATEGY_VERSION_FORWARD_V1)
    db.commit()
    assert triggered is False
    # 部位完全沒被動過
    assert db.query(ShadowVirtualPosition).count() == 1
    assert db.query(ShadowPositionLot).count() == 1


# ---------------------------------------------------------------------------
# FORWARD_V1_202609 整合測試：透過 `run_daily_trading_strategy` 端到端驗證
# ---------------------------------------------------------------------------
FV1 = sp.STRATEGY_VERSION_FORWARD_V1


def _seed_setup_a_universe(db, *, stock_id="1101", stock_name="台泥"):
    """讓 `stock_id` 在 D2 觸發 EARLY_HEALTHY_PULLBACK（setup_a：day_index 2~3、
    hit_count_so_far==1、momentum 68~80、tracking return -2.5~0、p4=CAUTION）。跟既有
    `test_capacity_allocation_skips_lower_ranked_candidate_when_cash_insufficient` 用
    同一套建構方式。"""
    _seed_trading_calendar(db, D0, 5)
    _seed_price(db, stock_id, D0, open_=100.0, close=100.0)
    _seed_price(db, stock_id, D1, open_=100.0, close=100.0)  # baseline=100
    _seed_price(db, stock_id, D2, open_=98.0, close=98.0)  # tracking return=-2.0%
    _seed_hit(db, stock_id=stock_id, stock_name=stock_name, snapshot_date_=D0)
    obs = _seed_observation(db, stock_id=stock_id, stock_name=stock_name, first_seen_date=D0)
    _seed_review(db, obs, D2, "CAUTION", momentum_score=75.0)


def test_forward_v1_blocks_add_when_position_not_profitable(db):
    _seed_setup_a_universe(db)
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=500000.0))
    db.commit()
    # entry_price 使得 D2 收盤(98) 換算 actual_position_return ≈ -3.0%（虧損，但沒觸發
    # -8% 真實停損，才能真正驗證是「加碼須先獲利」這條規則擋下，不是被停損短路）
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 98.0 / 0.97, "shares": 100000.0 / (98.0 / 0.97), "allocation": 100000.0}],
    )

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.trade_date == D2)
        .first()
    )
    assert decision.action == sp.ACTION_SKIPPED_ADD_NOT_PROFITABLE
    assert decision.actual_position_return is not None and decision.actual_position_return < 0
    assert db.query(ShadowStrategyOrder).count() == 0  # 完全沒有建立加碼訂單


def test_forward_v1_allows_add_when_position_profitable(db):
    _seed_setup_a_universe(db)
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=500000.0))
    db.commit()
    # entry_price=90 -> D2 收盤 98 時 actual_position_return ≈ +8.9%（獲利）
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 90.0, "shares": 100000.0 / 90.0, "allocation": 100000.0}],
    )

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.trade_date == D2)
        .first()
    )
    assert decision.action == sp.ACTION_ADD
    order = db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.strategy_version == FV1).first()
    assert order is not None and order.action == sp.ACTION_ADD


def test_forward_v1_no_unit_caps_allows_unlimited_adds_to_same_stock(db):
    """v1_frozen 的 max_units_per_stock=2 會擋下第 3 筆加碼；FORWARD_V1_202609 拿掉這個
    上限，同一檔股票應該可以繼續加碼（只要仍然獲利、現金/曝險allow）。"""
    _seed_setup_a_universe(db)
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=1_000_000.0))
    db.commit()
    # 已經有 2 個 lot（模擬 v1 情境下已達 max_units_per_stock=2 的狀態），entry_price 都
    # 遠低於 D2 收盤 98，確保獲利門檻通過
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[
            {"entry_price": 80.0, "shares": 100000.0 / 80.0, "allocation": 100000.0},
            {"entry_price": 82.0, "shares": 100000.0 / 82.0, "allocation": 100000.0},
        ],
    )

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.trade_date == D2)
        .first()
    )
    assert decision.action == sp.ACTION_ADD  # 沒有被 max_units_per_stock 擋下（因為 FORWARD_V1 沒有這個上限）


def test_forward_v1_position_exposure_limit_blocks_add_and_records_missed_candidate(db):
    """單一 stock 的 position cost 加上這次要加碼的金額，不得超過目前 portfolio
    equity 的 50%——這裡建構一個現金充足（不會先被「現金不足」短路擋下）、但既有部位
    市值已經很高的場景，驗證真正被擋下的是曝險上限，且寫入 `ShadowMissedCandidate`。"""
    _seed_setup_a_universe(db)
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=200000.0))
    db.commit()
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 50.0, "shares": 2000.0, "allocation": 100000.0}],
    )
    # equity = cash(200,000) + market_value(2000 shares * 98 收盤 = 196,000) = 396,000
    # existing_cost(100,000) + unit_capital(100,000) = 200,000 -> 200,000/396,000 ≈ 50.5% > 50%
    # （現金 200,000 >= unit_capital 100,000，所以不會被「現金不足」那個 elif 分支先短路）

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.trade_date == D2)
        .first()
    )
    assert decision.action == sp.ACTION_SKIPPED_CAPACITY
    assert sp.SKIP_REASON_POSITION_EXPOSURE_LIMIT in decision.action_reason

    missed = db.query(ShadowMissedCandidate).filter(ShadowMissedCandidate.strategy_version == FV1).first()
    assert missed is not None
    assert missed.skip_reason == sp.SKIP_REASON_POSITION_EXPOSURE_LIMIT
    assert missed.stock_id == "1101"
    assert missed.portfolio_snapshot["equity"] is not None


def test_forward_v1_skip_portfolio_full_records_missed_candidate(db):
    """FORWARD_V1 名額已滿（max_stocks=5）時，新的合格候選要記進 ShadowMissedCandidate，
    reason=SKIP_PORTFOLIO_FULL，且既有 5 檔部位完全不會被 Rotation 賣掉（Part 21）。"""
    _seed_setup_a_universe(db, stock_id="9999", stock_name="候選股")
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=1_000_000.0))
    db.commit()
    # 塞滿 5 檔既有部位（用跟候選股無關的股票代號，避免今天又被評估到）
    for i, sid in enumerate(["1001", "1002", "1003", "1004", "1005"]):
        _seed_position_with_lots(
            db, strategy_version=FV1, stock_id=sid, stock_name=f"既有{i}", first_seen_date=D0,
            lots=[{"entry_price": 50.0, "shares": 2000.0, "allocation": 100000.0}],
        )

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(
            ShadowStrategyDailyDecision.strategy_version == FV1,
            ShadowStrategyDailyDecision.trade_date == D2,
            ShadowStrategyDailyDecision.stock_id == "9999",
        )
        .first()
    )
    assert decision.action == sp.ACTION_SKIPPED_CAPACITY
    assert sp.SKIP_REASON_PORTFOLIO_FULL in decision.action_reason
    missed = db.query(ShadowMissedCandidate).filter(ShadowMissedCandidate.stock_id == "9999").first()
    assert missed is not None and missed.skip_reason == sp.SKIP_REASON_PORTFOLIO_FULL

    # Part 21：既有 5 檔部位完全沒有被強制賣出（沒有任何 SELL 訂單/決策）
    assert db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.action == sp.ACTION_SELL).count() == 0
    sell_decisions = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.action == sp.ACTION_SELL)
        .count()
    )
    assert sell_decisions == 0


def test_forward_v1_no_fixed_take_profit_even_when_actual_return_above_10_pct(db):
    """FORWARD_V1 完全沒有固定停利：即使真實部位報酬 >= +10%，也只維持 HOLD，不產生
    SELL/TAKE_PROFIT。"""
    # 用一檔不在候選池評估邏輯之外、單純持有的股票（不需要今天觸發任何進場訊號）
    _seed_trading_calendar(db, D0, 5)
    _seed_price(db, "1101", D2, close=115.0)
    db.add(ShadowVirtualPortfolio(strategy_version=FV1, cash=500000.0))
    db.commit()
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=D0)
    _seed_review(db, obs, D2, "CONTINUE", momentum_score=60.0)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=D0)
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 100.0, "shares": 1000.0, "allocation": 100000.0}],
    )
    # actual_position_return = (115/100 - 1) * 100 = +15% >= 10%，遠高於 winner 門檻

    sp.run_daily_trading_strategy(db, target_date=D2, strategy_version=FV1)
    db.commit()

    decision = (
        db.query(ShadowStrategyDailyDecision)
        .filter(ShadowStrategyDailyDecision.strategy_version == FV1, ShadowStrategyDailyDecision.stock_id == "1101")
        .first()
    )
    assert decision.action == sp.ACTION_HOLD  # 不是 SELL
    assert decision.actual_position_return == pytest.approx(15.0)
    assert db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.action == sp.ACTION_SELL).count() == 0


# ---------------------------------------------------------------------------
# update_winner_tracking（Part 49，純觀察）
# ---------------------------------------------------------------------------
def test_update_winner_tracking_creates_row_only_once_threshold_crossed(db):
    _seed_trading_calendar(db, D0, 5)
    _seed_price(db, "1101", D1, close=105.0)  # +5%，還沒到 +10%
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 100.0, "shares": 1000.0, "allocation": 100000.0}],
    )

    updated = sp.update_winner_tracking(db, target_date=D1, strategy_version=FV1)
    db.commit()
    assert updated == 0
    assert db.query(ShadowWinnerTracking).count() == 0

    _seed_price(db, "1101", D2, close=112.0)  # +12%，跨過門檻
    updated2 = sp.update_winner_tracking(db, target_date=D2, strategy_version=FV1)
    db.commit()
    assert updated2 == 1
    row = db.query(ShadowWinnerTracking).first()
    assert row.current_actual_return == pytest.approx(12.0)
    assert row.highest_actual_return == pytest.approx(12.0)
    assert row.winner_10_first_date == D2
    assert row.drawdown_from_peak_pct == pytest.approx(0.0)


def test_update_winner_tracking_keeps_tracking_after_falling_back_below_10_pct(db):
    """一旦進入 Winner 生命週期，即使之後報酬跌回 10% 以下，仍要繼續記錄（觀察完整的
    漲多少/回落多少），不能半路停止觀察。"""
    _seed_trading_calendar(db, D0, 5)
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 100.0, "shares": 1000.0, "allocation": 100000.0}],
    )
    _seed_price(db, "1101", D1, close=120.0)  # +20%，peak
    sp.update_winner_tracking(db, target_date=D1, strategy_version=FV1)
    db.commit()

    _seed_price(db, "1101", D2, close=105.0)  # 回落到 +5%（低於 10% 門檻，但曾經達標過）
    updated = sp.update_winner_tracking(db, target_date=D2, strategy_version=FV1)
    db.commit()
    assert updated == 1

    rows = db.query(ShadowWinnerTracking).order_by(ShadowWinnerTracking.trade_date).all()
    assert len(rows) == 2
    latest = rows[-1]
    assert latest.current_actual_return == pytest.approx(5.0)
    assert latest.highest_actual_return == pytest.approx(20.0)  # peak 仍是 D1 那天
    assert latest.drawdown_from_peak_pct == pytest.approx(5.0 - 20.0)
    assert latest.winner_10_first_date == D1  # 沿用第一次達標的日期，不會被之後的日期覆蓋


def test_update_winner_tracking_idempotent_on_rerun_same_day(db):
    _seed_trading_calendar(db, D0, 5)
    _seed_price(db, "1101", D1, close=115.0)
    _seed_position_with_lots(
        db, strategy_version=FV1, stock_id="1101", stock_name="台泥", first_seen_date=D0,
        lots=[{"entry_price": 100.0, "shares": 1000.0, "allocation": 100000.0}],
    )
    sp.update_winner_tracking(db, target_date=D1, strategy_version=FV1)
    db.commit()
    sp.update_winner_tracking(db, target_date=D1, strategy_version=FV1)  # 重跑同一天
    db.commit()
    assert db.query(ShadowWinnerTracking).count() == 1  # 沒有重複列，是 UPSERT
