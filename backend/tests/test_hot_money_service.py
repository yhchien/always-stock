from datetime import date
from typing import Optional

from app.hot_money_service import (
    compute_hot_money,
    get_recent_trade_dates,
)
from app.models import DailyPrice, InstStockFlow, StockMaster


def _seed_stock(db, stock_id: str, stock_name: str, industry_name: str, sub: Optional[str] = None) -> None:
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=stock_name,
        industry_name=industry_name,
        sub_industry=sub,
    ))


def _seed_flow(db, trade_date: date, stock_id: str, inst_type: str, net_amount: float) -> None:
    buy = max(net_amount, 0)
    sell = abs(min(net_amount, 0))
    db.add(InstStockFlow(
        trade_date=trade_date,
        stock_id=stock_id,
        inst_type=inst_type,
        buy_shares=0,
        sell_shares=0,
        net_shares=0,
        buy_amount_est=buy,
        sell_amount_est=sell,
        net_amount_est=net_amount,
    ))


def _seed_price(db, trade_date: date, stock_id: str, close_price: float) -> None:
    db.add(DailyPrice(
        trade_date=trade_date,
        stock_id=stock_id,
        close_price=close_price,
    ))


def test_get_recent_trade_dates_returns_ascending_dates(db):
    _seed_stock(db, "2330", "台積電", "半導體業")
    for day, amount in [(1, 1), (2, 2), (3, 3), (4, 4)]:
        _seed_flow(db, date(2026, 4, day), "2330", "foreign", amount)
    db.commit()

    result = get_recent_trade_dates(db, date(2026, 4, 3), days=3)

    assert result == [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]


def test_get_recent_trade_dates_respects_stock_ids_filter(db):
    _seed_stock(db, "2330", "台積電", "半導體業")
    _seed_stock(db, "2317", "鴻海", "其他電子")
    _seed_flow(db, date(2026, 4, 1), "2330", "foreign", 1)
    _seed_flow(db, date(2026, 4, 2), "2317", "foreign", 1)
    db.commit()

    result = get_recent_trade_dates(db, date(2026, 4, 2), days=2, stock_ids=["2330"])

    assert result == [date(2026, 4, 1)]


def test_compute_hot_money_ranks_by_total_net_amount_desc(db):
    _seed_stock(db, "2330", "台積電", "半導體業", sub="IC 製造")
    _seed_stock(db, "2317", "鴻海", "其他電子")
    _seed_stock(db, "1101", "台泥", "水泥工業")

    for d in (date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)):
        _seed_flow(db, d, "2330", "foreign", 1e8)
        _seed_flow(db, d, "2330", "trust", 5e7)
        _seed_flow(db, d, "2317", "foreign", 2e7)
        _seed_flow(db, d, "1101", "foreign", -3e7)

    _seed_price(db, date(2026, 4, 19), "2330", 1000.0)
    _seed_price(db, date(2026, 4, 22), "2330", 1050.0)
    _seed_price(db, date(2026, 4, 22), "2317", 200.0)
    db.commit()

    result = compute_hot_money(db, end_date=date(2026, 4, 22), days=3, limit=10)

    assert result.start_date == date(2026, 4, 20)
    assert result.end_date == date(2026, 4, 22)
    assert result.trade_dates == [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    ids = [item.stock_id for item in result.items]
    assert ids == ["2330", "2317", "1101"]

    top = result.items[0]
    assert top.rank == 1
    assert top.stock_name == "台積電"
    assert top.industry_name == "半導體業"
    assert top.sub_industry == "IC 製造"
    assert top.foreign_net_amount == 3e8  # 1e8 * 3 days
    assert top.trust_net_amount == 1.5e8
    assert top.dealer_net_amount == 0
    assert top.total_net_amount == 4.5e8
    assert top.start_close_price == 1000.0
    assert top.end_close_price == 1050.0
    assert top.price_change_pct == 5.0


def test_compute_hot_money_price_change_none_when_prev_missing(db):
    _seed_stock(db, "2330", "台積電", "半導體業")
    _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 1e8)
    _seed_price(db, date(2026, 4, 22), "2330", 1050.0)
    db.commit()

    result = compute_hot_money(db, end_date=date(2026, 4, 22), days=3, limit=10)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.start_close_price is None
    assert item.end_close_price == 1050.0
    assert item.price_change_pct is None


def test_compute_hot_money_respects_stock_ids_scope(db):
    _seed_stock(db, "2330", "台積電", "半導體業")
    _seed_stock(db, "1101", "台泥", "水泥工業")
    _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 1e9)
    _seed_flow(db, date(2026, 4, 22), "1101", "foreign", 5e8)
    db.commit()

    result = compute_hot_money(
        db,
        end_date=date(2026, 4, 22),
        days=1,
        limit=10,
        stock_ids=["1101"],
    )

    assert [item.stock_id for item in result.items] == ["1101"]


def test_compute_hot_money_empty_stock_ids_returns_empty(db):
    result = compute_hot_money(
        db,
        end_date=date(2026, 4, 22),
        days=3,
        limit=10,
        stock_ids=[],
    )
    assert result.items == []
    assert result.trade_dates == []
    assert result.start_date is None


def test_compute_hot_money_no_flow_returns_empty_result(db):
    result = compute_hot_money(db, end_date=date(2026, 4, 22), days=3, limit=10)
    assert result.items == []
    assert result.trade_dates == []


def test_compute_hot_money_limit_truncates(db):
    for idx in range(5):
        sid = f"100{idx}"
        _seed_stock(db, sid, f"S{idx}", "雜項")
        _seed_flow(db, date(2026, 4, 22), sid, "foreign", (5 - idx) * 1e7)
    db.commit()

    result = compute_hot_money(db, end_date=date(2026, 4, 22), days=1, limit=3)
    assert [item.stock_id for item in result.items] == ["1000", "1001", "1002"]


def test_compute_hot_money_ignores_non_big3_inst_types(db):
    _seed_stock(db, "2330", "台積電", "半導體業")
    _seed_flow(db, date(2026, 4, 22), "2330", "foreign", 1e8)
    _seed_flow(db, date(2026, 4, 22), "2330", "other_type", 9e9)
    db.commit()

    result = compute_hot_money(db, end_date=date(2026, 4, 22), days=1, limit=10)
    assert len(result.items) == 1
    assert result.items[0].total_net_amount == 1e8
