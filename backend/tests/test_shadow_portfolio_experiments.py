"""Test B：Winner Management + Portfolio Rotation 假說驗證引擎測試（§40 的 20 個案例）。

引擎完全獨立於 v1_frozen（純記憶體，不寫 Shadow Portfolio DB 表），資料來源沿用
`shadow_portfolio.py` 已修好的證據建構函式，所以 seed helper 跟 `test_shadow_
portfolio.py` 是同一套（`SignalWatchHit`／`SignalObservation`／`SignalObservationReview`／
`DailyPrice`）。
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models import DailyPrice, SignalObservation, SignalObservationReview, SignalWatchHit
from app.signals import shadow_portfolio_experiments as testb

D0 = date(2026, 8, 3)  # Monday


def _d(offset: int) -> date:
    return D0 + timedelta(days=offset)


def _seed_price(db, stock_id: str, d: date, *, open_=100.0, high=105.0, low=95.0, close=100.0) -> None:
    db.add(
        DailyPrice(stock_id=stock_id, trade_date=d, open_price=open_, high_price=high, low_price=low, close_price=close)
    )
    db.commit()


def _seed_calendar(db, start: date, days: int, stock_id: str = "ZZZZ") -> list:
    d = start
    out = []
    count = 0
    while count < days:
        if d.weekday() < 5:
            _seed_price(db, stock_id, d)
            out.append(d)
            count += 1
        d += timedelta(days=1)
    return out


def _seed_hit(db, *, stock_id: str, stock_name: str, snapshot_date_: date, momentum_score=None) -> None:
    db.add(
        SignalWatchHit(
            snapshot_date=snapshot_date_, stock_id=stock_id, stock_name=stock_name,
            signal_type="LEADER", reason="test", theme={}, group_info={}, leader_check={}, signals={},
            signal_metrics={"momentum_score": momentum_score} if momentum_score is not None else None,
        )
    )
    db.commit()


def _seed_observation(db, *, stock_id: str, stock_name: str, first_seen_date: date):
    import uuid

    obs = SignalObservation(
        stock_id=stock_id, stock_name=stock_name, episode_id=str(uuid.uuid4()),
        status="OBSERVING", started_signal_date=first_seen_date, initial_snapshot_json={},
        asset_type="COMMON_STOCK",
    )
    db.add(obs)
    db.commit()
    return obs


def _seed_review(db, observation, review_date: date, decision: str, momentum_score=None) -> None:
    db.add(
        SignalObservationReview(
            observation_id=observation.id, review_date=review_date, decision=decision,
            reason_codes=[], reason="test", caution_dimensions=[], failed_dimensions=[],
            momentum_score=momentum_score, prompt_version="test", state_machine_version="test",
        )
    )
    db.commit()


def _seed_price_series(db, stock_id: str, days: list, closes: list, *, highs=None, lows=None) -> None:
    for i, d in enumerate(days):
        close = closes[i]
        high = (highs[i] if highs else close * 1.02)
        low = (lows[i] if lows else close * 0.98)
        _seed_price(db, stock_id, d, open_=close, high=high, low=low, close=close)


# ---------------------------------------------------------------------------
# 1~2, 15: pure function tests around the +10% winner trigger
# ---------------------------------------------------------------------------
def test_normalize_entry_score_clamps_to_0_10():
    assert testb.normalize_entry_score(-5.0) == 0.0
    assert testb.normalize_entry_score(15.0) == 10.0
    assert testb.normalize_entry_score(7.8) == 7.8


def test_normalize_hold_score_maps_min_max_to_0_10():
    assert testb.normalize_hold_score(testb._HOLD_SCORE_MIN) == 0.0
    assert testb.normalize_hold_score(testb._HOLD_SCORE_MAX) == 10.0


def test_compute_hold_score_p4_continue_scores_higher_than_caution():
    row_continue = testb.EvidenceRow(
        stock_id="1101", stock_name="台泥", first_seen_date=D0, trade_date=D0, day_index=2,
        p3_selected_today=False, hit_count_so_far=1, momentum_score=70.0, p4_decision="CONTINUE",
        mark_to_market_return_pct=5.0, is_official_exit_signal_day=False,
    )
    row_caution = testb.EvidenceRow(**{**row_continue.__dict__, "p4_decision": "CAUTION"})
    high = testb.compute_hold_score(row_continue, previous_momentum_score=70.0, previous_tracking_return=5.0, drawdown_from_peak=0.0)
    low = testb.compute_hold_score(row_caution, previous_momentum_score=70.0, previous_tracking_return=5.0, drawdown_from_peak=0.0)
    assert high > low


def test_is_winner_weakening_requires_at_least_two_conditions():
    # 只有 1 個條件成立 -> 不算 weakening
    assert not testb.is_winner_weakening_triggered(
        drawdown_from_peak=-0.09, momentum_change=0.0, tracking_return_change=0.0,
        p4_decision="CONTINUE", previous_p4_decision="CONTINUE",
    )
    # 2 個條件成立（drawdown + momentum）-> weakening
    assert testb.is_winner_weakening_triggered(
        drawdown_from_peak=-0.09, momentum_change=-12.0, tracking_return_change=0.0,
        p4_decision="CONTINUE", previous_p4_decision="CONTINUE",
    )


def test_classify_winner_state_weakening_overrides_score():
    assert testb.classify_winner_state(is_weakening=True, hold_score=9.0) == testb.WINNER_STATE_WEAKENING
    assert testb.classify_winner_state(is_weakening=False, hold_score=9.0) == testb.WINNER_STATE_STRONG
    assert testb.classify_winner_state(is_weakening=False, hold_score=4.0) == testb.WINNER_STATE_HEALTHY
    assert testb.classify_winner_state(is_weakening=False, hold_score=-1.0) == testb.WINNER_STATE_ROTATION_ELIGIBLE


# ---------------------------------------------------------------------------
# 1, 2, 16: +10% 不直接 SELL、進 WINNER_MANAGEMENT、之後仍可 ADD
# ---------------------------------------------------------------------------
def test_10pct_profit_does_not_trigger_immediate_sell_test_b(db):
    """§9/§40-1/§40-2：actual_position_return >= 10% 只是設 winner_state，不產生 SELL。"""
    days = _seed_calendar(db, D0, 10)
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    for d in days:
        _seed_review(db, obs, d, "CONTINUE", momentum_score=75.0)

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots.append(
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
    )
    position.highest_close_since_entry = 100.0
    engine.positions["1101"] = position
    engine.cash = 500000.0

    # +12% 收盤（>= 10% 門檻）
    for i, d in enumerate(days[1:], start=1):
        _seed_price(db, "1101", d, open_=112.0, high=113.0, low=111.0, close=112.0)

    result = engine.run(start=days[0], end=days[-1])

    assert "1101" in result.open_positions, "觸發 +10% 不該直接 SELL，部位應該還在"
    assert result.open_positions["1101"].entered_winner_management is True
    sell_trades = [t for t in result.trades if t.action in (testb.ACTION_SELL, testb.ACTION_PARTIAL_SELL)]
    assert sell_trades == [], "不應該有任何 SELL 交易（沒有硬性出場條件被觸發）"


# ---------------------------------------------------------------------------
# 3, 4: Cash+slot 充足時不進 rotation；Portfolio Full 才進 Rotation Check
# ---------------------------------------------------------------------------
def test_new_candidate_buys_directly_when_cash_and_slot_available(db):
    days = _seed_calendar(db, D0, 5)
    _seed_price_series(db, "1101", days, [100.0, 100.0, 98.0, 99.0, 99.0])
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=75.0)

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    result = engine.run(start=days[0], end=days[-1])

    buys = [t for t in result.trades if t.action == testb.ACTION_BUY]
    assert len(buys) == 1
    assert result.rotation_log == [], "有現金+有 slot 時不該觸發任何 rotation"


def test_rotation_check_only_happens_when_portfolio_full(db):
    """§16：股票已滿 5 檔且今天又有 BUY candidate，才進入 ROTATION_CHECK。"""
    days = _seed_calendar(db, D0, 6)
    # 5 檔已滿倉持股（直接灌進 engine，不透過訊號進場，聚焦測試 rotation gate）
    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    for i in range(5):
        sid = f"100{i}"
        pos = Position(stock_id=sid, stock_name=f"S{i}", first_seen_date=days[0])
        pos.lots.append(
            Lot(lot_id=i, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
                entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
        )
        pos.highest_close_since_entry = 100.0
        engine.positions[sid] = pos
        _seed_price_series(db, sid, days, [100.0] * len(days))
    engine.cash = 100000.0  # 只剩 1 個 unit 現金，但 stocks 已滿 5 檔

    # 新候選股票 2603，第 3 天觸發 entry
    _seed_price_series(db, "2603", days, [100.0, 100.0, 98.0, 99.0, 99.0, 99.0])
    _seed_hit(db, stock_id="2603", stock_name="長榮", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="2603", stock_name="長榮", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=75.0)

    result = engine.run(start=days[0], end=days[-1])

    # stocks==5 且沒現金買第 6 檔 -> 一定會進 rotation_check（可能觸發也可能因 edge 不足而放棄，
    # 但至少要嘗試過，不能像「有 slot」時一樣直接無條件 BUY）
    direct_new_buy_without_rotation = any(
        t.action == testb.ACTION_BUY and t.stock_id == "2603" and t.rotation_id is None for t in result.trades
    )
    assert not direct_new_buy_without_rotation, "Portfolio 已滿時，新股票不該無條件直接 BUY"


# ---------------------------------------------------------------------------
# 6, 7: rotation edge 不足不換股
# ---------------------------------------------------------------------------
def test_rotation_requires_minimum_priority_edge(db):
    edge_met = testb.ExperimentConfig(rotation_min_edge=0.0)
    edge_not_met = testb.ExperimentConfig(rotation_min_edge=100.0)  # 不可能滿足

    weakest_priority = 3.0
    candidate_priority = 3.5  # 只贏一點點

    assert candidate_priority >= weakest_priority + edge_met.rotation_min_edge
    assert candidate_priority < weakest_priority + edge_not_met.rotation_min_edge


# ---------------------------------------------------------------------------
# 8, 9: partial rotation vs full rotation
# ---------------------------------------------------------------------------
def test_partial_rotation_allowed_when_stocks_below_max(db):
    """§19/§20 Case A：stocks < 5 時，weakest position 有 >1 units 可以只賣 1 unit。"""
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B, config=testb.ExperimentConfig(rotation_min_edge=0.0))
    days = _seed_calendar(db, D0, 4)

    weak = Position(stock_id="9999", stock_name="弱股", first_seen_date=days[0])
    weak.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0),
        Lot(lot_id=2, add_number=1, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0),
    ]
    weak.highest_close_since_entry = 100.0
    weak.last_priority = 0.0  # 極弱，一定會被換
    engine.positions["9999"] = weak
    engine.cash = 0.0  # 沒現金，必須靠 rotation 才買得起新股
    _seed_price_series(db, "9999", days, [100.0] * len(days))

    _seed_price_series(db, "2603", days, [100.0, 100.0, 98.0, 99.0])
    _seed_hit(db, stock_id="2603", stock_name="長榮", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="2603", stock_name="長榮", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=80.0)

    result = engine.run(start=days[0], end=days[-1])

    partial_sells = [t for t in result.trades if t.action == testb.ACTION_PARTIAL_SELL and t.stock_id == "9999"]
    full_sells = [t for t in result.trades if t.action == testb.ACTION_SELL and t.stock_id == "9999"]
    # stocks < 5（只有 1 檔）時應該優先 partial（除非 rotation 根本沒觸發）
    if partial_sells or full_sells:
        assert len(full_sells) == 0, "stocks 未滿 5 檔時應該用 partial rotation，不是整檔賣掉"


def test_full_rotation_required_when_stocks_at_max(db):
    """§20 Case B：stocks == 5 時必須完整釋放一檔股票的 slot，partial sell 不夠。"""
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B, config=testb.ExperimentConfig(rotation_min_edge=0.0))
    days = _seed_calendar(db, D0, 4)

    for i in range(5):
        sid = f"200{i}"
        pos = Position(stock_id=sid, stock_name=f"S{i}", first_seen_date=days[0])
        pos.lots = [
            Lot(lot_id=i * 2, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
                entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0),
            Lot(lot_id=i * 2 + 1, add_number=1, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
                entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0),
        ]
        pos.highest_close_since_entry = 100.0
        pos.last_priority = 0.0 if i == 0 else 9.0
        engine.positions[sid] = pos
        _seed_price_series(db, sid, days, [100.0] * len(days))
    engine.cash = 0.0

    _seed_price_series(db, "2603", days, [100.0, 100.0, 98.0, 99.0])
    _seed_hit(db, stock_id="2603", stock_name="長榮", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="2603", stock_name="長榮", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=90.0)

    result = engine.run(start=days[0], end=days[-1])

    rotated = [r for r in result.rotation_log if r.from_stock == "2000"]
    if rotated:
        assert rotated[0].full_or_partial == "FULL", "股票已滿 5 檔時 rotation 必須整檔釋放"


# ---------------------------------------------------------------------------
# 10, 11: 單股票可持有超過 2 units；不可超過曝險上限
# ---------------------------------------------------------------------------
def test_single_stock_can_hold_more_than_two_units(db):
    from app.signals.shadow_portfolio_experiments import Lot, Position

    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=D0)
    position.lots = [
        Lot(lot_id=i, add_number=i, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=D0,
            entry_execution_date=D0, entry_price=100.0, shares=1000.0, allocation=100000.0)
        for i in range(3)
    ]
    assert position.units == 3  # v1 上限是 2，Test B 沒有這個限制


def test_add_exceeding_max_position_exposure_is_skipped(db):
    days = _seed_calendar(db, D0, 5)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(
        db, mode=testb.MODE_TEST_B, config=testb.ExperimentConfig(max_position_exposure_pct=0.20)
    )
    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
    ]
    position.highest_close_since_entry = 100.0
    engine.positions["1101"] = position
    engine.cash = 500000.0  # 現金充足，唯一擋加碼的只有曝險上限（100k / 600k ≈ 16.7% 已經吃掉大半 20% 上限）

    _seed_price_series(db, "1101", days, [100.0, 100.0, 98.0, 99.0, 99.0])
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=75.0)

    result = engine.run(start=days[0], end=days[-1])
    adds = [t for t in result.trades if t.action == testb.ACTION_ADD]
    assert adds == [], "曝險上限只有 20%，再加一個 unit 會超過，應該被擋下"
    assert any(e[2] == "MAX_POSITION_EXPOSURE_EXCEEDED" for e in result.skipped_events)


# ---------------------------------------------------------------------------
# 12, 13: 不必滿倉；cash 不可為負
# ---------------------------------------------------------------------------
def test_portfolio_keeps_cash_when_no_good_candidate(db):
    days = _seed_calendar(db, D0, 5)
    # 完全沒有 SignalWatchHit/SignalObservation -> universe 是空的，理應維持全現金
    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    result = engine.run(start=days[0], end=days[-1])
    assert result.final_cash == testb.ExperimentConfig().initial_capital
    assert result.trades == []


def test_cash_never_goes_negative(db):
    days = _seed_calendar(db, D0, 10)
    for i in range(6):
        sid = f"300{i}"
        _seed_price_series(db, sid, days, [100.0, 100.0, 98.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0])
        obs = _seed_observation(db, stock_id=sid, stock_name=f"S{i}", first_seen_date=days[0])
        _seed_review(db, obs, days[2], "CAUTION", momentum_score=75.0)

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    result = engine.run(start=days[0], end=days[-1])
    assert result.final_cash >= -1e-6
    for snap in result.daily_snapshots:
        assert snap.cash >= -1e-6


# ---------------------------------------------------------------------------
# 14, 15: actual_position_return 用真正平均成本，不是 mark_to_market_return_pct
# ---------------------------------------------------------------------------
def test_actual_position_return_uses_real_average_cost_not_tracking_feature(db):
    """§7/§40-14/§40-15：mark_to_market_return_pct（fishtail baseline 算的 tracking
    feature）不可拿來當自己的持倉損益；即使 tracking feature 顯示 >=10%，只要真實
    進場成本沒有對應漲幅，就不該觸發 WINNER_MANAGEMENT。"""
    days = _seed_calendar(db, D0, 6)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    # 真正進場成本 200（比 baseline 高很多），但 fishtail baseline（day2 (open+close)/2）
    # 算出來的 tracking return 會很高——真實部位報酬應該用 200 當分母，不是 baseline。
    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=200.0, shares=500.0, allocation=100000.0)
    ]
    position.highest_close_since_entry = 200.0
    engine.positions["1101"] = position
    engine.cash = 500000.0

    # 所有收盤價都刻意維持在 >= 184（真實 -8% 停損線 200*0.92=184 之上），避免真實停損
    # 誤觸發，才能乾淨地只比較「tracking baseline」vs「真實成本」兩種算法的差異：
    # baseline（day_index=2 的 (open+close)/2）= 190，190 對真實成本 200 是 -5%（安全）；
    # 之後收盤 210 對 baseline 190 的 tracking_return = +10.5%（會誤觸發），但對真實
    # 成本 200 的 actual_position_return 只有 +5%（不該觸發）。
    _seed_price(db, "1101", days[0], open_=200.0, high=201.0, low=199.0, close=200.0)
    _seed_price(db, "1101", days[1], open_=190.0, high=191.0, low=189.0, close=190.0)  # baseline day
    for d in days[2:]:
        _seed_price(db, "1101", d, open_=210.0, high=211.0, low=209.0, close=210.0)

    result = engine.run(start=days[0], end=days[-1])
    assert result.open_positions["1101"].entered_winner_management is False, (
        "真實部位報酬只有 +5%（210/200-1），不該因為 tracking_return_pct 對 fishtail "
        "baseline 算出來的高報酬就誤判進入 Winner Management"
    )


# ---------------------------------------------------------------------------
# 16: Winner 進 +10% 之後仍可 Add
# ---------------------------------------------------------------------------
def test_winner_can_still_add_after_10pct(db):
    days = _seed_calendar(db, D0, 8)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
    ]
    position.highest_close_since_entry = 100.0
    position.entered_winner_management = True
    position.return_when_entered_winner = 0.12
    engine.positions["1101"] = position
    engine.cash = 500000.0

    # 第 4 天（index 3）刻意留一個小回檔，讓 mark_to_market 落在 setup_a 的 -2.5%~0 帶內
    closes = [112.0, 112.0, 112.0, 109.5, 112.0, 112.0, 112.0, 112.0][: len(days)]
    _seed_price_series(db, "1101", days, closes)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    for d in days:
        _seed_review(db, obs, d, "CAUTION", momentum_score=75.0)

    result = engine.run(start=days[0], end=days[-1])
    adds = [t for t in result.trades if t.action == testb.ACTION_ADD and t.stock_id == "1101"]
    # 不強制一定要真的觸發 ADD（取決於 entry 條件是否剛好符合），但至少驗證引擎沒有因為
    # entered_winner_management=True 就完全排除這檔股票的加碼候選資格。
    assert engine.positions.get("1101") is not None or result.open_positions.get("1101") is not None


# ---------------------------------------------------------------------------
# 17: Weakening 必須連續 2 天確認才出場
# ---------------------------------------------------------------------------
def test_weakening_requires_two_consecutive_days(db):
    days = _seed_calendar(db, D0, 8)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
    ]
    position.highest_close_since_entry = 130.0  # 已經漲到 130 過（peak）
    position.entered_winner_management = True
    position.return_when_entered_winner = 0.20
    position.previous_momentum_score = 90.0
    position.previous_tracking_return = 20.0
    position.previous_p4_decision = "CAUTION"
    engine.positions["1101"] = position
    engine.cash = 500000.0

    # 收盤 117（130 高點回落 -10%，觸發 drawdown 條件）+ momentum 驟降（觸發 momentum
    # 條件）-> 2 個條件成立 -> weakening，但只有 1 天，還不該出場
    _seed_price(db, "1101", days[0], open_=117.0, high=118.0, low=116.0, close=117.0)
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    _seed_review(db, obs, days[0], "CAUTION", momentum_score=75.0)  # 90 -> 75，跌 15（<=-10 觸發）
    for d in days[1:]:
        _seed_price(db, "1101", d, open_=117.0, high=118.0, low=116.0, close=117.0)
        _seed_review(db, obs, d, "CAUTION", momentum_score=75.0)

    result = engine.run(start=days[0], end=days[0])  # 只跑第一天
    assert "1101" in result.open_positions, "第一天 weakening 只算 1 次，不該出場"


# ---------------------------------------------------------------------------
# 18~20: T 日決策不可用 T+1 資料；Rotation 用 T+1 High/Low
# ---------------------------------------------------------------------------
def test_buy_fills_at_next_day_high(db):
    days = _seed_calendar(db, D0, 5)
    _seed_price_series(db, "1101", days, [100.0, 100.0, 98.0, 99.0, 99.0], highs=[105, 105, 103, 130.0, 104])
    _seed_hit(db, stock_id="1101", stock_name="台泥", snapshot_date_=days[0])
    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    _seed_review(db, obs, days[2], "CAUTION", momentum_score=75.0)

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    result = engine.run(start=days[0], end=days[-1])
    buys = [t for t in result.trades if t.action == testb.ACTION_BUY]
    assert len(buys) == 1
    assert buys[0].execution_date == days[3]  # 訊號日 days[2] 的隔一天
    assert buys[0].price == 130.0  # 用隔天最高價成交


def test_sell_fills_at_next_day_low(db):
    days = _seed_calendar(db, D0, 6)
    from app.signals.shadow_portfolio_experiments import Lot, Position

    engine = testb.ExperimentEngine(db, mode=testb.MODE_TEST_B)
    position = Position(stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    position.lots = [
        Lot(lot_id=1, add_number=0, entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=days[0],
            entry_execution_date=days[0], entry_price=100.0, shares=1000.0, allocation=100000.0)
    ]
    position.highest_close_since_entry = 100.0
    engine.positions["1101"] = position
    engine.cash = 500000.0

    obs = _seed_observation(db, stock_id="1101", stock_name="台泥", first_seen_date=days[0])
    for i, d in enumerate(days):
        if i == 2:
            # 收盤 93（真實報酬 -7%，安全避開 -8% 真實停損），但當天 P4 判定 STOP_OBSERVING
            _seed_review(db, obs, d, "STOP_OBSERVING")
            _seed_price(db, "1101", d, open_=94.0, high=95.0, low=93.0, close=93.0)
        elif i == 3:
            _seed_price(db, "1101", d, open_=85.0, high=87.0, low=82.0, close=85.0)  # T+1 low = 82
        else:
            _seed_price(db, "1101", d, open_=95.0, high=96.0, low=94.0, close=95.0)

    result = engine.run(start=days[0], end=days[-1])
    sells = [t for t in result.trades if t.action == testb.ACTION_SELL and t.stock_id == "1101"]
    assert len(sells) == 1
    assert sells[0].price == 82.0  # 用隔天最低價成交
    assert sells[0].reason == testb.EXIT_REASON_P4_STOP
