import pandas as pd
import pytest

from tools.market_calendar_rules import (
    _three_way_label,
    add_factor_states,
    binary_label_from_return,
    binary_prediction_with_momentum,
    build_rule_panel,
    expected_return_with_overnight,
    fit_overnight_return_model,
)
from tools.market_calendar import _effective_change, _rolling_z


def test_three_way_label_uses_requested_broad_cutoffs():
    assert _three_way_label(3.0, 0.0) == "走高"
    assert _three_way_label(1.0, 6.0) == "走高"
    assert _three_way_label(0.0, 0.0) == "震盪"
    assert _three_way_label(-3.0, 0.0) == "走低"


def test_binary_label_collapses_by_return_sign():
    assert binary_label_from_return(0.0) == "偏漲"
    assert binary_label_from_return(-0.01) == "偏跌"
    assert binary_prediction_with_momentum(pd.Series({"ret_1d": 1.0}), 0.1) == "偏漲"
    assert binary_prediction_with_momentum(pd.Series({"ret_1d": -1.0}), 0.1) == "偏跌"
    assert binary_prediction_with_momentum(
        pd.Series({"tx_night_return_pct": 0.4, "us_nq_change": 0.0, "ret_1d": -2.0}),
        -1.0,
    ) == "偏漲"
    assert binary_prediction_with_momentum(
        pd.Series({
            "tw_vix_change": 5.5,
            "us_sox_change": -3.5,
            "ret_3d": -6.0,
            "close_vs_ma20": -0.08,
            "tx_night_return_pct": 1.0,
            "us_nq_change": 1.0,
        }),
        1.0,
    ) == "偏跌"
    assert binary_prediction_with_momentum(
        pd.Series({
            "ret_3d": 2.7,
            "ret_1d": -0.4,
            "kdj_j": 87.0,
            "tx_night_return_pct": -0.1,
            "us_nq_change": -0.3,
            "tw_vix_change": 0.8,
        }),
        -0.2,
    ) == "偏漲"


def test_factor_states_are_low_middle_high():
    source = pd.DataFrame({"signal": [1.0, 2.0, 3.0, 4.0, 5.0]})
    result = add_factor_states(source, {"signal": (2.0, 4.0)})
    assert result["signal__state"].tolist() == ["低", "低", "中", "高", "高"]


def test_rule_panel_lags_next_return():
    raw = pd.DataFrame(
        [
            ["2026-08-%02d" % day, 100 + day, 101 + day, 99 + day, 100 + day]
            for day in range(1, 24)
        ],
        columns=["trade_date", "open_price", "high_price", "low_price", "close_price"],
    )
    panel = build_rule_panel(raw)
    assert panel.loc[0, "target_date"] == pd.Timestamp("2026-08-02")
    assert panel.loc[len(panel) - 1, "target_broad"] is None


def test_overnight_session_is_joined_to_next_target_date():
    raw = pd.DataFrame(
        [
            ["2026-08-%02d" % day, 100 + day, 101 + day, 99 + day, 100 + day]
            for day in range(1, 24)
        ],
        columns=["trade_date", "open_price", "high_price", "low_price", "close_price"],
    )
    overnight = pd.DataFrame(
        [
            {
                "trade_date": "2026-08-02",
                "tx_night_open": 100,
                "tx_night_high": 103,
                "tx_night_low": 99,
                "tx_night_close": 102,
                "tx_night_volume": 100,
                "mtx_night_open": 100,
                "mtx_night_high": 103,
                "mtx_night_low": 99,
                "mtx_night_close": 102,
                "mtx_night_volume": 100,
            }
        ]
    )
    panel = build_rule_panel(raw, overnight=overnight)
    assert round(panel.loc[0, "tx_night_return_pct"], 6) == round((102 / 101 - 1) * 100, 6)


def test_external_changes_use_previous_valid_observations():
    source = pd.Series([4.0, None, 4.2, 4.1, 4.3])
    result = _effective_change(source)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(0.2)
    assert result.iloc[4] == pytest.approx(0.2)


def test_vix_z_score_requires_twenty_valid_observations():
    source = pd.Series([float(index) for index in range(1, 21)] + [None, 22.0])
    result = _rolling_z(source, window=20)
    assert pd.isna(result.iloc[18])
    assert pd.notna(result.iloc[19])
    assert pd.isna(result.iloc[20])
    assert pd.notna(result.iloc[21])


def test_overnight_return_model_changes_numeric_forecast_and_keeps_sign_coherent():
    history = pd.DataFrame(
        {
            "tx_night_return_pct": [-1.0, -0.5, 0.5, 1.0] * 5,
            "us_nq_change": [-0.5, -0.25, 0.25, 0.5] * 5,
            "target_return": [-2.0, -1.0, 1.0, 2.0] * 5,
        }
    )
    model = fit_overnight_return_model(history)
    result = expected_return_with_overnight(
        pd.Series({"tx_night_return_pct": 1.0, "us_nq_change": 0.5}),
        rule_return=-0.2,
        final_binary="偏漲",
        overnight_model=model,
    )
    assert result > 0.0
    assert result != pytest.approx(-0.2)
