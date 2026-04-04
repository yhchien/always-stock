"""
tests for backend/etl/fetch_stock_master.py
使用 unittest.mock patch 掉 urllib.request.urlopen，不打真實 API。
"""
import json
from io import BytesIO
from unittest.mock import patch, MagicMock
import pytest

from etl.fetch_stock_master import fetch_and_upsert_stock_master
from app.models import StockMaster


def make_fake_response(rows: list[dict], status: int = 200) -> MagicMock:
    payload = json.dumps({"status": status, "msg": "ok", "data": rows}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


TWSE_ROW = lambda sid, name, cat: {
    "stock_id": sid, "stock_name": name,
    "industry_category": cat, "type": "twse", "date": "2024-01-01"
}
OTC_ROW = lambda sid, name: {
    "stock_id": sid, "stock_name": name,
    "industry_category": "其他", "type": "otc", "date": "2024-01-01"
}


class TestFetchAndUpsertStockMaster:
    def test_inserts_twse_stocks(self, db):
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), TWSE_ROW("2454", "聯發科", "IC設計")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            count = fetch_and_upsert_stock_master(db)
        assert count == 2
        assert db.get(StockMaster, "2330").stock_name == "台積電"
        assert db.get(StockMaster, "2454").stock_name == "聯發科"

    def test_filters_out_otc_stocks(self, db):
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), OTC_ROW("6488", "環球晶")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            count = fetch_and_upsert_stock_master(db)
        assert count == 1
        assert db.get(StockMaster, "6488") is None

    def test_deduplication_keeps_first_occurrence(self, db):
        # 同一股票出現兩次（不同產業別），只保留第一筆
        rows = [
            TWSE_ROW("2330", "台積電", "半導體業"),
            TWSE_ROW("2330", "台積電", "電子工業"),
        ]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            count = fetch_and_upsert_stock_master(db)
        assert count == 1
        assert db.get(StockMaster, "2330").industry_name == "半導體業"

    def test_empty_industry_defaults_to_other(self, db):
        rows = [TWSE_ROW("9999", "測試股", "")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            fetch_and_upsert_stock_master(db)
        assert db.get(StockMaster, "9999").industry_name == "其他"

    def test_upsert_updates_existing_record(self, db):
        # 先插入舊資料
        db.add(StockMaster(stock_id="2330", stock_name="舊名稱", industry_name="舊產業"))
        db.commit()

        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            fetch_and_upsert_stock_master(db)

        updated = db.get(StockMaster, "2330")
        assert updated.stock_name == "台積電"
        assert updated.industry_name == "半導體業"

    def test_raises_on_api_error_status(self, db):
        with patch("etl.fetch_stock_master.urllib.request.urlopen",
                   return_value=make_fake_response([], status=400)):
            with pytest.raises(RuntimeError, match="FinMind API error"):
                fetch_and_upsert_stock_master(db)

    def test_strips_whitespace_from_ids_and_names(self, db):
        rows = [{"stock_id": " 2330 ", "stock_name": " 台積電 ",
                 "industry_category": " 半導體業 ", "type": "twse", "date": "2024-01-01"}]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            fetch_and_upsert_stock_master(db)
        row = db.get(StockMaster, "2330")
        assert row is not None
        assert row.stock_name == "台積電"
