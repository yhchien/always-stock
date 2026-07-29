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

import pytest

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


def test_momentum_score_common_stock_missing_fundamental_stays_90():
    """一般股 MISSING 不可冒充 N/A，因此缺基本面時維持 0 分且不重配權重。"""
    out = momentum.compute_momentum_score(_full_strength_candidate())
    assert out["momentum_score"] == 90.0
    assert out["momentum_score_version"] == momentum.MOMENTUM_SCORE_VERSION
    assert out["fundamental_applicability"] == "MISSING"
    assert out["missing_score_weight"] == 10.0
    assert out["not_applicable_score_weight"] == 0.0
    assert out["score_confidence"] == "MEDIUM"
    detail = out["momentum_score_detail"]
    assert detail["price"] == 30.0
    assert detail["relative_strength"] == 25.0
    assert detail["institution"] == 20.0
    assert detail["volume_quality"] == 15.0
    assert detail["fundamental"] is None
    assert detail["risk_penalty"] == 0.0


def test_momentum_score_legacy_replay_honors_available_weight_env(monkeypatch):
    """舊 env 只控制 explicit legacy replay，不污染 production applicability mode。"""
    monkeypatch.setattr(momentum, "_AVAILABLE_WEIGHT_NORMALIZATION_ENABLED", True)
    out = momentum.compute_momentum_score(
        _full_strength_candidate(),
        score_mode="legacy",
    )
    assert out["momentum_score"] == 100.0
    assert out["momentum_score_version"] == momentum.LEGACY_AVAILABLE_WEIGHT_SCORE_VERSION
    assert out["feature_coverage"] == 0.9
    assert out["score_confidence"] == "MEDIUM"


def test_momentum_score_etf_fundamental_not_applicable_normalizes_to_100():
    out = momentum.compute_momentum_score(
        _full_strength_candidate(asset_type="ETF"),
    )
    assert out["momentum_score"] == 100.0
    assert out["fundamental_applicability"] == "NOT_APPLICABLE"
    assert out["applicable_score_weight"] == 90.0
    assert out["missing_score_weight"] == 0.0
    assert out["not_applicable_score_weight"] == 10.0
    assert out["score_before_penalty"] == 100.0
    assert out["feature_coverage"] == 1.0
    assert out["score_confidence"] == "HIGH"


def test_financial_fundamental_available_and_missing_are_not_na():
    available = momentum.compute_momentum_score(
        _full_strength_candidate(
            asset_type="FINANCIAL",
            revenue_yoy=12.0,
            revenue_yoy_percentile=80.0,
        )
    )
    missing = momentum.compute_momentum_score(
        _full_strength_candidate(asset_type="FINANCIAL"),
    )
    assert available["fundamental_applicability"] == "AVAILABLE"
    assert available["momentum_score_detail"]["fundamental"] == 4.8
    assert missing["fundamental_applicability"] == "MISSING"
    assert missing["momentum_score"] == 90.0


@pytest.mark.parametrize("asset_type", ["COMMON_STOCK", "FINANCIAL", "ETF"])
def test_same_complete_evidence_has_same_score_across_asset_types(asset_type):
    out = momentum.compute_momentum_score(
        _full_strength_candidate(
            asset_type=asset_type,
            revenue_yoy=20.0,
            revenue_yoy_percentile=100.0,
            revenue_yoy_acceleration=5.0,
            revenue_mom=3.0,
        )
    )
    assert out["fundamental_applicability"] == (
        "NOT_APPLICABLE" if asset_type == "ETF" else "AVAILABLE"
    )
    assert out["momentum_score"] == 100.0


