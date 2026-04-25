"""
Integration tests for app.analysis.build_trade_quality_context.

Covers: schema shape、unknown stock raise、notes 組合、fundamental null path、
news_input_stub 正確性、happy case snapshot。
"""
from datetime import date, timedelta
from typing import List

import pytest

from app.analysis import build_trade_quality_context
from app.models import DailyPrice, InstStockFlow, MonthlyRevenue, StockMaster

INDUSTRY = "AI 伺服器"
BUY_DATE = date(2026, 4, 22)


def _seed_peer(db, stock_id: str, stock_name: str = "", industry: str = INDUSTRY) -> None:
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=stock_name or f"stock_{stock_id}",
        industry_name=industry,
        is_active=True,
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


def _seed_flows(db, stock_id: str, nets: List[float], inst_type: str = "foreign", end_date: date = BUY_DATE) -> None:
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


def _seed_revenue(db, stock_id: str, revenue_month: date, yoy: float, mom: float) -> None:
    db.add(MonthlyRevenue(
        stock_id=stock_id,
        revenue_month=revenue_month,
        revenue=1000.0,
        yoy_pct=yoy,
        mom_pct=mom,
    ))
    db.commit()


# ---------------------------------------------------------------------------
# Raise path
# ---------------------------------------------------------------------------


def test_raises_when_stock_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        build_trade_quality_context(db, "NOPE", BUY_DATE)


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_output_has_all_required_sections(db):
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["stock_id"] == "A1"
    assert ctx["buy_date"] == str(BUY_DATE)
    for key in [
        "industry_summary", "chip_summary", "peer_rank",
        "fundamental", "price_structure", "news_input_stub",
        "data_quality_notes",
    ]:
        assert key in ctx


def test_industry_news_heat_always_null(db):
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["industry_summary"]["industry_news_heat"] is None


def test_guidance_always_null(db):
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    _seed_revenue(db, "A1", date(2026, 3, 31), yoy=15.0, mom=5.0)
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["fundamental"]["guidance"] is None
    assert ctx["fundamental"]["revenue_yoy"] == 15.0
    assert ctx["fundamental"]["revenue_mom"] == 5.0


# ---------------------------------------------------------------------------
# data_quality_notes behavior (決策 4b)
# ---------------------------------------------------------------------------


def test_notes_empty_when_no_data_gaps(db):
    # 3 檔 peers（peer_rank 門檻）+ 25 天歷史（price_structure 夠）+ revenue
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    _seed_flows(db, "A1", [100] * 5)
    _seed_flows(db, "A2", [100] * 5)
    _seed_flows(db, "A3", [100] * 5)
    _seed_revenue(db, "A1", date(2026, 3, 31), yoy=15.0, mom=5.0)
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["data_quality_notes"] == []


def test_notes_added_when_fundamental_missing(db):
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    # 不塞 revenue
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["fundamental"] == {"revenue_yoy": None, "revenue_mom": None, "guidance": None}
    assert any("fundamental is null" in n for n in ctx["data_quality_notes"])


def test_notes_added_when_peer_count_too_small(db):
    _seed_peer(db, "A1", "主角")  # 只有 1 檔
    _seed_prices(db, "A1", [100.0 + i for i in range(25)])
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["peer_rank"]["return_5d_percentile"] is None
    # industry + peer 都會產出 peer 不足的提示
    assert len(ctx["data_quality_notes"]) >= 1


def test_notes_added_when_price_structure_short_history(db):
    _seed_peer(db, "A1", "主角")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    # A1 只給 10 天歷史（< 21）
    _seed_prices(db, "A1", [100.0 + i for i in range(10)])
    _seed_prices(db, "A2", [100.0 + i for i in range(25)])
    _seed_prices(db, "A3", [100.0 + i for i in range(25)])
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx["price_structure"]["trend"] is None
    assert any("price_structure is null" in n for n in ctx["data_quality_notes"])


# ---------------------------------------------------------------------------
# news_input_stub
# ---------------------------------------------------------------------------


def test_news_input_stub_composes_query_strings(db):
    _seed_peer(db, "A1", stock_name="光寶科")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    stub = ctx["news_input_stub"]
    assert stub["query_stock"] == "A1 OR 光寶科"
    assert stub["query_industry"] == INDUSTRY
    assert stub["date_end"] == str(BUY_DATE)


# ---------------------------------------------------------------------------
# Snapshot happy case
# ---------------------------------------------------------------------------


def test_happy_case_snapshot(db):
    """固定 fixture 下驗證整體輸出 shape；不比對每個欄位值（那是各 section 測試的工作）。"""
    _seed_peer(db, "A1", stock_name="光寶科")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(
            db, sid,
            closes=[100.0 + i for i in range(25)],
            volumes=[100.0] * 22 + [200.0] * 3,
        )
        _seed_flows(db, sid, [100, 200, 300, 400, 500])
    _seed_revenue(db, "A1", date(2026, 3, 31), yoy=20.0, mom=8.0)
    db.commit()

    ctx = build_trade_quality_context(db, "A1", BUY_DATE)

    # 預期 shape + 關鍵欄位非 null
    assert ctx["stock_id"] == "A1"
    assert ctx["industry_summary"]["industry_name"] == INDUSTRY
    assert ctx["industry_summary"]["industry_hot_score"] is not None
    assert ctx["industry_summary"]["industry_hot_level"] in ("S", "A", "B", "C")
    assert ctx["chip_summary"]["foreign_buy_days"] == 5
    assert ctx["peer_rank"]["return_5d_percentile"] is not None
    assert ctx["peer_rank"]["leader_or_follower"] in ("leader", "follower")
    assert ctx["fundamental"]["revenue_yoy"] == 20.0
    assert ctx["price_structure"]["trend"] == "uptrend"
    assert ctx["data_quality_notes"] == []


def test_deterministic_repeated_calls_return_same_output(db):
    _seed_peer(db, "A1", stock_name="光寶科")
    _seed_peer(db, "A2")
    _seed_peer(db, "A3")
    for sid in ["A1", "A2", "A3"]:
        _seed_prices(db, sid, [100.0 + i for i in range(25)])
    db.commit()

    ctx1 = build_trade_quality_context(db, "A1", BUY_DATE)
    ctx2 = build_trade_quality_context(db, "A1", BUY_DATE)

    assert ctx1 == ctx2
