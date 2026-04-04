"""
tests for run_daily_etl.py
只驗證 run_one_day 的整合流程控制邏輯，不打真實 API。
"""
from datetime import date
from unittest.mock import MagicMock, patch

from run_daily_etl import run_one_day

TRADE_DATE = date(2025, 4, 1)


def _patch_all(price_count=100, flow_count=300, agg_count=20):
    """回傳多個 patch 的 context manager dict（用於 with patch(...)）。"""
    return {
        "master": patch("run_daily_etl.fetch_and_upsert_stock_master", return_value=500),
        "price":  patch("run_daily_etl.fetch_and_upsert_daily_price",  return_value=price_count),
        "flow":   patch("run_daily_etl.fetch_and_upsert_inst_flow",    return_value=flow_count),
        "agg":    patch("run_daily_etl.aggregate_industry_flow",       return_value=agg_count),
        "db":     patch("run_daily_etl.SessionLocal"),
        "init":   patch("run_daily_etl.init_db"),
    }


class TestRunOneDay:
    def test_returns_true_on_trading_day(self):
        patches = _patch_all(price_count=100)
        with patches["db"], patches["master"], patches["price"] as mp, \
             patches["flow"], patches["agg"]:
            result = run_one_day(TRADE_DATE, fugle_mapping_path=None,
                                 skip_master=False, token="")
        assert result is True

    def test_returns_false_on_non_trading_day(self):
        patches = _patch_all(price_count=0)
        with patches["db"], patches["master"], patches["price"], \
             patches["flow"] as mf, patches["agg"] as ma:
            result = run_one_day(TRADE_DATE, fugle_mapping_path=None,
                                 skip_master=False, token="")
        assert result is False
        mf.assert_not_called()  # 非交易日不應呼叫 inst_flow
        ma.assert_not_called()  # 非交易日不應呼叫 aggregation

    def test_skip_master_does_not_call_fetch_master(self):
        patches = _patch_all()
        with patches["db"], patches["master"] as mm, patches["price"], \
             patches["flow"], patches["agg"]:
            run_one_day(TRADE_DATE, fugle_mapping_path=None,
                        skip_master=True, token="")
        mm.assert_not_called()

    def test_returns_false_on_exception(self):
        with patch("run_daily_etl.SessionLocal"), \
             patch("run_daily_etl.fetch_and_upsert_stock_master", side_effect=RuntimeError("boom")):
            result = run_one_day(TRADE_DATE, fugle_mapping_path=None,
                                 skip_master=False, token="")
        assert result is False
