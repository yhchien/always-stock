import pandas as pd

from tools.report_market_calendar_data_quality import build_report


def test_report_lists_missing_source_and_derived_values():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-11-23", "2026-11-24"],
            "us_vix_change": [0.2, None],
            "source_metadata": [
                '{"us_vix": {"status": "available", "missing_reason": null}}',
                '{"us_vix": {"status": "missing", "missing_reason": "source holiday"}}',
            ],
        }
    )
    report = build_report(frame, recent=2)
    assert "us_vix" in report
    assert "source holiday" in report
    assert "2026-11-24" in report
