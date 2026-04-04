"""
tests for tools/scrape_fugle_industry.py
使用 unittest.mock patch 掉 HTTP 請求，不打真實 API。
"""
import csv
import json
import sys
from pathlib import Path
from io import StringIO
from unittest.mock import MagicMock, patch
import pytest

# 讓 tools/ 目錄可被 import
sys.path.insert(0, str(Path(__file__).parent.parent))
import scrape_fugle_industry as sut


# ── helpers ──────────────────────────────────────────────────────────────────

def make_response(data: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.raise_for_status.side_effect = (
        None if status_code < 400
        else lambda: (_ for _ in ()).throw(
            __import__("requests").exceptions.HTTPError(
                response=MagicMock(status_code=status_code)
            )
        )
    )
    return mock


# ── get_json ──────────────────────────────────────────────────────────────────

class TestGetJson:
    def test_returns_json_on_success(self):
        with patch.object(sut.SESSION, "get", return_value=make_response({"ok": True})):
            result = sut.get_json("http://fake")
        assert result == {"ok": True}

    def test_retries_on_5xx_then_succeeds(self):
        import requests
        fail = MagicMock()
        fail.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500)
        )
        success = make_response({"ok": True})
        with patch.object(sut.SESSION, "get", side_effect=[fail, success]):
            with patch("scrape_fugle_industry.time.sleep"):
                result = sut.get_json("http://fake")
        assert result == {"ok": True}

    def test_raises_after_max_retries(self):
        import requests
        fail = MagicMock()
        fail.raise_for_status.side_effect = requests.exceptions.Timeout()
        with patch.object(sut.SESSION, "get", return_value=fail):
            with patch("scrape_fugle_industry.time.sleep"):
                with pytest.raises(RuntimeError, match="連續失敗"):
                    sut.get_json("http://fake")

    def test_does_not_retry_on_4xx(self):
        import requests
        fail = MagicMock()
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=404))
        fail.raise_for_status.side_effect = err
        with patch.object(sut.SESSION, "get", return_value=fail) as mock_get:
            with pytest.raises(RuntimeError, match="HTTP 404"):
                sut.get_json("http://fake")
        assert mock_get.call_count == 1  # 沒有重試


# ── fetch_twse_stock_info ─────────────────────────────────────────────────────

class TestFetchTwseStockInfo:
    def test_returns_twse_only(self):
        fake_data = {"data": [
            {"stock_id": "2330", "stock_name": "台積電", "type": "twse"},
            {"stock_id": "6488", "stock_name": "環球晶", "type": "otc"},
        ]}
        with patch("scrape_fugle_industry.get_json", return_value=fake_data):
            result = sut.fetch_twse_stock_info()
        assert "2330" in result
        assert "6488" not in result
        assert result["2330"] == "台積電"

    def test_returns_empty_dict_on_failure(self):
        with patch("scrape_fugle_industry.get_json", side_effect=RuntimeError("連線失敗")):
            result = sut.fetch_twse_stock_info()
        assert result == {}

    def test_returns_empty_dict_on_empty_data(self):
        with patch("scrape_fugle_industry.get_json", return_value={"data": []}):
            result = sut.fetch_twse_stock_info()
        assert result == {}


# ── build_nested ──────────────────────────────────────────────────────────────

