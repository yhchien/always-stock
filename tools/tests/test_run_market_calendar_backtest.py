import pandas as pd

from tools.market_calendar import build_feature_panel
from tools.run_market_calendar_backtest import walk_forward_predictions


def test_walk_forward_does_not_predict_before_minimum_training_window():
    rows = []
    for index in range(36):
        value = 100.0 + index
        rows.append({
            "trade_date": pd.Timestamp("2026-07-01") + pd.Timedelta(days=index),
            "open_price": value,
            "high_price": value + 2.0,
            "low_price": value - 2.0,
            "close_price": value + 1.0,
        })
    panel = build_feature_panel(pd.DataFrame(rows))
    result = walk_forward_predictions(panel, {"technical": 1.0, "institutional": 0.0, "vix": 0.0, "futures": 0.0, "macro": 0.0})
    assert len(result) == 5
    assert result.iloc[0]["signal_date"] == panel.iloc[30]["trade_date"].date().isoformat()
