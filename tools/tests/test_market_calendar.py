import pandas as pd

from tools.market_calendar import OUTCOME_LABELS, build_feature_panel, classify_session


def _ohlc(rows):
    return pd.DataFrame(rows, columns=["trade_date", "open_price", "high_price", "low_price", "close_price"])


def test_classify_session_covers_requested_shapes():
    assert classify_session(pd.Series({"gap_pct": 5.5, "ret_1d": 6.0, "intraday_pct": 1.8, "close_location": 0.9})) == OUTCOME_LABELS[0]
    assert classify_session(pd.Series({"gap_pct": -1.5, "ret_1d": 3.5, "intraday_pct": 5.1, "close_location": 0.8})) == OUTCOME_LABELS[1]
    assert classify_session(pd.Series({"gap_pct": 5.5, "ret_1d": 2.0, "intraday_pct": -3.3, "close_location": 0.4})) == OUTCOME_LABELS[2]
    assert classify_session(pd.Series({"gap_pct": -0.4, "ret_1d": 0.3, "intraday_pct": 1.2, "close_location": 0.8})) == OUTCOME_LABELS[3]
    assert classify_session(pd.Series({"gap_pct": 0.1, "ret_1d": 0.3, "intraday_pct": 0.2, "close_location": 0.5})) == OUTCOME_LABELS[4]
    assert classify_session(pd.Series({"gap_pct": 0.0, "ret_1d": -3.5, "intraday_pct": -3.5, "close_location": 0.2})) == OUTCOME_LABELS[5]
    assert classify_session(pd.Series({"gap_pct": -1.0, "ret_1d": -0.8, "intraday_pct": -0.1, "close_location": 0.3})) == OUTCOME_LABELS[6]


def test_feature_panel_lags_target_to_next_session():
    frame = _ohlc([
        ("2026-07-15", 100, 102, 99, 101),
        ("2026-07-16", 100, 103, 99, 102),
        ("2026-07-17", 101, 102, 98, 99),
        ("2026-07-20", 98, 101, 97, 100),
        ("2026-07-21", 100, 100, 95, 96),
        ("2026-07-22", 95, 98, 94, 97),
        ("2026-07-23", 97, 99, 96, 98),
        ("2026-07-24", 98, 101, 97, 100),
        ("2026-07-27", 100, 103, 99, 102),
        ("2026-07-28", 102, 104, 101, 103),
    ])
    result = build_feature_panel(frame)
    assert result.loc[0, "target_date"] == pd.Timestamp("2026-07-16")
    assert result.loc[0, "target_label"] == result.loc[1, "session_label"]
    assert pd.isna(result.loc[len(result) - 1, "target_label"])