def test_etf_penalty_is_applied_after_not_applicable_normalization():
    out = momentum.compute_momentum_score(
        _full_strength_candidate(
            asset_type="ETF",
            volume_1d_to_60d_ratio=2.5,
            open_1d=100.0,
            close_1d=101.0,
            high_1d=108.0,
        )
    )
    assert out["score_before_penalty"] == 100.0
    assert out["risk_penalty_total"] == 10.0
    assert out["momentum_score"] == 90.0


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
    assert out["momentum_score"] == 80.0  # 90 - 10（v1 預設行為）
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

    # 2026-07-22（LLM v6 contract 對齊）：金融股不再被排除在 universe 外，
    # 只有人工黑名單才排除；資產類型不該影響是否算得到 percentile。
    assert "2881" in frame

    strongest = frame["S22"]
    weakest = frame["S01"]
    assert strongest["rs_market_percentile_20d"] == 100.0
    # 2881 加入 universe 後分母多 1 檔，S01 不再是 percentile 0（rank 1 of N+1）；
    # 這是 universe 擴大的預期副作用，不是 percentile 公式改變。
    assert weakest["rs_market_percentile_20d"] == pytest.approx(4.545454545454546)
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
        "momentum_score_detail": {
            "price": 28.0,
            "fundamental_applicability": "NOT_APPLICABLE",
        },
        "momentum_score_version": momentum.MOMENTUM_SCORE_VERSION,
        "applicable_score_weight": 90.0,
        "missing_score_weight": 0.0,
        "not_applicable_score_weight": 10.0,
        "score_before_penalty": 86.5,
        "risk_penalty_total": 10.0,
        "fundamental_applicability": "NOT_APPLICABLE",
    }
    regime_info = {"regime": "BULL_TREND", "reason": "多頭排列", "metrics": {"close": 47000.0}}
    out = momentum.build_signal_metrics(candidate, regime_info)
    encoded = json.dumps(out)  # 不可含 date 物件
    assert "BULL_TREND" in encoded
    assert out["momentum_score"] == 76.5
    assert out["distance_to_high_20d"] == -1.2
    assert out["breadth_score"] is None
    assert out["momentum_score_version"] == momentum.MOMENTUM_SCORE_VERSION
    assert out["fundamental_applicability"] == "NOT_APPLICABLE"
    assert out["not_applicable_score_weight"] == 10.0
    assert out["risk_penalty_total"] == 10.0


# ---------- 基本面動能（spec §6.1 D + available_date gate） ----------


def test_revenue_available_date_is_10th_of_next_month():
    assert momentum.revenue_available_date(date(2026, 6, 30)) == date(2026, 7, 10)
    assert momentum.revenue_available_date(date(2026, 2, 28)) == date(2026, 3, 10)
    assert momentum.revenue_available_date(date(2025, 12, 31)) == date(2026, 1, 10)


def test_channel_d_yoy_acceleration():
    yes = _feats(revenue_yoy=20.0, revenue_yoy_accel_2m=True)
    no_accel = _feats(revenue_yoy=20.0, revenue_yoy_accel_2m=False)
    low_yoy = _feats(revenue_yoy=10.0, revenue_yoy_accel_2m=True)
    out = momentum.select_momentum_candidates({"Y": yes, "A": no_accel, "L": low_yoy})
    assert out["fundamental"] == ["Y"]


def test_channel_d_yoy_turned_positive():
    frame = {"A": _feats(revenue_yoy_turned_positive=True)}
    assert momentum.select_momentum_candidates(frame)["fundamental"] == ["A"]


def test_channel_d_industry_percentile():
    frame = {"A": _feats(revenue_yoy_industry_percentile=85.0)}
    assert momentum.select_momentum_candidates(frame)["fundamental"] == ["A"]


@pytest.mark.parametrize("asset_type", ["COMMON_STOCK", "FINANCIAL", "ETF"])
def test_channels_b_and_c_are_asset_type_invariant(asset_type):
    frame = {
        "A": _feats(
            asset_type=asset_type,
            rs_market_percentile_20d=90.0,
            rs_rank_improvement_5d=250,
        )
    }
    out = momentum.select_momentum_candidates(frame)
    assert out["price_momentum"] == ["A"]
    assert out["acceleration"] == ["A"]


def test_channel_d_requires_actual_fundamental_evidence():
    frame = {
        "ETF": _feats(),
        "FIN": _feats(revenue_yoy=20.0, revenue_yoy_accel_2m=True),
    }
    assert momentum.select_momentum_candidates(frame)["fundamental"] == ["FIN"]


