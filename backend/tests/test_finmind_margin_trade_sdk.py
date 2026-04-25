from datetime import date

import pandas as pd
import pytest

from app.models import MarginTrade
from etl.finmind_margin_trade_sdk import fetch_and_upsert_margin_trade_finmind_sdk


class FakeFinMindSDKClient:
    """正常回傳一筆 2330、一筆 2317 的 fixture"""

    def __init__(self, df=None):
        self._df = df

    def fetch_margin_purchase_short_sale(self, stock_id_list, start_date, end_date, use_async):
        assert use_async is True
        if self._df is not None:
            return self._df
        return pd.DataFrame(
            [
                {
                    "date": "2026-04-21",
                    "stock_id": "2330",
                    "MarginPurchaseTodayBalance": 12000,
                    "MarginPurchaseYesterdayBalance": 10000,
                    "ShortSaleTodayBalance": 800,
                    "ShortSaleYesterdayBalance": 1000,
                },
                {
                    "date": "2026-04-21",
                    "stock_id": "2317",
                    "MarginPurchaseTodayBalance": 5000,
                    "MarginPurchaseYesterdayBalance": 5500,
                    "ShortSaleTodayBalance": 200,
                    "ShortSaleYesterdayBalance": 150,
                },
            ]
        )


def test_margin_trade_writes_balances_and_changes(db):
    trade_date = date(2026, 4, 21)

    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db=db,
        stock_ids=["2330", "2317"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(),
    )

    assert result["status"] == "ok"
    assert result["upserted"] == 2

    rec_2330 = db.query(MarginTrade).filter_by(stock_id="2330").one()
    assert rec_2330.trade_date == trade_date
    assert rec_2330.margin_balance == 12000
    assert rec_2330.margin_change == 2000   # 散戶融資追高
    assert rec_2330.short_balance == 800
    assert rec_2330.short_change == -200    # 融券回補
    assert rec_2330.source == "finmind"

    rec_2317 = db.query(MarginTrade).filter_by(stock_id="2317").one()
    assert rec_2317.margin_balance == 5000
    assert rec_2317.margin_change == -500   # 散戶融資減碼
    assert rec_2317.short_balance == 200
    assert rec_2317.short_change == 50      # 融券增加


def test_margin_trade_upsert_overwrites_existing_row(db):
    trade_date = date(2026, 4, 21)

    # 先 insert 一筆舊資料（模擬上一輪 ETL）
    db.add(
        MarginTrade(
            trade_date=trade_date,
            stock_id="2330",
            margin_balance=999,
            margin_change=999,
            short_balance=999,
            short_change=999,
            source="manual",
        )
    )
    db.commit()

    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(
            df=pd.DataFrame(
                [
                    {
                        "date": "2026-04-21",
                        "stock_id": "2330",
                        "MarginPurchaseTodayBalance": 12000,
                        "MarginPurchaseYesterdayBalance": 10000,
                        "ShortSaleTodayBalance": 800,
                        "ShortSaleYesterdayBalance": 1000,
                    }
                ]
            )
        ),
    )

    assert result["status"] == "ok"

    # 確認沒有產生重複 row
    rows = db.query(MarginTrade).filter_by(stock_id="2330").all()
    assert len(rows) == 1
    assert rows[0].margin_balance == 12000
    assert rows[0].source == "finmind"


def test_margin_trade_empty_returns_no_data(db):
    trade_date = date(2026, 4, 21)

    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(df=pd.DataFrame()),
    )

    assert result["status"] == "no_data"
    assert result["upserted"] == 0
    assert db.query(MarginTrade).count() == 0


def test_margin_trade_quota_error_returns_insufficient_quota(db):
    class QuotaExhaustedClient:
        def fetch_margin_purchase_short_sale(self, **kwargs):
            raise RuntimeError("Insufficient quota or critical state")

    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=date(2026, 4, 21),
        end_date=date(2026, 4, 21),
        client=QuotaExhaustedClient(),
    )

    assert result["status"] == "insufficient_quota"
    assert result["upserted"] == 0


def test_margin_trade_handles_nan_balances(db):
    """FinMind 偶有缺欄位時應寫 NULL 不 crash。"""
    trade_date = date(2026, 4, 21)
    df = pd.DataFrame(
        [
            {
                "date": "2026-04-21",
                "stock_id": "2330",
                "MarginPurchaseTodayBalance": 12000,
                "MarginPurchaseYesterdayBalance": None,
                "ShortSaleTodayBalance": None,
                "ShortSaleYesterdayBalance": 1000,
            }
        ]
    )

    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(df=df),
    )

    assert result["status"] == "ok"
    rec = db.query(MarginTrade).filter_by(stock_id="2330").one()
    assert rec.margin_balance == 12000
    assert rec.margin_change is None    # today - NaN → NaN → None
    assert rec.short_balance is None
    assert rec.short_change is None
