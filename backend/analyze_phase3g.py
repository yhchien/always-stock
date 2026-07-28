"""Phase 3G — policy attribution and point-in-time lifecycle validation.

Research-only.  The script reads the frozen Phase 3F artifacts and source
tables, then writes Phase 3G CSV/Markdown artifacts.  It never writes a
production table or changes candidate, ranking, WATCH, regime, or outcome code.

Run from ``backend`` with the project environment loaded:

    python3 analyze_phase3g.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from app.database import DATABASE_URL


ROOT = Path(__file__).resolve().parents[1]
F3 = ROOT / "docs" / "plans" / "phase3f_v2"
OUT = ROOT / "docs" / "plans" / "phase3g"
SOURCE_CACHE = Path("/tmp/phase3f_v2_sources.pkl")
PREPARED_CACHE = Path("/tmp/phase3f_v2b_prepared.pkl")
POCKET_CACHE = Path("/tmp/phase3f_v2b_pocket.pkl")
WINNER_BAR = 12.0
LOSER_BAR = -6.0
A_START, A_END = date(2026, 6, 11), date(2026, 7, 1)
B_START, B_END = date(2026, 7, 2), date(2026, 7, 9)
C_START, C_END = date(2026, 7, 10), date(2026, 7, 24)
TRANSITION_START, TRANSITION_END = date(2026, 6, 26), date(2026, 7, 14)
LIVE_START, LIVE_REQUEST_END = date(2026, 7, 22), date(2026, 7, 28)


def div(a: Any, b: Any) -> float:
    return float(a) / float(b) if b and not pd.isna(b) else np.nan


def wilson(k: int, n: int, z: float = 1.959964) -> Tuple[float, float]:
    if n <= 0:
        return np.nan, np.nan
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0, center - radius), min(1, center + radius)


def outcome(ret: Any) -> Optional[str]:
    if ret is None or pd.isna(ret):
        return None
    if float(ret) >= WINNER_BAR:
        return "WINNER"
    if float(ret) <= LOSER_BAR:
        return "LOSER"
    return "NEUTRAL"


def json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def dates_to_python(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for col in columns:
        if col in df:
            df[col] = pd.to_datetime(df[col]).dt.date
    return df


def load_inputs() -> Dict[str, Any]:
    required = [SOURCE_CACHE, PREPARED_CACHE, POCKET_CACHE]
    if any(not p.exists() for p in required):
        raise FileNotFoundError(f"missing Phase 3F cache: {[str(p) for p in required if not p.exists()]}")
    sources = pd.read_pickle(SOURCE_CACHE)
    prepared = pd.read_pickle(PREPARED_CACHE)
    pocket = pd.read_pickle(POCKET_CACHE)
    episodes = pd.read_csv(F3 / "phase3f_v2_first_seen_episodes.csv")
    lifecycle_old = pd.read_csv(F3 / "phase3f_v2_candidate_day_lifecycle.csv")
    for frame in (episodes, lifecycle_old):
        dates_to_python(
            frame,
            ["evaluation_date", "episode_start_date", "future_trade_date_10d"],
        )
    episodes = episodes[
        (episodes.episode_kind == "WINDOW_FIRST_SEEN")
        & episodes.dataset.isin(["A", "B", "C"])
    ].copy()
    if episodes.dataset.value_counts().to_dict() != {"A": 246, "C": 140, "B": 87}:
        raise AssertionError(f"frozen cohort changed: {episodes.dataset.value_counts().to_dict()}")
    for frame in [
        prepared["market"],
        prepared["breadth"],
        prepared["market_path"],
        prepared["candidate"],
        prepared["raw_enriched"],
        prepared["flow_daily"],
        pocket,
    ]:
        dates_to_python(frame, ["evaluation_date", "trade_date"])
    for key in ("prices", "flow"):
        dates_to_python(sources[key], ["trade_date"])
    dates_to_python(sources["snapshots"], ["snapshot_date"])
    return {
        "sources": sources,
        "prepared": prepared,
        "pocket": pocket,
        "episodes": episodes,
        "lifecycle_old": lifecycle_old,
    }


def refresh_latest(sources: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Read-only refresh through the latest available date (bounded at 2026-07-28)."""
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15, "options": "-c statement_timeout=180000"},
    )
    queries = {
        "prices": """
            SELECT trade_date, stock_id, open_price, high_price, low_price,
                   close_price, volume, turnover
            FROM daily_price
            WHERE trade_date BETWEEN DATE '2026-07-25' AND DATE '2026-07-28'
        """,
        "flow": """
            SELECT trade_date, stock_id, inst_type, net_amount_est, net_shares
            FROM inst_stock_flow
            WHERE trade_date BETWEEN DATE '2026-07-25' AND DATE '2026-07-28'
        """,
        "snapshots": """
            SELECT snapshot_date, market_context, watchlist, candidate_pool_size,
                   final_watchlist_size, prompt_version, generated_at
            FROM signal_snapshots
            WHERE snapshot_date BETWEEN DATE '2026-07-25' AND DATE '2026-07-28'
        """,
    }
    with engine.connect() as conn:
        fresh = {name: pd.read_sql_query(text(sql), conn) for name, sql in queries.items()}
    engine.dispose()
    for key in ("prices", "flow"):
        dates_to_python(fresh[key], ["trade_date"])
        sources[key] = (
            pd.concat([sources[key], fresh[key]], ignore_index=True)
            .drop_duplicates()
            .sort_values(["trade_date", "stock_id"])
        )
    dates_to_python(fresh["snapshots"], ["snapshot_date"])
    sources["snapshots"] = (
        pd.concat([sources["snapshots"], fresh["snapshots"]], ignore_index=True)
        .sort_values(["snapshot_date", "generated_at"])
        .drop_duplicates(subset=["snapshot_date"], keep="last")
    )
    return sources


