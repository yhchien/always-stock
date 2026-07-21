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


def test_prior_month_end():
    from etl.finmind_monthly_revenue_sdk import _prior_month_end

    assert _prior_month_end(date(2026, 6, 30), 1) == date(2026, 5, 31)
    assert _prior_month_end(date(2026, 6, 30), 12) == date(2025, 6, 30)
    assert _prior_month_end(date(2026, 1, 31), 1) == date(2025, 12, 31)


def test_recompute_missing_yoy_mom_uses_db_history_not_fetch_frame(db):
    """2026-07-21 根因修正 regression：yoy_pct 必須查 DB 既有歷史（回溯到去年同期），
    不能依賴只有 45 天窗的 fetch frame（frame 內 shift(12) 恆為 NaN，導致全市場全月份
    yoy_pct=NULL 的既有 bug）。"""
    from etl.finmind_monthly_revenue_sdk import recompute_missing_yoy_mom
    from sqlalchemy import text as sql_text

    # 去年同期 + 上個月營收（已存在於 DB，代表歷史資料）
    db.execute(sql_text("""
        INSERT INTO monthly_revenue (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source)
        VALUES
            ('2025-06-30', '2330', 100000, NULL, NULL, 'finmind'),
            ('2026-05-31', '2330', 180000, NULL, NULL, 'finmind'),
            ('2026-06-30', '2330', 200000, NULL, NULL, 'finmind')
    """))
    db.commit()

    updated = recompute_missing_yoy_mom(db, ["2330"], [date(2026, 6, 30)])
    assert updated == 1

    row = db.execute(sql_text(
        "SELECT yoy_pct, mom_pct FROM monthly_revenue WHERE stock_id='2330' AND revenue_month='2026-06-30'"
    )).fetchone()
    assert row.yoy_pct == 100.0  # 200000/100000 - 1 = 100%
    assert abs(row.mom_pct - 11.111111) < 1e-4  # 200000/180000 - 1 ≈ 11.11%


def test_recompute_missing_yoy_mom_leaves_null_when_no_prior_year_data(db):
    """新上市（無去年同期）不可幻想；應維持 NULL，不寫入猜測值。"""
    from etl.finmind_monthly_revenue_sdk import recompute_missing_yoy_mom
    from sqlalchemy import text as sql_text

    db.execute(sql_text("""
        INSERT INTO monthly_revenue (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source)
        VALUES ('2026-06-30', '9999', 50000, NULL, NULL, 'finmind')
    """))
    db.commit()

    updated = recompute_missing_yoy_mom(db, ["9999"], [date(2026, 6, 30)])
    assert updated == 0

    row = db.execute(sql_text(
        "SELECT yoy_pct, mom_pct FROM monthly_revenue WHERE stock_id='9999'"
    )).fetchone()
    assert row.yoy_pct is None
    assert row.mom_pct is None


def test_recompute_missing_yoy_mom_does_not_overwrite_existing_value(db):
    """已有值（不論來源）不覆寫——COALESCE 語意保護既有正確資料。"""
    from etl.finmind_monthly_revenue_sdk import recompute_missing_yoy_mom
    from sqlalchemy import text as sql_text

    db.execute(sql_text("""
        INSERT INTO monthly_revenue (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source)
        VALUES
            ('2025-06-30', '2330', 100000, NULL, NULL, 'finmind'),
            ('2026-06-30', '2330', 200000, 42.0, NULL, 'finmind')
    """))
    db.commit()

    updated = recompute_missing_yoy_mom(db, ["2330"], [date(2026, 6, 30)])
    assert updated == 1  # mom 仍需補（沒有上個月資料所以留 NULL），但函式仍會跑一次

    row = db.execute(sql_text(
        "SELECT yoy_pct FROM monthly_revenue WHERE stock_id='2330' AND revenue_month='2026-06-30'"
    )).fetchone()
    assert row.yoy_pct == 42.0  # 原值不被回算覆寫


def test_fetch_and_upsert_recalculates_yoy_from_db_history(db):
    """端到端 regression：daily ETL 只抓到 45 天窗內資料（無去年同期），但 DB 已有
    去年同期歷史時，yoy_pct 應該算得出來——這是本次修復的核心行為。"""
    from etl.finmind_monthly_revenue_sdk import fetch_and_upsert_monthly_revenue_sdk
    from sqlalchemy import text as sql_text

    # 預先寫入去年同期資料（模擬 DB 已有的歷史，不在本次 fetch frame 內）
    db.execute(sql_text("""
        INSERT INTO monthly_revenue (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source)
        VALUES ('2025-06-30', '2330', 100000, NULL, NULL, 'finmind')
    """))
    db.commit()

    df = pd.DataFrame([
        {"date": "2026-07-01", "stock_id": "2330", "revenue": 150000,
         "revenue_year": 2026, "revenue_month": 6},
    ])
    client = _FakeClient(df)
    result = fetch_and_upsert_monthly_revenue_sdk(
        db, ["2330"], date(2026, 7, 15), date(2026, 7, 15), client
    )

    assert result["status"] == "ok"
    assert result["yoy_mom_recalculated"] >= 1

    row = db.execute(sql_text(
        "SELECT yoy_pct FROM monthly_revenue WHERE stock_id='2330' AND revenue_month='2026-06-30'"
    )).fetchone()
    assert row.yoy_pct == 50.0  # 150000/100000 - 1 = 50%
