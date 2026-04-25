"""
Unit tests for app.analysis.peer_rank.compute_peer_rank.

Covers: percentile 正確值、peers 不足 null 路徑、leader 條件 4 取 2、no-hindsight。
"""
from datetime import date, timedelta
from typing import List

from app.analysis.peer_rank import compute_peer_rank
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


def _seed_flows(db, stock_id: str, nets: List[float], end_date: date = BUY_DATE) -> None:
    for i, net in enumerate(nets):
        d = end_date - timedelta(days=len(nets) - 1 - i)
        db.add(InstStockFlow(
            trade_date=d,
            stock_id=stock_id,
            inst_type="foreign",
            buy_shares=max(net, 0),
            sell_shares=abs(min(net, 0)),
            net_shares=net,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# null 路徑
# ---------------------------------------------------------------------------


def test_returns_null_when_fewer_than_min_peers(db):
    _seed_peer(db, "A1")
    _seed_peer(db, "A2")  # 只有 2 檔
    for sid in ["A1", "A2"]:
        _seed_prices(db, sid, [100.0 + i for i in range(5)])
    db.commit()

    result, notes = compute_peer_rank(db, "A1", BUY_DATE, INDUSTRY)

    assert result["return_5d_percentile"] is None
    assert result["volume_percentile"] is None
    assert result["institution_rank_percentile"] is None
    assert result["leader_or_follower"] is None
    assert any("only 2 active peers" in n for n in notes)


def test_returns_null_when_stock_not_in_industry(db):
    # 產業裡有 3 檔 peers 但主股不在裡面
    for sid in ["P1", "P2", "P3"]:
        _seed_peer(db, sid)
        _seed_prices(db, sid, [100.0] * 5)
    _seed_peer(db, "X1", industry="其他產業")
    _seed_prices(db, "X1", [100.0] * 5)
    db.commit()

    result, notes = compute_peer_rank(db, "X1", BUY_DATE, INDUSTRY)

    assert result["return_5d_percentile"] is None
    assert any("not in industry" in n for n in notes)


# ---------------------------------------------------------------------------
# percentile 值正確
# ---------------------------------------------------------------------------


def test_top_percentile_zero_when_best_return(db):
    _seed_peer(db, "TOP")
    _seed_peer(db, "MID")
    _seed_peer(db, "LOW")
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(5)])  # +20%
    _seed_prices(db, "MID", [100.0 + i * 2 for i in range(5)])  # +8%
    _seed_prices(db, "LOW", [100.0 + i * 0.5 for i in range(5)])  # +2%
    db.commit()

    result, _ = compute_peer_rank(db, "TOP", BUY_DATE, INDUSTRY)

    assert result["return_5d_percentile"] == 0.0  # 第 1 名


def test_top_percentile_one_when_worst_return(db):
    _seed_peer(db, "TOP")
    _seed_peer(db, "MID")
    _seed_peer(db, "LOW")
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(5)])
    _seed_prices(db, "MID", [100.0 + i * 2 for i in range(5)])
    _seed_prices(db, "LOW", [100.0 + i * 0.5 for i in range(5)])
    db.commit()

    result, _ = compute_peer_rank(db, "LOW", BUY_DATE, INDUSTRY)

    assert result["return_5d_percentile"] == 1.0


def test_top_percentile_mid_when_median(db):
    _seed_peer(db, "TOP")
    _seed_peer(db, "MID")
    _seed_peer(db, "LOW")
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(5)])
    _seed_prices(db, "MID", [100.0 + i * 2 for i in range(5)])
    _seed_prices(db, "LOW", [100.0 + i * 0.5 for i in range(5)])
    db.commit()

    result, _ = compute_peer_rank(db, "MID", BUY_DATE, INDUSTRY)

    # rank=1, n=3 → 1/2 = 0.5
    assert result["return_5d_percentile"] == 0.5


# ---------------------------------------------------------------------------
# leader_or_follower
# ---------------------------------------------------------------------------


def test_leader_when_top_return_and_high_inst_intensity(db):
    for sid in ["TOP", "MID", "LOW"]:
        _seed_peer(db, sid)
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(25)])  # 強勢且已 breakout
    _seed_prices(db, "MID", [100.0 + i * 0.5 for i in range(25)])
    _seed_prices(db, "LOW", [100.0 - i * 0.5 for i in range(25)])

    _seed_flows(db, "TOP", [1000, 2000, 3000, 4000, 5000])
    _seed_flows(db, "MID", [0, 0, 0, 0, 0])
    _seed_flows(db, "LOW", [-100, -200, -300, -400, -500])
    db.commit()

    result, _ = compute_peer_rank(db, "TOP", BUY_DATE, INDUSTRY)

    assert result["leader_or_follower"] == "leader"


def test_follower_when_only_one_criterion_met(db):
    # TOP 有高 return 但 inst/volume/breakout 都不佔優
    for sid in ["TOP", "MID", "LOW"]:
        _seed_peer(db, sid)
    # TOP 報酬高但 breakout baseline 不夠（不會觸發 breakout）
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(5)])
    _seed_prices(db, "MID", [100.0 + i * 2 for i in range(5)])
    _seed_prices(db, "LOW", [100.0 + i * 0.5 for i in range(5)])
    # inst intensity：TOP 墊底
    _seed_flows(db, "TOP", [0, 0, 0, 0, 0])
    _seed_flows(db, "MID", [1000, 1000, 1000, 1000, 1000])
    _seed_flows(db, "LOW", [2000, 2000, 2000, 2000, 2000])
    db.commit()

    result, _ = compute_peer_rank(db, "TOP", BUY_DATE, INDUSTRY)

    assert result["leader_or_follower"] == "follower"


# ---------------------------------------------------------------------------
# No-hindsight
# ---------------------------------------------------------------------------


def test_no_hindsight_future_prices_ignored_in_ranking(db):
    for sid in ["TOP", "MID", "LOW"]:
        _seed_peer(db, sid)
    _seed_prices(db, "TOP", [100.0 + i * 5 for i in range(5)])
    _seed_prices(db, "MID", [100.0 + i * 2 for i in range(5)])
    _seed_prices(db, "LOW", [100.0 + i * 0.5 for i in range(5)])
    # 把 TOP buy_date 後塞大跌
    db.add(DailyPrice(
        trade_date=BUY_DATE + timedelta(days=1),
        stock_id="TOP",
        close_price=1.0,
        volume=100.0,
        turnover=100.0,
    ))
    db.commit()

    result, _ = compute_peer_rank(db, "TOP", BUY_DATE, INDUSTRY)

    assert result["return_5d_percentile"] == 0.0  # 仍是第一，未被未來資料汙染
