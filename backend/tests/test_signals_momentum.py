"""魚尾 v2.1：momentum.py 測試（fishtail momentum upgrade spec §6.1B/C + §6.2 + §9.2）。

覆蓋：
  - compute_momentum_score 純函式（權重 / 缺值 / 風險扣分 / clamp）
  - select_momentum_candidates B / C 通道條件與上限
  - compute_market_momentum_frame 整合（in-memory SQLite：percentile / rank /
    distance / 法人買超佔成交比 / universe 排除金融）
  - build_signal_metrics JSON 可序列化
"""
import json
from datetime import date, timedelta

from app.models import DailyPrice, InstStockFlow, StockMaster
from app.signals import momentum


# ---------- compute_momentum_score ----------


def _full_strength_candidate(**overrides):
    base = {
        "rs_market_percentile_20d": 100.0,
        "return_percentile_60d": 100.0,
        "return_percentile_5d": 100.0,
        "rs_industry_percentile_20d": 100.0,
        "inst_buy_to_turnover_percentile_2d": 100.0,
        "consecutive_buy_days_3d": 3,
        "volume_ratio_percentile_5d_60d": 100.0,
    }
    base.update(overrides)
    return base


def test_momentum_score_full_strength_is_90():
    """滿分 = 30 + 25 + 20 + 15 = 90（基本面 10 分 v2.1 未接，恆 0）。"""
    out = momentum.compute_momentum_score(_full_strength_candidate())
    assert out["momentum_score"] == 90.0
    detail = out["momentum_score_detail"]
    assert detail["price"] == 30.0
    assert detail["relative_strength"] == 25.0
    assert detail["institution"] == 20.0
    assert detail["volume_quality"] == 15.0
    assert detail["fundamental"] is None
    assert detail["risk_penalty"] == 0.0


def test_momentum_score_all_missing_is_zero():
    out = momentum.compute_momentum_score({})
    assert out["momentum_score"] == 0.0


def test_momentum_score_missing_percentile_contributes_zero():
    """缺 rs_industry_percentile → RS 子分數只剩 market 那 60%。"""
    candidate = _full_strength_candidate(rs_industry_percentile_20d=None)
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score_detail"]["relative_strength"] == 15.0  # 25 × 0.6


def test_momentum_score_blowoff_penalty():
    candidate = _full_strength_candidate(
        volume_1d_to_60d_ratio=2.5,
        open_1d=100.0,
        close_1d=101.0,  # body = 1
        high_1d=108.0,   # upper shadow 7 > 2；101 < 108×0.97
    )
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score"] == 80.0  # 90 - 10
    assert "blowoff_upper_shadow" in out["momentum_score_detail"]["penalty_reasons"]


def test_momentum_score_rs_collapse_penalty():
    candidate = _full_strength_candidate(rs_rank_improvement_5d=-250)
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score"] == 80.0
    assert "rs_rank_collapse" in out["momentum_score_detail"]["penalty_reasons"]


def test_momentum_score_overheat_penalty():
    candidate = _full_strength_candidate(price_change_3d=13.0)
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score"] == 85.0
    assert "overheat_3d" in out["momentum_score_detail"]["penalty_reasons"]


def test_momentum_score_clamped_at_zero():
    """全缺資料 + 扣分 → 不會變負。"""
    candidate = {
        "volume_1d_to_60d_ratio": 3.0,
        "open_1d": 100.0,
        "close_1d": 100.5,
        "high_1d": 110.0,
        "rs_rank_improvement_5d": -300,
        "price_change_3d": 14.0,
    }
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score"] == 0.0


# ---------- select_momentum_candidates（B / C 通道） ----------


def _feats(**overrides):
    base = momentum.empty_momentum_features()
    base.update(overrides)
    return base


def test_channel_b_via_market_rs():
    frame = {"A": _feats(rs_market_percentile_20d=85.0)}
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == ["A"]


