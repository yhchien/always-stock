"""M23 slice 5：filters.py hard / soft 規則測試（spec §9）。"""
from datetime import date

from app.signals.classification import (
    PRELIM_TYPE_FOLLOWER,
    PRELIM_TYPE_LAGGARD_CANDIDATE,
    PRELIM_TYPE_LEADER,
)
from app.signals.filters import (
    HINT_DISTRIBUTION,
    HINT_RANGE_BOUND,
    HINT_RETAIL_OVERHEATED,
    HINT_WEAKENING,
    apply_hard_exclusions,
    apply_regime_gate,
    apply_soft_filters,
    regime_watch_intensity,
)
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)


# ---------- helpers ----------


def _candidate(**overrides):
    """產生「全部過 hard exclusion」的乾淨候選 dict。"""
    base = {
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體業",
        "prelim_type": PRELIM_TYPE_LEADER,
        "price_change_3d": 5.0,
        "total_institution_flow_5d": 1.0e8,
        "avg_turnover_5d": 5.0e8,
        # soft filter 預設都不命中
        "total_institution_flow_3d": 5.0e7,
        "total_institution_flow_1d": 1.0e7,
        "margin_change_3d": 0.0,
        "volume_1d_to_60d_ratio": 1.0,
        "price_change_1d": 0.5,
        "high_1d": 100.0,
        "low_1d": 98.0,
        "open_1d": 99.0,
        "close_1d": 99.5,
        "volume_1d": 2_000_000,  # 2000 張，過所有級距死線
        "high_10d": 105.0,
        "low_10d": 95.0,
    }
    base.update(overrides)
    return base


# ---------- §9.1 Hard Exclusions ----------


