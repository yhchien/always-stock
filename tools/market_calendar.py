"""Market calendar feature engineering and walk-forward classification.

This module is intentionally independent from the live signal pipeline.  It
builds a point-in-time daily panel where row ``t`` contains only information
that was available after Taiwan's session on ``t``; that row is used to
predict the session label on the next Taiwan trading day.

The downloader lives in ``scripts/download_market_calendar_data.py`` so that
the model can also be run on a checked-in or manually supplied CSV snapshot.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


OUTCOME_LABELS = (
    "1_開高走高",
    "2_平盤或開低後拉高至+3~+4",
    "3_開高+5後走低仍收+1~+3",
    "4_開低走高",
    "5_平盤上下震盪",
    "6_一路向下",
    "7_開低走低",
)

FAMILY_WEIGHTS = {
    "technical": 0.36,
    "institutional": 0.25,
    "vix": 0.12,
    "futures": 0.15,
    "macro": 0.12,
}

FEATURE_FAMILIES = {
    "technical": [
        "ret_1d",
        "ret_3d",
        "ret_5d",
        "gap_pct",
        "intraday_pct",
        "close_location",
        "kdj_k",
        "kdj_d",
        "kdj_j",
        "kdj_cross",
        "close_vs_ma5",
        "close_vs_ma20",
        "ma5_slope",
    ],
    "institutional": [
        "inst_total_net_amount_z20",
        "foreign_net_amount_z20",
        "trust_net_amount_z20",
        "dealer_net_amount_z20",
        "inst_buy_streak",
        "inst_sell_streak",
    ],
    "vix": [
        "tw_vix_level",
        "tw_vix_change",
        "tw_vix_z20",
        "us_vix_level",
        "us_vix_change",
        "us_vix_z20",
    ],
    "futures": [
        "mtx_net_oi",
        "mtx_net_oi_change",
        "mtx_long_oi_change",
        "mtx_short_oi_change",
        "mtx_long_short_ratio",
        "mtx_large_long_pct",
        "mtx_large_short_pct",
    ],
    "macro": [
        "us10y_change",
        "us10y_change_5d",
        "gold_change",
        "gold_change_5d",
        "usd_twd_change",
        "usd_twd_change_5d",
    ],
}


@dataclass
class ModelResult:
    labels: List[str]
    predictions: List[str]
    confidence: List[float]
    accuracy: float
    correct: int
    total: int
    weights: Dict[str, float]


def _safe_pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.astype(float).pct_change(periods=periods, fill_method=None) * 100.0


def _rolling_z(series: pd.Series, window: int = 20) -> pd.Series:
    mean = series.rolling(window, min_periods=5).mean()
    std = series.rolling(window, min_periods=5).std(ddof=0).replace(0, np.nan)
    return (series - mean) / std


def _streak(series: pd.Series, positive: bool) -> pd.Series:
    values = series.fillna(0).to_numpy(dtype=float)
    out = np.zeros(len(values), dtype=float)
    count = 0
    for i, value in enumerate(values):
        if (value > 0) == positive:
            count += 1
        else:
            count = 0
        out[i] = count
    return pd.Series(out, index=series.index)


def _compute_kdj(frame: pd.DataFrame, window: int = 9) -> pd.DataFrame:
    low_n = frame["low_price"].rolling(window, min_periods=window).min()
    high_n = frame["high_price"].rolling(window, min_periods=window).max()
    denom = (high_n - low_n).replace(0, np.nan)
    rsv = (frame["close_price"] - low_n) / denom * 100.0
    k = rsv.ewm(alpha=1.0 / 3.0, adjust=False, min_periods=window).mean()
    d = k.ewm(alpha=1.0 / 3.0, adjust=False, min_periods=window).mean()
    frame["kdj_k"] = k
    frame["kdj_d"] = d
    frame["kdj_j"] = 3.0 * k - 2.0 * d
    frame["kdj_cross"] = np.sign(k - d).diff()
    return frame


def classify_session(row: pd.Series) -> str:
    """Classify a completed TAIEX session into one of seven user labels.

    The rules are mutually exclusive and deliberately use broad thresholds;
    they describe the path (gap, open-to-close direction and close location)
    rather than pretending that every day has a clean textbook shape.
    """
    gap = float(row["gap_pct"])
    close_ret = float(row["ret_1d"])
    intraday = float(row["intraday_pct"])
    location = float(row["close_location"])

    # Percentages are measured against the previous TAIEX close.
    if close_ret >= 5.0 and intraday >= 0.0 and location >= 0.60:
        return OUTCOME_LABELS[0]
    if gap >= 5.0 and intraday < 0.0 and 1.0 <= close_ret <= 3.0:
        return OUTCOME_LABELS[2]
    if 3.0 <= close_ret < 5.0 and -2.5 <= gap <= 0.5 and intraday > 0.0:
        return OUTCOME_LABELS[1]
    # The user's numbered list omitted #4; retain the original open-low /
    # recover path so the calendar still has seven mutually exclusive labels.
    if gap <= -0.30 and intraday >= 0.20:
        return OUTCOME_LABELS[3]
    if close_ret <= -3.0 and intraday < 0.0 and location <= 0.40:
        return OUTCOME_LABELS[5]
    if gap <= -0.30 and intraday < 0.0:
        return OUTCOME_LABELS[6]
    if -2.0 <= close_ret <= 2.0:
        return OUTCOME_LABELS[4]
    if close_ret < 0 and intraday < 0:
        return OUTCOME_LABELS[6]
    if close_ret > 0 and intraday > 0:
        return OUTCOME_LABELS[4]
    return OUTCOME_LABELS[4]


def build_feature_panel(raw: pd.DataFrame) -> pd.DataFrame:
    """Build lag-safe features and next-session target labels.

    Required raw columns are the TAIEX OHLC columns plus any available factor
    columns. Missing external fields remain NaN and are excluded from the
    corresponding distance calculation; the report can therefore show data
    coverage instead of silently filling with future values.
    """
    frame = raw.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date").reset_index(drop=True)
    for col in ("open_price", "high_price", "low_price", "close_price"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    previous_close = frame["close_price"].shift(1)
    frame["ret_1d"] = _safe_pct_change(frame["close_price"])
    frame["ret_3d"] = _safe_pct_change(frame["close_price"], 3)
    frame["ret_5d"] = _safe_pct_change(frame["close_price"], 5)
    frame["gap_pct"] = (frame["open_price"] / previous_close - 1.0) * 100.0
    frame["intraday_pct"] = (frame["close_price"] / frame["open_price"] - 1.0) * 100.0
    daily_range = (frame["high_price"] - frame["low_price"]).replace(0, np.nan)
    frame["close_location"] = (frame["close_price"] - frame["low_price"]) / daily_range
    frame["close_vs_ma5"] = frame["close_price"] / frame["close_price"].rolling(5).mean() - 1.0
    frame["close_vs_ma20"] = frame["close_price"] / frame["close_price"].rolling(20).mean() - 1.0
    frame["ma5_slope"] = frame["close_price"].rolling(5).mean().pct_change(3)
    frame = _compute_kdj(frame)

    for source, target in (
        ("inst_total_net_amount", "inst_total_net_amount_z20"),
        ("foreign_net_amount", "foreign_net_amount_z20"),
        ("trust_net_amount", "trust_net_amount_z20"),
        ("dealer_net_amount", "dealer_net_amount_z20"),
        ("tw_vix", "tw_vix_z20"),
        ("us_vix", "us_vix_z20"),
    ):
        if source in frame:
            frame[target] = _rolling_z(pd.to_numeric(frame[source], errors="coerce"))
    if "inst_total_net_amount" in frame:
        frame["inst_buy_streak"] = _streak(frame["inst_total_net_amount"], True)
        frame["inst_sell_streak"] = _streak(frame["inst_total_net_amount"], False)

    for source, target in (
        ("tw_vix", "tw_vix_level"),
        ("us_vix", "us_vix_level"),
    ):
        if source in frame:
            frame[target] = pd.to_numeric(frame[source], errors="coerce")
            frame[target.replace("_level", "_change")] = frame[target].diff()

    if "mtx_oi_long" in frame and "mtx_oi_short" in frame:
        long_oi = pd.to_numeric(frame["mtx_oi_long"], errors="coerce")
        short_oi = pd.to_numeric(frame["mtx_oi_short"], errors="coerce")
        frame["mtx_net_oi"] = long_oi - short_oi
        frame["mtx_long_oi_change"] = long_oi.diff()
        frame["mtx_short_oi_change"] = short_oi.diff()
        frame["mtx_net_oi_change"] = frame["mtx_net_oi"].diff()
        frame["mtx_long_short_ratio"] = long_oi / short_oi.replace(0, np.nan)

    for source, target in (
        ("us10y", "us10y_change"),
        ("gold", "gold_change"),
        ("usd_twd", "usd_twd_change"),
    ):
        if source in frame:
            frame[source] = pd.to_numeric(frame[source], errors="coerce")
            frame[target] = _safe_pct_change(frame[source])
            frame[target + "_5d"] = _safe_pct_change(frame[source], 5)

    frame["session_label"] = frame.apply(classify_session, axis=1)
    frame["target_label"] = frame["session_label"].shift(-1)
    frame["target_date"] = frame["trade_date"].shift(-1)
    return frame


def _standardize(train: pd.DataFrame, test: pd.DataFrame, columns: Sequence[str]) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    usable = []
    for col in columns:
        if col in train and train[col].notna().sum() >= 5:
            usable.append(col)
    if not usable:
        return np.empty((len(train), 0)), np.empty((len(test), 0)), []
    means = train[usable].apply(pd.to_numeric, errors="coerce").mean()
    scales = train[usable].apply(pd.to_numeric, errors="coerce").std(ddof=0).replace(0, 1.0)
    train_x = ((train[usable] - means) / scales).replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
    test_x = ((test[usable] - means) / scales).replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
    return train_x, test_x, usable


def _weighted_nearest_centroid(
    train: pd.DataFrame,
    test: pd.DataFrame,
    weights: Dict[str, float],
) -> Tuple[List[str], List[float]]:
    distances = np.zeros((len(test), len(OUTCOME_LABELS)), dtype=float)
    present = np.zeros((len(test), len(OUTCOME_LABELS)), dtype=bool)
    for family, family_weight in weights.items():
        columns = FEATURE_FAMILIES[family]
        train_x, test_x, usable = _standardize(train, test, columns)
        if not usable:
            continue
        feature_scale = family_weight / max(len(usable), 1)
        for label_index, label in enumerate(OUTCOME_LABELS):
            class_rows = train.loc[train["target_label"] == label]
            if class_rows.empty:
                continue
            class_x, _, _ = _standardize(class_rows, test, usable)
            # _standardize re-centers class_rows.  Reuse train's scaling so
            # class centroids are comparable across classes.
            means = train[usable].apply(pd.to_numeric, errors="coerce").mean()
            scales = train[usable].apply(pd.to_numeric, errors="coerce").std(ddof=0).replace(0, 1.0)
            class_x = ((class_rows[usable] - means) / scales).replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
            centroid = class_x.mean(axis=0)
            distances[:, label_index] += feature_scale * np.mean((test_x - centroid) ** 2, axis=1)
            present[:, label_index] = True

    predictions = []
    confidences = []
    for row_index in range(len(test)):
        candidate_indices = np.where(present[row_index])[0]
        if len(candidate_indices) == 0:
            predictions.append(OUTCOME_LABELS[4])
            confidences.append(0.0)
            continue
        ordered = candidate_indices[np.argsort(distances[row_index, candidate_indices])]
        best = ordered[0]
        best_distance = distances[row_index, best]
        second_distance = distances[row_index, ordered[1]] if len(ordered) > 1 else best_distance + 1.0
        confidence = float(max(0.0, min(1.0, (second_distance - best_distance) / (second_distance + 1e-9))))
        predictions.append(OUTCOME_LABELS[best])
        confidences.append(confidence)
    return predictions, confidences


def evaluate_holdout(panel: pd.DataFrame, train_fraction: float = 0.60, weights: Optional[Dict[str, float]] = None) -> ModelResult:
    """Evaluate a single chronological holdout; no random shuffling."""
    weights = weights or FAMILY_WEIGHTS
    usable = panel.dropna(subset=["target_label"]).copy()
    split = max(20, int(len(usable) * train_fraction))
    split = min(split, len(usable) - 1)
    train = usable.iloc[:split]
    test = usable.iloc[split:]
    predictions, confidence = _weighted_nearest_centroid(train, test, weights)
    labels = test["target_label"].tolist()
    correct = sum(prediction == label for prediction, label in zip(predictions, labels))
    return ModelResult(
        labels=labels,
        predictions=predictions,
        confidence=confidence,
        accuracy=correct / len(labels) if labels else 0.0,
        correct=correct,
        total=len(labels),
        weights=dict(weights),
    )


def tune_family_weights(panel: pd.DataFrame) -> Dict[str, float]:
    """Small, deterministic grid search on the first 60% only.

    The chosen weights are then evaluated on the untouched final 40% by the
    caller.  This prevents the report from claiming that the test period was
    used to tune itself.
    """
    candidates = [
        {"technical": 0.45, "institutional": 0.25, "vix": 0.10, "futures": 0.10, "macro": 0.10},
        {"technical": 0.36, "institutional": 0.25, "vix": 0.12, "futures": 0.15, "macro": 0.12},
        {"technical": 0.30, "institutional": 0.30, "vix": 0.15, "futures": 0.15, "macro": 0.10},
        {"technical": 0.30, "institutional": 0.20, "vix": 0.20, "futures": 0.20, "macro": 0.10},
        {"technical": 0.25, "institutional": 0.30, "vix": 0.15, "futures": 0.20, "macro": 0.10},
    ]
    usable = panel.dropna(subset=["target_label"]).copy()
    split = max(20, int(len(usable) * 0.60))
    development = usable.iloc[:split]
    inner_split = max(15, int(len(development) * 0.65))
    inner_train = development.iloc[:inner_split]
    inner_test = development.iloc[inner_split:]
    best = dict(FAMILY_WEIGHTS)
    best_score = -1.0
    for candidate in candidates:
        predictions, _ = _weighted_nearest_centroid(inner_train, inner_test, candidate)
        labels = inner_test["target_label"].tolist()
        score = sum(p == y for p, y in zip(predictions, labels)) / len(labels) if labels else 0.0
        if score > best_score:
            best_score = score
            best = candidate
    return best


def outcome_counts(panel: pd.DataFrame) -> Dict[str, int]:
    counts = Counter(panel["session_label"].dropna().tolist())
    return {label: int(counts.get(label, 0)) for label in OUTCOME_LABELS}
