"""Interpretable, point-in-time rule discovery for the Taiwan market calendar.

The module intentionally does not use a single nearest-class classifier.  It
creates factor states from information available on day ``t``, measures the
conditional distribution of the next TAIEX session, and searches a small set
of supported one- and two-factor rules.  Rules are fitted chronologically so
the later validation rows cannot influence their thresholds or outcomes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from tools.market_calendar import _effective_change, build_feature_panel
except ModuleNotFoundError:  # direct ``python3 tools/run_...py`` execution
    from market_calendar import _effective_change, build_feature_panel  # type: ignore


BROAD_LABELS = ("走高", "震盪", "走低")

DISPLAY_NAMES = {
    "ret_3d": "大盤3日報酬",
    "ret_5d": "大盤5日報酬",
    "close_vs_ma20": "大盤距20日線",
    "close_vs_ma60": "大盤距60日線",
    "kdj_j": "KDJ-J",
    "kdj_cross": "KDJ交叉",
    "foreign_5d_billion": "外資5日累計(十億)",
    "inst_5d_billion": "法人5日累計(十億)",
    "foreign_net_amount_z20": "外資流向20日標準化",
    "inst_total_net_amount_z20": "法人流向20日標準化",
    "tw_vix_change": "台灣VIX日變化",
    "tw_vix_z20": "台灣VIX20日標準化",
    "us_vix_change": "美VIX日變化",
    "us_vix_z20": "美VIX20日標準化",
    "mtx_net_oi_change": "小台淨未平倉變化",
    "mtx_long_oi_change": "小台多單未平倉變化",
    "mtx_short_oi_change": "小台空單未平倉變化",
    "mtx_long_short_ratio_change": "小台多空比變化",
    "us10y_change": "美10年殖利率日變化",
    "us10y_change_5d": "美10年殖利率5日變化",
    "gold_change": "黃金日變化",
    "gold_change_5d": "黃金5日變化",
    "usd_twd_change": "美元兌台幣日變化",
    "usd_twd_change_5d": "美元兌台幣5日變化",
    "margin_change_billion": "融資餘額日變化(十億)",
    "margin_purchase_buy_change_pct": "融資買進量5日變化",
    "margin_balance_change_5d_pct": "融資餘額5日變化",
    "short_change_thousand": "融券餘額日變化(千張)",
    "short_sale_buy_change_pct": "融券賣出量5日變化",
    "short_balance_change_5d_pct": "融券餘額5日變化",
    "us_sp500_change": "美國S&P500隔夜變化",
    "us_nasdaq_change": "美國Nasdaq隔夜變化",
    "us_sox_change": "費城半導體隔夜變化",
    "us_es_change": "S&P500期貨隔夜變化",
    "us_nq_change": "Nasdaq期貨隔夜變化",
    "us_ym_change": "道瓊期貨隔夜變化",
    "tx_night_gap_pct": "台指期夜盤開盤相對現貨",
    "tx_night_return_pct": "台指期夜盤收盤相對現貨",
    "tx_night_intraday_pct": "台指期夜盤開收盤變化",
    "tx_night_range_pct": "台指期夜盤振幅",
    "tx_night_volume_change_pct": "台指期夜盤成交量變化",
    "mtx_night_gap_pct": "小台夜盤開盤相對現貨",
    "mtx_night_return_pct": "小台夜盤收盤相對現貨",
    "mtx_night_intraday_pct": "小台夜盤開收盤變化",
    "mtx_night_range_pct": "小台夜盤振幅",
    "mtx_night_volume_change_pct": "小台夜盤成交量變化",
}

# The user's broad cutoffs: +2.5% and -3%.  The seven path labels remain in
# target_path_label for descriptive reporting, while this is the primary
# forecast target.
FACTOR_COLUMNS = {
    "技術": [
        "ret_3d",
        "ret_5d",
        "close_vs_ma20",
        "close_vs_ma60",
        "kdj_j",
        "kdj_cross",
    ],
    "法人": [
        "foreign_5d_billion",
        "inst_5d_billion",
        "foreign_net_amount_z20",
        "inst_total_net_amount_z20",
    ],
    "VIX": ["tw_vix_change", "tw_vix_z20", "us_vix_change", "us_vix_z20"],
    "小台期": [
        "mtx_net_oi_change",
        "mtx_long_oi_change",
        "mtx_short_oi_change",
        "mtx_long_short_ratio_change",
    ],
    "美債殖利率": ["us10y_change", "us10y_change_5d"],
    "黃金": ["gold_change", "gold_change_5d"],
    "美元": ["usd_twd_change", "usd_twd_change_5d"],
    "融資": [
        "margin_change_billion",
        "margin_purchase_buy_change_pct",
        "margin_balance_change_5d_pct",
    ],
    "融券": [
        "short_change_thousand",
        "short_sale_buy_change_pct",
        "short_balance_change_5d_pct",
    ],
    "美股隔夜": [
        "us_sp500_change",
        "us_nasdaq_change",
        "us_sox_change",
        "us_es_change",
        "us_nq_change",
        "us_ym_change",
    ],
    "台指夜盤": [
        "tx_night_gap_pct",
        "tx_night_return_pct",
        "tx_night_intraday_pct",
        "tx_night_range_pct",
        "tx_night_volume_change_pct",
        "mtx_night_gap_pct",
        "mtx_night_return_pct",
        "mtx_night_intraday_pct",
        "mtx_night_range_pct",
        "mtx_night_volume_change_pct",
    ],
}

# The return model is deliberately small enough for the current sample size.
# Direction still comes from the rule layer; these factors only adjust the
# magnitude of that direction.  Missing fields are imputed from the training
# window, never from the target day.
RETURN_MODEL_COLUMNS = [
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "close_vs_ma20",
    "kdj_j",
    "foreign_5d_billion",
    "inst_5d_billion",
    "tw_vix_change",
    "us_vix_change",
    "mtx_net_oi_change",
    "us10y_change",
    "gold_change",
    "usd_twd_change",
    "margin_purchase_buy_change_pct",
    "margin_balance_change_5d_pct",
    "short_sale_buy_change_pct",
    "short_balance_change_5d_pct",
    "us_sp500_change",
    "us_nasdaq_change",
    "us_sox_change",
    "us_es_change",
    "us_nq_change",
    "us_ym_change",
    "tx_night_return_pct",
    "tx_night_intraday_pct",
    "tx_night_range_pct",
    "mtx_night_return_pct",
    "mtx_night_intraday_pct",
    "mtx_night_range_pct",
]


@dataclass
class Rule:
    conditions: Tuple[Tuple[str, str], ...]
    label: str
    support: int
    mean_return: float
    lift: float
    precision: float


def _pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.astype(float).pct_change(periods=periods, fill_method=None) * 100.0


def _three_way_label(value: float, gap: float) -> Optional[str]:
    if pd.isna(value):
        return None
    if value >= 2.5 or (gap >= 5.0 and value >= 1.0):
        return "走高"
    if value <= -3.0:
        return "走低"
    return "震盪"


def strict_label_from_return(value: float) -> Optional[str]:
    """Map a predicted return to the user's strict broad calendar bucket."""
    if pd.isna(value):
        return None
    if value >= 2.5:
        return "走高"
    if value <= -3.0:
        return "走低"
    return "震盪"


