"""
Unit tests for app.analysis.chip_signals.compute_chip_signals.

Covers: 連續買超天數、volume_trend 4 分類、price_trend、is_accumulation、chip_strength、資料不足、no-hindsight。
"""
from datetime import date, timedelta
from typing import List, Optional

from app.analysis.chip_signals import compute_chip_signals
from app.models import DailyPrice, InstStockFlow

STOCK_ID = "2330"
INDUSTRY = "半導體業"
END_DATE = date(2026, 4, 22)


def _seed_prices(db, closes: List[float], volumes: Optional[List[float]] = None, end_date: date = END_DATE) -> None:
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    assert len(closes) == len(volumes)
    for i, (c, v) in enumerate(zip(closes, volumes)):
        d = end_date - timedelta(days=len(closes) - 1 - i)
        db.add(DailyPrice(trade_date=d, stock_id=STOCK_ID, close_price=c, volume=v))
    db.commit()


def _seed_flows(db, inst_type: str, nets: List[float], end_date: date = END_DATE) -> None:
    """`nets[-1]` 是最新日（buy_date）；nets 由舊到新排列。"""
    for i, net in enumerate(nets):
        d = end_date - timedelta(days=len(nets) - 1 - i)
        db.add(InstStockFlow(
            trade_date=d,
            stock_id=STOCK_ID,
            inst_type=inst_type,
            buy_shares=max(net, 0),
            sell_shares=abs(min(net, 0)),
            net_shares=net,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# 連續買超天數
# ---------------------------------------------------------------------------


def test_foreign_buy_days_counts_back_from_buy_date(db):
    _seed_prices(db, [100.0] * 10)
    # 最新 3 天正、往前有 1 天負
    _seed_flows(db, "foreign", [-100, 50, 80, 120])
    _seed_flows(db, "trust", [0, 0, 0, 0])
    _seed_flows(db, "dealer", [0, 0, 0, 0])

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["foreign_buy_days"] == 3


def test_buy_days_stop_at_zero_net(db):
    _seed_prices(db, [100.0] * 10)
    # 最新日淨買為 0 → 不算買
    _seed_flows(db, "foreign", [100, 100, 0])
    _seed_flows(db, "trust", [0, 0, 0])
    _seed_flows(db, "dealer", [0, 0, 0])

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["foreign_buy_days"] == 0


def test_buy_days_returns_zero_when_no_rows(db):
    _seed_prices(db, [100.0] * 10)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["foreign_buy_days"] == 0
    assert result["investment_trust_buy_days"] == 0
    assert result["dealer_buy_days"] == 0


def test_each_inst_type_counted_independently(db):
    _seed_prices(db, [100.0] * 10)
    _seed_flows(db, "foreign", [100, 200])   # 2 天
    _seed_flows(db, "trust", [50, -10])      # 最新日負 → 0 天
    _seed_flows(db, "dealer", [10, 20])      # 2 天

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["foreign_buy_days"] == 2
    assert result["investment_trust_buy_days"] == 0
    assert result["dealer_buy_days"] == 2


# ---------------------------------------------------------------------------
# volume_trend
# ---------------------------------------------------------------------------


def test_volume_trend_increasing(db):
    # 前 5 日量 100，近 3 日量 130（+30% > 10% 門檻，且未超過 spike 50%）
    volumes = [100.0] * 5 + [130.0] * 3
    _seed_prices(db, [100.0] * 8, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["volume_trend"] == "increasing"


def test_volume_trend_spike(db):
    # 前 5 日量 100，最新一日爆量到 300
    volumes = [100.0] * 5 + [110.0, 115.0, 300.0]
    _seed_prices(db, [100.0] * 8, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["volume_trend"] == "spike"


def test_volume_trend_flat(db):
    # 前 5 日量 100，近 3 日量 102（幾乎持平）
    volumes = [100.0] * 5 + [102.0, 103.0, 101.0]
    _seed_prices(db, [100.0] * 8, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["volume_trend"] == "flat"


def test_volume_trend_declining(db):
    # 前 5 日量 200，近 3 日量 150
    volumes = [200.0] * 5 + [150.0] * 3
    _seed_prices(db, [100.0] * 8, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["volume_trend"] == "declining"


def test_volume_trend_null_when_history_too_short(db):
    _seed_prices(db, [100.0] * 5, volumes=[100.0] * 5)  # 需要 8 天

    result, notes = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["volume_trend"] is None
    assert any("volume_trend is null" in n for n in notes)


# ---------------------------------------------------------------------------
# price_trend
# ---------------------------------------------------------------------------


def test_price_trend_uptrend(db):
    closes = [100.0 + i * 2 for i in range(8)]  # 100..114
    _seed_prices(db, closes, volumes=[100.0] * 8)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["price_trend"] == "uptrend"


def test_price_trend_downtrend(db):
    closes = [150.0 - i * 2 for i in range(8)]
    _seed_prices(db, closes, volumes=[100.0] * 8)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["price_trend"] == "downtrend"


def test_price_trend_sideways(db):
    closes = [100.0, 100.3, 99.8, 100.1, 100.2, 99.9, 100.0, 100.1]
    _seed_prices(db, closes, volumes=[100.0] * 8)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["price_trend"] == "sideways"


# ---------------------------------------------------------------------------
# is_accumulation + chip_strength
# ---------------------------------------------------------------------------


def test_is_accumulation_true_when_all_conditions_met(db):
    # uptrend（漸進）+ volume increasing + 每日漲幅都 <= 2%
    closes = [100.0 * (1.01 ** i) for i in range(8)]  # 每天 +1%
    volumes = [100.0] * 5 + [120.0] * 3
    _seed_prices(db, closes, volumes=volumes)
    _seed_flows(db, "foreign", [100, 200])
    _seed_flows(db, "trust", [50, 50])
    _seed_flows(db, "dealer", [0, 0])

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["is_accumulation"] is True
    assert result["chip_strength"] == "strong"


def test_is_accumulation_false_when_volume_spike(db):
    closes = [100.0 + i for i in range(8)]
    volumes = [100.0] * 7 + [300.0]  # spike
    _seed_prices(db, closes, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["is_accumulation"] is False


def test_is_accumulation_false_when_single_day_jump_too_big(db):
    # uptrend + volume increasing 但最後一天單日 +10% → not gradual
    closes = [100.0, 100.5, 100.8, 101.0, 101.3, 101.5, 101.7, 112.0]
    volumes = [100.0] * 5 + [120.0] * 3
    _seed_prices(db, closes, volumes=volumes)

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["is_accumulation"] is False


def test_chip_strength_weak_when_spike_without_inst_support(db):
    closes = [100.0 + i * 0.5 for i in range(8)]
    volumes = [100.0] * 7 + [300.0]
    _seed_prices(db, closes, volumes=volumes)
    # 無任何法人 row

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["chip_strength"] == "weak"


def test_chip_strength_neutral_when_some_support_but_not_accumulation(db):
    closes = [100.0 + i * 0.1 for i in range(8)]  # sideways
    volumes = [100.0] * 8
    _seed_prices(db, closes, volumes=volumes)
    _seed_flows(db, "foreign", [100, 200])
    _seed_flows(db, "trust", [0, 0])
    _seed_flows(db, "dealer", [0, 0])

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["is_accumulation"] is False
    assert result["chip_strength"] == "neutral"


# ---------------------------------------------------------------------------
# No-hindsight
# ---------------------------------------------------------------------------


def test_no_hindsight_future_flow_does_not_affect(db):
    _seed_prices(db, [100.0] * 8)
    _seed_flows(db, "foreign", [100, 200, 300])  # 3 連買，最後一天是 buy_date
    # 塞 buy_date 之後的負值（若漏擋會把 count 算成 0）
    db.add(InstStockFlow(
        trade_date=END_DATE + timedelta(days=1),
        stock_id=STOCK_ID,
        inst_type="foreign",
        buy_shares=0, sell_shares=100, net_shares=-100,
    ))
    db.commit()

    result, _ = compute_chip_signals(db, STOCK_ID, END_DATE, INDUSTRY)

    assert result["foreign_buy_days"] == 3
