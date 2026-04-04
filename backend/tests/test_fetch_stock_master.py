"""
tests for backend/etl/fetch_stock_master.py
使用 unittest.mock patch 掉 urllib.request.urlopen，不打真實 API。
Fugle mapping 測試使用臨時 CSV 檔案。
"""
import csv
import json
import os
import tempfile
from io import BytesIO
from unittest.mock import patch, MagicMock
import pytest

from etl.fetch_stock_master import fetch_and_upsert_stock_master, load_fugle_mapping
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


def make_fugle_csv(rows: list[dict]) -> str:
    """建立臨時 Fugle CSV 檔，回傳路徑。呼叫端負責刪除。"""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["stock_id", "stock_name", "industry", "chain", "sub_industry"])
        writer.writeheader()
        writer.writerows(rows)
    return path


class TestLoadFugleMapping:
    def test_returns_first_row_per_stock(self):
        path = make_fugle_csv([
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "中游", "sub_industry": "晶圓代工"},
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "下游", "sub_industry": "封裝測試"},
            {"stock_id": "2454", "stock_name": "聯發科", "industry": "半導體", "chain": "上游", "sub_industry": "IC設計"},
        ])
        try:
            mapping = load_fugle_mapping(path)
            assert mapping["2330"]["sub_industry"] == "晶圓代工"
            assert mapping["2330"]["chain"] == "中游"
            assert mapping["2454"]["sub_industry"] == "IC設計"
        finally:
            os.unlink(path)

    def test_strips_whitespace(self):
        path = make_fugle_csv([
            {"stock_id": " 2330 ", "stock_name": "台積電", "industry": " 半導體 ", "chain": " 中游 ", "sub_industry": " 晶圓代工 "},
        ])
        try:
            mapping = load_fugle_mapping(path)
            assert "2330" in mapping
            assert mapping["2330"]["industry"] == "半導體"
            assert mapping["2330"]["chain"] == "中游"
            assert mapping["2330"]["sub_industry"] == "晶圓代工"
        finally:
            os.unlink(path)

    def test_empty_csv_returns_empty_dict(self):
        path = make_fugle_csv([])
        try:
            assert load_fugle_mapping(path) == {}
        finally:
            os.unlink(path)


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

    def test_no_fugle_mapping_leaves_chain_and_sub_industry_null(self, db):
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
            fetch_and_upsert_stock_master(db)
        row = db.get(StockMaster, "2330")
        assert row.chain is None
        assert row.sub_industry is None

    def test_fugle_mapping_overrides_industry(self, db):
        path = make_fugle_csv([
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "中游", "sub_industry": "晶圓代工"},
        ])
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        try:
            with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
                fetch_and_upsert_stock_master(db, fugle_mapping_path=path)
            row = db.get(StockMaster, "2330")
            assert row.industry_name == "半導體"
            assert row.chain == "中游"
            assert row.sub_industry == "晶圓代工"
        finally:
            os.unlink(path)

    def test_fugle_mapping_not_in_csv_uses_finmind(self, db):
        # 2330 在 Fugle，2454 不在
        path = make_fugle_csv([
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "中游", "sub_industry": "晶圓代工"},
        ])
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), TWSE_ROW("2454", "聯發科", "IC設計")]
        try:
            with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
                fetch_and_upsert_stock_master(db, fugle_mapping_path=path)
            tsmc = db.get(StockMaster, "2330")
            mtk = db.get(StockMaster, "2454")
            assert tsmc.sub_industry == "晶圓代工"
            assert mtk.industry_name == "IC設計"
            assert mtk.chain is None
            assert mtk.sub_industry is None
        finally:
            os.unlink(path)

    def test_fugle_mapping_first_row_wins_for_duplicate_stocks(self, db):
        path = make_fugle_csv([
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "中游", "sub_industry": "晶圓代工"},
            {"stock_id": "2330", "stock_name": "台積電", "industry": "半導體", "chain": "下游", "sub_industry": "封裝測試"},
        ])
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        try:
            with patch("etl.fetch_stock_master.urllib.request.urlopen", return_value=make_fake_response(rows)):
                fetch_and_upsert_stock_master(db, fugle_mapping_path=path)
            row = db.get(StockMaster, "2330")
            assert row.sub_industry == "晶圓代工"
        finally:
            os.unlink(path)