def test_channel_b_via_industry_rs():
    frame = {"A": _feats(rs_industry_percentile_20d=80.0)}
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == ["A"]


def test_channel_b_via_new_high_with_volume():
    frame = {"A": _feats(distance_to_20d_high=0.0, volume_1d_to_20d_avg=1.2)}
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == ["A"]


def test_channel_b_new_high_without_volume_not_selected():
    frame = {"A": _feats(distance_to_20d_high=0.0, volume_1d_to_20d_avg=1.1)}
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == []


def test_channel_b_via_return_60d_percentile_requires_positive_5d():
    yes = _feats(return_percentile_60d=90.0, return_5d=1.0)
    no = _feats(return_percentile_60d=90.0, return_5d=-1.0)
    out = momentum.select_momentum_candidates({"Y": yes, "N": no})
    assert out["price_momentum"] == ["Y"]


def test_channel_c_requires_both_conditions():
    both = _feats(rs_rank_improvement_5d=250, rs_market_percentile_20d=75.0)
    only_improvement = _feats(rs_rank_improvement_5d=250, rs_market_percentile_20d=60.0)
    only_rs = _feats(rs_rank_improvement_5d=100, rs_market_percentile_20d=75.0)
    out = momentum.select_momentum_candidates(
        {"B": both, "I": only_improvement, "R": only_rs}
    )
    assert out["acceleration"] == ["B"]


def test_channel_b_cap_keeps_strongest(monkeypatch):
    monkeypatch.setattr(momentum, "CHANNEL_B_LIMIT", 2)
    frame = {
        "A": _feats(rs_market_percentile_20d=99.0),
        "B": _feats(rs_market_percentile_20d=95.0),
        "C": _feats(rs_market_percentile_20d=90.0),
    }
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == ["A", "B"]


# ---------- compute_market_momentum_frame（整合） ----------


def _seed_market(db, n_days=30):
    """22 檔非金融 + 1 檔金融；日期連續 n_days 天；收盤依 slope 遞增。

    回傳 (dates, stock_ids)。S01 slope 最小（最弱）、S22 最大（最強）。
    """
    start = date(2026, 6, 1)
    dates = [start + timedelta(days=i) for i in range(n_days)]
    stock_ids = ["S%02d" % i for i in range(1, 23)]

    for idx, sid in enumerate(stock_ids, start=1):
        industry = "半導體" if idx <= 11 else "電子零組件"
        db.add(
            StockMaster(
                stock_id=sid, stock_name="股票" + sid, industry_name=industry, is_active=True
            )
        )
    db.add(
        StockMaster(stock_id="2881", stock_name="富邦金", industry_name="金融保險", is_active=True)
    )

    for d_idx, d in enumerate(dates):
        for s_idx, sid in enumerate(stock_ids, start=1):
            close = 100.0 + s_idx * 0.5 * d_idx  # slope 隨 s_idx 遞增
            db.add(
                DailyPrice(
                    trade_date=d,
                    stock_id=sid,
                    open_price=close,
                    high_price=close + 1,
                    low_price=close - 1,
                    close_price=close,
                    volume=1000.0,
                    turnover=1.0e8,
                )
            )
        # 金融股也有價格（但不該進 frame）
        db.add(
            DailyPrice(
                trade_date=d, stock_id="2881", close_price=100.0, volume=1000.0, turnover=1.0e8
            )
        )
        # 至少一筆 flow 建立交易日曆
        db.add(
            InstStockFlow(
                trade_date=d,
                stock_id="S01",
                inst_type="foreign",
                buy_shares=0,
                sell_shares=0,
                net_shares=0,
                buy_amount_est=0,
                sell_amount_est=0,
                net_amount_est=0,
            )
        )
    db.commit()
    return dates, stock_ids


def _masters_map(db):
    return {m.stock_id: m for m in db.query(StockMaster).all()}


