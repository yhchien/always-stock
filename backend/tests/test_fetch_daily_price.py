"""
tests for backend/etl/fetch_daily_price.py
Uses unittest.mock to patch urllib.request.urlopen — no real API calls.
"""
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models import DailyPrice
from etl.fetch_daily_price import _parse_number, fetch_and_upsert_daily_price

TRADE_DATE = date(2025, 4, 3)


def make_fake_response(stock_rows: list, stat: str = "OK") -> MagicMock:
    """Build a mock MI_INDEX API response with stock data in tables[8]."""
    tables = [{"title": "", "fields": [], "data": []} for _ in range(8)]
    tables.append({
        "title": "每日收盤行情",
        "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                    "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)",
                    "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價",
                    "最後揭示賣量", "本益比"],
        "data": stock_rows,
    })
    payload = json.dumps({"stat": stat, "tables": tables}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def mi_row(sid, name, volume, turnover, close):
    """Build a MI_INDEX tables[8] row (16 columns)."""
    return [sid, name, volume, "1,000", turnover,
            "100.00", "105.00", "99.00", close,
            '<p style= color:red>+</p>', "1.00",
            close, "10", close, "10", "15.00"]


class TestParseNumber:
    def test_normal_number(self):
        assert _parse_number("1,234.56") == 1234.56

    def test_integer_string(self):
        assert _parse_number("100") == 100.0

    def test_empty_string_returns_none(self):
        assert _parse_number("") is None

    def test_double_dash_returns_none(self):
        assert _parse_number("--") is None

    def test_whitespace_stripped(self):
        assert _parse_number("  500  ") == 500.0

    def test_large_number_with_commas(self):
        assert _parse_number("113,976,137") == 113976137.0


class TestFetchAndUpsertDailyPrice:
    def test_inserts_records(self, db):
        rows = [
            mi_row("2330", "台積電", "1,000,000", "100,000,000", "100.00"),
            mi_row("2454", "聯發科", "500,000", "80,000,000", "160.00"),
        ]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            count = fetch_and_upsert_daily_price(db, TRADE_DATE)

        assert count == 2
        rec = db.query(DailyPrice).filter_by(trade_date=TRADE_DATE, stock_id="2330").first()
        assert rec is not None
        assert rec.close_price == 100.0

    def test_returns_zero_on_non_ok_stat(self, db):
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response([], stat="很抱歉，沒有符合條件的資料")):
            count = fetch_and_upsert_daily_price(db, TRADE_DATE)

        assert count == 0
        assert db.query(DailyPrice).count() == 0

    def test_skips_rows_with_no_close_price(self, db):
        rows = [
            mi_row("2330", "台積電", "1,000,000", "100,000,000", "--"),  # suspended
            mi_row("2454", "聯發科", "500,000", "80,000,000", "160.00"),
        ]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            count = fetch_and_upsert_daily_price(db, TRADE_DATE)

        assert count == 1
        assert db.query(DailyPrice).filter_by(stock_id="2330").first() is None

    def test_calculates_avg_price(self, db):
        # avg_price = turnover / volume = 100_000_000 / 1_000_000 = 100.0
        rows = [mi_row("2330", "台積電", "1,000,000", "100,000,000", "100.00")]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            fetch_and_upsert_daily_price(db, TRADE_DATE)

        rec = db.query(DailyPrice).filter_by(stock_id="2330").first()
        assert rec.avg_price == pytest.approx(100.0)

    def test_avg_price_none_when_volume_zero(self, db):
        rows = [mi_row("2330", "台積電", "0", "0", "100.00")]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            fetch_and_upsert_daily_price(db, TRADE_DATE)

        rec = db.query(DailyPrice).filter_by(stock_id="2330").first()
        assert rec.avg_price is None

    def test_upsert_updates_existing_record(self, db):
        db.add(DailyPrice(
            trade_date=TRADE_DATE, stock_id="2330",
            close_price=99.0, volume=500000, turnover=49500000, avg_price=99.0,
        ))
        db.commit()

        rows = [mi_row("2330", "台積電", "1,000,000", "100,000,000", "100.00")]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            fetch_and_upsert_daily_price(db, TRADE_DATE)

        rec = db.query(DailyPrice).filter_by(stock_id="2330").first()
        assert rec.close_price == 100.0
        assert db.query(DailyPrice).count() == 1  # no duplicates

    def test_strips_whitespace_from_stock_id(self, db):
        rows = [mi_row(" 2330 ", "台積電", "1,000,000", "100,000,000", "100.00")]
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            fetch_and_upsert_daily_price(db, TRADE_DATE)

        assert db.query(DailyPrice).filter_by(stock_id="2330").first() is not None

    def test_empty_data_returns_zero(self, db):
        with patch("etl.fetch_daily_price.urllib.request.urlopen",
                   return_value=make_fake_response([])):
            count = fetch_and_upsert_daily_price(db, TRADE_DATE)

        assert count == 0

    def test_skips_weekend(self, db):
        # 2025-04-05 is a Saturday
        saturday = date(2025, 4, 5)
        count = fetch_and_upsert_daily_price(db, saturday)
        assert count == 0