def test_momentum_score_fundamental_full_strength_adds_10():
    """基本面滿分：yoy percentile 100（6 分）+ 加速（2 分）+ mom 正（2 分）→ 總分 100。"""
    candidate = _full_strength_candidate(
        revenue_yoy_percentile=100.0,
        revenue_yoy_acceleration=5.0,
        revenue_mom=3.0,
    )
    out = momentum.compute_momentum_score(candidate)
    assert out["momentum_score"] == 100.0
    assert out["momentum_score_detail"]["fundamental"] == 10.0


def test_momentum_score_fundamental_missing_stays_none():
    out = momentum.compute_momentum_score(_full_strength_candidate())
    assert out["momentum_score_detail"]["fundamental"] is None


def test_frame_fundamental_features_respect_available_date(db):
    """target_date=7/9（6 月營收可用日 7/10 之前）→ 只能看到 5 月營收；7/10 起看得到 6 月。"""
    from app.models import MonthlyRevenue

    dates, stock_ids = _seed_market(db)
    # 種 3 個月營收給 S22：4 月 yoy=10、5 月 yoy=20、6 月 yoy=30（連兩月加速）
    for month_end, yoy in [
        (date(2026, 4, 30), 10.0),
        (date(2026, 5, 31), 20.0),
        (date(2026, 6, 30), 30.0),
    ]:
        db.add(
            MonthlyRevenue(
                revenue_month=month_end, stock_id="S22", revenue=1000.0,
                yoy_pct=yoy, mom_pct=1.0,
            )
        )
    db.commit()

    masters = _masters_map(db)
    # 6/30 收盤當天：6 月營收尚未公告（可用日 7/10）→ 最新可用 = 5 月
    frame = momentum.compute_market_momentum_frame(db, dates[-1], masters)  # dates[-1] = 6/30
    assert frame["S22"]["revenue_month_used"] == "2026-05-31"
    assert frame["S22"]["revenue_yoy"] == 20.0
    assert frame["S22"]["revenue_yoy_acceleration"] == 10.0  # 20 - 10
    assert frame["S22"]["revenue_yoy_accel_2m"] is False  # 只有 2 個可用月

    # 沒營收資料的股票維持 None
    assert frame["S01"]["revenue_yoy"] is None


# ---------- 市值 / institution_buy_to_market_cap（2026-07-15 第二輪） ----------


def test_frame_market_cap_from_shares_outstanding(db):
    from app.models import StockSharesOutstanding

    dates, stock_ids = _seed_market(db)
    # S22 最新收盤 = 100 + 22*0.5*29 = 419.0；發行股數 1e9
    db.add(
        StockSharesOutstanding(
            trade_date=dates[-1], stock_id="S22", shares_issued=1_000_000_000,
            foreign_shares_ratio=50.0,
        )
    )
    # S22 給 2 日法人 flow（用 trust 避開 S01 foreign 的 unique key）
    for d in dates[-2:]:
        db.add(
            InstStockFlow(
                trade_date=d, stock_id="S22", inst_type="trust",
                buy_shares=0, sell_shares=0, net_shares=0,
                buy_amount_est=0, sell_amount_est=0, net_amount_est=2.0e8,
            )
        )
    db.commit()

    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    feats = frame["S22"]
    assert feats["shares_issued"] == 1_000_000_000
    expected_cap = 1_000_000_000 * 419.0
    assert abs(feats["market_cap"] - expected_cap) < 1e-3
    # 2 日買超 4e8 / 市值
    assert abs(feats["institution_buy_to_market_cap_2d"] - (4.0e8 / expected_cap)) < 1e-12
    # 沒 shares 資料的股票維持 None
    assert frame["S01"]["market_cap"] is None


