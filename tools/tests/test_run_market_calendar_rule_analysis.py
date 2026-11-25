import pandas as pd

from tools.run_market_calendar_rule_analysis import _accuracy


def test_accuracy_ignores_missing_targets():
    actual = pd.Series(["走高", "震盪", None])
    predicted = pd.Series(["走高", "走低", "走高"])
    correct, total, score = _accuracy(actual, predicted)
    assert (correct, total) == (1, 2)
    assert score == 0.5
