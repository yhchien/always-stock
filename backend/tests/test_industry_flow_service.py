from datetime import date

from app.industry_names import normalize_industry_name
from app.industry_flow_service import (
    get_latest_industry_trade_date,
    get_recent_industry_trade_dates,
    load_industry_flow_rows,
    load_industry_flow_rows_for_dates,
)
from app.models import IndustryDailyFlow, InstStockFlow, StockMaster


def _seed_stock(db, stock_id: str, stock_name: str, industry_name: str) -> None:
    db.add(StockMaster(
        stock_id=stock_id,
        stock_name=stock_name,
        industry_name=industry_name,
    ))


def _seed_flow(db, trade_date: date, stock_id: str, inst_type: str, net_amount: float) -> None:
    buy_amount = max(net_amount, 0)
    sell_amount = abs(min(net_amount, 0))
    db.add(InstStockFlow(
        trade_date=trade_date,
        stock_id=stock_id,
        inst_type=inst_type,
        buy_shares=buy_amount / 10 if buy_amount else 0,
        sell_shares=sell_amount / 10 if sell_amount else 0,
        net_shares=(buy_amount - sell_amount) / 10,
        buy_amount_est=buy_amount,
        sell_amount_est=sell_amount,
        net_amount_est=net_amount,
    ))


def test_load_industry_flow_rows_falls_back_to_inst_flow(db):
    trade_date = date(2024, 1, 3)
    _seed_stock(db, "2330", "台積電", "半導體")
    _seed_flow(db, trade_date, "2330", "foreign", 1200000)
    _seed_flow(db, trade_date, "2330", "trust", -200000)
    _seed_flow(db, trade_date, "2330", "dealer", 100000)
    db.commit()

    rows = load_industry_flow_rows(db, trade_date)

    assert len(rows) == 1
    row = rows[0]
    assert row.trade_date == trade_date
    assert row.industry_name == "半導體"
    assert row.total_net_amount == 1100000
    assert row.foreign_net_amount == 1200000
    assert row.trust_net_amount == -200000
    assert row.dealer_net_amount == 100000
    assert row.total_buy_amount == 1300000
    assert row.total_sell_amount == 200000
    assert row.streak == 0


def test_load_industry_flow_rows_for_dates_returns_descending_dates(db):
    earlier = date(2024, 1, 2)
    later = date(2024, 1, 3)
    _seed_stock(db, "2330", "台積電", "半導體")
    _seed_flow(db, earlier, "2330", "foreign", 500000)
    _seed_flow(db, later, "2330", "foreign", 700000)
    db.commit()

    rows = load_industry_flow_rows_for_dates(db, [earlier, later])

    assert [row.trade_date for row in rows] == [later, earlier]
    assert [row.total_net_amount for row in rows] == [700000, 500000]


def test_latest_and_recent_trade_dates_use_inst_flow_when_agg_is_missing(db):
    d1 = date(2024, 1, 2)
    d2 = date(2024, 1, 3)
    _seed_stock(db, "2330", "台積電", "半導體")
    _seed_flow(db, d1, "2330", "foreign", 100000)
    _seed_flow(db, d2, "2330", "foreign", 200000)
    db.commit()

    assert get_latest_industry_trade_date(db, d2) == d2
    assert get_recent_industry_trade_dates(db, d2, 2) == [d2, d1]


def test_load_industry_flow_rows_merges_legacy_and_canonical_names(db):
    trade_date = date(2024, 1, 3)
    db.add(
        IndustryDailyFlow(
            trade_date=trade_date,
            industry_name="塑膠工業",
            total_buy_amount=100,
            total_sell_amount=50,
            total_net_amount=50,
            foreign_net_amount=40,
            trust_net_amount=5,
            dealer_net_amount=5,
            streak=2,
        )
    )
    db.add(
        IndustryDailyFlow(
            trade_date=trade_date,
            industry_name="石化及塑橡膠",
            total_buy_amount=60,
            total_sell_amount=30,
            total_net_amount=30,
            foreign_net_amount=20,
            trust_net_amount=5,
            dealer_net_amount=5,
            streak=2,
        )
    )
    db.commit()

    rows = load_industry_flow_rows(db, trade_date)

    assert len(rows) == 1
    row = rows[0]
    assert row.industry_name == normalize_industry_name("塑膠工業")
    assert row.total_net_amount == 80
    assert row.total_buy_amount == 160
    assert row.total_sell_amount == 80
    assert row.streak == 2