def test_frame_market_cap_uses_latest_snapshot_within_lookback(db):
    from datetime import timedelta as td
    from app.models import StockSharesOutstanding

    dates, stock_ids = _seed_market(db)
    # 只有 3 天前的快照（模擬停牌 / ETL 缺日）→ 應往回找到它
    db.add(
        StockSharesOutstanding(
            trade_date=dates[-1] - td(days=3), stock_id="S22", shares_issued=500_000_000,
        )
    )
    db.commit()
    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    assert frame["S22"]["shares_issued"] == 500_000_000


# ---------- v2.2 × v5：ATR / up-down volume / grade / phase / momentum_signals ----------


def test_momentum_grade_bands():
    assert momentum.momentum_grade(80.0) == "A"
    assert momentum.momentum_grade(75.0) == "A"
    assert momentum.momentum_grade(74.9) == "B"
    assert momentum.momentum_grade(60.0) == "B"
    assert momentum.momentum_grade(59.9) == "C"
    assert momentum.momentum_grade(45.0) == "C"
    assert momentum.momentum_grade(44.9) == "D"
    assert momentum.momentum_grade(None) is None


def test_momentum_phase_weakening_on_rank_collapse():
    c = {"rs_market_percentile_20d": 80.0, "rs_rank_improvement_5d": -150}
    assert momentum.classify_momentum_phase(c) == "weakening"


def test_momentum_phase_weakening_on_decline_with_negative_return():
    c = {"rs_market_percentile_20d": 80.0, "rs_rank_improvement_5d": -10, "return_5d": -2.0}
    assert momentum.classify_momentum_phase(c) == "weakening"


def test_momentum_phase_extended_on_hot_5d():
    c = {"rs_market_percentile_20d": 90.0, "rs_rank_improvement_5d": 50, "return_5d": 13.0}
    assert momentum.classify_momentum_phase(c) == "extended"


def test_momentum_phase_accelerating():
    c = {"rs_market_percentile_20d": 65.0, "rs_rank_improvement_5d": 150, "return_5d": 5.0}
    assert momentum.classify_momentum_phase(c) == "accelerating"


def test_momentum_phase_trending():
    c = {"rs_market_percentile_20d": 85.0, "rs_rank_improvement_5d": 10, "return_5d": 2.0}
    assert momentum.classify_momentum_phase(c) == "trending"


def test_momentum_phase_emerging():
    c = {"rs_market_percentile_20d": 55.0, "rs_rank_improvement_5d": 30, "return_5d": 1.0}
    assert momentum.classify_momentum_phase(c) == "emerging"


def test_momentum_phase_none_when_rs_missing():
    assert momentum.classify_momentum_phase({"rs_market_percentile_20d": None}) is None


def test_build_momentum_signals_uses_v5_naming():
    candidate = {
        "return_20d": 12.0,
        "rs_market_percentile_20d": 88.0,
        "relative_strength_market_20d": 5.5,
        "relative_strength_industry_20d": 2.2,
        "rs_rank_improvement_5d": 120,
        "distance_to_20d_high": -1.5,
        "distance_to_60d_high": -4.0,
        "distance_to_ma20": 3.0,
        "volume_5d_to_60d_ratio": 1.4,
        "atr_pct_14d": 2.8,
        "up_down_volume_ratio_20d": 1.6,
        "momentum_score": 76.0,
        "return_5d": 3.0,
    }
    ms = momentum.build_momentum_signals(candidate)
    # v5 命名對齊
    assert ms["rs_rank_change_5d"] == 120
    assert ms["distance_to_high_20d_pct"] == -1.5
    assert ms["distance_to_high_60d_pct"] == -4.0
    assert ms["distance_to_ma20_pct"] == 3.0
    assert ms["rs_market_20d"] == 5.5
    assert ms["rs_industry_20d"] == 2.2
    assert ms["volume_ratio_5d_60d"] == 1.4
    assert ms["return_percentile_20d"] == 88.0  # = rs_market_percentile_20d（等價）
    assert ms["momentum_grade"] == "A"
    assert ms["momentum_phase"] == "accelerating"
    assert ms["atr_pct_14d"] == 2.8
    assert ms["up_down_volume_ratio_20d"] == 1.6


