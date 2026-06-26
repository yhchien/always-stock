from app.signals import market_regime as mr


def _metrics(**overrides):
    """預設一組「多頭排列」指標，測試各自覆寫。"""
    base = {
        "close": 110.0,
        "ma10": 108.0,
        "ma20": 105.0,
        "ma60": 100.0,
        "ma20_slope_5d": 1.5,
        "return_5d_pct": 2.0,
        "return_10d_pct": 4.0,
        "intraday_range_5d_avg_pct": 1.2,
        "sample_size": 60,
    }
    base.update(overrides)
    return base


def test_classify_bull_trend():
    regime, _ = mr.classify_regime(_metrics())
    assert regime == mr.REGIME_BULL_TREND


def test_classify_risk_off_close_below_ma20_and_ma10_below_ma20():
    regime, _ = mr.classify_regime(
        _metrics(close=98.0, ma10=99.0, ma20=105.0, return_5d_pct=-1.0)
    )
    assert regime == mr.REGIME_RISK_OFF


def test_classify_risk_off_close_below_ma20_and_sharp_5d_drop():
    regime, _ = mr.classify_regime(
        _metrics(close=104.0, ma10=106.0, ma20=105.0, return_5d_pct=-4.0)
    )
    assert regime == mr.REGIME_RISK_OFF


def test_close_below_ma20_but_mild_drop_is_range_not_risk_off():
    # 收破 MA20 但短均仍在長均上、且近 5 日只小跌 → 不算退潮，視為震盪
    regime, _ = mr.classify_regime(
        _metrics(close=104.0, ma10=106.0, ma20=105.0, return_5d_pct=-1.0)
    )
    assert regime == mr.REGIME_VOLATILE_RANGE


def test_classify_volatile_range_when_ma20_flat():
    # 多頭排列但 MA20 沒上揚 → 不夠格 BULL，落入震盪
    regime, _ = mr.classify_regime(_metrics(ma20_slope_5d=-0.1))
    assert regime == mr.REGIME_VOLATILE_RANGE


def test_classify_volatile_range_when_10d_return_not_positive():
    regime, _ = mr.classify_regime(_metrics(return_10d_pct=-0.5))
    assert regime == mr.REGIME_VOLATILE_RANGE


def test_insufficient_data_defaults_to_volatile_range():
    regime, reason = mr.classify_regime(_metrics(sample_size=10, ma20=None))
    assert regime == mr.REGIME_VOLATILE_RANGE
    assert "不足" in reason


def test_compute_metrics_moving_averages_and_returns():
    closes = [float(x) for x in range(1, 31)]  # 1..30 升序
    m = mr.compute_regime_metrics(closes)
    assert m["sample_size"] == 30
    assert m["close"] == 30.0
    assert m["ma10"] == sum(range(21, 31)) / 10  # 25.5
    assert m["ma20"] == sum(range(11, 31)) / 20  # 20.5
    # 近 5 日報酬 = 30 / 25 - 1
    assert round(m["return_5d_pct"], 4) == round((30.0 / 25.0 - 1) * 100, 4)
    # MA20 上揚（升序資料）
    assert m["ma20_slope_5d"] > 0


def test_bull_metrics_but_high_intraday_range_is_volatile():
    # 指數多頭排列但近 5 日盤中振幅大 → 視為震盪（即使創高）
    regime, reason = mr.classify_regime(_metrics(intraday_range_5d_avg_pct=3.3))
    assert regime == mr.REGIME_VOLATILE_RANGE
    assert "震盪" in reason


def test_bull_metrics_but_reversal_day_is_volatile():
    # 創高急殺反轉日 → 震盪
    regime, _ = mr.classify_regime(_metrics(reversal_days_5d=1))
    assert regime == mr.REGIME_VOLATILE_RANGE


def test_bull_metrics_but_big_single_day_drop_is_volatile():
    regime, _ = mr.classify_regime(_metrics(max_down_1d_3d_pct=-3.5))
    assert regime == mr.REGIME_VOLATILE_RANGE


def test_bull_stays_bull_when_calm():
    regime, _ = mr.classify_regime(
        _metrics(intraday_range_5d_avg_pct=1.5, reversal_days_5d=0, max_down_1d_3d_pct=-0.5)
    )
    assert regime == mr.REGIME_BULL_TREND


def test_compute_metrics_detects_reversal_day_from_ohlc():
    # 5 天，最後一天創高急殺：high 遠高於 close 且收黑
    closes = [100.0, 101.0, 102.0, 103.0, 100.5]
    highs = [100.5, 101.5, 102.5, 103.5, 103.5]  # 末日 high 103.5，close 100.5 → -2.9% 距高
    lows = [99.5, 100.5, 101.5, 102.5, 100.0]
    m = mr.compute_regime_metrics(closes, highs=highs, lows=lows)
    assert m["reversal_days_5d"] >= 1
    assert m["intraday_range_5d_avg_pct"] is not None


def test_compute_metrics_handles_short_series():
    m = mr.compute_regime_metrics([10.0, 11.0, 12.0])
    assert m["sample_size"] == 3
    assert m["ma10"] is None
    assert m["ma20"] is None
    regime, _ = mr.classify_regime(m)
    assert regime == mr.REGIME_VOLATILE_RANGE
