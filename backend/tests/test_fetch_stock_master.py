"""
tests for backend/etl/fetch_stock_master.py

Mock 策略：
- `urllib.request.urlopen` 負責 TaiwanStockInfo（含 stock_name / industry_category / type）
- `fetch_industry_chain` 用 patch 直接給 dict，模擬 TaiwanStockIndustryChain
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
TPEX_INDEX_ROW = {
    "stock_id": "TPEx", "stock_name": "櫃買指數",
    "industry_category": "大盤", "type": "tpex", "date": "2024-01-01",
}


def patch_finmind(info_rows: list[dict], chain_map: dict = None, status: int = 200):
    """一次 patch 掉 urlopen (TaiwanStockInfo) 與 fetch_industry_chain。"""
    if chain_map is None:
        chain_map = {}
    return (
        patch("etl.fetch_stock_master.urllib.request.urlopen",
              return_value=make_fake_response(info_rows, status=status)),
        patch("etl.fetch_stock_master.fetch_industry_chain",
              return_value=chain_map),
    )


class TestFetchAndUpsertStockMaster:
    def test_inserts_twse_stocks(self, db):
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), TWSE_ROW("2454", "聯發科", "IC設計")]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 2
        assert db.get(StockMaster, "2330").stock_name == "台積電"
        assert db.get(StockMaster, "2454").stock_name == "聯發科"

    def test_filters_out_otc_stocks(self, db):
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), OTC_ROW("6488", "環球晶")]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 1
        assert db.get(StockMaster, "6488") is None

    def test_filters_out_real_tpex_stocks_even_with_tpex_type(self, db):
        """type='tpex' 的真實個股（數字代號）仍要被過濾，只有指數佔位列例外。"""
        rows = [
            TWSE_ROW("2330", "台積電", "半導體業"),
            {"stock_id": "6488", "stock_name": "環球晶",
             "industry_category": "半導體業", "type": "tpex", "date": "2024-01-01"},
        ]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 1
        assert db.get(StockMaster, "6488") is None

    def test_admits_tpex_composite_index_placeholder(self, db):
        """2026-08-13：櫃買指數（stock_id='TPEx'，type='tpex'）是唯一放行的例外，
        讓 /signals 首頁『今日市場狀態』的 OTC 指數能拿到真實 ETL 資料。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), TPEX_INDEX_ROW]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 2
        row = db.get(StockMaster, "TPEx")
        assert row is not None
        assert row.stock_name == "櫃買指數"
        assert row.industry_name == "大盤"
        assert row.market == "tpex"
        assert db.get(StockMaster, "2330").market == "twse"

    def test_deduplication_keeps_first_occurrence(self, db):
        rows = [
            TWSE_ROW("2330", "台積電", "半導體業"),
            TWSE_ROW("2330", "台積電", "電子工業"),
        ]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 1
        assert db.get(StockMaster, "2330").industry_name == "半導體業"

    def test_industry_chain_overrides_industry_category(self, db):
        """IndustryChain 有資料時，industry_name 吃 chain.industry 而非 industry_category。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        chain_map = {"2330": {"industry": "半導體", "sub_industry": "晶圓代工"}}
        urlopen_patch, chain_patch = patch_finmind(rows, chain_map)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")
        row = db.get(StockMaster, "2330")
        assert row.industry_name == "半導體"
        assert row.sub_industry == "晶圓代工"
        assert row.source == "finmind"

    def test_fallback_to_industry_category_when_not_in_chain(self, db):
        """不在 TaiwanStockIndustryChain 的股票，industry_name 退回 industry_category。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業"), TWSE_ROW("2454", "聯發科", "電子工業")]
        chain_map = {"2330": {"industry": "半導體", "sub_industry": "晶圓代工"}}
        urlopen_patch, chain_patch = patch_finmind(rows, chain_map)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")
        mtk = db.get(StockMaster, "2454")
        assert mtk.industry_name == "電子工業"
        assert mtk.sub_industry is None

    def test_empty_industry_defaults_to_other(self, db):
        rows = [TWSE_ROW("9999", "測試股", "")]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")
        assert db.get(StockMaster, "9999").industry_name == "其他"

    def test_chain_column_always_null(self, db):
        """chain（上游/中游/下游）已停用，任何情況下都寫 None。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        chain_map = {"2330": {"industry": "半導體", "sub_industry": "晶圓代工"}}
        urlopen_patch, chain_patch = patch_finmind(rows, chain_map)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")
        row = db.get(StockMaster, "2330")
        assert row.chain is None

    def test_upsert_updates_existing_record(self, db):
        db.add(StockMaster(
            stock_id="2330", stock_name="舊名稱", industry_name="舊產業",
            chain="中游", sub_industry="舊細類", source="fugle",
        ))
        db.commit()

        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        chain_map = {"2330": {"industry": "半導體", "sub_industry": "晶圓代工"}}
        urlopen_patch, chain_patch = patch_finmind(rows, chain_map)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")

        updated = db.get(StockMaster, "2330")
        assert updated.stock_name == "台積電"
        assert updated.industry_name == "半導體"
        assert updated.sub_industry == "晶圓代工"
        assert updated.chain is None  # 升級時舊 chain 值會被洗掉
        assert updated.source == "finmind"

    def test_raises_on_api_error_status(self, db):
        urlopen_patch, chain_patch = patch_finmind([], status=400)
        with urlopen_patch, chain_patch:
            with pytest.raises(RuntimeError, match="FinMind API error"):
                fetch_and_upsert_stock_master(db, token="fake-token")

    def test_strips_whitespace_from_ids_and_names(self, db):
        rows = [{"stock_id": " 2330 ", "stock_name": " 台積電 ",
                 "industry_category": " 半導體業 ", "type": "twse", "date": "2024-01-01"}]
        urlopen_patch, chain_patch = patch_finmind(rows)
        with urlopen_patch, chain_patch:
            fetch_and_upsert_stock_master(db, token="fake-token")
        row = db.get(StockMaster, "2330")
        assert row is not None
        assert row.stock_name == "台積電"

    def test_no_token_skips_industry_chain(self, db):
        """未提供 token 時，不呼叫 fetch_industry_chain，sub_industry 留 None。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            with patch("etl.fetch_stock_master.fetch_industry_chain") as mock_chain:
                fetch_and_upsert_stock_master(db)  # 不帶 token
                mock_chain.assert_not_called()
        row = db.get(StockMaster, "2330")
        assert row.industry_name == "半導體業"  # fallback
        assert row.sub_industry is None

    def test_industry_chain_failure_falls_back_gracefully(self, db):
        """IndustryChain 抓取失敗時不應整個 ETL 爆掉，改走 industry_category fallback。"""
        rows = [TWSE_ROW("2330", "台積電", "半導體業")]
        with patch("etl.fetch_stock_master.urllib.request.urlopen",
                   return_value=make_fake_response(rows)):
            with patch("etl.fetch_stock_master.fetch_industry_chain",
                       side_effect=RuntimeError("FinMind quota exceeded")):
                count = fetch_and_upsert_stock_master(db, token="fake-token")
        assert count == 1
        row = db.get(StockMaster, "2330")
        assert row.industry_name == "半導體業"
        assert row.sub_industry is None
