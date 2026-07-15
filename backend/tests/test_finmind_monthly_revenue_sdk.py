from datetime import date

import pandas as pd

from etl.finmind_monthly_revenue_sdk import (
    _pick_numeric_series,
    _resolve_revenue_month_series,
    _to_month_end,
)


def test_to_month_end_supports_yyyy_mm_and_yyyy_mm_dd():
    assert _to_month_end("2026-03") == date(2026, 3, 31)
    assert _to_month_end("2026-03-15") == date(2026, 3, 31)


def test_pick_numeric_series_uses_first_available_column():
    df = pd.DataFrame(
        {
            "stock_id": ["1802", "2330"],
            "YoY": ["10.5", "-3.2"],
        }
    )
    series = _pick_numeric_series(df, ["revenue_year_difference_per", "YoY", "yoy"])
    assert list(series.round(1)) == [10.5, -3.2]


def test_resolve_revenue_month_series_prefers_revenue_month_over_announcement_date():
    df = pd.DataFrame(
        {
            "stock_id": ["2330"],
            "revenue_year": ["2026"],
            "revenue_month": ["2026-01"],
            "date": ["2026-02-10"],
        }
    )
    series = _resolve_revenue_month_series(df)
    assert list(series) == [date(2026, 1, 31)]


class _FakeClient:
    """紀錄 fetch 參數的假 client。"""

    def __init__(self, df):
        self.df = df
        self.calls = []

    def fetch_month_revenue_dataset(self, start_date, end_date):
        self.calls.append((start_date, end_date))
        return self.df


def test_month_first_days_between():
    from etl.finmind_monthly_revenue_sdk import _month_first_days_between

    # start 非 1 號 → 從次月 1 號開始
    assert _month_first_days_between(date(2026, 5, 31), date(2026, 7, 15)) == [
        date(2026, 6, 1),
        date(2026, 7, 1),
    ]
    # start 是 1 號 → 含當月；跨年
    assert _month_first_days_between(date(2025, 12, 1), date(2026, 2, 1)) == [
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    # 區間內無 1 號 → 空
    assert _month_first_days_between(date(2026, 7, 2), date(2026, 7, 15)) == []


def test_fetch_calls_each_month_key_within_45_day_lookback(db):
    """2026-07-15 根因修正：FinMind 把 N 月營收全掛在 N+1 月 1 號、dataset-level
    只回 start_date 當日、且公告陸續補進 → 必須對回看 45 天內每個「月 1 號」
    date key 各打一次（start=end=key）。"""
    from etl.finmind_monthly_revenue_sdk import fetch_and_upsert_monthly_revenue_sdk

    df = pd.DataFrame(
        [
            {"date": "2026-07-01", "stock_id": "2330", "revenue": 1000,
             "revenue_year": 2026, "revenue_month": 6},
            {"date": "2026-07-01", "stock_id": "2317", "revenue": 2000,
             "revenue_year": 2026, "revenue_month": 6},
        ]
    )
    client = _FakeClient(df)
    result = fetch_and_upsert_monthly_revenue_sdk(
        db, ["2330", "2317"], date(2026, 7, 15), date(2026, 7, 15), client
    )

    assert result["status"] == "ok"
    assert result["upserted"] == 4  # fake client 每個 key 都回 2 筆，upsert 冪等去重後 DB 仍 2 筆
    # 回看 45 天（2026-05-31 起）內的月 key：6/1、7/1，各打一次 start=end=key
    assert client.calls == [
        ("2026-06-01", "2026-06-01"),
        ("2026-07-01", "2026-07-01"),
    ]

    from sqlalchemy import text as sql_text

    rows = db.execute(
        sql_text("SELECT stock_id, revenue_month FROM monthly_revenue ORDER BY stock_id")
    ).fetchall()
    assert [(r[0], str(r[1])) for r in rows] == [
        ("2317", "2026-06-30"),
        ("2330", "2026-06-30"),
    ]
