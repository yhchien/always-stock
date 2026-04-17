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