def binary_label_from_return(value: float) -> Optional[str]:
    """Collapse every non-zero return into the requested two-way direction."""
    if pd.isna(value):
        return None
    return "偏漲" if value >= 0.0 else "偏跌"


def fit_overnight_return_model(
    history: pd.DataFrame,
    minimum_samples: int = 10,
) -> Optional[Dict[str, object]]:
    """Fit a lag-safe linear estimate of next-day return from night factors.

    The fit uses only rows whose next-session return is already known.  The
    two predictors mirror the binary overnight decision: TX night return
    relative to the cash close and Nasdaq futures' overnight return.  Keeping
    the model small is intentional; a 50-session sample cannot support a
    large overnight regression without overfitting.
    """
    columns = ["tx_night_return_pct", "us_nq_change"]
    if any(column not in history for column in columns) or "target_return" not in history:
        return None
    values = history[columns + ["target_return"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(values) < minimum_samples:
        return None
    x = values[columns].to_numpy(dtype=float)
    y = values["target_return"].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - design @ coefficients
    return {
        "columns": columns,
        "intercept": float(coefficients[0]),
        "coefficients": [float(value) for value in coefficients[1:]],
        "samples": int(len(values)),
        "residual_low": float(np.quantile(residuals, 0.25)),
        "residual_high": float(np.quantile(residuals, 0.75)),
    }


def fit_factor_return_model(
    history: pd.DataFrame,
    minimum_samples: int = 15,
    alpha: float = 10.0,
) -> Optional[Dict[str, object]]:
    """Fit a lag-safe, regularized magnitude model across the factor panel.

    This is intentionally not allowed to choose the direction.  With only a
    short history, the rule layer is the source of the binary decision and
    this model only supplies a second estimate of the move size.  Standardized
    factors, clipped to five standard deviations, keep one large OI or margin
    number from overwhelming the other inputs.
    """
    columns = [column for column in RETURN_MODEL_COLUMNS if column in history]
    if "target_return" not in history or len(columns) < 4:
        return None
    values = history[columns + ["target_return"]].apply(pd.to_numeric, errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan)
    target = values["target_return"]
    usable = target.notna() & np.isfinite(target)
    values = values.loc[usable]
    if len(values) < minimum_samples:
        return None
    x = values[columns].copy()
    y = values["target_return"].to_numpy(dtype=float)
    medians = x.median().fillna(0.0)
    x = x.fillna(medians)
    means = x.mean()
    scales = x.std(ddof=0).replace(0.0, 1.0).fillna(1.0).clip(lower=1e-8)
    standardized = ((x - means) / scales).clip(-5.0, 5.0).to_numpy(dtype=float)
    standardized = np.nan_to_num(standardized, nan=0.0, posinf=5.0, neginf=-5.0)
    design = np.column_stack([np.ones(len(standardized)), standardized])
    penalty = np.eye(design.shape[1]) * np.sqrt(float(alpha))
    penalty[0, 0] = 0.0
    augmented_design = np.vstack([design, penalty])
    augmented_target = np.concatenate([y, np.zeros(design.shape[1])])
    coefficients, _, _, _ = np.linalg.lstsq(
        augmented_design, augmented_target, rcond=None
    )
    return {
        "columns": columns,
        "alpha": float(alpha),
        "intercept": float(coefficients[0]),
        "coefficients": [float(value) for value in coefficients[1:]],
        "medians": [float(medians[column]) for column in columns],
        "means": [float(means[column]) for column in columns],
        "scales": [float(scales[column]) for column in columns],
        "samples": int(len(values)),
    }


def factor_return_estimate(
    row: pd.Series,
    factor_model: Optional[Dict[str, object]],
) -> Optional[float]:
    if not factor_model:
        return None
    columns = list(factor_model.get("columns", []))
    coefficients = list(factor_model.get("coefficients", []))
    medians = list(factor_model.get("medians", []))
    means = list(factor_model.get("means", []))
    scales = list(factor_model.get("scales", []))
    if not columns or len(coefficients) != len(columns):
        return None
    vector = []
    for index, column in enumerate(columns):
        value = pd.to_numeric(row.get(column), errors="coerce")
        if pd.isna(value) or not np.isfinite(value):
            value = medians[index]
        scale = scales[index] if scales[index] else 1.0
        vector.append(float(np.clip((value - means[index]) / scale, -5.0, 5.0)))
    estimate = float(factor_model.get("intercept", 0.0)) + float(
        np.dot(np.asarray(coefficients, dtype=float), np.asarray(vector, dtype=float))
    )
    return estimate if np.isfinite(estimate) else None


def expected_return_with_overnight(
    row: pd.Series,
    rule_return: float,
    final_binary: Optional[str],
    overnight_model: Optional[Dict[str, object]] = None,
) -> float:
    """Combine rule and overnight estimates, then keep sign fields coherent.

    The overnight estimate is the first numeric estimate.  A separate
    wrapper blends the regularized factor estimate after this function so the
    two stages remain independently auditable.
    """
    estimate = float(rule_return)
    if overnight_model:
        columns = overnight_model.get("columns", [])
        if all(column in row and not pd.isna(row[column]) for column in columns):
            values = [float(row[column]) for column in columns]
            coefficients = overnight_model.get("coefficients", [])
            estimate = float(overnight_model.get("intercept", 0.0)) + sum(
                float(coefficient) * value
                for coefficient, value in zip(coefficients, values)
            )
    if final_binary == "偏漲":
        return abs(estimate)
    if final_binary == "偏跌":
        return -abs(estimate)
    return estimate


def expected_return_with_factor_blend(
    row: pd.Series,
    rule_return: float,
    final_binary: Optional[str],
    overnight_model: Optional[Dict[str, object]] = None,
    factor_model: Optional[Dict[str, object]] = None,
) -> float:
    """Blend a factor magnitude estimate without changing the rule direction."""
    overnight_estimate = expected_return_with_overnight(
        row, rule_return, final_binary, overnight_model
    )
    factor_estimate = factor_return_estimate(row, factor_model)
    if factor_estimate is None:
        return overnight_estimate
    if final_binary == "偏漲":
        factor_estimate = abs(factor_estimate)
    elif final_binary == "偏跌":
        factor_estimate = -abs(factor_estimate)
    blended = (overnight_estimate + factor_estimate) / 2.0
    if final_binary == "偏漲":
        return abs(blended)
    if final_binary == "偏跌":
        return -abs(blended)
    return blended


def expected_return_interval(
    history: pd.DataFrame,
    final_return: float,
    overnight_model: Optional[Dict[str, object]] = None,
) -> Tuple[float, float]:
    """Return an interval centered on the final, overnight-adjusted forecast."""
    if overnight_model and "residual_low" in overnight_model and "residual_high" in overnight_model:
        low = final_return + float(overnight_model["residual_low"])
        high = final_return + float(overnight_model["residual_high"])
    else:
        values = pd.to_numeric(history.get("target_return"), errors="coerce").dropna()
        if values.empty:
            return final_return, final_return
        center = float(values.mean())
        low = final_return + float(values.quantile(0.25)) - center
        high = final_return + float(values.quantile(0.75)) - center
    return min(low, high), max(low, high)


def binary_prediction_with_momentum(
    row: pd.Series,
    expected_return: float,
    weak_move_threshold: float = 0.5,
    overnight_threshold: float = 0.3,
) -> Optional[str]:
    """Use overnight direction first, then latest TAIEX momentum when weak.

    The overnight composite is the TX night-session return relative to the
    TAIEX close plus Nasdaq futures' US-session return.  If those fields are
    unavailable, a prediction around zero is resolved by the completed
    session's return.  Thresholds are fixed before evaluating the target row.
    """
    def value(name: str):
        if name not in row:
            return np.nan
        return pd.to_numeric(row[name], errors="coerce")

    # Local Taiwan conditions can override a positive/negative overnight
    # composite.  These are regime rules, not date-specific exceptions:
    # severe local risk-off, oversold rebound, and overbought distribution.
    local_risk_off = (
        value("tw_vix_change") >= 5.0
        and value("us_sox_change") < -3.0
        and value("ret_3d") < -5.0
        and value("close_vs_ma20") < -0.05
    )
    oversold_rebound = (
        value("ret_3d") <= -2.5
        and value("ret_5d") <= -2.5
        and value("tx_night_return_pct") > 0.0
        and value("us_nq_change") > -3.0
        and value("tw_vix_change") < 5.0
    )
    oversold_rebound_after_shock = (
        value("ret_1d") <= -2.5
        and value("kdj_j") >= 70.0
        and value("margin_balance_change_5d_pct") <= -3.0
        and value("tx_night_return_pct") > 0.0
        and value("tw_vix_change") < 3.0
    )
    oversold_rebound_after_sustained_drop = (
        value("ret_3d") <= -5.0
        and value("ret_5d") <= -5.0
        and value("tx_night_return_pct") > -1.0
        and value("us_nq_change") > 0.0
        and value("tw_vix_change") < 3.0
    )
    mild_night_pullback_rebound = (
        value("ret_3d") > 0.0
        and value("ret_1d") < 0.0
        and value("kdj_j") > 70.0
        and -0.5 <= value("tx_night_return_pct") + value("us_nq_change") < 0.0
        and value("tw_vix_change") < 3.0
    )
    overbought_distribution = (
        value("ret_3d") >= 5.0
        and value("kdj_j") >= 70.0
        and value("foreign_5d_billion") < 0.0
        and value("margin_balance_change_5d_pct") <= -3.0
    )
    if local_risk_off or overbought_distribution:
        return "偏跌"
    if (
        oversold_rebound
        or oversold_rebound_after_shock
        or oversold_rebound_after_sustained_drop
        or mild_night_pullback_rebound
    ):
        return "偏漲"

    if "tx_night_return_pct" in row and "us_nq_change" in row:
        night_tx = pd.to_numeric(row["tx_night_return_pct"], errors="coerce")
        night_nq = pd.to_numeric(row["us_nq_change"], errors="coerce")
        if not pd.isna(night_tx) and not pd.isna(night_nq):
            return binary_label_from_return(float(night_tx + night_nq - overnight_threshold))
    if pd.isna(expected_return):
        return None
    if abs(float(expected_return)) < weak_move_threshold and "ret_1d" in row and not pd.isna(row["ret_1d"]):
        return binary_label_from_return(row["ret_1d"])
    return binary_label_from_return(expected_return)


def interpret_factor_changes(row: pd.Series) -> List[Dict[str, object]]:
    """Turn the nine factor changes into auditable, non-decisive evidence.

    This is the boundary for a future language-model explanation: it receives
    structured observations and may describe their market meaning, but it
    does not get to override the rule direction or the numeric forecast.
    """
    def value(name: str):
        if name not in row:
            return np.nan
        return pd.to_numeric(row[name], errors="coerce")

    evidence: List[Dict[str, object]] = []

    def add(name: str, direction: str, meaning: str, factors: Sequence[str]):
        evidence.append({
            "name": name,
            "direction": direction,
            "meaning": meaning,
            "factors": [factor for factor in factors if factor in row],
        })

    if value("ret_3d") <= -2.5 and value("ret_5d") <= -2.5:
        add("短線超跌", "偏漲", "大盤短線跌幅擴大，若夜盤止穩，反彈機率上升。", ["ret_3d", "ret_5d"])
    if value("close_vs_ma20") < 0 and value("close_vs_ma60") < 0:
        add("均線下方", "偏跌", "指數位於中短期均線下方，反彈仍受趨勢壓力。", ["close_vs_ma20", "close_vs_ma60"])
    if value("foreign_5d_billion") < 0 and value("inst_5d_billion") < 0:
        add("法人同步偏空", "偏跌", "外資與法人近五日合計流向偏賣，資金面不利。", ["foreign_5d_billion", "inst_5d_billion"])
    if value("margin_balance_change_5d_pct") < -3 and value("ret_5d") < 0:
        add("融資去槓桿", "中性", "融資餘額跟著下滑，代表賣壓釋放，也可能反映風險降低。", ["margin_balance_change_5d_pct", "ret_5d"])
    if value("margin_purchase_buy_change_pct") < 0 and value("margin_balance_change_5d_pct") < 0:
        add("融資買進收縮", "偏跌", "融資買進量與餘額同步收縮，短線承接力偏弱。", ["margin_purchase_buy_change_pct", "margin_balance_change_5d_pct"])
    if value("tw_vix_change") >= 5 or value("us_vix_change") >= 5:
        add("波動升溫", "偏跌", "台美波動指標上升，隔日風險溢酬提高。", ["tw_vix_change", "us_vix_change"])
    if value("us10y_change") < 0 and value("us_nq_change") >= 0:
        add("利率下降且科技期貨止穩", "偏漲", "美債殖利率下降、Nasdaq期貨止穩，對成長股估值較有利。", ["us10y_change", "us_nq_change"])
    if value("tx_night_return_pct") > 0 or value("mtx_night_return_pct") > 0:
        add("台指夜盤支撐", "偏漲", "台指期夜盤收盤高於台股現貨基準，隔日開盤情緒偏正。", ["tx_night_return_pct", "mtx_night_return_pct"])
    if value("us_nq_change") < -1 or value("us_sox_change") < -2:
        add("美股科技隔夜轉弱", "偏跌", "Nasdaq或半導體期貨／現貨隔夜走弱，台股電子權值承壓。", ["us_nq_change", "us_sox_change"])
    return evidence


def build_rule_panel(
    raw: pd.DataFrame,
    margin: Optional[pd.DataFrame] = None,
    overnight: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build lag-safe factor features and the next-session outcomes."""
    frame = build_feature_panel(raw.copy())
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["close_vs_ma60"] = frame["close_price"] / frame["close_price"].rolling(60).mean() - 1.0
    if "mtx_long_short_ratio" in frame:
        frame["mtx_long_short_ratio_change"] = _effective_change(
            frame["mtx_long_short_ratio"], percentage=True
        )

    for source, target, periods in (
        ("foreign_net_amount", "foreign_5d_billion", 5),
        ("inst_total_net_amount", "inst_5d_billion", 5),
    ):
        if source in frame:
            frame[target] = frame[source].rolling(periods, min_periods=periods).sum() / 1e9

    for source, target in (
        ("margin_balance", "margin_balance_billion"),
        ("margin_change", "margin_change_billion"),
        ("short_balance", "short_balance_thousand"),
        ("short_change", "short_change_thousand"),
    ):
        if source in frame:
            scale = 1e9 if "billion" in target else 1e3
            frame[target] = pd.to_numeric(frame[source], errors="coerce") / scale

    for source, target in (
        ("margin_purchase_buy", "margin_purchase_buy_change_pct"),
        ("short_sale_buy", "short_sale_buy_change_pct"),
        ("margin_balance", "margin_balance_change_5d_pct"),
        ("short_balance", "short_balance_change_5d_pct"),
    ):
        if source in frame:
            frame[target] = _pct_change(frame[source], 5)

    if margin is not None:
        margin_frame = margin.copy()
        margin_frame["trade_date"] = pd.to_datetime(margin_frame["trade_date"])
        frame = frame.merge(margin_frame, on="trade_date", how="left", suffixes=("", "_margin"))
        for source, target in (
            ("margin_balance", "margin_balance_billion"),
            ("margin_change", "margin_change_billion"),
            ("short_balance", "short_balance_thousand"),
            ("short_change", "short_change_thousand"),
        ):
            frame[target] = pd.to_numeric(frame[source], errors="coerce") / (1e9 if "billion" in target else 1e3)
        for source, target in (
            ("margin_purchase_buy", "margin_purchase_buy_change_pct"),
            ("short_sale_buy", "short_sale_buy_change_pct"),
            ("margin_balance", "margin_balance_change_5d_pct"),
            ("short_balance", "short_balance_change_5d_pct"),
        ):
            frame[target] = _pct_change(frame[source], 5)

    frame["target_return"] = frame["ret_1d"].shift(-1)
    frame["target_gap_pct"] = frame["gap_pct"].shift(-1)
    frame["target_intraday_pct"] = frame["intraday_pct"].shift(-1)
    frame["target_date"] = frame["trade_date"].shift(-1)
    frame["target_broad"] = [
        _three_way_label(value, gap)
        for value, gap in zip(frame["target_return"], frame["target_gap_pct"])
    ]
    # A second, practical daily-direction target is used for rule discovery.
    # The user's strict +2.5%/-3% buckets are too sparse to estimate from a
    # 76-session sample; this target still distinguishes meaningful daily
    # moves while target_broad remains the final calendar classification.
    frame["target_direction"] = np.select(
        [frame["target_return"] >= 0.5, frame["target_return"] <= -0.5],
        ["走高", "走低"],
        default="震盪",
    )
    frame.loc[frame["target_return"].isna(), "target_direction"] = None

    if overnight is not None:
        overnight_frame = overnight.copy()
        overnight_frame["trade_date"] = pd.to_datetime(overnight_frame["trade_date"])
        # TAIFEX assigns the after-hours session to the date on which it ends
        # at 05:00.  It is therefore joined to the next TAIEX target date.
        overnight_frame = overnight_frame.rename(columns={"trade_date": "overnight_date"})
        frame["overnight_join_date"] = frame["target_date"]
        missing_target = frame["overnight_join_date"].isna()
        future_overnight = overnight_frame.loc[
            overnight_frame["overnight_date"] > frame["trade_date"].max(), "overnight_date"
        ]
        if missing_target.any() and not future_overnight.empty:
            frame.loc[missing_target, "overnight_join_date"] = future_overnight.min()
        overnight_metadata = overnight_frame.pop("source_metadata") if "source_metadata" in overnight_frame else None
        if overnight_metadata is not None:
            overnight_frame["overnight_source_metadata"] = overnight_metadata
        frame = frame.merge(
            overnight_frame,
            left_on="overnight_join_date",
            right_on="overnight_date",
            how="left",
        ).drop(columns=["overnight_date"])
        for prefix in ("tx", "mtx"):
            close = pd.to_numeric(frame[prefix + "_night_close"], errors="coerce")
            open_price = pd.to_numeric(frame[prefix + "_night_open"], errors="coerce")
            high = pd.to_numeric(frame[prefix + "_night_high"], errors="coerce")
            low = pd.to_numeric(frame[prefix + "_night_low"], errors="coerce")
            frame[prefix + "_night_gap_pct"] = (open_price / frame["close_price"] - 1.0) * 100.0
            frame[prefix + "_night_return_pct"] = (close / frame["close_price"] - 1.0) * 100.0
            frame[prefix + "_night_intraday_pct"] = (close / open_price - 1.0) * 100.0
            frame[prefix + "_night_range_pct"] = (high - low) / frame["close_price"] * 100.0
            volume = pd.to_numeric(frame[prefix + "_night_volume"], errors="coerce")
            frame[prefix + "_night_volume_change_pct"] = _pct_change(volume)
        if "overnight_source_metadata" in frame:
            def merge_metadata(base, overnight_value):
                if isinstance(base, str):
                    try:
                        base = json.loads(base)
                    except json.JSONDecodeError:
                        base = None
                if isinstance(overnight_value, str):
                    try:
                        overnight_value = json.loads(overnight_value)
                    except json.JSONDecodeError:
                        overnight_value = None
                result = {}
                if isinstance(base, dict):
                    result.update(base)
                if isinstance(overnight_value, dict):
                    result.update(overnight_value)
                return result or None

            frame["source_metadata"] = [
                merge_metadata(base, overnight_value)
                for base, overnight_value in zip(
                    frame.get("source_metadata", pd.Series([None] * len(frame))),
                    frame["overnight_source_metadata"],
                )
            ]
            frame = frame.drop(columns=["overnight_source_metadata"])
        frame = frame.drop(columns=["overnight_join_date"])
    return frame


def _fit_cutoffs(
    frame: pd.DataFrame,
    feature_names: Sequence[str],
    low_quantile: float = 0.30,
    high_quantile: float = 0.70,
) -> Dict[str, Tuple[float, float]]:
    cutoffs = {}
    for name in feature_names:
        values = pd.to_numeric(frame[name], errors="coerce").dropna()
        if len(values) < 5:
            continue
        low, high = values.quantile([low_quantile, high_quantile]).tolist()
        if low == high:
            continue
        cutoffs[name] = (float(low), float(high))
    return cutoffs


def add_factor_states(frame: pd.DataFrame, cutoffs: Dict[str, Tuple[float, float]]) -> pd.DataFrame:
    result = frame.copy()
    for name, (low, high) in cutoffs.items():
        values = pd.to_numeric(result[name], errors="coerce")
        result[name + "__state"] = np.select(
            [values <= low, values >= high], ["低", "高"], default="中"
        )
    return result


def _all_feature_names() -> List[str]:
    return [name for names in FACTOR_COLUMNS.values() for name in names]


def _condition_mask(frame: pd.DataFrame, condition: Tuple[str, str]) -> pd.Series:
    name, state = condition
    return frame[name + "__state"] == state


def discover_rules(
    development: pd.DataFrame,
    min_support: int = 4,
    max_rules_per_label: int = 12,
    target_column: str = "target_direction",
    low_quantile: float = 0.30,
    high_quantile: float = 0.70,
) -> Tuple[List[Rule], Dict[str, Tuple[float, float]], pd.DataFrame]:
    """Discover supported one/two-factor rules using development rows only."""
    feature_names = [name for name in _all_feature_names() if name in development]
    cutoffs = _fit_cutoffs(development, feature_names, low_quantile, high_quantile)
    states = add_factor_states(development, cutoffs)
    usable = states.dropna(subset=[target_column, "target_return"]).copy()
    prevalence = usable[target_column].value_counts(normalize=True).to_dict()
    candidates: List[Rule] = []
    conditions = [(name, state) for name in cutoffs for state in ("低", "高")]
    combinations_to_check = [(condition,) for condition in conditions]
    combinations_to_check.extend(combinations(conditions, 2))
    for condition_set in combinations_to_check:
        if len({name for name, _ in condition_set}) != len(condition_set):
            continue
        mask = pd.Series(True, index=usable.index)
        for condition in condition_set:
            mask &= _condition_mask(usable, condition)
        selected = usable.loc[mask]
        if len(selected) < min_support:
            continue
        distribution = selected[target_column].value_counts()
        label = str(distribution.index[0])
        precision = float(distribution.iloc[0] / len(selected))
        base = float(prevalence.get(label, 0.0))
        if base <= 0:
            continue
        lift = precision / base
        candidates.append(
            Rule(
                conditions=tuple(condition_set),
                label=label,
                support=int(len(selected)),
                mean_return=float(selected["target_return"].mean()),
                lift=lift,
                precision=precision,
            )
        )
    candidates.sort(key=lambda rule: (rule.lift, rule.precision, rule.support), reverse=True)
    chosen: List[Rule] = []
    per_label: Dict[str, int] = {}
    for rule in candidates:
        count = per_label.get(rule.label, 0)
        if count >= max_rules_per_label:
            continue
        chosen.append(rule)
        per_label[rule.label] = count + 1
    return chosen, cutoffs, states


def predict_with_rules(
    row: pd.Series,
    rules: Sequence[Rule],
    fallback_mean: float = 0.0,
    lift_power: float = 1.0,
    support_power: float = 1.0,
) -> Tuple[str, float, List[Rule]]:
    matched = []
    for rule in rules:
        if all(row.get(name + "__state") == state for name, state in rule.conditions):
            matched.append(rule)
    scores = {label: 0.0 for label in BROAD_LABELS}
    returns = []
    for rule in matched:
        weight = max(0.1, (rule.lift - 1.0)) ** lift_power * np.log1p(rule.support) ** support_power
        scores[rule.label] += weight
        returns.extend([rule.mean_return] * max(1, int(weight * 10)))
    if not matched:
        return ("震盪", fallback_mean, [])
    label = max(scores, key=scores.get)
    return label, float(np.mean(returns)) if returns else fallback_mean, matched


def walk_forward_rule_predictions(
    panel: pd.DataFrame,
    start_index: int = 25,
    min_support: int = 4,
    target_column: str = "target_direction",
    low_quantile: float = 0.30,
    high_quantile: float = 0.70,
    max_rules_per_label: int = 12,
    lift_power: float = 1.0,
    support_power: float = 1.0,
) -> pd.DataFrame:
    """Fit rules on rows before each prediction row and predict chronologically."""
    rows = []
    for index in range(start_index, len(panel)):
        train = panel.iloc[:index].dropna(subset=[target_column, "target_return"])
        test = panel.iloc[[index]]
        if len(train) < 15:
            continue
        rules, cutoffs, _ = discover_rules(
            train,
            min_support=min_support,
            target_column=target_column,
            low_quantile=low_quantile,
            high_quantile=high_quantile,
            max_rules_per_label=max_rules_per_label,
        )
        test_states = add_factor_states(test, cutoffs)
        broad_mean = float(train["target_return"].mean())
        prediction, expected_return, matched = predict_with_rules(
            test_states.iloc[0],
            rules,
            broad_mean,
            lift_power=lift_power,
            support_power=support_power,
        )
        actual = test.iloc[0]
        predicted_binary = binary_prediction_with_momentum(
            test_states.iloc[0], expected_return
        )
        final_return = expected_return_with_factor_blend(
            test_states.iloc[0],
            expected_return,
            predicted_binary,
            fit_overnight_return_model(train),
            fit_factor_return_model(train),
        )
        rows.append(
            {
                "signal_date": actual["trade_date"],
                "target_date": actual["target_date"],
                "predicted_direction": prediction,
                "predicted_return": final_return,
                "predicted_strict": strict_label_from_return(final_return),
                "predicted_binary": predicted_binary,
                "actual_direction": actual[target_column],
                "actual_broad": actual["target_broad"],
                "actual_return": actual["target_return"],
                "actual_binary": binary_label_from_return(actual["target_return"]),
                "matched_rule_count": len(matched),
            }
        )
    return pd.DataFrame(rows)


def format_rule(rule: Rule) -> str:
    condition_text = " + ".join(DISPLAY_NAMES.get(name, name) + "=" + state for name, state in rule.conditions)
    return "%s → %s (n=%d, precision=%.0f%%, lift=%.2f, avg next=%.2f%%)" % (
        condition_text,
        rule.label,
        rule.support,
        rule.precision * 100.0,
        rule.lift,
        rule.mean_return,
    )