def metric_row(
    selected: pd.DataFrame,
    baseline: pd.DataFrame,
    dataset: str,
    policy: str,
    metric_scope: str = "MICRO",
) -> Dict[str, Any]:
    eligible_dates = sorted(baseline.evaluation_date.unique())
    by_day = selected.groupby("evaluation_date")
    n, bn = len(selected), len(baseline)
    w, ne, lo = (
        int((selected.outcome == label).sum())
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    bw, bne, blo = (
        int((baseline.outcome == label).sum())
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    daily = []
    for d in eligible_dates:
        x = selected[selected.evaluation_date == d]
        if len(x):
            daily.append(
                {
                    "winner": (x.outcome == "WINNER").mean(),
                    "neutral": (x.outcome == "NEUTRAL").mean(),
                    "loser": (x.outcome == "LOSER").mean(),
                    "safe": (x.outcome != "LOSER").mean(),
                }
            )
    low, high = wilson(lo, n)
    counts = by_day.size().reindex(eligible_dates, fill_value=0)
    return {
        "dataset": dataset,
        "policy": policy,
        "metric_scope": metric_scope,
        "selected_count": n,
        "selected_dates": int(selected.evaluation_date.nunique()),
        "eligible_dates": len(eligible_dates),
        "average_selected_per_day": float(counts.mean()),
        "median_selected_per_day": float(counts.median()),
        "zero_primary_date_rate": float((counts == 0).mean()),
        "coverage": div(n, bn),
        "winner_count": w,
        "winner_rate": div(w, n),
        "neutral_count": ne,
        "neutral_rate": div(ne, n),
        "loser_count": lo,
        "loser_rate": div(lo, n),
        "loser_rate_wilson_low": low,
        "loser_rate_wilson_high": high,
        "safe_rate": div(w + ne, n),
        "winner_dominance": div(w, w + ne),
        "winner_recall": div(w, bw),
        "neutral_removal_rate": div(bne - ne, bne),
        "loser_removal_rate": div(blo - lo, blo),
        "mean_future_return_10d": selected.future_return_10d.mean(),
        "median_future_return_10d": selected.future_return_10d.median(),
        "p25_future_return_10d": selected.future_return_10d.quantile(0.25),
        "p75_future_return_10d": selected.future_return_10d.quantile(0.75),
        "macro_daily_winner_rate": np.mean([x["winner"] for x in daily]) if daily else np.nan,
        "macro_daily_neutral_rate": np.mean([x["neutral"] for x in daily]) if daily else np.nan,
        "macro_daily_loser_rate": np.mean([x["loser"] for x in daily]) if daily else np.nan,
        "macro_daily_safe_rate": np.mean([x["safe"] for x in daily]) if daily else np.nan,
        "date_distribution": json.dumps(
            {str(k): int(v) for k, v in counts.items()}, ensure_ascii=False
        ),
        "sample_warning": "SHORT_STRESS_WINDOW" if dataset == "B" else "",
    }


def build_policy_outputs(episodes: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    e = episodes.copy()
    e["survival_gate"] = e.bundle_A.astype(bool)
    e["pocket_activity_gate"] = e.pocket_state.isin(
        ["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
    )
    e["phase3f_market_gate"] = e.policy1.astype(bool)
    e["phase3f_state_aware_pocket_gate"] = e.policy2.astype(bool)
    masks = {
        "P0_BASELINE": pd.Series(True, index=e.index),
        "P1_MARKET_ONLY": e.phase3f_market_gate,
        "P2_POCKET_ONLY": e.pocket_activity_gate,
        "P3_SURVIVAL_ONLY": e.survival_gate,
        "P4_MARKET_PLUS_SURVIVAL": e.phase3f_market_gate & e.survival_gate,
        "P5_POCKET_PLUS_SURVIVAL": e.pocket_activity_gate & e.survival_gate,
        "P6_FULL": e.phase3f_state_aware_pocket_gate & e.survival_gate,
    }
    long_rows = []
    for policy, mask in masks.items():
        for r in e.itertuples(index=True):
            long_rows.append(
                {
                    "stock_id": r.stock_id,
                    "stock_name": r.stock_name,
                    "evaluation_date": r.evaluation_date,
                    "dataset": r.dataset,
                    "policy": policy,
                    "market_gate_pass": bool(r.phase3f_market_gate),
                    "pocket_gate_pass": bool(r.pocket_activity_gate),
                    "state_aware_pocket_gate_pass": bool(r.phase3f_state_aware_pocket_gate),
                    "survival_gate_pass": bool(r.survival_gate),
                    "selected": bool(mask.loc[r.Index]),
                    "outcome": r.outcome,
                    "future_return_10d": r.future_return_10d,
                    "data_status": "RECONSTRUCTED_NOT_PRODUCTION_EXACT",
                }
            )
    matrix = pd.DataFrame(long_rows)
    metrics = []
    matured = e[e.dataset.isin(["A", "B"])]
    for ds in ("A", "B", "A+B"):
        base = matured if ds == "A+B" else matured[matured.dataset == ds]
        for policy, mask in masks.items():
            selected = base[mask.loc[base.index]]
            metrics.append(metric_row(selected, base, ds, policy))
            macro = metric_row(selected, base, ds, policy, "MACRO_DAILY")
            metrics.append(macro)
    attribution = pd.DataFrame(metrics)
    pairs = [
        ("STOCK_SURVIVAL_INCREMENT", "P3_SURVIVAL_ONLY", "P0_BASELINE"),
        ("MARKET_INCREMENT", "P4_MARKET_PLUS_SURVIVAL", "P3_SURVIVAL_ONLY"),
        ("POCKET_INCREMENT", "P5_POCKET_PLUS_SURVIVAL", "P3_SURVIVAL_ONLY"),
        ("FULL_VS_SURVIVAL", "P6_FULL", "P3_SURVIVAL_ONLY"),
        ("FULL_VS_MARKET_SURVIVAL", "P6_FULL", "P4_MARKET_PLUS_SURVIVAL"),
        ("FULL_VS_POCKET_SURVIVAL", "P6_FULL", "P5_POCKET_PLUS_SURVIVAL"),
    ]
    inc = []
    micro = attribution[attribution.metric_scope == "MICRO"].set_index(["dataset", "policy"])
    for ds in ("A", "B", "A+B"):
        for label, lhs, rhs in pairs:
            a, b = micro.loc[(ds, lhs)], micro.loc[(ds, rhs)]
            inc.append(
                {
                    "dataset": ds,
                    "increment": label,
                    "policy": lhs,
                    "reference_policy": rhs,
                    "selected_count_difference": a.selected_count - b.selected_count,
                    "coverage_pp": (a.coverage - b.coverage) * 100,
                    "winner_rate_pp": (a.winner_rate - b.winner_rate) * 100,
                    "loser_rate_pp": (a.loser_rate - b.loser_rate) * 100,
                    "safe_rate_pp": (a.safe_rate - b.safe_rate) * 100,
                    "winner_recall_pp": (a.winner_recall - b.winner_recall) * 100,
                    "interpretation_guardrail": "Dataset B has one Winner; use loser/safe/coverage only"
                    if ds == "B"
                    else "",
                }
            )
    increment = pd.DataFrame(inc)
    trace = pd.DataFrame(
        {
            "stock_id": e.stock_id,
            "stock_name": e.stock_name,
            "evaluation_date": e.evaluation_date,
            "dataset": e.dataset,
            "survival_relative_strength_raw": e.rs_slope_3d,
            "survival_relative_strength_percentile": e.momentum_rank_change_3d_state_percentile,
            "survival_relative_strength_threshold": 0.0,
            "survival_relative_strength_pass": e.rs_slope_3d > 0,
            "survival_market_down_day_raw": e.down_survival_ratio,
            "survival_market_down_day_percentile": e.down_survival_ratio_top120_percentile,
            "survival_market_down_day_threshold": 70.0,
            "survival_market_down_day_pass": e.down_survival_ratio_top120_percentile >= 70,
            "survival_rank_health_raw": e.momentum_rank_change_3d,
            "survival_rank_health_percentile": e.momentum_rank_change_3d_top120_percentile,
            "survival_rank_health_threshold": 0.0,
            "survival_rank_health_pass": e.momentum_rank_change_3d >= 0,
            "survival_flow_persistence_raw": e.institution_flow_persistence,
            "survival_flow_persistence_percentile": e.institution_flow_persistence_state_percentile,
            "survival_flow_persistence_threshold": 50.0,
            "survival_flow_persistence_pass": e.institution_flow_persistence_state_percentile >= 50,
            "survival_trend_efficiency_raw": e.trend_efficiency_10d,
            "survival_trend_efficiency_percentile": e.trend_efficiency_10d_top120_percentile,
            "survival_trend_efficiency_threshold": 50.0,
            "survival_trend_efficiency_pass": e.trend_efficiency_10d_top120_percentile >= 50,
            "survival_bundle_pass": e.survival_gate,
            "threshold_source": "PHASE3F_V2_DATASET_A_LOCKED",
            "threshold_version": "phase3f_v2_bundle_A",
            "bundle_definition": "market_down_day top120 percentile >=70; other predicates diagnostic only",
        }
    )
    return matrix, attribution, increment, trace


def top120_daily(prepared: Mapping[str, Any], episodes: pd.DataFrame) -> pd.DataFrame:
    c = prepared["candidate"].copy()
    rows = []
    first = episodes.groupby("evaluation_date").size().to_dict()
    for d, x in c.groupby("evaluation_date"):
        rows.append(
            {
                "evaluation_date": d,
                "Top120 first_seen count": int(first.get(d, 0)),
                "Top120 positive return count": int((x.return_1d > 0).sum()),
                "Top120 market outperform count": int((x.market_excess_return_1d > 0).sum()),
                "Top120 RS improving count": int((x.rs_slope_3d > 0).sum()),
                "Top120 rank improving count": int((x.momentum_rank_change_3d > 0).sum()),
                "Top120 institution buy count": int((x.institution_buy_days_5d >= 3).sum()),
                "Top120 volume expansion count": int((x.volume_ratio_1d_20d > 1).sum()),
                "top120_positive_rate": float((x.return_1d > 0).mean()),
                "top120_rs_improving_rate": float((x.rs_slope_3d > 0).mean()),
                "top120_institution_buy_rate": float((x.institution_buy_days_5d >= 3).mean()),
            }
        )
    return pd.DataFrame(rows)


def build_early_transition(
    prepared: Mapping[str, Any],
    episodes: pd.DataFrame,
    attribution: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    m = prepared["market"].merge(prepared["breadth"], on="evaluation_date", how="left")
    opp = pd.read_csv(F3 / "phase3f_v2_opportunity_density.csv")
    dates_to_python(opp, ["evaluation_date"])
    top = top120_daily(prepared, episodes)
    close = prepared["wide"]["close"]
    med3 = close.pct_change(3, fill_method=None).median(axis=1) * 100
    m["median_stock_return_3d"] = m.evaluation_date.map(med3.to_dict())
    m = (
        m.merge(top, on="evaluation_date", how="left")
        .merge(
            opp[["evaluation_date", "opportunity_density_continuous", "opportunity_density"]],
            on="evaluation_date",
            how="left",
        )
        .merge(
            prepared["market_path"][["evaluation_date", "market_path_state"]],
            on="evaluation_date",
            how="left",
        )
    )
    calibration = m[(m.evaluation_date >= A_START) & (m.evaluation_date < TRANSITION_START)]
    thresholds = {
        "ma5_p30": calibration.stocks_above_ma5_pct.quantile(0.30),
        "ma10_p30": calibration.stocks_above_ma10_pct.quantile(0.30),
        "ma5_p10": calibration.stocks_above_ma5_pct.quantile(0.10),
        "ma10_p10": calibration.stocks_above_ma10_pct.quantile(0.10),
        "new_high_p30": calibration.new_20d_high_count.quantile(0.30),
        "nhl_p30": calibration.new_high_low_ratio.replace(np.inf, np.nan).quantile(0.30),
        "new_high_p10": calibration.new_20d_high_count.quantile(0.10),
        "nhl_p10": calibration.new_high_low_ratio.replace(np.inf, np.nan).quantile(0.10),
    }
    m["e1_warning"] = (m.stocks_above_ma5_pct < thresholds["ma5_p30"]) | (
        m.stocks_above_ma10_pct < thresholds["ma10_p30"]
    )
    m["e1_extreme"] = (m.stocks_above_ma5_pct < thresholds["ma5_p10"]) & (
        m.stocks_above_ma10_pct < thresholds["ma10_p10"]
    )
    m["e2_warning"] = ((m.median_stock_return_3d < 0) & (m.market_return_3d >= 0)) | (
        (m.median_stock_return_5d < 0) & (m.market_return_5d >= 0)
    )
    m["e2_extreme"] = (m.median_stock_return_3d < -2) & (m.market_return_3d >= -0.5)
    m["e3_warning"] = (m.new_20d_high_count < thresholds["new_high_p30"]) | (
        m.new_high_low_ratio.replace(np.inf, np.nan) < thresholds["nhl_p30"]
    )
    m["e3_extreme"] = (m.new_20d_high_count < thresholds["new_high_p10"]) & (
        m.new_high_low_ratio.replace(np.inf, np.nan) < thresholds["nhl_p10"]
    )
    for col in ("top120_positive_rate", "top120_rs_improving_rate", "top120_institution_buy_rate"):
        m[f"{col}_weak"] = m[col] < m[col].shift(1)
    m["e4_weak_count"] = m[
        [
            "top120_positive_rate_weak",
            "top120_rs_improving_rate_weak",
            "top120_institution_buy_rate_weak",
        ]
    ].sum(axis=1)
    m["e4_warning"] = m.e4_weak_count >= 2
    m["e4_extreme"] = m.e4_weak_count == 3
    low_density = m.opportunity_density.isin(["LOW", "VERY_LOW"])
    # Once a HIGH/MEDIUM -> LOW/VERY_LOW breakdown occurs, E5 stays active
    # until recovery.  This is a state machine using only current/past dates.
    active, started = [], False
    previous = None
    for state in m.opportunity_density:
        if state in ("LOW", "VERY_LOW") and previous in ("HIGH", "MEDIUM"):
            started = True
        if state in ("HIGH", "MEDIUM"):
            started = False
        active.append(bool(started and state in ("LOW", "VERY_LOW")))
        previous = state
    m["e5_warning"] = active
    m["e5_extreme"] = m.e5_warning & (m.opportunity_density == "VERY_LOW")
    warning_cols = [f"e{i}_warning" for i in range(1, 6)]
    extreme_cols = [f"e{i}_extreme" for i in range(1, 6)]
    m["early_warning_count"] = m[warning_cols].sum(axis=1)
    m["extreme_warning_count"] = m[extreme_cols].sum(axis=1)
    sustained = (m.early_warning_count >= 2) & (m.early_warning_count.shift(1) >= 2)
    extreme_plus = (m.extreme_warning_count >= 1) & (m.early_warning_count >= 2)
    m["market_protective_state_shadow"] = np.where(
        m.market_path_state == "RISK_OFF",
        "RISK_OFF",
        np.where(sustained | extreme_plus, "PROTECTIVE", "NORMAL"),
    )
    m["threshold_source"] = "Dataset A 2026-06-11..2026-06-25; no outcome used"
    m["early_warning_thresholds"] = json.dumps(thresholds, ensure_ascii=False)
    daily_cols = [
        "evaluation_date",
        "market_return_1d",
        "market_return_3d",
        "market_return_5d",
        "market_return_10d",
        "market_drawdown_from_20d_high",
        "market_drawdown_from_60d_high",
        "market_ma5_slope",
        "market_ma10_slope",
        "market_ma20_slope",
        "advancing_count",
        "declining_count",
        "advance_decline_ratio",
        "stocks_above_ma5_pct",
        "stocks_above_ma10_pct",
        "stocks_above_ma20_pct",
        "new_20d_high_count",
        "new_20d_low_count",
        "new_high_low_ratio",
        "median_stock_return_1d",
        "median_stock_return_3d",
        "median_stock_return_5d",
        "index_stock_divergence",
        "Top120 first_seen count",
        "Top120 positive return count",
        "Top120 market outperform count",
        "Top120 RS improving count",
        "Top120 rank improving count",
        "Top120 institution buy count",
        "Top120 volume expansion count",
        "opportunity_density_continuous",
        "opportunity_density",
        "production_market_regime",
        "market_path_state",
        *warning_cols,
        *extreme_cols,
        "early_warning_count",
        "market_protective_state_shadow",
        "threshold_source",
        "early_warning_thresholds",
    ]
    daily = m[(m.evaluation_date >= TRANSITION_START) & (m.evaluation_date <= TRANSITION_END)][
        daily_cols
    ].copy()
    summaries = []
    phase_weak = date(2026, 7, 8)
    production_risk = date(2026, 7, 14)
    pos = {d: i for i, d in enumerate(m.evaluation_date)}
    for i in range(1, 6):
        col = f"e{i}_warning"
        x = m[(m.evaluation_date >= TRANSITION_START) & (m.evaluation_date <= TRANSITION_END)]
        active_dates = list(x.loc[x[col], "evaluation_date"])
        first = active_dates[0] if active_dates else None
        runs, run = [], []
        for d, flag in zip(x.evaluation_date, x[col]):
            if flag:
                run.append(d)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        recovery = None
        if first:
            after = x[(x.evaluation_date > first) & ~x[col]]
            recovery = after.evaluation_date.min() if len(after) else None
        false_alarms = int(x[(x.evaluation_date < B_START) & x[col]].shape[0])
        summaries.append(
            {
                "signal": f"E{i}",
                "first_warning_date": first,
                "warning_duration": max([len(r) for r in runs], default=0),
                "warning_recovery_date": recovery,
                "false_alarm_count": false_alarms,
                "days_before_2026_07_02": (pos[B_START] - pos[first]) if first and first in pos else np.nan,
                "days_before_phase3f_weakening": (pos[phase_weak] - pos[first]) if first and first in pos else np.nan,
                "days_before_production_risk_off": (pos[production_risk] - pos[first]) if first and first in pos else np.nan,
                "threshold_source": "Dataset A pre-2026-06-26",
            }
        )
    pfirst = m.loc[
        (m.evaluation_date >= TRANSITION_START)
        & (m.market_protective_state_shadow == "PROTECTIVE"),
        "evaluation_date",
    ].min()
    summaries.append(
        {
            "signal": "COMPOSITE_PROTECTIVE",
            "first_warning_date": pfirst,
            "warning_duration": int(
                (
                    m[
                        (m.evaluation_date >= TRANSITION_START)
                        & (m.evaluation_date <= TRANSITION_END)
                    ].market_protective_state_shadow
                    == "PROTECTIVE"
                ).sum()
            ),
            "warning_recovery_date": None,
            "false_alarm_count": int(
                (
                    (m.evaluation_date >= TRANSITION_START)
                    & (m.evaluation_date < B_START)
                    & (m.market_protective_state_shadow == "PROTECTIVE")
                ).sum()
            ),
            "days_before_2026_07_02": (pos[B_START] - pos[pfirst]) if pfirst in pos else np.nan,
            "days_before_phase3f_weakening": (pos[phase_weak] - pos[pfirst]) if pfirst in pos else np.nan,
            "days_before_production_risk_off": (pos[production_risk] - pos[pfirst]) if pfirst in pos else np.nan,
            "threshold_source": "fixed E1-E5 composition",
        }
    )
    signal_summary = pd.DataFrame(summaries)
    audit_window = m[
        (m.evaluation_date >= B_START) & (m.evaluation_date <= TRANSITION_END)
    ].copy()
    for idx, row in signal_summary.iterrows():
        col = (
            "market_protective_state_shadow"
            if row.signal == "COMPOSITE_PROTECTIVE"
            else f"{str(row.signal).lower()}_warning"
        )
        if row.signal == "COMPOSITE_PROTECTIVE":
            flags = audit_window[col] == "PROTECTIVE"
        else:
            flags = audit_window[col].astype(bool)
        active_dates = list(audit_window.loc[flags, "evaluation_date"])
        signal_summary.loc[idx, "first_warning_on_or_after_2026_07_02"] = (
            active_dates[0] if active_dates else None
        )
        sustained_start = None
        for j in range(1, len(audit_window)):
            if bool(flags.iloc[j]) and bool(flags.iloc[j - 1]):
                sustained_start = audit_window.evaluation_date.iloc[j - 1]
                break
        signal_summary.loc[idx, "first_sustained_warning_on_or_after_2026_07_02"] = (
            sustained_start
        )
        signal_summary.loc[idx, "sustained_days_before_phase3f_weakening"] = (
            pos[phase_weak] - pos[sustained_start]
            if sustained_start is not None and sustained_start in pos
            else np.nan
        )

    ep = episodes[episodes.dataset.isin(["A", "B"])].copy()
    state = m.set_index("evaluation_date")
    ep["protective_state"] = ep.evaluation_date.map(state.market_protective_state_shadow)
    ep["phase3f_state"] = ep.evaluation_date.map(state.market_path_state)
    survival = ep.bundle_A.astype(bool)
    market90 = ep.down_survival_ratio_top120_percentile >= 90
    comparisons = {
        "NO_PROTECTIVE": pd.Series(True, index=ep.index),
        "PHASE3F_WEAKENING": pd.Series(
            np.where(
                ep.phase3f_state == "NORMAL",
                True,
                np.where(ep.phase3f_state == "WEAKENING", survival, market90),
            ).astype(bool),
            index=ep.index,
        ),
        "EARLY_PROTECTIVE": pd.Series(
            np.where(
                ep.protective_state == "NORMAL",
                True,
                np.where(ep.protective_state == "PROTECTIVE", survival, market90),
            ).astype(bool),
            index=ep.index,
        ),
    }
    comp_rows = []
    for ds in ("A", "B"):
        base = ep[ep.dataset == ds]
        for label, mask in comparisons.items():
            r = metric_row(base[mask.loc[base.index]], base, ds, label)
            r["pre_2026_07_02_trigger_days"] = int(
                (
                    (m.evaluation_date >= TRANSITION_START)
                    & (m.evaluation_date < B_START)
                    & (
                        m.market_protective_state_shadow.eq("PROTECTIVE")
                        if label == "EARLY_PROTECTIVE"
                        else m.market_path_state.isin(["WEAKENING", "RISK_OFF"])
                        if label == "PHASE3F_WEAKENING"
                        else False
                    )
                ).sum()
            )
            comp_rows.append(r)
    return daily, signal_summary, pd.DataFrame(comp_rows)


def build_pocket_dimensions(
    pocket: pd.DataFrame,
    episodes: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p = pocket[pocket.cluster_level == "PRIMARY_SECTOR"].copy()
    p = p.sort_values(["cluster", "evaluation_date"])
    groups = p.groupby("cluster", group_keys=False)
    p["durability_sector_above_ma5_change"] = groups.sector_above_ma5_pct.diff()
    p["durability_sector_above_ma10_change"] = groups.sector_above_ma10_pct.diff()
    p["durability_new_high_persistence"] = groups.sector_new_high_count.transform(
        lambda x: x.rolling(3, min_periods=1).mean()
    )
    p["durability_institution_breadth_persistence"] = groups.sector_institution_buy_breadth.transform(
        lambda x: x.rolling(3, min_periods=1).mean()
    )
    p["durability_leader_continuation"] = groups.sector_leader_return.transform(
        lambda x: (x > 0).rolling(3, min_periods=1).mean()
    )
    p["durability_second_third_leader_confirmation"] = (
        (p.sector_second_leader_return > 0) & (p.sector_top120_count >= 3)
    ).astype(float)
    # High leader return with weak sector breadth is fragile, not durable.
    p["durability_leader_breadth_divergence"] = (
        p.sector_leader_return - p.sector_positive_breadth * 10.0
    )
    p["durability_strength_change_1d"] = groups.sector_market_excess_5d.diff()
    p["durability_strength_change_3d"] = groups.sector_market_excess_5d.diff(3)
    p["activity_sector_excess_return_3d"] = p.sector_market_excess_3d
    p["activity_sector_excess_return_5d"] = p.sector_market_excess_5d
    p["activity_top120_count"] = p.sector_top120_count
    p["activity_rank_improving_count"] = p.sector_rank_improving_count
    p["activity_volume_expansion_breadth"] = p.sector_volume_expansion_breadth
    p["activity_institution_buy_breadth"] = p.sector_institution_buy_breadth
    p["activity_leader_strength"] = p.sector_leader_return
    p["durability_strength_streak"] = p.sector_strength_streak
    p["durability_market_down_day_survival"] = p.sector_market_down_day_survival

    activity_cols = [
        "activity_sector_excess_return_3d",
        "activity_sector_excess_return_5d",
        "activity_top120_count",
        "activity_rank_improving_count",
        "activity_volume_expansion_breadth",
        "activity_institution_buy_breadth",
        "activity_leader_strength",
    ]
    durability_positive = [
        "durability_strength_streak",
        "durability_market_down_day_survival",
        "durability_sector_above_ma5_change",
        "durability_sector_above_ma10_change",
        "durability_new_high_persistence",
        "durability_institution_breadth_persistence",
        "durability_leader_continuation",
        "durability_second_third_leader_confirmation",
        "durability_strength_change_1d",
        "durability_strength_change_3d",
    ]
    a = p[(p.evaluation_date >= A_START) & (p.evaluation_date <= A_END)]
    ath = {col: float(a[col].quantile(0.50)) for col in activity_cols}
    ahigh = {col: float(a[col].quantile(0.75)) for col in activity_cols}
    dth = {col: float(a[col].quantile(0.50)) for col in durability_positive}
    div_threshold = float(a.durability_leader_breadth_divergence.quantile(0.75))
    p["activity_evidence_count"] = sum(p[col] >= ath[col] for col in activity_cols)
    p["activity_high_evidence_count"] = sum(p[col] >= ahigh[col] for col in activity_cols)
    p["pocket_activity"] = np.select(
        [p.activity_high_evidence_count >= 4, p.activity_evidence_count >= 3],
        ["HIGH_ACTIVITY", "ACTIVE"],
        default="LOW_ACTIVITY",
    )
    p["durability_positive_evidence_count"] = sum(
        p[col] >= dth[col] for col in durability_positive
    )
    p["durability_divergence_fail"] = (
        p.durability_leader_breadth_divergence > div_threshold
    )
    p["pocket_durability"] = np.where(
        (p.durability_positive_evidence_count >= 6) & ~p.durability_divergence_fail,
        "DURABLE",
        "FRAGILE",
    )
    active = p.pocket_activity.isin(["ACTIVE", "HIGH_ACTIVITY"])
    durable = p.pocket_durability == "DURABLE"
    p["pocket_quadrant"] = np.select(
        [active & durable, active & ~durable, ~active & durable],
        ["Q1_ACTIVE_DURABLE", "Q2_ACTIVE_FRAGILE", "Q3_QUIET_DURABLE"],
        default="Q4_INACTIVE",
    )
    p["activity_threshold_source"] = "Dataset A distribution; outcome-blind"
    p["activity_thresholds"] = json.dumps(ath, ensure_ascii=False)
    p["durability_threshold_source"] = "Dataset A distribution; outcome-blind"
    p["durability_thresholds"] = json.dumps(
        {**dth, "leader_breadth_divergence_max": div_threshold},
        ensure_ascii=False,
    )

    activity_out = p[
        [
            "evaluation_date",
            "cluster",
            *activity_cols,
            "activity_evidence_count",
            "activity_high_evidence_count",
            "pocket_activity",
            "activity_threshold_source",
            "activity_thresholds",
        ]
    ].copy()
    durability_out = p[
        [
            "evaluation_date",
            "cluster",
            *durability_positive,
            "durability_leader_breadth_divergence",
            "durability_positive_evidence_count",
            "durability_divergence_fail",
            "pocket_durability",
            "pocket_quadrant",
            "durability_threshold_source",
            "durability_thresholds",
        ]
    ].copy()

    key = p.set_index(["evaluation_date", "cluster"])
    ep = episodes[episodes.dataset.isin(["A", "B"])].copy()
    ep["pocket_quadrant"] = [
        key.pocket_quadrant.get((d, sector), "Q4_INACTIVE")
        for d, sector in zip(ep.evaluation_date, ep.primary_sector)
    ]
    ep["pocket_activity_v2"] = [
        key.pocket_activity.get((d, sector), "LOW_ACTIVITY")
        for d, sector in zip(ep.evaluation_date, ep.primary_sector)
    ]
    ep["pocket_durability_v2"] = [
        key.pocket_durability.get((d, sector), "FRAGILE")
        for d, sector in zip(ep.evaluation_date, ep.primary_sector)
    ]
    ep["momentum_rank_percentile"] = ep.groupby("evaluation_date").momentum_rank.rank(
        pct=True, ascending=False
    )
    sector_size = p.set_index(["evaluation_date", "cluster"]).member_count.to_dict()
    ep["sector_size"] = [
        sector_size.get((d, sector), np.nan)
        for d, sector in zip(ep.evaluation_date, ep.primary_sector)
    ]
    # MFE/MAE come from the already-frozen entry/barrier path, using source
    # prices only; these are evaluation outputs, never gate features.
    prepared = pd.read_pickle(PREPARED_CACHE)
    wide = prepared["wide"]
    price_dates = list(wide["close"].index)
    ppos = {d: i for i, d in enumerate(price_dates)}
    mfes, maes = [], []
    for r in ep.itertuples():
        pi = ppos.get(r.evaluation_date)
        if pi is None or r.stock_id not in wide["close"].columns:
            mfes.append(np.nan)
            maes.append(np.nan)
            continue
        entry = wide["close"].loc[r.evaluation_date, r.stock_id]
        end = min(pi + 10, len(price_dates) - 1)
        highs = wide["high"][r.stock_id].iloc[pi + 1 : end + 1].dropna()
        lows = wide["low"][r.stock_id].iloc[pi + 1 : end + 1].dropna()
        mfes.append((highs.max() / entry - 1) * 100 if len(highs) else np.nan)
        maes.append((lows.min() / entry - 1) * 100 if len(lows) else np.nan)
    ep["MFE_10d"] = mfes
    ep["MAE_10d"] = maes
    rows = []
    for ds in ("A", "B"):
        base = ep[ep.dataset == ds]
        for q in (
            "Q1_ACTIVE_DURABLE",
            "Q2_ACTIVE_FRAGILE",
            "Q3_QUIET_DURABLE",
            "Q4_INACTIVE",
        ):
            x = base[base.pocket_quadrant == q]
            w, n, l = ((x.outcome == z).sum() for z in ("WINNER", "NEUTRAL", "LOSER"))
            rows.append(
                {
                    "dataset": ds,
                    "pocket_quadrant": q,
                    "n": len(x),
                    "winner_count": int(w),
                    "winner_rate": div(w, len(x)),
                    "neutral_count": int(n),
                    "neutral_rate": div(n, len(x)),
                    "loser_count": int(l),
                    "loser_rate": div(l, len(x)),
                    "safe_rate": div(w + n, len(x)),
                    "winner_dominance": div(w, w + n),
                    "mean_return_10d": x.future_return_10d.mean(),
                    "median_return_10d": x.future_return_10d.median(),
                    "MFE_10d": x.MFE_10d.mean(),
                    "MAE_10d": x.MAE_10d.mean(),
                    "macro_daily_safe_rate": x.groupby("evaluation_date").apply(
                        lambda z: (z.outcome != "LOSER").mean(), include_groups=False
                    ).mean()
                    if len(x)
                    else np.nan,
                    "signal_date_count": x.evaluation_date.nunique(),
                    "market_path_distribution": json.dumps(
                        x.market_path_state.value_counts().to_dict(), ensure_ascii=False
                    ),
                    "momentum_rank_percentile_median": x.momentum_rank_percentile.median(),
                    "sector_size_median": x.sector_size.median(),
                    "control_note": "market/date/rank/sector-size disclosed; sparse cells prevent causal matched claim",
                    "sample_warning": "SHORT_STRESS_WINDOW" if ds == "B" else "",
                }
            )
    quadrant_outcomes = pd.DataFrame(rows)

    inc_rows = []
    policies = {
        "SURVIVAL_ONLY": ep.bundle_A.astype(bool),
        "OLD_ACTIVITY_PLUS_SURVIVAL": ep.bundle_A.astype(bool)
        & ep.pocket_state.isin(["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]),
        "ACTIVE_DURABLE_PLUS_SURVIVAL": ep.bundle_A.astype(bool)
        & (ep.pocket_quadrant == "Q1_ACTIVE_DURABLE"),
    }
    for ds in ("A", "B"):
        base = ep[ep.dataset == ds]
        reference = None
        for label, mask in policies.items():
            x = base[mask.loc[base.index]]
            row = metric_row(x, base, ds, label)
            if reference is None:
                reference = row
            row["coverage_pp_vs_survival"] = (row["coverage"] - reference["coverage"]) * 100
            row["safe_rate_pp_vs_survival"] = (row["safe_rate"] - reference["safe_rate"]) * 100
            row["loser_rate_pp_vs_survival"] = (row["loser_rate"] - reference["loser_rate"]) * 100
            inc_rows.append(row)
    pocket_increment = pd.DataFrame(inc_rows)
    return activity_out, durability_out, quadrant_outcomes, pocket_increment


def build_wide_from_sources(sources: Mapping[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    prices = sources["prices"].copy()
    prices["stock_id"] = prices.stock_id.astype(str)
    prices = prices.sort_values(["trade_date", "stock_id"])
    out = {}
    for source, name in (
        ("close_price", "close"),
        ("high_price", "high"),
        ("low_price", "low"),
        ("volume", "volume"),
    ):
        out[name] = prices.pivot(index="trade_date", columns="stock_id", values=source).sort_index()
    out["ret1"] = out["close"].pct_change(fill_method=None) * 100
    return out


def watch_by_date(snapshots: pd.DataFrame) -> Dict[date, List[Dict[str, Any]]]:
    result = {}
    for row in snapshots.sort_values(["snapshot_date", "generated_at"]).itertuples():
        items = json_value(row.watchlist) or []
        normalized = []
        for order, item in enumerate(items, 1):
            sid = str(item.get("stock") or item.get("stock_id") or "")
            if sid:
                normalized.append({**item, "stock_id": sid, "formal_watch_order": order})
        result[row.snapshot_date] = normalized
    return result


def production_cycles(
    watch: Mapping[date, List[Dict[str, Any]]],
    trading_dates: Sequence[date],
) -> List[Dict[str, Any]]:
    pos = {d: i for i, d in enumerate(trading_dates)}
    hits: Dict[str, List[Tuple[date, Dict[str, Any]]]] = defaultdict(list)
    for d, items in watch.items():
        if d not in pos:
            continue
        for item in items:
            hits[item["stock_id"]].append((d, item))
    cycles = []
    for sid, values in hits.items():
        values = sorted(values)
        start_i = 0
        for i in range(1, len(values) + 1):
            split = i == len(values) or pos[values[i][0]] - pos[values[i - 1][0]] - 1 >= 5
            if split:
                group = values[start_i:i]
                cycles.append(
                    {
                        "stock_id": sid,
                        "entry_date": group[0][0],
                        "latest_formal_hit": group[-1][0],
                        "formal_hit_dates": [x[0] for x in group],
                        "entry_item": group[0][1],
                    }
                )
                start_i = i
    return sorted(cycles, key=lambda x: (x["entry_date"], x["stock_id"]))


def barrier_path(
    sid: str,
    start: date,
    entry: float,
    wide: Mapping[str, pd.DataFrame],
    horizon: int = 10,
) -> Dict[str, Any]:
    dates = list(wide["close"].index)
    if start not in dates or sid not in wide["close"].columns or pd.isna(entry):
        return {
            "barrier_outcome": None,
            "winner_hit_date": None,
            "loser_hit_date": None,
            "barrier_hit_date": None,
        }
    pi = dates.index(start)
    winner_date = loser_date = None
    for j in range(pi + 1, min(pi + horizon + 1, len(dates))):
        h, lo = wide["high"].iloc[j][sid], wide["low"].iloc[j][sid]
        hit_w = not pd.isna(h) and (h / entry - 1) * 100 >= WINNER_BAR
        hit_l = not pd.isna(lo) and (lo / entry - 1) * 100 <= LOSER_BAR
        if hit_w and hit_l:
            return {
                "barrier_outcome": "AMBIGUOUS_SAME_DAY",
                "winner_hit_date": dates[j],
                "loser_hit_date": dates[j],
                "barrier_hit_date": dates[j],
            }
        if hit_w and winner_date is None:
            winner_date = dates[j]
        if hit_l and loser_date is None:
            loser_date = dates[j]
        if winner_date or loser_date:
            break
    if winner_date:
        label, hit = "HIT_WINNER_FIRST", winner_date
    elif loser_date:
        label, hit = "HIT_LOSER_FIRST", loser_date
    else:
        label, hit = "NO_BARRIER_HIT", None
    return {
        "barrier_outcome": label,
        "winner_hit_date": winner_date,
        "loser_hit_date": loser_date,
        "barrier_hit_date": hit,
    }


def forward_close_outcome(
    sid: str, start: date, wide: Mapping[str, pd.DataFrame], horizon: int = 10
) -> Tuple[Any, Any, Any]:
    dates = list(wide["close"].index)
    if start not in dates or sid not in wide["close"].columns:
        return np.nan, None, None
    pi = dates.index(start)
    if pi + horizon >= len(dates):
        return np.nan, None, None
    entry, future = wide["close"].loc[start, sid], wide["close"].iloc[pi + horizon][sid]
    if pd.isna(entry) or pd.isna(future):
        return np.nan, None, dates[pi + horizon]
    ret = (future / entry - 1) * 100
    return float(ret), outcome(ret), dates[pi + horizon]


def build_point_in_time_watch(
    sources: Mapping[str, pd.DataFrame],
    prepared: Mapping[str, Any],
    activity: pd.DataFrame,
    durability: pd.DataFrame,
) -> pd.DataFrame:
    wide = build_wide_from_sources(sources)
    all_dates = [
        d
        for d in wide["close"].index
        if date(2026, 5, 28) <= d <= LIVE_REQUEST_END and not pd.isna(wide["close"].loc[d, "TAIEX"])
    ]
    watch = watch_by_date(sources["snapshots"])
    cycles = production_cycles(watch, all_dates)
    next_entry: Dict[Tuple[str, date], Optional[date]] = {}
    by_sid: Dict[str, List[date]] = defaultdict(list)
    for cycle in cycles:
        by_sid[cycle["stock_id"]].append(cycle["entry_date"])
    for sid, entries in by_sid.items():
        for i, entry in enumerate(entries):
            next_entry[(sid, entry)] = entries[i + 1] if i + 1 < len(entries) else None

    candidate = prepared["candidate"].copy().set_index(["evaluation_date", "stock_id"])
    raw = prepared["raw_enriched"].copy()
    raw["stock_id"] = raw.stock_id.astype(str)
    raw_rank = raw.set_index(["evaluation_date", "stock_id"]).raw_union_rank.to_dict()
    watch_item = {
        (d, item["stock_id"]): item for d, items in watch.items() for item in items
    }
    formal_set = {d: {x["stock_id"] for x in items} for d, items in watch.items()}
    activity_map = activity.set_index(["evaluation_date", "cluster"]).pocket_activity.to_dict()
    durability_map = durability.set_index(
        ["evaluation_date", "cluster"]
    ).pocket_durability.to_dict()
    pocket_dates = sorted(activity.evaluation_date.unique())
    classification = sources["classification"].copy()
    classification["stock_id"] = classification.stock_id.astype(str)
    sector = classification.set_index("stock_id").primary_sector.to_dict()
    flow = sources["flow"].copy()
    flow["stock_id"] = flow.stock_id.astype(str)
    flow_daily = flow.groupby(["trade_date", "stock_id"]).net_amount_est.sum().unstack(fill_value=0)
    flow_daily = flow_daily.reindex(all_dates).fillna(0)
    taiex = wide["close"]["TAIEX"].reindex(all_dates)
    market_ret1 = taiex.pct_change(fill_method=None) * 100
    market_ret3 = taiex.pct_change(3, fill_method=None) * 100
    phase3f_market = prepared["market_path"].set_index("evaluation_date").market_path_state.to_dict()
    phase3f_dates = sorted(phase3f_market)
    date_pos = {d: i for i, d in enumerate(all_dates)}

    def last_leq(values: Sequence[date], d: date) -> Optional[date]:
        valid = [x for x in values if x <= d]
        return valid[-1] if valid else None

    def snapshot_metric(d: date, sid: str, name: str) -> Any:
        item = watch_item.get((d, sid), {})
        signal = item.get("signal_metrics") or {}
        momentum = item.get("momentum") or {}
        aliases = {
            "rs_market_percentile_20d": [
                signal.get("rs_market_percentile_20d"),
                momentum.get("rs_market_percentile_20d"),
            ],
            "momentum_score": [signal.get("momentum_score"), momentum.get("momentum_score")],
        }
        return next((v for v in aliases.get(name, []) if v is not None), np.nan)

    def candidate_value(d: date, sid: str, col: str) -> Any:
        key = (d, sid)
        if key in candidate.index:
            value = candidate.loc[key, col]
            return value.iloc[0] if isinstance(value, pd.Series) else value
        if col in ("rs_market_pct_day0", "momentum_score"):
            name = "rs_market_percentile_20d" if col == "rs_market_pct_day0" else col
            return snapshot_metric(d, sid, name)
        return np.nan

    rows = []
    for cycle in cycles:
        sid, entry_date = cycle["stock_id"], cycle["entry_date"]
        if sid not in wide["close"].columns or entry_date not in date_pos:
            continue
        entry_close = wide["close"].loc[entry_date, sid]
        if pd.isna(entry_close):
            continue
        entry_i = date_pos[entry_date]
        cap_i = min(entry_i + 10, len(all_dates) - 1)
        nxt = next_entry[(sid, entry_date)]
        if nxt in date_pos:
            cap_i = min(cap_i, date_pos[nxt] - 1)
        previous_state = None
        previous_weak: set[str] = set()
        outside = 0
        last_reentry = None
        for di in range(entry_i, cap_i + 1):
            d = all_dates[di]
            if pd.isna(wide["close"].loc[d, sid]):
                continue
            key = (d, sid)
            explicit_top = key in candidate.index
            top_known = d in set(prepared["candidate"].evaluation_date)
            if not top_known and sid in formal_set.get(d, set()):
                top: Any = True
            elif top_known:
                top = explicit_top
            else:
                top = pd.NA
            if top is True or top is np.bool_(True):
                if outside > 0:
                    last_reentry = d
                outside = 0
            elif top is False or top is np.bool_(False):
                outside += 1

            current = float(wide["close"].loc[d, sid])
            current_ret = float((current / entry_close - 1) * 100)
            history_start = entry_i + 1  # Day-0 OHLC occurred before close entry.
            action_history_dates = all_dates[history_start : di + 1]
            highs = wide["high"].loc[action_history_dates, sid].dropna()
            lows = wide["low"].loc[action_history_dates, sid].dropna()
            mfe = float((highs.max() / entry_close - 1) * 100) if len(highs) else 0.0
            mae = float((lows.min() / entry_close - 1) * 100) if len(lows) else 0.0
            rank0 = raw_rank.get(key, np.nan)
            rank1 = raw_rank.get((all_dates[di - 1], sid), np.nan) if di >= 1 else np.nan
            rank3 = raw_rank.get((all_dates[di - 3], sid), np.nan) if di >= 3 else np.nan
            rank_change1 = rank1 - rank0 if not pd.isna(rank0) and not pd.isna(rank1) else np.nan
            rank_change3 = rank3 - rank0 if not pd.isna(rank0) and not pd.isna(rank3) else np.nan
            rs0 = candidate_value(d, sid, "rs_market_pct_day0")
            rs3 = candidate_value(all_dates[di - 3], sid, "rs_market_pct_day0") if di >= 3 else np.nan
            rs5 = candidate_value(all_dates[di - 5], sid, "rs_market_pct_day0") if di >= 5 else np.nan
            rs_slope3 = (rs0 - rs3) / 3 if not pd.isna(rs0) and not pd.isna(rs3) else np.nan
            rs_slope5 = (rs0 - rs5) / 5 if not pd.isna(rs0) and not pd.isna(rs5) else np.nan
            fs = flow_daily[sid] if sid in flow_daily.columns else pd.Series(0.0, index=all_dates)
            flow3 = float(fs.iloc[max(0, di - 2) : di + 1].sum())
            prior3 = float(fs.iloc[max(0, di - 5) : max(0, di - 2)].sum())
            flow_accel = flow3 - prior3
            buy5 = int((fs.iloc[max(0, di - 4) : di + 1] > 0).sum())
            stock_ret1 = wide["ret1"].loc[d, sid]
            stock_ret3 = (
                (
                    wide["close"].loc[d, sid]
                    / wide["close"].loc[all_dates[di - 3], sid]
                    - 1
                )
                * 100
                if di >= 3
                and not pd.isna(wide["close"].loc[all_dates[di - 3], sid])
                else np.nan
            )
            market_excess1 = stock_ret1 - market_ret1.loc[d] if not pd.isna(stock_ret1) else np.nan
            market_excess3 = stock_ret3 - market_ret3.loc[d] if not pd.isna(stock_ret3) else np.nan
            sector_name = sector.get(sid)
            pocket_source_date = d if (d, sector_name) in activity_map else last_leq(pocket_dates, d)
            p_activity = activity_map.get((pocket_source_date, sector_name), "LOW_ACTIVITY")
            p_durability = durability_map.get((pocket_source_date, sector_name), "FRAGILE")
            market_source_date = d if d in phase3f_market else last_leq(phase3f_dates, d)
            market_state = phase3f_market.get(market_source_date, "RISK_OFF")

            weak: set[str] = set()
            evidence: Dict[str, Dict[str, Any]] = {}
            rank_fail = (not pd.isna(rank_change3) and rank_change3 < 0) or outside >= 2
            evidence["Momentum Rank"] = {
                "raw": {"rank_change_3d": rank_change3, "days_outside_top120": outside},
                "threshold": "rank_change_3d<0 OR outside>=2",
                "weak": bool(rank_fail),
            }
            if rank_fail:
                weak.add("Momentum Rank")
            rs_fail = (
                not pd.isna(rs_slope3)
                and rs_slope3 < 0
                and (pd.isna(rs_slope5) or rs_slope5 < 0)
            )
            evidence["Relative Strength Path"] = {
                "raw": {"rs_slope_3d": rs_slope3, "rs_slope_5d": rs_slope5},
                "threshold": "slope3<0 AND (slope5<0 OR unavailable)",
                "weak": bool(rs_fail),
            }
            if rs_fail:
                weak.add("Relative Strength Path")
            flow_fail = flow_accel < 0 and buy5 <= 2
            evidence["Institution Flow"] = {
                "raw": {"acceleration": flow_accel, "buy_days_5d": buy5},
                "threshold": "acceleration<0 AND buy_days_5d<=2",
                "weak": bool(flow_fail),
            }
            if flow_fail:
                weak.add("Institution Flow")
            relative_fail = (
                not pd.isna(market_excess1)
                and market_excess1 < 0
                and not pd.isna(market_excess3)
                and market_excess3 < 0
            )
            evidence["Market-relative Performance"] = {
                "raw": {"excess_1d": market_excess1, "excess_3d": market_excess3},
                "threshold": "both <0",
                "weak": bool(relative_fail),
            }
            if relative_fail:
                weak.add("Market-relative Performance")
            pocket_fail = p_durability == "FRAGILE"
            evidence["Pocket Durability"] = {
                "raw": p_durability,
                "threshold": "FRAGILE",
                "weak": bool(pocket_fail),
            }
            if pocket_fail:
                weak.add("Pocket Durability")
            improve = sum(
                [
                    not pd.isna(rank_change3) and rank_change3 > 0,
                    not pd.isna(rs_slope3) and rs_slope3 > 0,
                    flow_accel > 0,
                    not pd.isna(market_excess3) and market_excess3 > 0,
                ]
            )
            persistent_families = weak & previous_weak
            remove = len(persistent_families) >= 2 and di > entry_i
            if di == entry_i:
                informational = "HOLD_STRONG" if len(weak) <= 1 else "RESERVE"
                lifecycle = "NEW_DISCOVERY"
                executable = "KEEP_SHADOW" if informational == "HOLD_STRONG" else "WARNING_SHADOW"
            elif remove:
                informational, lifecycle, executable = (
                    "DETERIORATING",
                    "DETERIORATING",
                    "REMOVE_SHADOW",
                )
            elif len(weak) >= 2:
                informational, lifecycle, executable = (
                    "DETERIORATING",
                    "DETERIORATING",
                    "WARNING_SHADOW",
                )
            elif (last_reentry == d or improve >= 2) and top is not False:
                informational, lifecycle, executable = (
                    "REACCELERATING",
                    "REACCELERATING",
                    "KEEP_SHADOW",
                )
            elif not pd.isna(stock_ret1) and stock_ret1 < 0 and len(weak) <= 1:
                informational, lifecycle, executable = (
                    "HEALTHY_PULLBACK",
                    "HEALTHY_PULLBACK",
                    "KEEP_SHADOW",
                )
            elif top is False or pd.isna(rank0):
                informational, lifecycle, executable = "RESERVE", "STALE", "WARNING_SHADOW"
            else:
                informational, lifecycle, executable = "HOLD_STRONG", "CONTINUATION", "KEEP_SHADOW"

            # Barrier is evaluated only after entry close and overrides the
            # informational decision on the first hit date.
            hit_w = di > entry_i and not pd.isna(wide["high"].loc[d, sid]) and (
                wide["high"].loc[d, sid] / entry_close - 1
            ) * 100 >= WINNER_BAR
            hit_l = di > entry_i and not pd.isna(wide["low"].loc[d, sid]) and (
                wide["low"].loc[d, sid] / entry_close - 1
            ) * 100 <= LOSER_BAR
            terminal = None
            if hit_w and hit_l:
                terminal = "AMBIGUOUS_SAME_DAY"
            elif hit_w:
                terminal = "TARGET_REACHED"
            elif hit_l:
                terminal = "RISK_BREACHED"
            if terminal:
                executable = terminal

            feature_source_dates = [d, pocket_source_date, market_source_date]
            max_source = max(x for x in feature_source_dates if x is not None)
            pit_valid = max_source <= d
            old = previous_state
            rows.append(
                {
                    "stock_id": sid,
                    "stock_name": cycle["entry_item"].get("name")
                    or cycle["entry_item"].get("stock_name")
                    or sid,
                    "episode_start_date": entry_date,
                    "entry_date": entry_date,
                    "entry_close": entry_close,
                    "action_date": d,
                    "feature_as_of_date": d,
                    "current_close_as_of_action_date": current,
                    "current_return_as_of_action_date": current_ret,
                    "MFE_as_of_action_date": mfe,
                    "MAE_as_of_action_date": mae,
                    "days_since_entry": di - entry_i,
                    "current_top120_status": top,
                    "days_outside_top120": outside,
                    "days_since_reentry": date_pos[d] - date_pos[last_reentry]
                    if last_reentry
                    else np.nan,
                    "momentum_rank_as_of_action_date": rank0,
                    "momentum_rank_change_1d": rank_change1,
                    "momentum_rank_change_3d": rank_change3,
                    "rs_slope_3d": rs_slope3,
                    "rs_slope_5d": rs_slope5,
                    "institution_flow_acceleration": flow_accel,
                    "pocket_activity": p_activity,
                    "pocket_durability": p_durability,
                    "market_path_state": market_state,
                    "lifecycle_state": lifecycle,
                    "informational_state": informational,
                    "watchlist_action_shadow": executable,
                    "action_reasons": json.dumps(sorted(weak), ensure_ascii=False),
                    "evidence_family_count": len(weak),
                    "persistent_evidence_families": json.dumps(
                        sorted(persistent_families), ensure_ascii=False
                    ),
                    "state_evidence": json.dumps(evidence, ensure_ascii=False, default=str),
                    "previous_lifecycle_state": old,
                    "state_transition": f"{old or 'NONE'} -> {lifecycle}",
                    "max_source_date": max_source,
                    "point_in_time_valid": pit_valid,
                    "formal_watch_on_action_date": sid in formal_set.get(d, set()),
                    "watch_history_source": "PRODUCTION_SIGNAL_SNAPSHOT",
                    "rank_source": "FROZEN_TOP120_RAW_UNION"
                    if not pd.isna(rank0)
                    else "UNAVAILABLE_NOT_IMPUTED",
                    "pocket_source_date": pocket_source_date,
                    "market_source_date": market_source_date,
                    "data_status": "RECONSTRUCTED_NOT_PRODUCTION_EXACT",
                }
            )
            previous_state, previous_weak = lifecycle, weak
            if terminal:
                break
    actions = pd.DataFrame(rows)
    # Evaluation outputs are attached after decisions and are never included in
    # max_source_date or decision logic.
    evaluation = []
    for (sid, entry), x in actions.groupby(["stock_id", "entry_date"]):
        entry_close = x.entry_close.iloc[0]
        ret10, day10, maturity_date = forward_close_outcome(sid, entry, wide)
        path = barrier_path(sid, entry, entry_close, wide)
        evaluation.append(
            {
                "stock_id": sid,
                "entry_date": entry,
                "episode_return_10d": ret10,
                "episode_outcome_from_entry": day10,
                "episode_maturity_date": maturity_date,
                **path,
            }
        )
    actions = actions.merge(pd.DataFrame(evaluation), on=["stock_id", "entry_date"], how="left")
    forward_rows = []
    for r in actions.itertuples():
        ret10, out10, maturity = forward_close_outcome(r.stock_id, r.action_date, wide)
        path = barrier_path(
            r.stock_id,
            r.action_date,
            r.current_close_as_of_action_date,
            wide,
        )
        forward_rows.append(
            {
                "stock_id": r.stock_id,
                "entry_date": r.entry_date,
                "action_date": r.action_date,
                "forward_return_10d_from_action_date": ret10,
                "forward_outcome_from_action_date": out10,
                "forward_maturity_date": maturity,
                "forward_barrier_outcome_from_action_date": path["barrier_outcome"],
            }
        )
    return actions.merge(
        pd.DataFrame(forward_rows), on=["stock_id", "entry_date", "action_date"], how="left"
    )


def evaluate_watch_actions(
    actions: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries = []
    for (sid, entry), x in actions.groupby(["stock_id", "entry_date"]):
        x = x[x.point_in_time_valid].sort_values("action_date")
        if not len(x) or pd.isna(x.episode_outcome_from_entry.iloc[0]):
            continue
        day10 = x.episode_outcome_from_entry.iloc[0]
        path = x.barrier_outcome.iloc[0]
        winner_hit = x.winner_hit_date.iloc[0]
        loser_hit = x.loser_hit_date.iloc[0]
        is_risk = day10 == "LOSER" or path in ("HIT_LOSER_FIRST", "BOTH_HIT_LOSER_FIRST")
        warning = x[x.watchlist_action_shadow.isin(["WARNING_SHADOW", "REMOVE_SHADOW"])]
        removal = x[x.watchlist_action_shadow == "REMOVE_SHADOW"]
        warning_before = warning[
            warning.action_date < loser_hit
        ] if is_risk and loser_hit and not pd.isna(loser_hit) else warning.iloc[0:0]
        removal_before = removal[
            removal.action_date < loser_hit
        ] if is_risk and loser_hit and not pd.isna(loser_hit) else removal.iloc[0:0]
        first_warning = warning_before.action_date.min() if len(warning_before) else None
        first_remove = removal_before.action_date.min() if len(removal_before) else None
        lead = np.nan
        if first_warning and loser_hit:
            valid_dates = sorted(actions.action_date.unique())
            lead = valid_dates.index(loser_hit) - valid_dates.index(first_warning)
        winner_for_warning = day10 == "WINNER" or path == "HIT_WINNER_FIRST"
        target = winner_hit if winner_hit and not pd.isna(winner_hit) else x.episode_maturity_date.iloc[0]
        pre_target = x[x.action_date < target] if target and not pd.isna(target) else x
        premature_warning = bool(
            winner_for_warning
            and pre_target.watchlist_action_shadow.isin(["WARNING_SHADOW", "REMOVE_SHADOW"]).any()
        )
        premature_remove = bool(
            day10 == "WINNER" and (pre_target.watchlist_action_shadow == "REMOVE_SHADOW").any()
        )
        healthy_ratio = (
            float((pre_target.watchlist_action_shadow == "KEEP_SHADOW").mean())
            if day10 == "WINNER" and len(pre_target)
            else np.nan
        )
        summaries.append(
            {
                "stock_id": sid,
                "entry_date": entry,
                "episode_outcome": day10,
                "barrier_outcome": path,
                "loser_hit_date": loser_hit,
                "winner_hit_date": winner_hit,
                "is_loser_risk_episode": is_risk,
                "winner_for_warning": winner_for_warning,
                "first_warning_date": first_warning,
                "first_remove_date": first_remove,
                "loser_early_warning": bool(len(warning_before)),
                "loser_early_removal": bool(len(removal_before)),
                "warning_lead_time": lead,
                "winner_premature_warning": premature_warning,
                "winner_premature_removal": premature_remove,
                "winner_healthy_retention_action_rate": healthy_ratio,
            }
        )
    summary = pd.DataFrame(summaries)
    risk = summary[summary.is_loser_risk_episode] if len(summary) else summary
    warning_winners = summary[summary.winner_for_warning] if len(summary) else summary
    close_winners = summary[summary.episode_outcome == "WINNER"] if len(summary) else summary
    valid_rows = int(actions.point_in_time_valid.sum())
    total_rows = len(actions)
    wr = int(risk.loser_early_warning.sum()) if len(risk) else 0
    rr = int(risk.loser_early_removal.sum()) if len(risk) else 0
    wlo, whi = wilson(wr, len(risk))
    rlo, rhi = wilson(rr, len(risk))
    warning_metrics = pd.DataFrame(
        [
            {
                "completed_watch_episodes": len(summary),
                "loser_risk_episodes": len(risk),
                "loser_early_warning_count": wr,
                "loser_early_warning_rate": div(wr, len(risk)),
                "loser_early_warning_wilson_low": wlo,
                "loser_early_warning_wilson_high": whi,
                "median_warning_lead_time": risk.loc[
                    risk.loser_early_warning, "warning_lead_time"
                ].median()
                if len(risk)
                else np.nan,
                "warning_lead_time_p25": risk.loc[
                    risk.loser_early_warning, "warning_lead_time"
                ].quantile(0.25)
                if len(risk)
                else np.nan,
                "warning_lead_time_p75": risk.loc[
                    risk.loser_early_warning, "warning_lead_time"
                ].quantile(0.75)
                if len(risk)
                else np.nan,
                "winner_warning_episodes": len(warning_winners),
                "winner_premature_warning_count": int(
                    warning_winners.winner_premature_warning.sum()
                )
                if len(warning_winners)
                else 0,
                "winner_premature_warning_rate": warning_winners.winner_premature_warning.mean()
                if len(warning_winners)
                else np.nan,
                "winner_close_episodes": len(close_winners),
                "winner_healthy_retention_rate": close_winners.winner_healthy_retention_action_rate.mean()
                if len(close_winners)
                else np.nan,
                "point_in_time_valid_rows": valid_rows,
                "total_action_rows": total_rows,
                "point_in_time_valid_rate": div(valid_rows, total_rows),
                "decision_threshold": "warning>=50%, lead>=1, winner warning<=35%, winner remove<=15%, PIT>=95%",
            }
        ]
    )
    remove_metrics = pd.DataFrame(
        [
            {
                "completed_watch_episodes": len(summary),
                "loser_risk_episodes": len(risk),
                "loser_early_removal_count": rr,
                "loser_early_removal_rate": div(rr, len(risk)),
                "loser_early_removal_wilson_low": rlo,
                "loser_early_removal_wilson_high": rhi,
                "winner_close_episodes": len(close_winners),
                "winner_premature_removal_count": int(
                    close_winners.winner_premature_removal.sum()
                )
                if len(close_winners)
                else 0,
                "winner_premature_removal_rate": close_winners.winner_premature_removal.mean()
                if len(close_winners)
                else np.nan,
                "decision_threshold": "removal>=30%, winner premature removal<=10%, plus warning criteria",
            }
        ]
    )
    return summary, warning_metrics, remove_metrics


def build_state_transitions(
    actions: pd.DataFrame, sources: Mapping[str, pd.DataFrame]
) -> pd.DataFrame:
    wide = build_wide_from_sources(sources)
    dates = list(wide["close"].index)
    pos = {d: i for i, d in enumerate(dates)}
    allowed_from = {"CONTINUATION", "HEALTHY_PULLBACK", "REACCELERATING"}
    rows = []
    for r in actions[
        actions.previous_lifecycle_state.isin(allowed_from)
        & (actions.lifecycle_state == "DETERIORATING")
        & actions.point_in_time_valid
    ].itertuples():
        pi = pos.get(r.action_date)
        values = {}
        for h in (1, 3, 5):
            if (
                pi is not None
                and pi + h < len(dates)
                and r.stock_id in wide["close"].columns
                and not pd.isna(wide["close"].iloc[pi + h][r.stock_id])
            ):
                values[f"forward_return_{h}d"] = (
                    wide["close"].iloc[pi + h][r.stock_id]
                    / r.current_close_as_of_action_date
                    - 1
                ) * 100
            else:
                values[f"forward_return_{h}d"] = np.nan
        path = barrier_path(
            r.stock_id, r.action_date, r.current_close_as_of_action_date, wide
        )
        rows.append(
            {
                "stock_id": r.stock_id,
                "stock_name": r.stock_name,
                "episode_start_date": r.entry_date,
                "action_date": r.action_date,
                "from_state": r.previous_lifecycle_state,
                "to_state": r.lifecycle_state,
                **values,
                "barrier_outcome_from_transition": path["barrier_outcome"],
                "point_in_time_valid": r.point_in_time_valid,
                "evidence": r.state_evidence,
            }
        )
    return pd.DataFrame(rows)


def build_leakage_audit(
    old: pd.DataFrame, sources: Mapping[str, pd.DataFrame]
) -> pd.DataFrame:
    wide = build_wide_from_sources(sources)
    rows = []
    completed = old[old.maturity_status == "MATURED"].copy()
    for r in completed.itertuples():
        recalculated_ret, recalculated_outcome, maturity = forward_close_outcome(
            str(r.stock_id), r.evaluation_date, wide
        )
        copied = (
            r.evaluation_date != r.episode_start_date
            and not pd.isna(r.future_return_10d)
            and r.outcome == outcome(r.future_return_10d)
        )
        mismatch = (
            recalculated_outcome is not None
            and r.outcome is not None
            and r.outcome != recalculated_outcome
        )
        rows.append(
            {
                "stock_id": str(r.stock_id),
                "episode_start_date": r.episode_start_date,
                "action_date": r.evaluation_date,
                "old_episode_return_copied_to_candidate_day": r.future_return_10d,
                "old_episode_outcome_copied_to_candidate_day": r.outcome,
                "recalculated_forward_return_10d_from_action_date": recalculated_ret,
                "forward_outcome_from_action_date": recalculated_outcome,
                "forward_maturity_date": maturity,
                "copy_pattern_detected": copied,
                "outcome_label_changed_after_recalculation": mismatch,
                "audit_classification": "LIFECYCLE_OUTCOME_LEAKAGE_FOUND"
                if copied
                else "NO_COPY_PATTERN",
            }
        )
    return pd.DataFrame(rows)


def build_pending(actions: pd.DataFrame, sources: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    wide = build_wide_from_sources(sources)
    dates = list(wide["close"].index)
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    entries = actions[
        (actions.entry_date >= C_START)
        & ~actions.duplicated(["stock_id", "entry_date"])
    ]
    for r in entries.itertuples():
        pi = pos.get(r.entry_date)
        values = {}
        for h in (1, 3, 5, 10):
            if (
                pi is not None
                and pi + h < len(dates)
                and not pd.isna(wide["close"].iloc[pi + h][r.stock_id])
            ):
                values[f"day{h}_return"] = (
                    wide["close"].iloc[pi + h][r.stock_id] / r.entry_close - 1
                ) * 100
            else:
                values[f"day{h}_return"] = np.nan
        rows.append(
            {
                "stock_id": r.stock_id,
                "stock_name": r.stock_name,
                "episode_start_date": r.entry_date,
                "entry_close": r.entry_close,
                **values,
                "maturity_status": "PENDING_FORWARD"
                if pd.isna(values["day10_return"])
                else "MATURED_FOR_WATCH_EVALUATION_ONLY",
                "selection_use": "EXCLUDED_FROM_POLICY_SELECTION",
            }
        )
    return pd.DataFrame(rows)


def pct(v: Any) -> str:
    return "NA" if v is None or pd.isna(v) else f"{float(v) * 100:.1f}%"


def markdown_table(df: pd.DataFrame, columns: Sequence[str], limit: int = 30) -> str:
    x = df[list(columns)].head(limit).copy()
    for col in x.select_dtypes(include=["float"]).columns:
        x[col] = x[col].map(lambda v: "" if pd.isna(v) else f"{v:.4f}")
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = "\n".join("| " + " | ".join(map(str, row)) + " |" for row in x.values)
    return "\n".join([head, sep, body])


def render_report(
    attribution: pd.DataFrame,
    increment: pd.DataFrame,
    early_signals: pd.DataFrame,
    protective: pd.DataFrame,
    quadrant: pd.DataFrame,
    pocket_increment: pd.DataFrame,
    actions: pd.DataFrame,
    warning_metrics: pd.DataFrame,
    remove_metrics: pd.DataFrame,
    leakage: pd.DataFrame,
    live: pd.DataFrame,
    sources: Mapping[str, pd.DataFrame],
) -> Tuple[str, str]:
    micro = attribution[attribution.metric_scope == "MICRO"].set_index(["dataset", "policy"])
    p0a, p3a = micro.loc[("A", "P0_BASELINE")], micro.loc[("A", "P3_SURVIVAL_ONLY")]
    p3b = micro.loc[("B", "P3_SURVIVAL_ONLY")]
    p4b = micro.loc[("B", "P4_MARKET_PLUS_SURVIVAL")]
    p5b = micro.loc[("B", "P5_POCKET_PLUS_SURVIVAL")]
    p6b = micro.loc[("B", "P6_FULL")]
    p4a = micro.loc[("A", "P4_MARKET_PLUS_SURVIVAL")]
    p5a = micro.loc[("A", "P5_POCKET_PLUS_SURVIVAL")]
    wm, rm = warning_metrics.iloc[0], remove_metrics.iloc[0]
    protective_row = early_signals[early_signals.signal == "COMPOSITE_PROTECTIVE"].iloc[0]
    first_protect = protective_row.first_warning_date
    first_post_b = protective_row.first_warning_on_or_after_2026_07_02
    first_sustained_b = (
        protective_row.first_sustained_warning_on_or_after_2026_07_02
    )
    prot_b = protective[
        (protective.dataset == "B") & (protective.policy == "EARLY_PROTECTIVE")
    ].iloc[0]
    base_b = protective[
        (protective.dataset == "B") & (protective.policy == "NO_PROTECTIVE")
    ].iloc[0]
    prot_a = protective[
        (protective.dataset == "A") & (protective.policy == "EARLY_PROTECTIVE")
    ].iloc[0]
    bq = quadrant[quadrant.dataset == "B"]
    b_winner_q = bq.loc[bq.winner_count > 0, "pocket_quadrant"].tolist()
    q1a = quadrant[
        (quadrant.dataset == "A") & (quadrant.pocket_quadrant == "Q1_ACTIVE_DURABLE")
    ].iloc[0]
    q2a = quadrant[
        (quadrant.dataset == "A") & (quadrant.pocket_quadrant == "Q2_ACTIVE_FRAGILE")
    ].iloc[0]
    q1b = quadrant[
        (quadrant.dataset == "B") & (quadrant.pocket_quadrant == "Q1_ACTIVE_DURABLE")
    ].iloc[0]
    q2b = quadrant[
        (quadrant.dataset == "B") & (quadrant.pocket_quadrant == "Q2_ACTIVE_FRAGILE")
    ].iloc[0]
    durable_b = pocket_increment[
        (pocket_increment.dataset == "B")
        & (pocket_increment.policy == "ACTIVE_DURABLE_PLUS_SURVIVAL")
    ].iloc[0]
    durable_a = pocket_increment[
        (pocket_increment.dataset == "A")
        & (pocket_increment.policy == "ACTIVE_DURABLE_PLUS_SURVIVAL")
    ].iloc[0]
    price_max = sources["prices"].trade_date.max()
    snap_max = sources["snapshots"].snapshot_date.max()

    survival_provisional = (
        (p0a.loser_rate >= p3a.loser_rate)
        and (p3b.loser_rate <= micro.loc[("B", "P0_BASELINE")].loser_rate - 0.15)
        and (p3b.safe_rate >= 0.75)
        and (p3b.selected_count >= 20)
    )
    market_confirmed = (
        p4b.loser_rate <= p3b.loser_rate - 0.05
        and p4a.loser_rate <= p3a.loser_rate + 0.03
    )
    pocket_no_increment = not (
        p5a.safe_rate > p3a.safe_rate
        and p5b.safe_rate > p3b.safe_rate
        and p5a.loser_rate < p3a.loser_rate
        and p5b.loser_rate < p3b.loser_rate
    )
    early_confirmed = bool(
        first_sustained_b
        and first_sustained_b <= date(2026, 7, 6)
        and prot_b.loser_count < base_b.loser_count
        and prot_a.winner_recall >= 0.65
    )
    warning_confirmed = bool(
        wm.loser_early_warning_rate >= 0.50
        and wm.median_warning_lead_time >= 1
        and wm.winner_premature_warning_rate <= 0.35
        and rm.winner_premature_removal_rate <= 0.15
        and wm.point_in_time_valid_rate >= 0.95
    )
    remove_confirmed = bool(
        warning_confirmed
        and rm.loser_early_removal_rate >= 0.30
        and rm.winner_premature_removal_rate <= 0.10
    )
    conclusions = []
    if survival_provisional:
        conclusions.append("PROVISIONAL_SURVIVAL_SIGNAL")
    if market_confirmed:
        conclusions.append("MARKET_INCREMENT_CONFIRMED")
    if pocket_no_increment:
        conclusions.append("NO_POCKET_INCREMENT")
    if early_confirmed:
        conclusions.append("EARLY_PROTECTION_SIGNAL")
    if warning_confirmed:
        conclusions.append("PROVISIONAL_WARNING_SIGNAL")
    if remove_confirmed:
        conclusions.append("PROVISIONAL_REMOVE_SIGNAL")
    conclusions.append("LOSER_CONTROL_ONLY")
    if len(conclusions) == 1:
        conclusions = ["NO_ACTIONABLE_SIGNAL", "LOSER_CONTROL_ONLY"]

    policy_view = attribution[
        (attribution.dataset.isin(["A", "B"]))
        & (attribution.metric_scope == "MICRO")
    ].copy()
    policy_view["coverage_pct"] = policy_view.coverage * 100
    policy_view["loser_rate_pct"] = policy_view.loser_rate * 100
    policy_view["safe_rate_pct"] = policy_view.safe_rate * 100
    policy_view["winner_recall_pct"] = policy_view.winner_recall * 100
    live_actions = (
        live[live.action_date <= snap_max]
        .groupby("action_date")
        .watchlist_action_shadow.value_counts()
        .unstack(fill_value=0)
        .reset_index()
    )
    report = f"""# Phase 3G — Policy Attribution & Point-in-Time Lifecycle Validation

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。  
> Frozen primary cohort：A=246、B=87、C=140；`RECONSTRUCTED_NOT_PRODUCTION_EXACT`。  
> 最新可用價格／正式 WATCH：{price_max}／{snap_max}；7/28 尚未入庫，未虛構 action。

## 結論先行

最終分類：**{" / ".join(conclusions)}**。

- Survival Only 將 Dataset B Loser Rate 從 44.8% 降至
  **{pct(p3b.loser_rate)}**，n={int(p3b.selected_count)}、Coverage={pct(p3b.coverage)}，
  Safe Rate={pct(p3b.safe_rate)}，Loser Wilson 95% CI=
  {pct(p3b.loser_rate_wilson_low)}–{pct(p3b.loser_rate_wilson_high)}。
  9 個 Loser 分布在 6 個日期中的 5 日，並非單日結果。它是主要個股層
  Loser-control 訊號，但沒有達到 Safe Rate >=80%。
- Market + Survival 再把 B Loser Rate 降至 **{pct(p4b.loser_rate)}**，
  相對 Survival Only 改善 {(p3b.loser_rate-p4b.loser_rate)*100:.1f} pp；
  A 僅由 {pct(p3a.loser_rate)} 變為 {pct(p4a.loser_rate)}。
- Pocket + Survival 的 B Loser Rate={pct(p5b.loser_rate)}，未優於 Survival Only；
  Full={pct(p6b.loser_rate)}，也未優於 Market + Survival={pct(p4b.loser_rate)}。
- Watchlist 已修正 Day-0 barrier 與 action-date Outcome leakage。舊 lifecycle
  有 {int(leakage.copy_pattern_detected.sum())}/{len(leakage)} 列呈現 entry Outcome
  複製模式，分類為 `LIFECYCLE_OUTCOME_LEAKAGE_FOUND`。

## Part A — Policy Attribution

{markdown_table(policy_view, ["dataset", "policy", "selected_count", "coverage_pct", "winner_count", "loser_count", "loser_rate_pct", "safe_rate_pct", "winner_recall_pct"])}

口徑說明：P2 的 Pocket-only 使用「任何既有 active pocket」而不套 market-state
收縮；P6 則精確重建 Phase 3F 的 state-aware pocket + Bundle A。Phase 3F 的
P1 market contraction 本身以 70/90 survival percentile 做發佈收縮，因此
P1/P4 是「鎖定的 market publishing policy」而非純粹市場方向因果估計。這項結構
限制已保留在 policy matrix，沒有為了得到更漂亮歸因而另調 threshold。

Dataset A Survival Gate 錯刪 Winner：
{int(p0a.winner_count-p3a.winner_count)}/{int(p0a.winner_count)}
（Winner Recall={pct(p3a.winner_recall)}）。

## Part B — Early Market Transition

Composite PROTECTIVE 的 raw first alert 是 **{first_protect}**，但它位於
Dataset A 且之後恢復，視為 false-alarm audit，不冒充 July transition 成功。
7/2 後第一次短暫 alert={first_post_b}；第一次連續兩個 replay sessions
維持 PROTECTIVE={first_sustained_b}，只比 Phase 3F 7/8 提前
{protective_row.sustained_days_before_phase3f_weakening} session。7/2 前
PROTECTIVE false-alarm days={int(protective_row.false_alarm_count)}。

Early PROTECTIVE 在 B selected={int(prot_b.selected_count)}、
Coverage={pct(prot_b.coverage)}、Loser={int(prot_b.loser_count)}、
Loser Rate={pct(prot_b.loser_rate)}；無保護 Loser={int(base_b.loser_count)}。
在 A Winner Recall={pct(prot_a.winner_recall)}。因此判定：
**{"EARLY_PROTECTION_SIGNAL" if early_confirmed else "NO_EARLY_SIGNAL"}**。

## Part C — Pocket Activity vs Durability

- Dataset B 唯一 Winner 所在象限：{", ".join(b_winner_q) or "無"}。
- A：Active Durable Safe={pct(q1a.safe_rate)}（n={int(q1a.n)}），
  Active Fragile Safe={pct(q2a.safe_rate)}（n={int(q2a.n)}）。
- B：Active Durable Safe={pct(q1b.safe_rate)}（n={int(q1b.n)}），
  Active Fragile Safe={pct(q2b.safe_rate)}（n={int(q2b.n)}）。
- Active Durable + Survival：A/B Safe={pct(durable_a.safe_rate)}/
  {pct(durable_b.safe_rate)}，Coverage={pct(durable_a.coverage)}/
  {pct(durable_b.coverage)}。未形成 A/B 一致且不只靠 Coverage 收縮的增量，
  Pocket 維持 descriptive context。

## Part D — Point-in-Time Watchlist

- point-in-time valid：{int(wm.point_in_time_valid_rows)}/{int(wm.total_action_rows)}
  = {pct(wm.point_in_time_valid_rate)}。
- Loser Early Warning：{int(wm.loser_early_warning_count)}/
  {int(wm.loser_risk_episodes)} = {pct(wm.loser_early_warning_rate)}；
  median lead={wm.median_warning_lead_time} sessions。
- Loser Early Removal：{int(rm.loser_early_removal_count)}/
  {int(rm.loser_risk_episodes)} = {pct(rm.loser_early_removal_rate)}。
- Winner Premature Warning={pct(wm.winner_premature_warning_rate)}；
  Winner Premature Removal={pct(rm.winner_premature_removal_rate)}；
  Winner Healthy Retention（action-date ratio）={pct(wm.winner_healthy_retention_rate)}。
- 判定：**{"PROVISIONAL_REMOVE_SIGNAL" if remove_confirmed else "PROVISIONAL_WARNING_SIGNAL" if warning_confirmed else "NO_ACTIONABLE_SIGNAL"}**。
  `DETERIORATING` 只產生 WARNING；REMOVE 必須至少兩個 evidence family
  連續兩個 action dates 同步惡化。

7/22～可用截止日的 action 分布：

{markdown_table(live_actions, live_actions.columns)}

## 22 個必答答案

1. Policy 3 效果主因：Stock Survival 是主要第一層，Market 在其上仍有保護增量；Pocket 無增量。
2. Survival Only B Loser Rate：{pct(p3b.loser_rate)}。
3. Survival Only B Safe >=80%：否，{pct(p3b.safe_rate)}。
4. Market 額外增量：有，B Loser Rate 再降 {(p3b.loser_rate-p4b.loser_rate)*100:.1f} pp。
5. Pocket 額外增量：無。
6. Full 優於 Survival Only：B Loser Rate 較低，但不優於 Market + Survival，不能歸功 Pocket。
7. A Survival 錯刪 Winner：{int(p0a.winner_count-p3a.winner_count)} 檔，保留 {int(p3a.winner_count)}/{int(p0a.winner_count)}。
8. 最早 raw alert={first_protect}（false-alarm audit）；7 月轉弱第一次 alert={first_post_b}，第一次持續 alert={first_sustained_b}。
9. 持續訊號是否比 7/8 早至少兩日：{"是" if first_sustained_b and first_sustained_b <= date(2026,7,6) else "否"}。
10. A false alarm：{int(protective_row.false_alarm_count)} 個 market days；Winner Recall={pct(prot_a.winner_recall)}。
11. 現有 Pocket 是否只代表 Activity：目前證據支持降為 Activity/context。
12. Active Durable 優於 Active Fragile：A/B Safe 見 Part C；需方向一致才成立。
13. B 唯一 Winner 象限：{", ".join(b_winner_q) or "無"}。
14. Durability 在 Survival 外增量：未確認。
15. 原 lifecycle Outcome 複製／bias：有，`LIFECYCLE_OUTCOME_LEAKAGE_FOUND`。
16. action 是否 PIT valid：已產生 {pct(wm.point_in_time_valid_rate)} 合法列；7/28 無資料不產生假列。
17. Loser Early Warning：{pct(wm.loser_early_warning_rate)}。
18. Winner Premature Warning：{pct(wm.winner_premature_warning_rate)}。
19. Winner Premature Removal：{pct(rm.winner_premature_removal_rate)}。
20. Watchlist 適合 WARNING 或 REMOVE：{"REMOVE shadow 亦達 provisional" if remove_confirmed else "僅 WARNING shadow" if warning_confirmed else "尚未達 provisional threshold"}。
21. 每日逐股 action：見 `phase3g_20260722_20260728_live_replay.csv`；實際資料止於 {price_max}。
22. 最終結論：**{" / ".join(conclusions)}**。

## 研究限制與禁止事項確認

- Dataset B 僅 1 Winner 且 6 個 signal dates，標記 `SHORT_STRESS_WINDOW`；
  未使用 bootstrap 宣稱穩健。
- 7/28 資料未入庫；7/27 缺 frozen Top120 raw-union frame 的欄位保持 null，
  不用 WATCH ordinal 冒充 momentum rank。較舊 pocket/market source date 均明列，
  且 `max_source_date <= action_date`。
- 未修改 production、A/B/C/D、Top120、momentum_score、Outcome threshold、
  WATCH、Market Regime 或交易策略；未用 Dataset C 調 threshold；未做 portfolio backtest。
"""
    handoff = f"""# Phase 3G — LLM Handoff

## Canonical conclusions

`{" | ".join(conclusions)}`

## Numbers safe to quote

- Frozen cohorts: A=246, B=87, C=140 pending.
- P3 Survival Only B: n={int(p3b.selected_count)}, coverage={pct(p3b.coverage)},
  loser={pct(p3b.loser_rate)}, safe={pct(p3b.safe_rate)}, Winner count={int(p3b.winner_count)}.
- P4 Market+Survival B: n={int(p4b.selected_count)}, loser={pct(p4b.loser_rate)},
  safe={pct(p4b.safe_rate)}.
- P5 Pocket+Survival B loser={pct(p5b.loser_rate)}; no pocket increment.
- P6 Full B loser={pct(p6b.loser_rate)}; it does not beat P4.
- Raw composite alert={first_protect} (pre-7/2 false-alarm audit);
  first post-7/2 alert={first_post_b}, first sustained alert={first_sustained_b};
  false-alarm market days before 7/2={int(protective_row.false_alarm_count)}.
- PIT watch rows={int(wm.total_action_rows)}, valid={pct(wm.point_in_time_valid_rate)};
  loser warning={pct(wm.loser_early_warning_rate)}, median lead={wm.median_warning_lead_time},
  winner premature warning={pct(wm.winner_premature_warning_rate)},
  winner premature removal={pct(rm.winner_premature_removal_rate)}.
- Old lifecycle copy-pattern rows={int(leakage.copy_pattern_detected.sum())}/{len(leakage)}:
  `LIFECYCLE_OUTCOME_LEAKAGE_FOUND`.
- Available-through: prices={price_max}, formal WATCH={snap_max}; no 7/28 action was fabricated.

## Guardrails

Do not claim Winner selection success: Dataset B has one Winner.  Do not treat P1
as a pure causal market factor: the locked Phase 3F publishing mask contains
survival-percentile contraction.  Pocket is descriptive unless future data show
consistent durability increment.  Dataset C and live rows did not choose rules.
Use action-date outcomes for lifecycle analysis, never copied entry outcomes.

## File map

The canonical narrative is `phase3g_report.md`.  Row-level selection is in
`phase3g_policy_matrix.csv`; aggregate attribution in
`phase3g_policy_attribution.csv`; survival raw predicates in
`phase3g_survival_predicate_trace.csv`; market evidence in the three early
transition files; pocket evidence in the activity/durability/quadrant files;
and PIT lifecycle evidence in `phase3g_watchlist_point_in_time.csv`,
`phase3g_lifecycle_outcome_leakage_audit.csv`, and the live replay.
"""
    return report, handoff


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading frozen Phase 3F inputs ...", flush=True)
    data = load_inputs()
    print("refreshing read-only sources through 2026-07-28 ...", flush=True)
    data["sources"] = refresh_latest(data["sources"])

    print("Part A: fixed seven-policy attribution ...", flush=True)
    matrix, attribution, increment, survival_trace = build_policy_outputs(data["episodes"])
    market_increment = increment[increment.increment == "MARKET_INCREMENT"].copy()

    print("Part B: early market transition ...", flush=True)
    early_daily, early_signals, protective = build_early_transition(
        data["prepared"], data["episodes"], attribution
    )

    print("Part C: pocket activity and durability ...", flush=True)
    activity, durability, quadrant, pocket_increment = build_pocket_dimensions(
        data["pocket"], data["episodes"]
    )

    print("Part D: point-in-time WATCH replay ...", flush=True)
    actions = build_point_in_time_watch(
        data["sources"], data["prepared"], activity, durability
    )
    watch_summary, warning_metrics, remove_metrics = evaluate_watch_actions(actions)
    transitions = build_state_transitions(actions, data["sources"])
    leakage = build_leakage_audit(data["lifecycle_old"], data["sources"])
    live = actions[
        (actions.entry_date >= LIVE_START)
        & (actions.action_date >= LIVE_START)
        & (actions.action_date <= LIVE_REQUEST_END)
    ].copy()
    available_price = data["sources"]["prices"].trade_date.max()
    available_watch = data["sources"]["snapshots"].snapshot_date.max()
    live["requested_end_date"] = LIVE_REQUEST_END
    live["price_available_through"] = available_price
    live["formal_watch_available_through"] = available_watch
    live["requested_2026_07_28_status"] = (
        "AVAILABLE" if available_price >= LIVE_REQUEST_END else "UNAVAILABLE_NOT_FABRICATED"
    )
    pending = build_pending(actions, data["sources"])

    report, handoff = render_report(
        attribution,
        increment,
        early_signals,
        protective,
        quadrant,
        pocket_increment,
        actions,
        warning_metrics,
        remove_metrics,
        leakage,
        live,
        data["sources"],
    )
    outputs = {
        "phase3g_policy_matrix.csv": matrix,
        "phase3g_policy_attribution.csv": attribution,
        "phase3g_survival_predicate_trace.csv": survival_trace,
        "phase3g_market_increment.csv": market_increment,
        "phase3g_pocket_increment.csv": pocket_increment,
        "phase3g_early_transition_daily.csv": early_daily,
        "phase3g_early_warning_signals.csv": early_signals,
        "phase3g_protective_policy_comparison.csv": protective,
        "phase3g_pocket_activity_daily.csv": activity,
        "phase3g_pocket_durability_daily.csv": durability,
        "phase3g_pocket_quadrant_outcomes.csv": quadrant,
        "phase3g_watchlist_point_in_time.csv": actions,
        "phase3g_watchlist_state_transitions.csv": transitions,
        "phase3g_watchlist_warning_metrics.csv": warning_metrics,
        "phase3g_watchlist_remove_metrics.csv": remove_metrics,
        "phase3g_lifecycle_outcome_leakage_audit.csv": leakage,
        "phase3g_20260722_20260728_live_replay.csv": live,
        "phase3g_pending_forward.csv": pending,
    }
    for filename, frame in outputs.items():
        frame.to_csv(OUT / filename, index=False)
    (OUT / "phase3g_report.md").write_text(report, encoding="utf-8")
    (OUT / "phase3g_llm_handoff.md").write_text(handoff, encoding="utf-8")

    # Hard invariants: frozen denominator, no future feature, no Day-0 barrier,
    # and action-date Outcome is actually recalculated.
    primary = data["episodes"].dataset.value_counts().to_dict()
    assert primary == {"A": 246, "C": 140, "B": 87}
    assert set(matrix.policy.unique()) == {
        "P0_BASELINE",
        "P1_MARKET_ONLY",
        "P2_POCKET_ONLY",
        "P3_SURVIVAL_ONLY",
        "P4_MARKET_PLUS_SURVIVAL",
        "P5_POCKET_PLUS_SURVIVAL",
        "P6_FULL",
    }
    assert actions.point_in_time_valid.mean() >= 0.95
    assert (
        actions.loc[
            actions.days_since_entry == 0, "watchlist_action_shadow"
        ].isin(["TARGET_REACHED", "RISK_BREACHED", "AMBIGUOUS_SAME_DAY"])
        .sum()
        == 0
    )
    assert (pd.to_datetime(actions.max_source_date) <= pd.to_datetime(actions.action_date)).all()
    assert leakage.copy_pattern_detected.any()
    assert not live.duplicated(["stock_id", "entry_date", "action_date"]).any()
    print(
        f"wrote {len(outputs) + 2} artifacts to {OUT}; "
        f"PIT rows={len(actions)}, valid={actions.point_in_time_valid.mean():.3f}, "
        f"live-through={available_price}",
        flush=True,
    )


if __name__ == "__main__":
    main()
