import pandas as pd

from tools.download_market_calendar_overnight import _month_ranges


def test_month_ranges_split_at_month_boundaries():
    result = list(_month_ranges(pd.Timestamp("2026-08-03").date(), pd.Timestamp("2026-10-02").date()))
    assert result == [
        (pd.Timestamp("2026-08-03").date(), pd.Timestamp("2026-08-31").date()),
        (pd.Timestamp("2026-09-01").date(), pd.Timestamp("2026-09-30").date()),
        (pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-02").date()),
    ]