class TestBuildNested:
    MAIN_TOPICS = [{"code": "IND-L000", "name": "印刷電路板"}]
    SUB_TOPICS = [
        {"code": "IND-L100", "name": "玻璃纖維", "chain": "上游", "numOfStocks": 2},
        {"code": "IND-L610", "name": "硬板製造", "chain": "中游", "numOfStocks": 2},
    ]
    SYMBOL_IDS = {"IND-L100": ["1234", "5678"], "IND-L610": ["9999"]}
    TWSE_INFO = {"1234": "A公司", "5678": "B公司", "9999": "C公司"}

    def _mock_fetch(self, main_code):
        return self.SUB_TOPICS

    def _mock_detail(self, sub_code):
        return self.SYMBOL_IDS.get(sub_code, [])

    def test_basic_structure(self):
        failed = []
        with patch("scrape_fugle_industry.fetch_sub_topics", side_effect=self._mock_fetch):
            with patch("scrape_fugle_industry.fetch_symbol_ids", side_effect=self._mock_detail):
                with patch("scrape_fugle_industry.time.sleep"):
                    result = sut.build_nested(self.MAIN_TOPICS, self.TWSE_INFO, failed)

        assert len(result) == 1
        ind = result[0]
        assert ind["industry"] == "印刷電路板"
        chain_names = [c["name"] for c in ind["chain"]]
        assert "上游" in chain_names
        assert "中游" in chain_names

    def test_stocks_are_filtered_to_twse(self):
        twse_info = {"1234": "A公司"}  # 只有 1234 是上市
        failed = []
        with patch("scrape_fugle_industry.fetch_sub_topics", side_effect=self._mock_fetch):
            with patch("scrape_fugle_industry.fetch_symbol_ids", side_effect=self._mock_detail):
                with patch("scrape_fugle_industry.time.sleep"):
                    result = sut.build_nested(self.MAIN_TOPICS, twse_info, failed)

        upstream = next(c for c in result[0]["chain"] if c["name"] == "上游")
        stocks = upstream["sub_industries"][0]["stocks"]
        assert len(stocks) == 1
        assert stocks[0]["code"] == "1234"

    def test_no_filter_when_twse_info_empty(self):
        """twse_info 為空時不過濾（FinMind fallback 情境）。"""
        failed = []
        with patch("scrape_fugle_industry.fetch_sub_topics", side_effect=self._mock_fetch):
            with patch("scrape_fugle_industry.fetch_symbol_ids", side_effect=self._mock_detail):
                with patch("scrape_fugle_industry.time.sleep"):
                    result = sut.build_nested(self.MAIN_TOPICS, {}, failed)

        upstream = next(c for c in result[0]["chain"] if c["name"] == "上游")
        stocks = upstream["sub_industries"][0]["stocks"]
        assert len(stocks) == 2  # 全部保留

    def test_failed_sub_topic_is_recorded(self):
        failed = []
        def bad_detail(sub_code):
            raise RuntimeError("timeout")

        with patch("scrape_fugle_industry.fetch_sub_topics", side_effect=self._mock_fetch):
            with patch("scrape_fugle_industry.fetch_symbol_ids", side_effect=bad_detail):
                with patch("scrape_fugle_industry.time.sleep"):
                    sut.build_nested(self.MAIN_TOPICS, self.TWSE_INFO, failed)

        assert len(failed) == 2  # 兩個子產業都失敗

    def test_failed_main_topic_is_recorded(self):
        failed = []
        with patch("scrape_fugle_industry.fetch_sub_topics", side_effect=RuntimeError("timeout")):
            with patch("scrape_fugle_industry.time.sleep"):
                sut.build_nested(self.MAIN_TOPICS, self.TWSE_INFO, failed)

        assert "IND-L000" in failed


# ── write_csv ─────────────────────────────────────────────────────────────────

class TestWriteCsv:
    NESTED = [
        {
            "industry": "印刷電路板",
            "chain": [
                {
                    "name": "上游",
                    "sub_industries": [
                        {
                            "name": "玻璃纖維",
                            "stocks": [
                                {"code": "1234", "name": "A公司"},
                                {"code": "5678", "name": "B公司"},
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    def test_csv_headers(self, tmp_path):
        csv_file = tmp_path / "out.csv"
        with patch.object(sut, "CSV_OUTPUT", csv_file):
            sut.write_csv(self.NESTED)

        with open(csv_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {"stock_id", "stock_name", "industry", "chain", "sub_industry"}

    def test_csv_row_count(self, tmp_path):
        csv_file = tmp_path / "out.csv"
        with patch.object(sut, "CSV_OUTPUT", csv_file):
            sut.write_csv(self.NESTED)

        with open(csv_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_csv_row_values(self, tmp_path):
        csv_file = tmp_path / "out.csv"
        with patch.object(sut, "CSV_OUTPUT", csv_file):
            sut.write_csv(self.NESTED)

        with open(csv_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["stock_id"] == "1234"
        assert rows[0]["industry"] == "印刷電路板"
        assert rows[0]["chain"] == "上游"
        assert rows[0]["sub_industry"] == "玻璃纖維"