def test_hard_exclusions_drops_etf():
    pool = [_candidate(stock_id="0050", name="元大台灣 50")]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_drops_financial():
    pool = [_candidate(stock_id="2880", name="華南金", industry="金融保險業")]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_drops_negative_5d_flow_for_non_laggard():
    pool = [_candidate(total_institution_flow_5d=-1.0e8)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_negative_5d_flow_for_laggard():
    pool = [
        _candidate(
            total_institution_flow_5d=-1.0e8,
            prelim_type=PRELIM_TYPE_LAGGARD_CANDIDATE,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1
    assert out[0]["prelim_type"] == PRELIM_TYPE_LAGGARD_CANDIDATE


def test_hard_exclusions_drops_overheated_3d_change():
    pool = [_candidate(price_change_3d=15.5)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_at_threshold_15_pct():
    """15.0% 不嚴格大於 → 應保留。"""
    pool = [_candidate(price_change_3d=15.0)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_drops_low_liquidity():
    pool = [_candidate(avg_turnover_5d=4.0e7)]  # < 5e7
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_when_avg_turnover_unknown():
    """avg_turnover_5d=None → 不剔除（資料缺）。"""
    pool = [_candidate(avg_turnover_5d=None)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_keeps_when_5d_flow_unknown():
    pool = [_candidate(total_institution_flow_5d=None)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_handles_empty_pool():
    assert apply_hard_exclusions(None, date(2026, 4, 25), []) == []


# ---------- 當日成交量死線（依股價級距） ----------


def test_volume_deadline_drops_low_price_thin_volume():
    """股價 < 1000 元、日量 1499 張（< 1500）→ 剔除。"""
    pool = [_candidate(close_1d=500.0, volume_1d=1_499_000)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_volume_deadline_keeps_low_price_above_threshold():
    """股價 < 1000 元、日量 1501 張（> 1500）→ 保留。"""
    pool = [_candidate(close_1d=500.0, volume_1d=1_501_000)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_volume_deadline_drops_exact_threshold_not_breakout():
    """剛好 1500 張不算「突破」→ 剔除。"""
    pool = [_candidate(close_1d=500.0, volume_1d=1_500_000)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_volume_deadline_mid_price_tier():
    """1000 <= 股價 < 5000 元 → 門檻 800 張。"""
    assert apply_hard_exclusions(
        None, date(2026, 4, 25), [_candidate(close_1d=2000.0, volume_1d=799_000)]
    ) == []
    assert len(
        apply_hard_exclusions(
            None, date(2026, 4, 25), [_candidate(close_1d=2000.0, volume_1d=801_000)]
        )
    ) == 1


def test_volume_deadline_price_1000_boundary_uses_mid_tier():
    """股價剛好 1000 元 → 落入中間級距（門檻 800 張，不是 1500）。"""
    pool = [_candidate(close_1d=1000.0, volume_1d=900_000)]  # 900 張 > 800
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_volume_deadline_high_price_tier():
    """股價 >= 5000 元 → 門檻 500 張。"""
    assert apply_hard_exclusions(
        None, date(2026, 4, 25), [_candidate(close_1d=6000.0, volume_1d=499_000)]
    ) == []
    assert len(
        apply_hard_exclusions(
            None, date(2026, 4, 25), [_candidate(close_1d=6000.0, volume_1d=501_000)]
        )
    ) == 1


def test_volume_deadline_price_5000_boundary_uses_high_tier():
    """股價剛好 5000 元 → 落入高價級距（門檻 500 張）。"""
    pool = [_candidate(close_1d=5000.0, volume_1d=600_000)]  # 600 張 > 500
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_volume_deadline_keeps_when_volume_unknown():
    """volume_1d=None → 不剔除（資料缺）。"""
    pool = [_candidate(close_1d=500.0, volume_1d=None)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_volume_deadline_keeps_when_price_unknown():
    """close_1d=None → 不剔除（資料缺）。"""
    pool = [_candidate(close_1d=None, volume_1d=100_000)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


# ---------- §9.2 Soft Filters ----------


def test_soft_filter_no_hints_for_clean_candidate():
    pool = [_candidate()]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert out[0]["soft_hints"] == []


def test_soft_filter_weakening_when_3d_positive_1d_large_sell():
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=-6.0e7,  # < -1e8 × 0.5
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_WEAKENING in out[0]["soft_hints"]


def test_soft_filter_weakening_not_triggered_when_1d_only_slightly_negative():
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=-3.0e7,  # > -5e7（不夠大）
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_WEAKENING not in out[0]["soft_hints"]


def test_soft_filter_retail_overheated_when_margin_up_inst_flat():
    pool = [
        _candidate(
            margin_change_3d=0.07,  # +7%
            total_institution_flow_3d=0.0,  # 法人未買
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_RETAIL_OVERHEATED in out[0]["soft_hints"]


def test_soft_filter_retail_overheated_not_triggered_when_inst_buying():
    pool = [
        _candidate(
            margin_change_3d=0.10,
            total_institution_flow_3d=1.0e8,  # 法人在買
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_RETAIL_OVERHEATED not in out[0]["soft_hints"]


def test_soft_filter_distribution_volume_no_rise():
    pool = [
        _candidate(
            volume_1d_to_60d_ratio=2.5,  # > 2
            price_change_1d=-1.0,        # 不漲
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_DISTRIBUTION in out[0]["soft_hints"]


def test_soft_filter_distribution_upper_shadow():
    """high=110, open=99, close=100 → upper_shadow=10, body=1, ratio=10>2;
    close/high = 100/110 = 0.909 < 0.97 → distribution。"""
    pool = [
        _candidate(
            high_1d=110.0,
            open_1d=99.0,
            close_1d=100.0,
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_DISTRIBUTION in out[0]["soft_hints"]


def test_soft_filter_distribution_not_triggered_when_close_near_high():
    """close=109/110=0.99 > 0.97 → 不算長上影。"""
    pool = [
        _candidate(
            high_1d=110.0,
            open_1d=99.0,
            close_1d=109.0,
            volume_1d_to_60d_ratio=1.0,
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_DISTRIBUTION not in out[0]["soft_hints"]


def test_soft_filter_range_bound_when_10d_volatility_below_5pct():
    pool = [_candidate(high_10d=100.0, low_10d=97.0)]  # (100-97)/97 ≈ 3.09%
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_RANGE_BOUND in out[0]["soft_hints"]


def test_soft_filter_range_bound_not_triggered_when_volatility_above_5pct():
    pool = [_candidate(high_10d=110.0, low_10d=95.0)]  # ≈15.8%
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    assert HINT_RANGE_BOUND not in out[0]["soft_hints"]


def test_soft_filter_can_emit_multiple_hints():
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=-6.0e7,    # weakening
            margin_change_3d=0.10,                # 但 inst 在買 → retail 不觸發
            volume_1d_to_60d_ratio=2.5,           # distribution (vol)
            price_change_1d=-0.5,
            high_10d=100.0,
            low_10d=97.0,                         # range_bound
        )
    ]
    out = apply_soft_filters(None, date(2026, 4, 25), pool)
    hints = set(out[0]["soft_hints"])
    assert HINT_WEAKENING in hints
    assert HINT_DISTRIBUTION in hints
    assert HINT_RANGE_BOUND in hints
    assert HINT_RETAIL_OVERHEATED not in hints


def test_soft_filter_does_not_mutate_input():
    cand = _candidate()
    apply_soft_filters(None, date(2026, 4, 25), [cand])
    assert "soft_hints" not in cand


def test_soft_filter_handles_empty_pool():
    assert apply_soft_filters(None, date(2026, 4, 25), []) == []


# ---------- 2026-05-26 新增：再偵測閘門 + 派發確認硬閘門 ----------


def test_hard_exclusions_drops_failed_follow_through():
    """tracking_status 顯示 3 日驗證失敗 → 不再進候選池。"""
    pool = [_candidate(failed_follow_through=True)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_when_failed_follow_through_false():
    pool = [_candidate(failed_follow_through=False)]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_drops_price_extended_with_inst_selling():
    """price_change_10d=+27% 且 flow_1d<0 → 派發前兆，剔除。"""
    pool = [
        _candidate(
            price_change_10d=27.0,
            total_institution_flow_1d=-1.0e7,
            price_change_3d=8.0,  # 不被 3d 過熱規則攔到
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_extended_when_inst_still_buying():
    """price_change_10d=+27% 但 flow_1d>0 → 主升段未確認反轉，保留。"""
    pool = [
        _candidate(
            price_change_10d=27.0,
            total_institution_flow_1d=1.0e7,
            price_change_3d=8.0,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_keeps_at_extended_boundary_25_pct():
    """price_change_10d=25.0 不嚴格 > 25 → 不剔除。"""
    pool = [
        _candidate(
            price_change_10d=25.0,
            total_institution_flow_1d=-1.0e7,
            price_change_3d=8.0,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_drops_inst_divergence_with_price_drop():
    """3d 累積買超 + 1d 反轉大賣 + 股價跌 1.6% → 主力出貨確認。"""
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=-3.0e7,
            price_change_1d=-1.6,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert out == []


def test_hard_exclusions_keeps_inst_divergence_when_price_drop_mild():
    """price_change_1d=-1.4% → 還沒到 -1.5% 確認門檻，保留。"""
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=-3.0e7,
            price_change_1d=-1.4,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


def test_hard_exclusions_keeps_inst_divergence_when_flow_1d_positive():
    """flow_1d 還是正的 → 沒有反轉訊號。"""
    pool = [
        _candidate(
            total_institution_flow_3d=1.0e8,
            total_institution_flow_1d=2.0e7,
            price_change_1d=-2.0,
        )
    ]
    out = apply_hard_exclusions(None, date(2026, 4, 25), pool)
    assert len(out) == 1


# ---------- M27 Market Regime Gate ----------


def _regime_candidate(**overrides):
    base = {
        "stock_id": "2330",
        "prelim_type": PRELIM_TYPE_LEADER,
        "hit_count": 3,
        "soft_hints": [],
        "total_institution_flow_5d": 1.0e8,
        "volume_1d_to_5d_ratio": 1.0,
        "price_change_1d": 1.0,
    }
    base.update(overrides)
    return base


def test_regime_gate_bull_keeps_all_and_stamps_conviction():
    pool = [
        _regime_candidate(stock_id="A", prelim_type=PRELIM_TYPE_LEADER, hit_count=3),
        _regime_candidate(stock_id="B", prelim_type=PRELIM_TYPE_FOLLOWER, hit_count=1),
        _regime_candidate(stock_id="C", soft_hints=[HINT_DISTRIBUTION]),
    ]
    out = apply_regime_gate(pool, REGIME_BULL_TREND)
    assert len(out) == 3  # 大多頭不額外剔除
    by = {c["stock_id"]: c for c in out}
    assert by["A"]["regime_conviction"] == "high"  # leader + hit>=2
    assert by["B"]["regime_conviction"] == "low"   # follower hit=1
    assert all(c["market_regime"] == REGIME_BULL_TREND for c in out)


def test_regime_gate_volatile_drops_single_hit_follower():
    pool = [_regime_candidate(prelim_type=PRELIM_TYPE_FOLLOWER, hit_count=1)]
    assert apply_regime_gate(pool, REGIME_VOLATILE_RANGE) == []


def test_regime_gate_volatile_drops_single_hit_laggard():
    pool = [_regime_candidate(prelim_type=PRELIM_TYPE_LAGGARD_CANDIDATE, hit_count=0)]
    assert apply_regime_gate(pool, REGIME_VOLATILE_RANGE) == []


def test_regime_gate_volatile_keeps_single_hit_leader_as_medium():
    # LEADER 即使單次命中，震盪盤仍給 medium（比單次命中的 follower 有理由），留校 watch
    pool = [_regime_candidate(prelim_type=PRELIM_TYPE_LEADER, hit_count=0)]
    out = apply_regime_gate(pool, REGIME_VOLATILE_RANGE)
    assert len(out) == 1
    assert out[0]["regime_conviction"] == "medium"


def test_regime_gate_volatile_keeps_low_when_tracked_and_holding():
    # 單次命中 follower（conviction=low）但已追蹤且表現中 → 留校例外
    pool = [
        _regime_candidate(
            prelim_type=PRELIM_TYPE_FOLLOWER,
            hit_count=1,
            is_tracked=True,
            max_positive_return_pct=4.0,
            max_negative_return_pct=-3.0,
        )
    ]
    out = apply_regime_gate(pool, REGIME_VOLATILE_RANGE)
    assert len(out) == 1
    assert out[0]["regime_conviction"] == "low"


def test_regime_gate_volatile_drops_low_when_tracked_but_underwater():
    pool = [
        _regime_candidate(
            prelim_type=PRELIM_TYPE_FOLLOWER,
            hit_count=1,
            is_tracked=True,
            max_positive_return_pct=1.0,   # < 3
            max_negative_return_pct=-8.0,  # < -6
        )
    ]
    assert apply_regime_gate(pool, REGIME_VOLATILE_RANGE) == []


def test_regime_gate_volatile_drops_distribution():
    pool = [_regime_candidate(soft_hints=[HINT_DISTRIBUTION])]
    assert apply_regime_gate(pool, REGIME_VOLATILE_RANGE) == []


def test_regime_gate_volatile_drops_spike_breakout():
    pool = [_regime_candidate(volume_1d_to_5d_ratio=3.0, price_change_1d=8.0)]
    assert apply_regime_gate(pool, REGIME_VOLATILE_RANGE) == []


def test_regime_gate_volatile_keeps_repeat_hit_leader_as_high():
    pool = [_regime_candidate(prelim_type=PRELIM_TYPE_LEADER, hit_count=3)]
    out = apply_regime_gate(pool, REGIME_VOLATILE_RANGE)
    assert len(out) == 1
    assert out[0]["regime_conviction"] == "high"


def test_regime_gate_risk_off_keeps_only_strong_leader():
    pool = [
        _regime_candidate(stock_id="strong", prelim_type=PRELIM_TYPE_LEADER, hit_count=3,
                          total_institution_flow_5d=1.0e8),
        _regime_candidate(stock_id="weakhit", prelim_type=PRELIM_TYPE_LEADER, hit_count=1),
        _regime_candidate(stock_id="follower", prelim_type=PRELIM_TYPE_FOLLOWER, hit_count=5),
        _regime_candidate(stock_id="negflow", prelim_type=PRELIM_TYPE_LEADER, hit_count=3,
                          total_institution_flow_5d=-1.0),
    ]
    out = apply_regime_gate(pool, REGIME_RISK_OFF)
    assert [c["stock_id"] for c in out] == ["strong"]
    assert out[0]["regime_conviction"] == "high"  # 退潮盤存活者是最強的一批


def test_regime_gate_does_not_mutate_input():
    c = _regime_candidate()
    apply_regime_gate([c], REGIME_VOLATILE_RANGE)
    assert "regime_conviction" not in c
    assert "market_regime" not in c


def test_regime_watch_intensity_mapping():
    assert regime_watch_intensity(REGIME_BULL_TREND, "high") == "aggressive"
    assert regime_watch_intensity(REGIME_BULL_TREND, "medium") == "normal"
    assert regime_watch_intensity(REGIME_BULL_TREND, "low") == "cautious"
    assert regime_watch_intensity(REGIME_VOLATILE_RANGE, "high") == "normal"
    assert regime_watch_intensity(REGIME_VOLATILE_RANGE, "medium") == "cautious"
    assert regime_watch_intensity(REGIME_RISK_OFF, "high") == "cautious"
    assert regime_watch_intensity(REGIME_RISK_OFF, None) == "cautious"