def test_frame_computes_atr_and_up_down_volume(db):
    dates, stock_ids = _seed_market(db)
    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    feats = frame["S22"]
    # _seed_market：high = close+1、low = close-1、日漲 = 11 → TR = max(2, |close+1-prev|)
    # S22 slope = 11/日 → TR = 12（high - prev_close = close+1-(close-11) = 12）
    # close 最後一日 = 100 + 22*0.5*29 = 419 → ATR% = 12/419*100
    assert feats["atr_pct_14d"] is not None
    assert abs(feats["atr_pct_14d"] - (12.0 / 419.0 * 100.0)) < 1e-6
    # 一路上漲 → 沒有下跌日 → up/down ratio 分母 0 → None
    assert feats["up_down_volume_ratio_20d"] is None


def test_frame_up_down_volume_ratio_with_mixed_days(db):
    dates, stock_ids = _seed_market(db)
    # 把 S05 改成漲跌交錯：偶數日 100、奇數日 90（量固定 1000）
    rows = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == "S05")
        .order_by(DailyPrice.trade_date)
        .all()
    )
    for i, row in enumerate(rows):
        row.close_price = 100.0 if i % 2 == 0 else 90.0
    db.commit()

    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    ratio = frame["S05"]["up_down_volume_ratio_20d"]
    # 近 20 個轉換裡漲跌各半、量相同 → ratio = 1.0
    assert ratio is not None
    assert abs(ratio - 1.0) < 1e-9


def test_pool_candidates_carry_momentum_signals_and_grade(db):
    """整合：候選池每檔都帶 v5 momentum_signals nested dict + flat grade/phase。"""
    from app.signals.candidate_pool import build_candidate_pool, compute_rankings, ingest_data
    from app.models import IndustryDailyFlow

    dates, stock_ids = _seed_market(db)
    # 半導體產業 flow 讓 rankings 成立
    for d in dates:
        db.add(
            IndustryDailyFlow(
                trade_date=d, industry_name="半導體",
                total_buy_amount=5.0e8, total_sell_amount=0, total_net_amount=5.0e8,
                foreign_net_amount=5.0e8, trust_net_amount=0, dealer_net_amount=0,
            )
        )
    db.commit()

    target = dates[-1]
    ingestion = ingest_data(db, target)
    rankings = compute_rankings(db, target, ingestion)
    pool = build_candidate_pool(db, target, ingestion, rankings)

    assert pool
    for c in pool:
        ms = c.get("momentum_signals")
        assert isinstance(ms, dict)
        assert "momentum_grade" in ms and "momentum_phase" in ms
        assert c.get("momentum_grade") == ms["momentum_grade"]


# ---------- NaN 防禦（2026-07-16 prod 事故 regression） ----------


def test_frame_fundamental_sanitizes_nan_yoy(db):
    """DB monthly_revenue 殘留 NaN（歷史 ETL 對新上市股回算的副作用）→
    frame 必須清成 None，否則 signal_metrics JSON 寫 Postgres 會炸。"""
    from app.models import MonthlyRevenue

    dates, stock_ids = _seed_market(db)
    db.add(
        MonthlyRevenue(
            revenue_month=date(2026, 5, 31), stock_id="S22", revenue=1000.0,
            yoy_pct=float("nan"), mom_pct=float("nan"),
        )
    )
    db.commit()

    frame = momentum.compute_market_momentum_frame(db, dates[-1], _masters_map(db))
    assert frame["S22"]["revenue_yoy"] is None
    assert frame["S22"]["revenue_mom"] is None


def test_build_signal_metrics_scrubs_nan():
    candidate = {
        "return_20d": float("nan"),
        "momentum_score": 70.0,
        "momentum_score_detail": {"price": float("inf")},
        "revenue_yoy": float("nan"),
    }
    out = momentum.build_signal_metrics(candidate)
    assert out["return_20d"] is None
    assert out["revenue_yoy"] is None
    assert out["momentum_score_detail"]["price"] is None
    assert out["momentum_score"] == 70.0
    json.dumps(out, allow_nan=False)  # 不可再含 NaN token
