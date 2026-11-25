from datetime import date

import pandas as pd
from tools import download_market_calendar_data as downloader
from tools.download_market_calendar_data import (
    _date_range,
    align_external_observations,
    fetch_cbc_usd_twd,
)


def test_date_range_is_inclusive():
    assert list(_date_range(date(2026, 7, 15), date(2026, 7, 17))) == [
        date(2026, 7, 15),
        date(2026, 7, 16),
        date(2026, 7, 17),
    ]


def test_cbc_usd_twd_parses_compact_dates(monkeypatch):
    class Response:
        content = "\ufeff日期,NTD/USD\n20261123,31.25\n20261124,31.30\n".encode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(downloader.requests, "get", lambda *args, **kwargs: Response())
    result = fetch_cbc_usd_twd(date(2026, 11, 23), date(2026, 11, 24))
    assert result.to_dict("records") == [
        {"trade_date": "2026-11-23", "usd_twd": 31.25},
        {"trade_date": "2026-11-24", "usd_twd": 31.30},
    ]


def test_mtx_month_ranges_are_exposed_for_completed_month_requests():
    assert list(downloader._month_ranges(date(2026, 7, 28), date(2026, 9, 2))) == [
        (date(2026, 7, 28), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 2)),
    ]


def test_external_observation_is_attached_to_next_taiwan_session():
    source = pd.DataFrame(
        {"trade_date": ["2026-11-20", "2026-11-23"], "value": [1.0, 2.0]}
    )
    result = align_external_observations(
        source,
        ["2026-11-20", "2026-11-23", "2026-11-24"],
        "observed_date",
    )
    assert result[["trade_date", "observed_date"]].to_dict("records") == [
        {"trade_date": "2026-11-23", "observed_date": "2026-11-20"},
        {"trade_date": "2026-11-24", "observed_date": "2026-11-23"},
    ]
