"""
tests for backend/etl/fetch_inst_flow.py
使用 unittest.mock patch 掉 urllib.request.urlopen，不打真實 API。
收盤價直接預先寫入 DB（走真實 DB fixture）。
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models import DailyPrice, InstStockFlow
from etl.fetch_inst_flow import _parse_shares, fetch_and_upsert_inst_flow

TRADE_DATE = date(2025, 4, 1)


def make_fake_response(rows: list, stat: str = "OK") -> MagicMock:
    payload = json.dumps({"stat": stat, "data": rows}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def t86_row(sid, foreign_buy, foreign_sell, trust_buy, trust_sell,
            dealer_self_buy, dealer_self_sell, dealer_hedge_buy, dealer_hedge_sell,
            dealer_net="0"):
    """
    產生一筆 T86 row（19 個欄位）。
    外陸資 net = foreign_buy - foreign_sell（簡化）
    """
    def s(n): return str(n)
    foreign_net = str(int(foreign_buy) - int(foreign_sell))
    trust_net   = str(int(trust_buy) - int(trust_sell))
    dealer_total_buy  = int(dealer_self_buy) + int(dealer_hedge_buy)
    dealer_total_sell = int(dealer_self_sell) + int(dealer_hedge_sell)
    return [
        sid, "股票名稱",
        s(foreign_buy), s(foreign_sell), foreign_net,  # 2,3,4
        "0", "0", "0",                                  # 5,6,7 外資自營商
        s(trust_buy), s(trust_sell), trust_net,         # 8,9,10
        dealer_net,                                     # 11 自營商買賣超合計
        s(dealer_self_buy), s(dealer_self_sell), "0",   # 12,13,14 自行
        s(dealer_hedge_buy), s(dealer_hedge_sell), "0", # 15,16,17 避險
        "0",                                            # 18 三大法人合計
    ]


def seed_close_price(db, stock_id, close_price):
    db.add(DailyPrice(
        trade_date=TRADE_DATE, stock_id=stock_id,
        close_price=close_price, volume=1000000,
        turnover=close_price * 1000000, avg_price=close_price,
    ))
    db.commit()


class TestParseShares:
    def test_normal_number(self):
        assert _parse_shares("1,000,000") == 1000000.0

    def test_zero(self):
        assert _parse_shares("0") == 0.0

    def test_double_dash_returns_zero(self):
        assert _parse_shares("--") == 0.0

    def test_empty_returns_zero(self):
        assert _parse_shares("") == 0.0


class TestFetchAndUpsertInstFlow:
    def test_inserts_three_inst_types_per_stock(self, db):
        row = t86_row("2330", 1000000, 200000, 50000, 10000, 30000, 5000, 20000, 3000)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            count = fetch_and_upsert_inst_flow(db, TRADE_DATE)

        assert count == 3
        types = {r.inst_type for r in db.query(InstStockFlow).all()}
        assert types == {"foreign", "trust", "dealer"}

    def test_returns_zero_on_non_ok_stat(self, db):
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([], stat="很抱歉，沒有符合條件的資料!")):
            count = fetch_and_upsert_inst_flow(db, TRADE_DATE)

        assert count == 0
        assert db.query(InstStockFlow).count() == 0

    def test_foreign_shares_correct(self, db):
        row = t86_row("2330", 1000000, 200000, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").first()
        assert rec.buy_shares == 1000000.0
        assert rec.sell_shares == 200000.0
        assert rec.net_shares == 800000.0

    def test_dealer_combines_self_and_hedge(self, db):
        # dealer_buy = 自行(30000) + 避險(20000) = 50000
        # dealer_sell = 自行(5000) + 避險(3000) = 8000
        row = t86_row("2330", 0, 0, 0, 0, 30000, 5000, 20000, 3000)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="dealer").first()
        assert rec.buy_shares == 50000.0
        assert rec.sell_shares == 8000.0

    def test_amount_est_uses_close_price(self, db):
        seed_close_price(db, "2330", 1000.0)
        row = t86_row("2330", 500, 100, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").first()
        assert rec.buy_amount_est == pytest.approx(500 * 1000.0)
        assert rec.sell_amount_est == pytest.approx(100 * 1000.0)

    def test_amount_est_zero_when_no_close_price(self, db):
        # DB 沒有 2330 的收盤價
        row = t86_row("2330", 500, 100, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").first()
        assert rec.buy_amount_est == 0.0
        assert rec.sell_amount_est == 0.0

    def test_upsert_updates_existing_record(self, db):
        db.add(InstStockFlow(
            trade_date=TRADE_DATE, stock_id="2330", inst_type="foreign",
            buy_shares=999, sell_shares=999, net_shares=0,
            buy_amount_est=0, sell_amount_est=0, net_amount_est=0,
        ))
        db.commit()

        row = t86_row("2330", 1000000, 200000, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").first()
        assert rec.buy_shares == 1000000.0
        assert db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").count() == 1

    def test_strips_whitespace_from_stock_id(self, db):
        row = t86_row(" 2330 ", 1000, 500, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([row])):
            fetch_and_upsert_inst_flow(db, TRADE_DATE)

        assert db.query(InstStockFlow).filter_by(stock_id="2330").count() == 3

    def test_multiple_stocks(self, db):
        rows = [
            t86_row("2330", 1000000, 200000, 50000, 10000, 0, 0, 0, 0),
            t86_row("2454", 300000, 100000, 20000, 5000, 0, 0, 0, 0),
        ]
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            count = fetch_and_upsert_inst_flow(db, TRADE_DATE)

        assert count == 6
        assert db.query(InstStockFlow).count() == 6

    def test_skips_rows_with_insufficient_columns(self, db):
        # 16 欄的 row（認購權證格式）應被跳過
        short_row = ["047037", "揚博購01", "0", "0", "0", "0", "0", "0",
                     "239,000", "0", "0", "0", "239,000", "0", "239,000", "239,000"]
        valid_row = t86_row("2330", 1000000, 200000, 0, 0, 0, 0, 0, 0)
        with patch("etl.fetch_inst_flow.urllib.request.urlopen",
                   return_value=make_fake_response([short_row, valid_row])):
            count = fetch_and_upsert_inst_flow(db, TRADE_DATE)

        assert count == 3  # 只有 valid_row 的 3 筆
        assert db.query(InstStockFlow).filter_by(stock_id="047037").count() == 0
