"""
Unit tests for app.analysis.industry_signals.compute_industry_signals.

Covers: price_strength / volume_trend / institution_flow 分類、hot_score 映射、
capital_type / is_false_hot 組合條件、資料不足 fallback。
"""
from datetime import date, timedelta
from typing import List

from app.analysis.industry_signals import compute_industry_signals
from app.models import DailyPrice, InstStockFlow, StockMaster

INDUSTRY = "AI 伺服器"
BUY_DATE = date(2026, 4, 22)


def _seed_peer(db, stock_id: str, industry: str = INDUSTRY, is_active: bool = True) -> None:
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=f"stock_{stock_id}",
        industry_name=industry,
        is_active=is_active,
    ))


def _seed_prices(
    db,
    stock_id: str,
    closes: List[float],
    volumes: List[float] = None,
    end_date: date = BUY_DATE,
) -> None:
    if volumes is None:
        volumes = [100.0] * len(closes)
    for i, (c, v) in enumerate(zip(closes, volumes)):
        d = end_date - timedelta(days=len(closes) - 1 - i)
        db.add(DailyPrice(trade_date=d, stock_id=stock_id, close_price=c, volume=v, turnover=v))
    db.commit()


def _seed_flows(db, stock_id: str, inst_type: str, nets: List[float], end_date: date = BUY_DATE) -> None:
    for i, net in enumerate(nets):
        d = end_date - timedelta(days=len(nets) - 1 - i)
        db.add(InstStockFlow(
            trade_date=d,
            stock_id=stock_id,
            inst_type=inst_type,
            buy_shares=max(net, 0),
            sell_shares=abs(min(net, 0)),
            net_shares=net,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# price_strength
# ---------------------------------------------------------------------------


def test_price_strength_strong_when_three_or_more_peers_positive(db):
    for sid in ["A1", "A2", "A3", "A4"]:
        _seed_peer(db, sid)
        _seed_prices(db, sid, [100.0 + i for i in range(5)])  # 每檔 +4% → 正報酬
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_price_strength"] == "strong"


def test_price_strength_medium_when_exactly_two_positive(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    _seed_peer(db, "A4")
    _seed_prices(db, "A1", [100.0 + i for i in range(5)])  # 正
    _seed_prices(db, "A2", [100.0 + i for i in range(5)])  # 正
    _seed_prices(db, "A3", [100.0 - i for i in range(5)])  # 負
    _seed_prices(db, "A4", [100.0 - i for i in range(5)])  # 負
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_price_strength"] == "medium"


def test_price_strength_weak_when_none_positive(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 - i for i in range(5)])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_price_strength"] == "weak"


# ---------------------------------------------------------------------------
# volume_trend
# ---------------------------------------------------------------------------


def test_volume_trend_expanding_3d(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    # 前 5 日 turnover 100、近 3 日 150（+50% > 15%）
    volumes = [100.0] * 5 + [150.0] * 3
    closes = [100.0] * 8
    _seed_prices(db, "A1", closes, volumes=volumes)
    _seed_prices(db, "A2", closes, volumes=volumes)
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_volume_trend"] == "expanding_3d"


def test_volume_trend_intermittent(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    # 前 5 日 100，近 3 日 ~110（+10%，落在 5% ~ 15% 區間）
    volumes = [100.0] * 5 + [110.0] * 3
    _seed_prices(db, "A1", [100.0] * 8, volumes=volumes)
    _seed_prices(db, "A2", [100.0] * 8, volumes=volumes)
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_volume_trend"] == "intermittent"


def test_volume_trend_flat(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    volumes = [100.0] * 8  # 完全持平
    _seed_prices(db, "A1", [100.0] * 8, volumes=volumes)
    _seed_prices(db, "A2", [100.0] * 8, volumes=volumes)
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_volume_trend"] == "flat"


# ---------------------------------------------------------------------------
# institution_flow
# ---------------------------------------------------------------------------


def test_institution_flow_strong_buy(db):
    # 3 檔以上每檔近 3 日有 >=2 日法人淨買
    for sid in ["A1", "A2", "A3"]:
        _seed_peer(db, sid)
        _seed_prices(db, sid, [100.0] * 8)
        _seed_flows(db, sid, "foreign", [100, 200, 300])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_institution_flow"] == "strong_buy"


def test_institution_flow_mixed(db):
    # 只有 1 檔符合 >= 2 日淨買；另一檔全是 0
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    _seed_prices(db, "A1", [100.0] * 8)
    _seed_prices(db, "A2", [100.0] * 8)
    _seed_prices(db, "A3", [100.0] * 8)
    _seed_flows(db, "A1", "foreign", [100, 200, 300])  # 3 天
    _seed_flows(db, "A2", "foreign", [0, 0, 0])
    _seed_flows(db, "A3", "foreign", [0, 0, 0])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_institution_flow"] == "mixed"


def test_institution_flow_none_when_no_broad_buying(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_prices(db, "A1", [100.0] * 8)
    _seed_prices(db, "A2", [100.0] * 8)
    # 雖然有 inst_stock_flow row，但全部淨賣
    _seed_flows(db, "A1", "foreign", [-10, -20, -30])
    _seed_flows(db, "A2", "foreign", [-10, -20, -30])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_institution_flow"] == "none"


# ---------------------------------------------------------------------------
# hot_score / hot_level / capital_type / is_false_hot
# ---------------------------------------------------------------------------


def test_hot_score_and_level_S(db):
    # 要觸發 S：price strong + volume expanding_3d + institution strong_buy = 2+2+2 = 6
    # 6 對應 A (5~6)。想 S 要有 news=2，但 spec 說目前 news 永遠 0 → S 其實在本版不可達
    # 因此這個 case 驗收 A。
    for sid in ["A1", "A2", "A3", "A4"]:
        _seed_peer(db, sid)
        _seed_prices(
            db, sid,
            [100.0 + i for i in range(8)],
            volumes=[100.0] * 5 + [200.0] * 3,
        )
        _seed_flows(db, sid, "foreign", [100, 200, 300])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_hot_score"] == 6
    assert result["industry_hot_level"] == "A"
    assert result["industry_capital_type"] == "re_rating_hot"


def test_hot_level_C_when_everything_weak(db):
    for sid in ["A1", "A2"]:
        _seed_peer(db, sid)
        _seed_prices(db, sid, [100.0] * 8, volumes=[100.0] * 8)
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_hot_score"] == 0
    assert result["industry_hot_level"] == "C"
    assert result["industry_capital_type"] is None
    assert result["is_false_hot"] is False


def test_is_false_hot_true_when_short_spike_without_inst_support(db):
    # 近 3 日單點爆量（只 1 天 spike），無法人支持，價格不延續
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    # 前 5 日量 100；近 3 日前兩天回歸 100、最後一天爆到 300
    volumes = [100.0] * 7 + [300.0]
    closes = [100.0] * 7 + [105.0]
    _seed_prices(db, "A1", closes, volumes=volumes)
    _seed_prices(db, "A2", closes, volumes=volumes)
    # 無 inst flow
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["is_false_hot"] is True


def test_is_false_hot_false_when_strong_institutional_buying(db):
    for sid in ["A1", "A2", "A3"]:
        _seed_peer(db, sid)
        _seed_prices(
            db, sid,
            [100.0 + i for i in range(8)],
            volumes=[100.0] * 5 + [200.0] * 3,
        )
        _seed_flows(db, sid, "foreign", [100, 200, 300])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["is_false_hot"] is False


def test_industry_news_heat_always_null(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_prices(db, "A1", [100.0] * 8)
    _seed_prices(db, "A2", [100.0] * 8)
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_news_heat"] is None


def test_note_emitted_when_too_few_peers(db):
    _seed_peer(db, "A1")  # 只有 1 檔
    _seed_prices(db, "A1", [100.0] * 8)
    db.commit()

    result, notes = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert any("only 1 active peers" in n or "only 1 active peer" in n for n in notes)
    assert result["industry_name"] == INDUSTRY


def test_inactive_peers_ignored(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3", is_active=False)  # 不納入
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(5)])
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    # 只有 A1 A2 算，positive=2 → medium
    assert result["industry_price_strength"] == "medium"


def test_no_hindsight_future_prices_ignored(db):
    for sid in ["A1", "A2", "A3"]:
        _seed_peer(db, sid)
        _seed_prices(db, sid, [100.0 + i for i in range(5)])
        # 塞 buy_date 之後的大跌
        db.add(DailyPrice(
            trade_date=BUY_DATE + timedelta(days=1),
            stock_id=sid,
            close_price=50.0,
            volume=100.0,
            turnover=100.0,
        ))
    db.commit()

    result, _ = compute_industry_signals(db, "A1", BUY_DATE, INDUSTRY)

    assert result["industry_price_strength"] == "strong"
