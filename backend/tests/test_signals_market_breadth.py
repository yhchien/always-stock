"""魚尾 v2.2：market_breadth.py 測試（spec §7.1 / §7.2）。"""
from app.signals import market_breadth
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)


class _Master:
    def __init__(self, industry):
        self.industry_name = industry


def _feats(**overrides):
    base = {
        "_above_ma20": True,
        "_above_ma60": True,
        "_ret_1d": 1.0,
        "_new_high_20d": False,
        "_new_low_20d": False,
        "return_5d": 2.0,
        "industry_return_20d": 5.0,
    }
    base.update(overrides)
    return base


def _frame(n_strong, n_weak):
    frame = {}
    for i in range(n_strong):
        frame["S%03d" % i] = _feats(_new_high_20d=True)
    for i in range(n_weak):
        frame["W%03d" % i] = _feats(
            _above_ma20=False,
            _above_ma60=False,
            _ret_1d=-1.0,
            _new_low_20d=True,
            return_5d=-3.0,
            industry_return_20d=-2.0,
        )
    return frame


def _masters(frame):
    """強勢股（S*）歸半導體、弱勢股（W*）歸航運，避免同產業互相覆蓋 industry_return。"""
    return {
        sid: _Master("半導體" if sid.startswith("S") else "航運")
        for sid in frame
    }


def test_breadth_empty_frame_returns_none_metrics():
    out = market_breadth.compute_breadth_from_frame({})
    assert out["breadth_score"] is None
    assert out["sample_size"] == 0


def test_breadth_small_sample_guard():
    """樣本 < 100 → breadth 不可信，全 None。"""
    out = market_breadth.compute_breadth_from_frame(_frame(50, 30))
    assert out["breadth_score"] is None
    assert out["sample_size"] == 80


def test_breadth_strong_market_scores_high():
    frame = _frame(180, 20)
    masters = _masters(frame)
    out = market_breadth.compute_breadth_from_frame(frame, masters)
    assert out["pct_above_ma20"] == 90.0
    assert out["breadth_score"] is not None
    assert out["breadth_score"] >= 70.0
    assert out["advance_decline_ratio"] == 9.0  # 180 / 20


def test_breadth_weak_market_scores_low():
    frame = _frame(30, 170)
    masters = _masters(frame)
    out = market_breadth.compute_breadth_from_frame(frame, masters)
    assert out["breadth_score"] is not None
    assert out["breadth_score"] <= 40.0
    assert out["new_low_20d_count"] == 170


def test_breadth_median_return_5d():
    frame = _frame(100, 100)
    out = market_breadth.compute_breadth_from_frame(frame)
    # 100 檔 +2.0、100 檔 -3.0 → 中位數 = (2 + -3) / 2 = -0.5
    assert out["median_stock_return_5d"] == -0.5


# ---------- resolve_regime_detail ----------


def test_resolve_regime_detail_broad_bull():
    assert (
        market_breadth.resolve_regime_detail(REGIME_BULL_TREND, 65.0)
        == market_breadth.REGIME_DETAIL_BROAD_BULL
    )


def test_resolve_regime_detail_narrow_bull():
    assert (
        market_breadth.resolve_regime_detail(REGIME_BULL_TREND, 49.9)
        == market_breadth.REGIME_DETAIL_NARROW_BULL
    )


def test_resolve_regime_detail_bull_with_missing_breadth_stays_broad():
    """breadth 缺值（樣本不足）→ 保守不加嚴 = BROAD_BULL。"""
    assert (
        market_breadth.resolve_regime_detail(REGIME_BULL_TREND, None)
        == market_breadth.REGIME_DETAIL_BROAD_BULL
    )


def test_resolve_regime_detail_passthrough_non_bull():
    assert market_breadth.resolve_regime_detail(REGIME_VOLATILE_RANGE, 20.0) == REGIME_VOLATILE_RANGE
    assert market_breadth.resolve_regime_detail(REGIME_RISK_OFF, 80.0) == REGIME_RISK_OFF