def test_frame_percentiles_and_distance(db):
    dates, stock_ids = _seed_market(db)
    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))

    # 金融股不在 universe
    assert "2881" not in frame

    strongest = frame["S22"]
    weakest = frame["S01"]
    assert strongest["rs_market_percentile_20d"] == 100.0
    assert weakest["rs_market_percentile_20d"] == 0.0
    # 一路上漲 → 收盤即 20 日高點
    assert strongest["distance_to_20d_high"] == 0.0
    # 只有 30 天資料 → 60d 報酬缺值
    assert strongest["return_60d"] is None
    # 產業內 percentile：S22 是電子零組件最強
    assert strongest["rs_industry_percentile_20d"] == 100.0
    # slope 固定 → 20d 排名穩定 → improvement = 0
    assert strongest["rs_rank_improvement_5d"] == 0


def test_frame_rank_improvement_detects_acceleration(db):
    dates, stock_ids = _seed_market(db)
    # S05 最後 5 天急拉：把收盤改成大幅跳升
    for i, d in enumerate(dates[-5:]):
        row = (
            db.query(DailyPrice)
            .filter(DailyPrice.stock_id == "S05", DailyPrice.trade_date == d)
            .one()
        )
        row.close_price = float(row.close_price) + 200.0 * (i + 1)
    db.commit()

    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    assert frame["S05"]["rs_rank_improvement_5d"] > 0


def test_frame_institution_buy_to_turnover(db):
    dates, stock_ids = _seed_market(db)
    # 全部股票最後 2 日都給 flow（percentile 需要 >= 20 樣本）；S22 給最大買超
    # 用 trust 避開 _seed_market 已種的 (date, S01, foreign) unique key
    for d in dates[-2:]:
        for s_idx, sid in enumerate(stock_ids, start=1):
            db.add(
                InstStockFlow(
                    trade_date=d,
                    stock_id=sid,
                    inst_type="trust",
                    buy_shares=0,
                    sell_shares=0,
                    net_shares=0,
                    buy_amount_est=0,
                    sell_amount_est=0,
                    net_amount_est=1.0e6 * s_idx,
                )
            )
    db.commit()

    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    # 2 日買超 = 2 × 22e6；2 日成交金額 = 2 × 1e8
    expected_ratio = (2 * 22 * 1.0e6) / (2 * 1.0e8)
    assert abs(frame["S22"]["institution_buy_to_turnover_2d"] - expected_ratio) < 1e-9
    assert frame["S22"]["inst_buy_to_turnover_percentile_2d"] == 100.0


def test_frame_empty_when_no_trade_dates(db):
    db.add(StockMaster(stock_id="S01", stock_name="X", industry_name="半導體", is_active=True))
    db.commit()
    frame = momentum.compute_market_momentum_frame(db, date(2026, 6, 30), _masters_map(db))
    assert frame == {}


# ---------- build_signal_metrics ----------


def test_build_signal_metrics_is_json_serializable():
    candidate = {
        "return_5d": 3.2,
        "return_20d": 12.5,
        "return_60d": None,
        "rs_market_percentile_20d": 91.0,
        "rs_industry_percentile_20d": 88.0,
        "rs_rank_improvement_5d": 120,
        "institution_buy_to_turnover_2d": 0.11,
        "trend_efficiency_20d": 0.6,
        "distance_to_20d_high": -1.2,
        "distance_to_ma20": 4.0,
        "momentum_score": 76.5,
        "momentum_score_detail": {"price": 28.0},
    }
    regime_info = {"regime": "BULL_TREND", "reason": "多頭排列", "metrics": {"close": 47000.0}}
    out = momentum.build_signal_metrics(candidate, regime_info)
    encoded = json.dumps(out)  # 不可含 date 物件
    assert "BULL_TREND" in encoded
    assert out["momentum_score"] == 76.5
    assert out["distance_to_high_20d"] == -1.2
    assert out["breadth_score"] is None
