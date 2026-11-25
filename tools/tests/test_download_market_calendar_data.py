from datetime import date

from tools.download_market_calendar_data import _date_range


def test_date_range_is_inclusive():
    assert list(_date_range(date(2026, 7, 15), date(2026, 7, 17))) == [
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
    ]
