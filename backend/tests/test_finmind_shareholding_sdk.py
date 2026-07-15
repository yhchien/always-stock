"""fishtail momentum upgrade（2026-07-15）：發行股數 ETL 測試。

TaiwanStockShareholding 的 dataset-level fetch 與 margin_trade 同款：
只回 start_date 當日 → 多日 backfill 必須逐 daily_price 交易日呼叫。
"""
from datetime import date

import pandas as pd

from app.models import DailyPrice
from etl.finmind_shareholding_sdk import fetch_and_upsert_shareholding_sdk


class _FakeClient:
    def __init__(self, df_by_date=None, df=None):
        self.df_by_date = df_by_date or {}
        self.df = df
        self.calls = []

    def fetch_shareholding_dataset(self, start_date, end_date):
        self.calls.append((start_date, end_date))
        if self.df is not None:
            return self.df
        return self.df_by_date.get(start_date, pd.DataFrame())


def _row(d, sid, shares, ratio=10.0):
    return {
        "date": d,
        "stock_id": sid,
        "stock_name": "X",
        "NumberOfSharesIssued": shares,
        "ForeignInvestmentSharesRatio": ratio,
    }


def test_single_day_upserts(db):
    df = pd.DataFrame([_row("2026-07-14", "2330", 25930380458, 69.58)])
    client = _FakeClient(df=df)
    result = fetch_and_upsert_shareholding_sdk(
        db, ["2330"], date(2026, 7, 14), date(2026, 7, 14), client
    )
    assert result["status"] == "ok"
    assert result["upserted"] == 1
    assert client.calls == [("2026-07-14", "2026-07-14")]

    from sqlalchemy import text

    row = db.execute(text(
        "SELECT shares_issued, foreign_shares_ratio FROM stock_shares_outstanding "
        "WHERE stock_id='2330'"
    )).fetchone()
    assert row[0] == 25930380458
    assert abs(row[1] - 69.58) < 1e-9


def test_multi_day_loops_daily_price_trade_dates(db):
    """多日 backfill 逐 daily_price 交易日呼叫（週末 / 休市日不打 API）。"""
    for d in [date(2026, 7, 13), date(2026, 7, 14)]:  # 只有兩個交易日
        db.add(DailyPrice(trade_date=d, stock_id="2330", close_price=1000.0))
    db.commit()

    client = _FakeClient(df_by_date={
        "2026-07-13": pd.DataFrame([_row("2026-07-13", "2330", 100)]),
        "2026-07-14": pd.DataFrame([_row("2026-07-14", "2330", 100)]),
    })
    result = fetch_and_upsert_shareholding_sdk(
        db, ["2330"], date(2026, 7, 11), date(2026, 7, 15), client
    )
    assert result["status"] == "ok"
    assert result["upserted"] == 2
    assert client.calls == [
        ("2026-07-13", "2026-07-13"),
        ("2026-07-14", "2026-07-14"),
    ]


def test_empty_returns_no_data(db):
    client = _FakeClient(df=pd.DataFrame())
    result = fetch_and_upsert_shareholding_sdk(
        db, ["2330"], date(2026, 7, 14), date(2026, 7, 14), client
    )
    assert result["status"] == "no_data"


def test_filters_to_stocks_master_ids(db):
    df = pd.DataFrame([
        _row("2026-07-14", "2330", 100),
        _row("2026-07-14", "00878", 200),  # 不在 stocks_master 名單 → 過濾
    ])
    client = _FakeClient(df=df)
    result = fetch_and_upsert_shareholding_sdk(
        db, ["2330"], date(2026, 7, 14), date(2026, 7, 14), client
    )
    assert result["upserted"] == 1
