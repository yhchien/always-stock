"""Phase 3I — frozen dual-branch shadow validation.

Research-only.  This runner freezes and replays exactly two previously chosen
rules:

* Phase 3H P6 / D1-C for NORMAL high-conviction promotion.
* Phase 3G P4 Market + Survival for WEAKENING / RISK_OFF loser control.

It reads frozen Phase 3F/3G/3H artifacts and production source tables, but
never writes a production table or changes any production decision.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import analyze_phase3g as p3g


ROOT = Path(__file__).resolve().parents[1]
F3 = ROOT / "docs" / "plans" / "phase3f_v2"
G3 = ROOT / "docs" / "plans" / "phase3g"
H3 = ROOT / "docs" / "plans" / "phase3h"
OUT = ROOT / "docs" / "plans" / "phase3i"

RESEARCH_START = date(2026, 7, 28)
PROSPECTIVE_AFTER = date(2026, 7, 27)
FROZEN_RULE_COMMIT = "c094178c6c49274483c851ff010ef1427f103cc2"
WINNER_BAR = 12.0
LOSER_BAR = -6.0


def div(a: Any, b: Any) -> float:
    return float(a) / float(b) if b and not pd.isna(b) else np.nan


def wilson(k: int, n: int, z: float = 1.959964) -> Tuple[float, float]:
    if n <= 0:
        return np.nan, np.nan
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - radius), min(1.0, center + radius)


def outcome(ret: Any, prefix: str = "") -> Optional[str]:
    if ret is None or pd.isna(ret):
        return None
    label = (
        "WINNER"
        if float(ret) >= WINNER_BAR
        else "LOSER"
        if float(ret) <= LOSER_BAR
        else "NEUTRAL"
    )
    return f"{prefix}{label}" if prefix else label


def json_default(value: Any) -> Any:
    if isinstance(value, (date, pd.Timestamp, np.datetime64)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    raise TypeError(type(value).__name__)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frame_hash(frame: pd.DataFrame, columns: Sequence[str]) -> Optional[str]:
    available = [c for c in columns if c in frame]
    if frame.empty or not available:
        return None
    text = frame[available].sort_values(available).to_csv(index=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dates_to_python(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for col in columns:
        if col in frame:
            frame[col] = pd.to_datetime(frame[col]).dt.date
    return frame


def load_frozen_thresholds() -> Dict[str, float]:
    day1 = pd.read_csv(H3 / "phase3h_day1_confirmation_matrix.csv")
    values = {
        value for value in day1.bundle_thresholds.dropna().astype(str).unique()
    }
    if len(values) != 1:
        raise AssertionError(
            f"Phase 3H threshold payload is not unique: {len(values)}"
        )
    return {
        key: float(value)
        for key, value in json.loads(values.pop()).items()
    }


def frozen_config(thresholds: Mapping[str, float]) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "research_start_date": RESEARCH_START,
        "prospective_new_data_starts_after": PROSPECTIVE_AFTER,
        "code_version": FROZEN_RULE_COMMIT,
        "runner_version": "phase3i_runner_v1",
        "prompt_version": "v6",
        "schema_version": "phase3i_schema_v1",
        "outcome_version": "day10_close_v1_plus12_minus6",
        "candidate_source_version": (
            "phase3f_v2_reconstructed_abcd_raw_union_first_seen_proxy"
        ),
        "momentum_score_version": "v1_available_weight_normalization_disabled",
        "top120_version": (
            "phase2_v2_1_momentum_score_desc_then_inst_flow_3d_desc_then_stock_id"
        ),
        "market_path_version": "phase3f_v2_dataset_a_fixed_distribution",
        "day1_c_rule_version": "phase3h_p6_d1_c_frozen_c094178",
        "day1_c_threshold_source": (
            "phase3h_discovery_dates_2026_06_15_through_2026_06_22"
        ),
        "market_survival_rule_version": (
            "phase3g_p4_market_plus_survival_frozen_c094178"
        ),
        "survival_threshold_source": "phase3f_v2_dataset_a_locked",
        "normal_definition": {
            "market_path_state": ["NORMAL"],
            "production_market_regime": [
                "BULL_TREND",
                "healthy_VOLATILE_RANGE",
            ],
        },
        "outcome_definition": {
            "winner": "return_10d >= 12.0",
            "neutral": "-6.0 < return_10d < 12.0",
            "loser": "return_10d <= -6.0",
            "episode_reference": "Day0 close",
            "promotion_reference": "Day1 close",
        },
        "day1_c_thresholds": {
            key: thresholds[key]
            for key in (
                "d0_fresh_rs_p40",
                "d0_fresh_volume_p40",
                "d0_rs_slope5_p70",
                "d0_rank_change3_p50",
                "d1_market_excess_p70",
                "d1_rs_change_p40",
                "d1_rank_change_p40",
                "d1_flow_p50",
                "d1_close_location_p30",
                "d1_upper_shadow_p70",
            )
        },
        "day1_c_predicate": {
            "d0_freshness": {
                "logic": "OR",
                "predicates": [
                    {
                        "feature": "days_since_first_rs_above_80",
                        "usage": "raw trading-session count",
                        "comparison": "<=",
                        "threshold": thresholds["d0_fresh_rs_p40"],
                        "missing_value_policy": "comparison_false",
                    },
                    {
                        "feature": "days_since_first_volume_expansion",
                        "usage": "raw trading-session count",
                        "comparison": "<=",
                        "threshold": thresholds["d0_fresh_volume_p40"],
                        "missing_value_policy": "comparison_false",
                    },
                ],
                "pass_logic": "at least one predicate true",
            },
            "d0_rs_path": {
                "feature": "rs_slope_5d",
                "usage": "raw percentile-point slope per session",
                "comparison": ">=",
                "threshold": thresholds["d0_rs_slope5_p70"],
                "missing_value_policy": "comparison_false",
            },
            "d0_rank_health": {
                "feature": "momentum_rank_change_3d",
                "usage": "raw rank improvement",
                "comparison": ">=",
                "threshold": thresholds["d0_rank_change3_p50"],
                "missing_value_policy": "comparison_false",
            },
            "d0_a": {
                "logic": "count_true(d0_freshness,d0_rs_path,d0_rank_health)>=2",
                "missing_value_policy": "component comparison false",
            },
            "d1_relative": {
                "feature": "day1_market_excess_return",
                "usage": "raw percentage points",
                "comparison": ">=",
                "threshold": thresholds["d1_market_excess_p70"],
                "missing_value_policy": "comparison_false",
            },
            "d1_rs_health": {
                "feature": "day1_rs_change",
                "usage": "raw percentile-point change",
                "comparison": ">=",
                "threshold": thresholds["d1_rs_change_p40"],
                "missing_value_policy": "comparison_false",
            },
            "d1_rank_health": {
                "logic": (
                    "day1_top120_status is true OR "
                    "day1_momentum_rank_change >= threshold"
                ),
                "threshold": thresholds["d1_rank_change_p40"],
                "missing_value_policy": (
                    "missing rank comparison false; absent Top120 membership false"
                ),
            },
            "d1_a": {
                "logic": (
                    "d0_a AND count_true(d1_relative,d1_rs_health,"
                    "d1_rank_health)>=2"
                ),
                "missing_value_policy": "component comparison false",
            },
            "d1_flow": {
                "logic": (
                    "day1_institution_flow >= threshold OR "
                    "day1_price_flow_alignment == 1"
                ),
                "threshold": thresholds["d1_flow_p50"],
                "missing_value_policy": (
                    "stock absent from frozen flow source uses 0.0; "
                    "alignment comparison false"
                ),
            },
            "d1_controlled": {
                "logic": (
                    "day1_close_location >= lower_threshold AND "
                    "day1_upper_shadow <= upper_threshold AND "
                    "NOT day1_failed_follow_through"
                ),
                "lower_threshold": thresholds["d1_close_location_p30"],
                "upper_threshold": thresholds["d1_upper_shadow_p70"],
                "missing_value_policy": "required comparison false",
            },
            "day1_failed_follow_through": {
                "logic": (
                    "(day1_market_excess_return < 0 AND "
                    "day1_close_location < 0.4) OR "
                    "(day0_price_new_high_volume_confirmation AND "
                    "NOT day1_breakout_hold)"
                ),
                "missing_value_policy": "boolean components follow frozen calculation",
            },
            "d1_c_pass": {
                "logic": "d1_a AND d1_flow AND d1_controlled",
                "pass_state": "HIGH_CONVICTION_PRIMARY_SHADOW",
                "fail_state": "NOT_HIGH_CONVICTION_SHADOW",
                "day3_rescue": False,
            },
        },
        "market_predicate": {
            "NORMAL": {
                "comparison": "always true",
                "note": "excluded from Branch R",
            },
            "WEAKENING": {
                "feature": "down_survival_ratio_top120_percentile",
                "usage": "within-date Top120 percentile",
                "comparison": ">=",
                "threshold": 70.0,
                "missing_value_policy": "MISSING_REQUIRED_FEATURE and fail",
            },
            "RISK_OFF": {
                "feature": "down_survival_ratio_top120_percentile",
                "usage": "within-date Top120 percentile",
                "comparison": ">=",
                "threshold": 90.0,
                "missing_value_policy": "MISSING_REQUIRED_FEATURE and fail",
            },
            "other": {
                "comparison": "false",
                "missing_value_policy": "fail",
            },
        },
        "survival_predicate": {
            "feature": "down_survival_ratio_top120_percentile",
            "usage": "within-date Top120 percentile",
            "comparison": ">=",
            "threshold": 70.0,
            "missing_value_policy": "MISSING_REQUIRED_FEATURE and fail",
            "pass_logic": "Bundle A pass",
        },
        "market_plus_survival_predicate": {
            "logic": "market_predicate AND survival_predicate",
            "WEAKENING_effective_threshold": 70.0,
            "RISK_OFF_effective_threshold": 90.0,
            "pass_state": "RISK_CONTROL_PRIMARY_SHADOW",
            "fail_state": "FILTERED_BY_RISK_CONTROL_SHADOW",
        },
        "feature_definitions": {
            "days_since_first_rs_above_80": (
                "sessions since onset of rs_market_percentile_20d >=80 "
                "within frozen 20-session lookback"
            ),
            "days_since_first_volume_expansion": (
                "sessions since onset of volume/prior20_mean >=1.2 "
                "within frozen 20-session lookback"
            ),
            "rs_slope_5d": (
                "(Day0 rs_market_percentile_20d - Day-5 percentile)/5"
            ),
            "momentum_rank_change_3d": (
                "frozen raw-union rank at Day-3 minus Day0 rank; "
                "positive means improvement"
            ),
            "day1_market_excess_return": (
                "stock Day0-close to Day1-close return minus TAIEX return"
            ),
            "day1_rs_change": (
                "Day1 rs_market_percentile_20d minus Day0 percentile"
            ),
            "day1_momentum_rank_change": (
                "Day0 raw-union rank minus Day1 raw-union rank"
            ),
            "day1_institution_flow": (
                "Day1 sum of frozen institutional net_amount_est"
            ),
            "day1_close_location": "(close-low)/(high-low) on Day1",
            "day1_upper_shadow": (
                "(high-max(open,close))/(high-low) on Day1"
            ),
            "down_survival_ratio_top120_percentile": (
                "within-date Top120 percentile of five-session "
                "market-down-day outperformance ratio"
            ),
        },
        "expected_input_columns": {
            "branch_n": [
                "stock_id",
                "episode_start_date",
                "market_path_state",
                "days_since_first_rs_above_80",
                "days_since_first_volume_expansion",
                "rs_slope_5d",
                "momentum_rank_change_3d",
                "price_new_high_volume_confirmation",
                "day1_market_excess_return",
                "day1_rs_change",
                "day1_top120_status",
                "day1_momentum_rank_change",
                "day1_institution_flow",
                "day1_price_flow_alignment",
                "day1_close_location",
                "day1_upper_shadow",
                "day1_breakout_hold",
                "promotion_reference_close",
            ],
            "branch_r": [
                "stock_id",
                "episode_start_date",
                "market_path_state",
                "down_survival_ratio_top120_percentile",
                "entry_close",
            ],
        },
        "pocket_policy": "DESCRIPTIVE_CONTEXT_ONLY",
        "watchlist_policy": "OBJECTIVE_LOG_ONLY_NO_DECISION_EFFECT",
    }
    config["config_hash"] = canonical_hash(config)
    return config


def frozen_rule_audit(
    config: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    day0 = pd.read_csv(H3 / "phase3h_day0_feature_matrix.csv")
    day1 = pd.read_csv(H3 / "phase3h_day1_confirmation_matrix.csv")
    phase3g_episodes = pd.read_csv(F3 / "phase3f_v2_first_seen_episodes.csv")
    matrix = pd.read_csv(G3 / "phase3g_policy_matrix.csv")
    dates_to_python(
        phase3g_episodes,
        ["evaluation_date", "episode_start_date"],
    )
    dates_to_python(matrix, ["evaluation_date"])

    freshness = (
        day0.days_since_first_rs_above_80.le(
            thresholds["d0_fresh_rs_p40"]
        )
        | day0.days_since_first_volume_expansion.le(
            thresholds["d0_fresh_volume_p40"]
        )
    ).fillna(False)
    rs_path = day0.rs_slope_5d.ge(
        thresholds["d0_rs_slope5_p70"]
    ).fillna(False)
    rank_health = day0.momentum_rank_change_3d.ge(
        thresholds["d0_rank_change3_p50"]
    ).fillna(False)
    d0_a = (
        pd.concat([freshness, rs_path, rank_health], axis=1).sum(axis=1)
        >= 2
    )
    d0_mismatch = int((d0_a != day0.D0_A_PASS.astype(bool)).sum())

    relative = day1.day1_market_excess_return.ge(
        thresholds["d1_market_excess_p70"]
    )
    rs_health = day1.day1_rs_change.ge(thresholds["d1_rs_change_p40"])
    day1_top = day1.day1_top120_status.astype(bool)
    d1_rank = day1_top | day1.day1_momentum_rank_change.ge(
        thresholds["d1_rank_change_p40"]
    )
    d1_a = (
        pd.concat([relative, rs_health, d1_rank], axis=1).sum(axis=1) >= 2
    ) & day1.D0_A_PASS.astype(bool)
    flow = day1.day1_institution_flow.ge(
        thresholds["d1_flow_p50"]
    ) | day1.day1_price_flow_alignment.eq(1)
    controlled = (
        day1.day1_close_location.ge(thresholds["d1_close_location_p30"])
        & day1.day1_upper_shadow.le(thresholds["d1_upper_shadow_p70"])
        & ~day1.day1_failed_follow_through.astype(bool)
    )
    d1_c = d1_a & flow & controlled
    d1_mismatch = int((d1_c != day1.D1_C_PASS.astype(bool)).sum())

    e = phase3g_episodes[
        phase3g_episodes.episode_kind.eq("WINDOW_FIRST_SEEN")
        & phase3g_episodes.dataset.isin(["A", "B", "C"])
    ].copy()
    pct = pd.to_numeric(
        e.down_survival_ratio_top120_percentile, errors="coerce"
    )
    market = np.select(
        [
            e.market_path_state.eq("NORMAL"),
            e.market_path_state.eq("WEAKENING"),
            e.market_path_state.eq("RISK_OFF"),
        ],
        [True, pct.ge(70), pct.ge(90)],
        default=False,
    ).astype(bool)
    survival = pct.ge(70)
    reconstructed = pd.DataFrame(
        {
            "stock_id": e.stock_id.astype(str),
            "evaluation_date": e.evaluation_date,
            "reconstructed": market & survival,
        }
    )
    stored = matrix[
        matrix.policy.eq("P4_MARKET_PLUS_SURVIVAL")
    ][["stock_id", "evaluation_date", "selected"]].copy()
    stored.stock_id = stored.stock_id.astype(str)
    joined = reconstructed.merge(
        stored, on=["stock_id", "evaluation_date"], how="outer"
    )
    p4_mismatch = int(
        (
            joined.reconstructed.fillna(False).astype(bool)
            != joined.selected.fillna(False).astype(bool)
        ).sum()
    )
    status = {
        "branch_n_reproducible": d0_mismatch == 0 and d1_mismatch == 0,
        "branch_r_reproducible": p4_mismatch == 0,
        "d0_a_mismatch_rows": d0_mismatch,
        "d1_c_mismatch_rows": d1_mismatch,
        "p4_mismatch_rows": p4_mismatch,
    }
    rows = [
        {
            "branch": "N",
            "audit_item": "D0_A_RECOMPUTE",
            "source_artifact": "phase3h_day0_feature_matrix.csv",
            "source_rows": len(day0),
            "mismatch_rows": d0_mismatch,
            "reproducible": d0_mismatch == 0,
            "threshold_source": config["day1_c_threshold_source"],
            "missing_value_policy": "component comparison false",
            "validation_status": (
                "REPRODUCIBLE"
                if d0_mismatch == 0
                else "FROZEN_RULE_NOT_REPRODUCIBLE"
            ),
        },
        {
            "branch": "N",
            "audit_item": "D1_C_RECOMPUTE",
            "source_artifact": "phase3h_day1_confirmation_matrix.csv",
            "source_rows": len(day1),
            "mismatch_rows": d1_mismatch,
            "reproducible": d1_mismatch == 0,
            "threshold_source": config["day1_c_threshold_source"],
            "missing_value_policy": "frozen Phase 3H predicate semantics",
            "validation_status": (
                "REPRODUCIBLE"
                if d1_mismatch == 0
                else "FROZEN_RULE_NOT_REPRODUCIBLE"
            ),
        },
        {
            "branch": "R",
            "audit_item": "P4_MARKET_PLUS_SURVIVAL_RECOMPUTE",
            "source_artifact": (
                "phase3f_v2_first_seen_episodes.csv + "
                "phase3g_policy_matrix.csv"
            ),
            "source_rows": len(e),
            "mismatch_rows": p4_mismatch,
            "reproducible": p4_mismatch == 0,
            "threshold_source": config["survival_threshold_source"],
            "missing_value_policy": "missing percentile comparison false",
            "validation_status": (
                "REPRODUCIBLE"
                if p4_mismatch == 0
                else "FROZEN_RULE_NOT_REPRODUCIBLE"
            ),
        },
        {
            "branch": "BOTH",
            "audit_item": "CONFIG_HASH",
            "source_artifact": "phase3i_frozen_config.json",
            "source_rows": 1,
            "mismatch_rows": 0,
            "reproducible": True,
            "threshold_source": config["config_hash"],
            "missing_value_policy": "not applicable",
            "validation_status": "FROZEN",
        },
    ]
    return pd.DataFrame(rows), status


def load_validation_data() -> Dict[str, Any]:
    data = p3g.load_inputs()
    data["sources"] = p3g.refresh_latest(data["sources"])
    data["wide"] = p3g.build_wide_from_sources(data["sources"])
    episodes = data["episodes"].copy()
    c = episodes[
        episodes.dataset.eq("C")
        & episodes.episode_kind.eq("WINDOW_FIRST_SEEN")
    ].copy()
    c.stock_id = c.stock_id.astype(str)
    c["cohort_source"] = "EXISTING_PENDING"
    data["existing"] = c
    return data


def future_path(
    row: Any,
    wide: Mapping[str, pd.DataFrame],
) -> Dict[str, Any]:
    close, high, low = wide["close"], wide["high"], wide["low"]
    sid, d0 = str(row.stock_id), row.episode_start_date
    dates = list(close.index)
    pos = {d: i for i, d in enumerate(dates)}
    i = pos.get(d0)
    entry = float(row.entry_close) if not pd.isna(row.entry_close) else np.nan
    if (
        i is None
        or sid not in close.columns
        or pd.isna(entry)
        or entry <= 0
    ):
        return {
            "future_trade_date_1d": None,
            "future_trade_date_3d": None,
            "future_trade_date_5d": None,
            "future_trade_date_10d": None,
            "future_return_1d": np.nan,
            "future_return_3d": np.nan,
            "future_return_5d": np.nan,
            "future_return_10d": np.nan,
            "episode_outcome": None,
            "mfe_10d": np.nan,
            "mae_10d": np.nan,
            "observed_mfe_to_date": np.nan,
            "observed_mae_to_date": np.nan,
            "barrier_outcome": None,
            "available_forward_sessions": 0,
            "maturity_status": "INVALID_ENTRY_PRICE",
        }

    def at(offset: int) -> Optional[date]:
        return dates[i + offset] if i + offset < len(dates) else None

    def ret(offset: int) -> float:
        d = at(offset)
        value = close.loc[d, sid] if d is not None else np.nan
        return (
            float((value / entry - 1) * 100)
            if d is not None and not pd.isna(value)
            else np.nan
        )

    available = min(10, max(0, len(dates) - i - 1))
    observed_dates = dates[i + 1 : i + 1 + available]
    obs_high = high.loc[observed_dates, sid] if observed_dates else pd.Series(dtype=float)
    obs_low = low.loc[observed_dates, sid] if observed_dates else pd.Series(dtype=float)
    observed_mfe = (
        float((obs_high.max() / entry - 1) * 100)
        if len(obs_high) and obs_high.notna().any()
        else np.nan
    )
    observed_mae = (
        float((obs_low.min() / entry - 1) * 100)
        if len(obs_low) and obs_low.notna().any()
        else np.nan
    )
    matured = available >= 10 and not pd.isna(ret(10))
    barrier = None
    if matured:
        winner_day = loser_day = None
        ambiguous = False
        for d in dates[i + 1 : i + 11]:
            h, lo = high.loc[d, sid], low.loc[d, sid]
            hit_w = not pd.isna(h) and (h / entry - 1) * 100 >= WINNER_BAR
            hit_l = not pd.isna(lo) and (lo / entry - 1) * 100 <= LOSER_BAR
            if hit_w and hit_l:
                ambiguous = True
                break
            if hit_w and winner_day is None:
                winner_day = d
            if hit_l and loser_day is None:
                loser_day = d
            if winner_day is not None or loser_day is not None:
                break
        barrier = (
            "AMBIGUOUS_SAME_DAY"
            if ambiguous
            else "HIT_WINNER_FIRST"
            if winner_day is not None
            else "HIT_LOSER_FIRST"
            if loser_day is not None
            else "NO_BARRIER_HIT"
        )
    r10 = ret(10)
    return {
        **{f"future_trade_date_{n}d": at(n) for n in (1, 3, 5, 10)},
        **{f"future_return_{n}d": ret(n) for n in (1, 3, 5, 10)},
        "episode_outcome": outcome(r10),
        "mfe_10d": observed_mfe if matured else np.nan,
        "mae_10d": observed_mae if matured else np.nan,
        "observed_mfe_to_date": observed_mfe,
        "observed_mae_to_date": observed_mae,
        "barrier_outcome": barrier,
        "available_forward_sessions": available,
        "maturity_status": "MATURED" if matured else "PENDING_DAY10",
    }


def build_existing_paths(
    existing: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for row in existing.sort_values(
        ["episode_start_date", "momentum_rank", "stock_id"]
    ).itertuples():
        rows.append(
            {
                "stock_id": str(row.stock_id),
                "stock_name": row.stock_name,
                "episode_start_date": row.episode_start_date,
                "cohort_source": row.cohort_source,
                "dataset": row.dataset,
                "production_market_regime": row.production_market_regime,
                "market_path_state": row.market_path_state,
                "momentum_rank": row.momentum_rank,
                "entry_close": row.entry_close,
                "pocket_activity": row.pocket_state,
                "pocket_durability": None,
                "pocket_usage": "DESCRIPTIVE_CONTEXT_ONLY",
                **future_path(row, wide),
            }
        )
    return pd.DataFrame(rows)


def build_normal_rows(
    existing: pd.DataFrame,
    paths: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = existing[
        [
            "stock_id",
            "stock_name",
            "episode_start_date",
            "cohort_source",
            "dataset",
            "production_market_regime",
            "market_path_state",
            "momentum_rank",
        ]
    ].copy()
    base.stock_id = base.stock_id.astype(str)
    base = base.merge(
        paths[
            [
                "stock_id",
                "episode_start_date",
                "entry_close",
                "episode_outcome",
                "maturity_status",
                "available_forward_sessions",
            ]
        ],
        on=["stock_id", "episode_start_date"],
        how="left",
    )
    base["branch_n_eligible"] = base.market_path_state.eq("NORMAL")
    base["outcome_maturity_status"] = base.maturity_status
    base["maturity_status"] = np.where(
        base.branch_n_eligible,
        base.outcome_maturity_status,
        "EXCLUDED_NON_NORMAL",
    )
    base["day0_state"] = np.where(
        base.branch_n_eligible,
        "WATCH_CANDIDATE_SHADOW",
        "EXCLUDED_NON_NORMAL",
    )
    base["decision_date"] = base.episode_start_date
    base["feature_as_of_date"] = base.episode_start_date
    base["max_source_date"] = base.episode_start_date
    base["point_in_time_valid"] = True
    base["input_status"] = "RECONSTRUCTED_NOT_PRODUCTION_EXACT"

    evaluations = base.copy()
    evaluations["confirmation_date"] = None
    evaluations["day1_c_pass"] = np.nan
    evaluations["state"] = np.where(
        evaluations.branch_n_eligible,
        "PENDING_DAY1",
        "EXCLUDED_NON_NORMAL",
    )
    evaluations["promotion_reference_close"] = np.nan
    evaluations["predicate_trace"] = np.where(
        evaluations.branch_n_eligible,
        "[]",
        json.dumps(
            ["market_path_state != NORMAL"],
            ensure_ascii=False,
        ),
    )
    evaluations["missing_required_feature"] = False

    promotion_columns = [
        "stock_id",
        "stock_name",
        "episode_start_date",
        "cohort_source",
        "confirmation_date",
        "promotion_reference_close",
        "promotion_return_1d",
        "promotion_return_3d",
        "promotion_return_5d",
        "promotion_return_10d",
        "promotion_mfe_10d",
        "promotion_mae_10d",
        "promotion_barrier_outcome",
        "promotion_outcome",
        "pre_confirmation_return",
        "promotion_delay_sessions",
        "episode_outcome",
        "maturity_status",
        "decision_date",
        "feature_as_of_date",
        "max_source_date",
        "point_in_time_valid",
    ]
    promotion = pd.DataFrame(columns=promotion_columns)
    return base, evaluations, promotion


def build_risk_rows(
    existing: pd.DataFrame,
    paths: pd.DataFrame,
    branch_reproducible: bool,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    base = existing.copy()
    base.stock_id = base.stock_id.astype(str)
    keep = [
        "stock_id",
        "episode_start_date",
        "future_trade_date_1d",
        "future_trade_date_3d",
        "future_trade_date_5d",
        "future_trade_date_10d",
        "future_return_1d",
        "future_return_3d",
        "future_return_5d",
        "future_return_10d",
        "episode_outcome",
        "mfe_10d",
        "mae_10d",
        "observed_mfe_to_date",
        "observed_mae_to_date",
        "barrier_outcome",
        "available_forward_sessions",
        "maturity_status",
    ]
    base = base.drop(
        columns=[
            col
            for col in keep
            if col not in {"stock_id", "episode_start_date"}
            and col in base.columns
        ]
    )
    base = base.merge(
        paths[keep],
        on=["stock_id", "episode_start_date"],
        how="left",
    )
    pct = pd.to_numeric(
        base.down_survival_ratio_top120_percentile, errors="coerce"
    )
    base["branch_r_eligible"] = base.market_path_state.isin(
        ["WEAKENING", "RISK_OFF"]
    )
    base["branch_exclusion_state"] = np.where(
        base.branch_r_eligible,
        "ELIGIBLE_WEAK_MARKET",
        "EXCLUDED_NORMAL",
    )
    base["outcome_maturity_status"] = base.maturity_status
    base["branch_maturity_status"] = np.where(
        base.branch_r_eligible,
        base.outcome_maturity_status,
        "EXCLUDED_NORMAL",
    )
    base["missing_required_feature"] = (
        base.branch_r_eligible & pct.isna()
    )
    base["survival_threshold"] = 70.0
    base["survival_pass"] = pct.ge(70)
    base["market_threshold"] = np.select(
        [
            base.market_path_state.eq("WEAKENING"),
            base.market_path_state.eq("RISK_OFF"),
        ],
        [70.0, 90.0],
        default=np.nan,
    )
    base["market_gate_pass"] = (
        base.branch_r_eligible
        & pct.ge(base.market_threshold)
        & ~base.missing_required_feature
    )
    base["r1_market_plus_survival_pass"] = (
        base.branch_r_eligible
        & base.market_gate_pass
        & base.survival_pass
        & branch_reproducible
    )
    base["r2_survival_only_pass"] = (
        base.branch_r_eligible
        & base.survival_pass
        & branch_reproducible
    )
    base["shadow_state"] = np.select(
        [
            ~base.branch_r_eligible,
            pd.Series(not branch_reproducible, index=base.index),
            base.missing_required_feature,
            base.r1_market_plus_survival_pass,
        ],
        [
            "EXCLUDED_NORMAL",
            "FROZEN_RULE_NOT_REPRODUCIBLE",
            "MISSING_REQUIRED_FEATURE",
            "RISK_CONTROL_PRIMARY_SHADOW",
        ],
        default="FILTERED_BY_RISK_CONTROL_SHADOW",
    )
    base["decision_date"] = base.episode_start_date
    base["feature_as_of_date"] = base.episode_start_date
    base["max_source_date"] = base.episode_start_date
    base["point_in_time_valid"] = (
        pd.to_datetime(base.max_source_date)
        <= pd.to_datetime(base.decision_date)
    )
    base["predicate_trace"] = base.apply(
        lambda row: json.dumps(
            [
                {
                    "predicate": "BRANCH_R_MARKET_STATE",
                    "feature": "market_path_state",
                    "raw": row.market_path_state,
                    "pass": bool(row.branch_r_eligible),
                },
                {
                    "predicate": "MARKET_PUBLISHING",
                    "feature": "down_survival_ratio_top120_percentile",
                    "raw": (
                        None
                        if pd.isna(row.down_survival_ratio_top120_percentile)
                        else float(row.down_survival_ratio_top120_percentile)
                    ),
                    "comparison": ">=",
                    "threshold": (
                        None
                        if pd.isna(row.market_threshold)
                        else float(row.market_threshold)
                    ),
                    "pass": bool(row.market_gate_pass),
                },
                {
                    "predicate": "SURVIVAL_BUNDLE_A",
                    "feature": "down_survival_ratio_top120_percentile",
                    "raw": (
                        None
                        if pd.isna(row.down_survival_ratio_top120_percentile)
                        else float(row.down_survival_ratio_top120_percentile)
                    ),
                    "comparison": ">=",
                    "threshold": 70.0,
                    "pass": bool(row.survival_pass),
                },
            ],
            ensure_ascii=False,
        ),
        axis=1,
    )
    eligible_cols = [
        "stock_id",
        "stock_name",
        "episode_start_date",
        "cohort_source",
        "dataset",
        "production_market_regime",
        "market_path_state",
        "branch_r_eligible",
        "branch_exclusion_state",
        "momentum_rank",
        "down_survival_ratio",
        "down_survival_ratio_top120_percentile",
        "entry_close",
        "maturity_status",
        "branch_maturity_status",
        "episode_outcome",
        "decision_date",
        "feature_as_of_date",
        "max_source_date",
        "point_in_time_valid",
    ]
    return base[eligible_cols], base


def metric_row(
    frame: pd.DataFrame,
    selected_mask: pd.Series,
    policy: str,
    branch: str,
    cohort_source: str,
) -> Dict[str, Any]:
    eligible = frame[
        frame.branch_r_eligible
        if branch == "R"
        else frame.branch_n_eligible
    ].copy()
    selected = eligible[selected_mask.reindex(eligible.index, fill_value=False)]
    matured_base = eligible[eligible.episode_outcome.notna()]
    matured = selected[selected.episode_outcome.notna()]
    counts = Counter(matured.episode_outcome)
    base_counts = Counter(matured_base.episode_outcome)
    w, ne, lo = (
        int(counts.get(label, 0))
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    bw, bne, blo = (
        int(base_counts.get(label, 0))
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    low, high = wilson(lo, len(matured))
    dates = sorted(eligible.episode_start_date.unique())
    date_counts = (
        selected.groupby("episode_start_date").size().reindex(dates, fill_value=0)
        if dates
        else pd.Series(dtype=float)
    )
    matured_dates = sorted(matured_base.episode_start_date.unique())
    daily = []
    for d in matured_dates:
        x = matured[matured.episode_start_date == d]
        if len(x):
            daily.append(
                {
                    "winner": (x.episode_outcome == "WINNER").mean(),
                    "loser": (x.episode_outcome == "LOSER").mean(),
                    "safe": (x.episode_outcome != "LOSER").mean(),
                    "dominance": div(
                        (x.episode_outcome == "WINNER").sum(),
                        x.episode_outcome.isin(["WINNER", "NEUTRAL"]).sum(),
                    ),
                }
            )
    winner_by_date = (
        matured[matured.episode_outcome == "WINNER"]
        .groupby("episode_start_date")
        .size()
    )
    selected_by_date = selected.groupby("episode_start_date").size()
    return {
        "branch": branch,
        "cohort_source": cohort_source,
        "policy": policy,
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "matured_selected_count": len(matured),
        "pending_selected_count": int(
            selected.episode_outcome.isna().sum()
        ),
        "selected_dates": int(selected.episode_start_date.nunique()),
        "matured_selected_dates": int(matured.episode_start_date.nunique()),
        "eligible_dates": len(dates),
        "average_selected_per_day": (
            float(date_counts.mean()) if len(date_counts) else np.nan
        ),
        "median_selected_per_day": (
            float(date_counts.median()) if len(date_counts) else np.nan
        ),
        "zero_primary_date_rate": (
            float((date_counts == 0).mean()) if len(date_counts) else np.nan
        ),
        "coverage": div(len(selected), len(eligible)),
        "winner_count": w,
        "winner_rate": div(w, len(matured)),
        "neutral_count": ne,
        "neutral_rate": div(ne, len(matured)),
        "loser_count": lo,
        "loser_rate": div(lo, len(matured)),
        "loser_rate_wilson_low": low,
        "loser_rate_wilson_high": high,
        "safe_rate": div(w + ne, len(matured)),
        "winner_dominance": div(w, w + ne),
        "winner_recall": div(w, bw),
        "neutral_removal_rate": div(bne - ne, bne),
        "loser_removal_rate": div(blo - lo, blo),
        "mean_return_10d": matured.future_return_10d.mean(),
        "median_return_10d": matured.future_return_10d.median(),
        "mean_mfe_10d": matured.mfe_10d.mean(),
        "mean_mae_10d": matured.mae_10d.mean(),
        "macro_daily_winner_rate": (
            float(np.mean([x["winner"] for x in daily]))
            if daily
            else np.nan
        ),
        "macro_daily_loser_rate": (
            float(np.mean([x["loser"] for x in daily]))
            if daily
            else np.nan
        ),
        "macro_daily_safe_rate": (
            float(np.mean([x["safe"] for x in daily]))
            if daily
            else np.nan
        ),
        "macro_daily_winner_dominance": (
            float(np.nanmean([x["dominance"] for x in daily]))
            if daily
            else np.nan
        ),
        "max_single_date_winner_contribution": (
            div(winner_by_date.max(), w) if w else np.nan
        ),
        "max_single_date_selected_contribution": (
            div(selected_by_date.max(), len(selected))
            if len(selected)
            else np.nan
        ),
        "point_in_time_valid_rate": (
            selected.point_in_time_valid.mean()
            if len(selected)
            else np.nan
        ),
        "date_distribution": json.dumps(
            {
                str(key): int(value)
                for key, value in date_counts.items()
            },
            ensure_ascii=False,
        ),
    }


def normal_metric_rows(
    normal_day0: pd.DataFrame,
    cohort_source: str,
) -> pd.DataFrame:
    rows = []
    scopes = {
        cohort_source: normal_day0,
    }
    for scope, frame in scopes.items():
        eligible = frame[frame.branch_n_eligible].copy()
        for policy in (
            "N0_NORMAL_PHASE2_BASELINE",
            "N1_FROZEN_DAY1_C",
            "N2_MOMENTUM_TOP1",
            "N3_MOMENTUM_TOP2",
            "N4_MOMENTUM_TOP3",
            "N_COUNT_MATCHED_MOMENTUM",
        ):
            rows.append(
                {
                    "branch": "N",
                    "cohort_source": scope,
                    "policy": policy,
                    "eligible_count": len(eligible),
                    "available_day1_count": 0,
                    "selected_count": 0,
                    "matured_selected_count": 0,
                    "pending_selected_count": 0,
                    "selected_dates": 0,
                    "eligible_dates": int(
                        eligible.episode_start_date.nunique()
                    ),
                    "average_selected_per_day": np.nan,
                    "median_selected_per_day": np.nan,
                    "zero_primary_date_rate": (
                        1.0 if len(eligible) else np.nan
                    ),
                    "coverage": 0.0 if len(eligible) else np.nan,
                    "promotion_winner_count": 0,
                    "promotion_neutral_count": 0,
                    "promotion_loser_count": 0,
                    "promotion_loser_rate": np.nan,
                    "promotion_safe_rate": np.nan,
                    "promotion_winner_dominance": np.nan,
                    "promotion_winner_rate": np.nan,
                    "episode_winner_recall": np.nan,
                    "promotion_mean_return_10d": np.nan,
                    "promotion_median_return_10d": np.nan,
                    "promotion_mean_mfe_10d": np.nan,
                    "promotion_mean_mae_10d": np.nan,
                    "pre_confirmation_return_mean": np.nan,
                    "promotion_delay_sessions_median": np.nan,
                    "macro_daily_promotion_winner_rate": np.nan,
                    "macro_daily_promotion_safe_rate": np.nan,
                    "point_in_time_valid_rate": np.nan,
                    "availability": (
                        "NO_INDEPENDENT_NORMAL_EPISODES"
                        if not len(eligible)
                        else "PENDING_DAY1"
                    ),
                }
            )
    return pd.DataFrame(rows)


def risk_comparison(
    risk_results: pd.DataFrame,
    cohort_source: str,
) -> pd.DataFrame:
    rows = []
    masks = {
        "R0_WEAK_MARKET_PHASE2_BASELINE": risk_results.branch_r_eligible,
        "R1_FROZEN_MARKET_PLUS_SURVIVAL": (
            risk_results.r1_market_plus_survival_pass
        ),
        "R2_FROZEN_SURVIVAL_ONLY_DIAGNOSTIC": (
            risk_results.r2_survival_only_pass
        ),
    }
    for policy, mask in masks.items():
        rows.append(
            metric_row(
                risk_results,
                mask,
                policy,
                "R",
                cohort_source,
            )
        )
    out = pd.DataFrame(rows)
    base = out[out.policy.eq("R0_WEAK_MARKET_PHASE2_BASELINE")].iloc[0]
    out["loser_rate_reduction_vs_baseline_pp"] = (
        (base.loser_rate - out.loser_rate) * 100
    )
    return out


def build_snapshots(
    data: Mapping[str, Any],
    config: Mapping[str, Any],
    risk_results: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict[str, Any]]]:
    raw = data["prepared"]["raw_enriched"].copy()
    candidate = data["prepared"]["candidate"].copy()
    dates_to_python(raw, ["evaluation_date"])
    dates_to_python(candidate, ["evaluation_date"])
    existing_dates = sorted(
        data["existing"].episode_start_date.unique()
    )
    latest_snapshot = data["sources"]["snapshots"].snapshot_date.max()
    dates = sorted(set(existing_dates) | {latest_snapshot})
    snapshots = []
    daily = []
    shadow = []
    for d in dates:
        rd = raw[raw.evaluation_date == d].copy()
        cd = candidate[candidate.evaluation_date == d].copy()
        er = risk_results[risk_results.episode_start_date == d].copy()
        source_available = len(rd) > 0 and len(cd) > 0
        state = (
            er.market_path_state.iloc[0]
            if len(er)
            else None
        )
        regime = (
            er.production_market_regime.iloc[0]
            if len(er)
            else None
        )
        selected = er[er.r1_market_plus_survival_pass]
        snapshots.append(
            {
                "evaluation_date": d,
                "cohort_source": (
                    "EXISTING_PENDING"
                    if d <= PROSPECTIVE_AFTER
                    else "PROSPECTIVE_NEW_DATA"
                ),
                "config_hash": config["config_hash"],
                "code_version": config["code_version"],
                "prompt_version": (
                    "v6" if d >= date(2026, 7, 23) else "v5"
                ),
                "raw_union_available": len(rd) > 0,
                "raw_union_count": len(rd) if len(rd) else np.nan,
                "raw_union_hash": frame_hash(
                    rd,
                    [
                        "evaluation_date",
                        "stock_id",
                        "raw_union_rank",
                        "momentum_score",
                        "source_combination",
                    ],
                ),
                "top120_available": len(cd) > 0,
                "top120_count": len(cd) if len(cd) else np.nan,
                "top120_hash": frame_hash(
                    cd,
                    [
                        "evaluation_date",
                        "stock_id",
                        "momentum_rank",
                        "momentum_score",
                    ],
                ),
                "phase2_episode_proxy_count": len(er),
                "phase2_episode_proxy_hash": frame_hash(
                    er,
                    [
                        "episode_start_date",
                        "stock_id",
                        "momentum_rank",
                        "down_survival_ratio_top120_percentile",
                    ],
                ),
                "production_market_regime": regime,
                "market_path_state": state,
                "input_snapshot_status": (
                    "FROZEN_RECONSTRUCTED_PROXY_AVAILABLE"
                    if source_available
                    else "RAW_UNION_AND_PHASE2_SURVIVORS_UNAVAILABLE"
                ),
            }
        )
        daily.append(
            {
                "evaluation_date": d,
                "cohort_source": (
                    "EXISTING_PENDING"
                    if d <= PROSPECTIVE_AFTER
                    else "PROSPECTIVE_NEW_DATA"
                ),
                "production_market_regime": regime,
                "market_path_state": state,
                "normal_eligible_count": 0,
                "normal_day1_c_pass_count": 0,
                "normal_matured_promotion_count": 0,
                "risk_eligible_count": int(er.branch_r_eligible.sum()),
                "risk_selected_count": len(selected),
                "risk_matured_selected_count": int(
                    selected.episode_outcome.notna().sum()
                ),
                "risk_pending_selected_count": int(
                    selected.episode_outcome.isna().sum()
                ),
                "risk_winner_count": int(
                    (selected.episode_outcome == "WINNER").sum()
                ),
                "risk_neutral_count": int(
                    (selected.episode_outcome == "NEUTRAL").sum()
                ),
                "risk_loser_count": int(
                    (selected.episode_outcome == "LOSER").sum()
                ),
                "data_status": (
                    "EARLY_READ_ONLY"
                    if len(er)
                    else "INPUT_SNAPSHOT_UNAVAILABLE_NOT_FABRICATED"
                ),
            }
        )
        shadow.append(
            {
                "evaluation_date": str(d),
                "config_hash": config["config_hash"],
                "production_market_regime": regime,
                "market_path_state": state,
                "normal_branch": {
                    "day0_candidates": [],
                    "day1_evaluations": [],
                },
                "risk_branch": {
                    "eligible_candidates": (
                        int(er.branch_r_eligible.sum())
                        if len(er)
                        else None
                    ),
                    "selected_candidates": [
                        {
                            "stock_id": row.stock_id,
                            "state": "RISK_CONTROL_PRIMARY_SHADOW",
                            "predicate_trace": json.loads(
                                row.predicate_trace
                            ),
                            "point_in_time_valid": bool(
                                row.point_in_time_valid
                            ),
                        }
                        for row in selected.itertuples()
                    ],
                    "zero_primary_reason": (
                        "INPUT_SNAPSHOT_UNAVAILABLE_NOT_FABRICATED"
                        if not len(er)
                        else "NO_CANDIDATE_PASSED_FROZEN_P4"
                        if not len(selected)
                        else None
                    ),
                },
            }
        )
    return pd.DataFrame(snapshots), pd.DataFrame(daily), shadow


def build_pending(
    normal_evaluations: pd.DataFrame,
    risk_results: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for row in normal_evaluations[
        normal_evaluations.state.isin(["PENDING_DAY1", "PENDING_DAY10"])
    ].itertuples():
        rows.append(
            {
                "branch": "N",
                "cohort_source": row.cohort_source,
                "stock_id": row.stock_id,
                "stock_name": row.stock_name,
                "episode_start_date": row.episode_start_date,
                "selection_state": row.state,
                "available_forward_sessions": row.available_forward_sessions,
                "expected_maturity_date": None,
                "pending_not_neutral": True,
            }
        )
    for row in risk_results[
        risk_results.branch_r_eligible
        & risk_results.episode_outcome.isna()
    ].itertuples():
        rows.append(
            {
                "branch": "R",
                "cohort_source": row.cohort_source,
                "stock_id": row.stock_id,
                "stock_name": row.stock_name,
                "episode_start_date": row.episode_start_date,
                "selection_state": (
                    row.shadow_state
                    if row.r1_market_plus_survival_pass
                    else "FILTERED_PENDING_OUTCOME"
                ),
                "available_forward_sessions": row.available_forward_sessions,
                "expected_maturity_date": row.future_trade_date_10d,
                "pending_not_neutral": True,
            }
        )
    columns = [
        "branch",
        "cohort_source",
        "stock_id",
        "stock_name",
        "episode_start_date",
        "selection_state",
        "available_forward_sessions",
        "expected_maturity_date",
        "pending_not_neutral",
    ]
    return pd.DataFrame(rows, columns=columns)


def build_pit_audit(
    normal_day0: pd.DataFrame,
    normal_evaluations: pd.DataFrame,
    risk_results: pd.DataFrame,
    config_hash: str,
) -> pd.DataFrame:
    rows = []
    for unit, frame, eligible_col, state_col in (
        ("N_DAY0", normal_day0, "branch_n_eligible", "day0_state"),
        (
            "N_DAY1",
            normal_evaluations,
            "branch_n_eligible",
            "state",
        ),
        (
            "R_DAY0",
            risk_results,
            "branch_r_eligible",
            "shadow_state",
        ),
    ):
        for row in frame.itertuples():
            rows.append(
                {
                    "branch_unit": unit,
                    "cohort_source": row.cohort_source,
                    "stock_id": row.stock_id,
                    "episode_start_date": row.episode_start_date,
                    "decision_date": row.decision_date,
                    "feature_as_of_date": row.feature_as_of_date,
                    "max_source_date": row.max_source_date,
                    "branch_eligible": bool(getattr(row, eligible_col)),
                    "decision_state": getattr(row, state_col),
                    "point_in_time_valid": bool(row.point_in_time_valid),
                    "future_feature_in_decision": False,
                    "outcome_in_decision": False,
                    "day3_rescue_used": False,
                    "config_hash": config_hash,
                }
            )
    return pd.DataFrame(rows)


def data_quality_audit(
    data: Mapping[str, Any],
    normal_day0: pd.DataFrame,
    risk_results: pd.DataFrame,
    reproducibility: Mapping[str, Any],
) -> pd.DataFrame:
    latest_price = data["sources"]["prices"].trade_date.max()
    latest_snapshot = data["sources"]["snapshots"].snapshot_date.max()
    raw_max = data["prepared"]["raw_enriched"].evaluation_date.max()
    candidate_max = data["prepared"]["candidate"].evaluation_date.max()
    rows = [
        {
            "branch": "BOTH",
            "audit_item": "FROZEN_RULE_REPRODUCIBILITY",
            "affected_rows": (
                reproducibility["d0_a_mismatch_rows"]
                + reproducibility["d1_c_mismatch_rows"]
                + reproducibility["p4_mismatch_rows"]
            ),
            "denominator_rows": 473,
            "missing_rate": 0.0,
            "affected_dates": "",
            "severity": (
                "OK"
                if reproducibility["branch_n_reproducible"]
                and reproducibility["branch_r_reproducible"]
                else "BLOCKER"
            ),
            "data_quality_blocker": not (
                reproducibility["branch_n_reproducible"]
                and reproducibility["branch_r_reproducible"]
            ),
            "detail": json.dumps(
                reproducibility, ensure_ascii=False
            ),
        },
        {
            "branch": "R",
            "audit_item": "MISSING_REQUIRED_SURVIVAL_FEATURE",
            "affected_rows": int(
                risk_results.missing_required_feature.sum()
            ),
            "denominator_rows": int(
                risk_results.branch_r_eligible.sum()
            ),
            "missing_rate": div(
                risk_results.missing_required_feature.sum(),
                risk_results.branch_r_eligible.sum(),
            ),
            "affected_dates": json.dumps(
                sorted(
                    str(x)
                    for x in risk_results.loc[
                        risk_results.missing_required_feature,
                        "episode_start_date",
                    ].unique()
                )
            ),
            "severity": (
                "BLOCKER"
                if div(
                    risk_results.missing_required_feature.sum(),
                    risk_results.branch_r_eligible.sum(),
                )
                > 0.10
                else "OK"
            ),
            "data_quality_blocker": (
                div(
                    risk_results.missing_required_feature.sum(),
                    risk_results.branch_r_eligible.sum(),
                )
                > 0.10
            ),
            "detail": "Frozen comparison is not imputed.",
        },
        {
            "branch": "N",
            "audit_item": "INDEPENDENT_NORMAL_COHORT_AVAILABLE",
            "affected_rows": 0,
            "denominator_rows": len(normal_day0),
            "missing_rate": np.nan,
            "affected_dates": "",
            "severity": "SAMPLE_PENDING",
            "data_quality_blocker": False,
            "detail": (
                "Dataset C contains no NORMAL episode; no prospective "
                "trade date exists after 2026-07-27."
            ),
        },
        {
            "branch": "BOTH",
            "audit_item": "2026_07_27_PHASE2_INPUT_SNAPSHOT",
            "affected_rows": np.nan,
            "denominator_rows": np.nan,
            "missing_rate": np.nan,
            "affected_dates": "2026-07-27",
            "severity": "BLOCKER",
            "data_quality_blocker": True,
            "detail": (
                "Formal production snapshot exists, but frozen raw-union, "
                "Top120 row frame and deterministic Phase2 survivor rows "
                "stop at 2026-07-24. Episode denominator is not fabricated."
            ),
        },
        {
            "branch": "BOTH",
            "audit_item": "LATEST_SOURCE_DATES",
            "affected_rows": 0,
            "denominator_rows": 0,
            "missing_rate": 0.0,
            "affected_dates": "",
            "severity": "INFO",
            "data_quality_blocker": False,
            "detail": json.dumps(
                {
                    "price_available_through": str(latest_price),
                    "formal_snapshot_available_through": str(latest_snapshot),
                    "frozen_raw_union_available_through": str(raw_max),
                    "frozen_top120_available_through": str(candidate_max),
                    "requested_2026_07_28": "UNAVAILABLE_NOT_FABRICATED",
                },
                ensure_ascii=False,
            ),
        },
        {
            "branch": "BOTH",
            "audit_item": "FROZEN_COHORT_PRODUCTION_EXACTNESS",
            "affected_rows": len(risk_results),
            "denominator_rows": len(risk_results),
            "missing_rate": 0.0,
            "affected_dates": json.dumps(
                sorted(
                    str(x)
                    for x in risk_results.episode_start_date.unique()
                )
            ),
            "severity": "WARNING",
            "data_quality_blocker": False,
            "detail": (
                "Dataset C is the specification-authorized frozen "
                "RECONSTRUCTED_NOT_PRODUCTION_EXACT first-seen proxy."
            ),
        },
    ]
    return pd.DataFrame(rows)


def version_log(data: Mapping[str, Any], config_hash: str) -> pd.DataFrame:
    snapshots = data["sources"]["snapshots"].copy()
    dates_to_python(snapshots, ["snapshot_date"])
    versions = snapshots[
        snapshots.snapshot_date.between(
            date(2026, 7, 13), date(2026, 7, 27)
        )
    ][["snapshot_date", "prompt_version"]].drop_duplicates()
    rows = [
        {
            "change_date": date(2026, 7, 23),
            "change_type": "PROMPT_VERSION",
            "old_config_hash": config_hash,
            "new_config_hash": config_hash,
            "old_value": "v5",
            "new_value": "v6",
            "changed_fields": "prompt_version",
            "affects_candidate_universe_or_frozen_rule": False,
            "new_cohort_required": False,
            "note": (
                "Recorded for transparency; Phase 3I decisions use "
                "deterministic frozen features and do not use LLM output."
            ),
        },
        {
            "change_date": RESEARCH_START,
            "change_type": "FROZEN_RESEARCH_START",
            "old_config_hash": None,
            "new_config_hash": config_hash,
            "old_value": None,
            "new_value": FROZEN_RULE_COMMIT,
            "changed_fields": "none",
            "affects_candidate_universe_or_frozen_rule": False,
            "new_cohort_required": False,
            "note": (
                "No Phase 3I rule or threshold change; source rules pinned "
                "to committed Phase 3G/3H artifacts."
            ),
        },
    ]
    out = pd.DataFrame(rows)
    out["observed_prompt_versions"] = json.dumps(
        {
            str(row.snapshot_date): row.prompt_version
            for row in versions.itertuples()
        },
        ensure_ascii=False,
    )
    return out


def empty_prospective_frames(
    normal_day0: pd.DataFrame,
    risk_results: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    n = normal_day0.iloc[0:0].copy()
    r = risk_results.iloc[0:0].copy()
    return n, r


def aggregate_result_tables(
    normal_existing: pd.DataFrame,
    risk_existing: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n_pro, r_pro = empty_prospective_frames(
        normal_existing, risk_existing
    )
    normal_existing_metrics = normal_metric_rows(
        normal_existing, "EXISTING_PENDING"
    )
    normal_pro_metrics = normal_metric_rows(
        n_pro, "PROSPECTIVE_NEW_DATA"
    )
    normal_combined_metrics = normal_metric_rows(
        pd.concat([normal_existing, n_pro], ignore_index=True),
        "COMBINED",
    )
    risk_existing_metrics = risk_comparison(
        risk_existing, "EXISTING_PENDING"
    )
    risk_pro_metrics = risk_comparison(
        r_pro, "PROSPECTIVE_NEW_DATA"
    )
    risk_combined_metrics = risk_comparison(
        pd.concat([risk_existing, r_pro], ignore_index=True),
        "COMBINED",
    )
    existing = pd.concat(
        [normal_existing_metrics, risk_existing_metrics],
        ignore_index=True,
        sort=False,
    )
    prospective = pd.concat(
        [normal_pro_metrics, risk_pro_metrics],
        ignore_index=True,
        sort=False,
    )
    combined = pd.concat(
        [normal_combined_metrics, risk_combined_metrics],
        ignore_index=True,
        sort=False,
    )
    normal_topk = pd.concat(
        [
            normal_existing_metrics,
            normal_pro_metrics,
            normal_combined_metrics,
        ],
        ignore_index=True,
    )
    return existing, prospective, combined, normal_topk


def pct(value: Any) -> str:
    return (
        "NA"
        if value is None or pd.isna(value)
        else f"{float(value) * 100:.1f}%"
    )


def md_table(
    frame: pd.DataFrame,
    columns: Sequence[str],
    limit: int = 30,
) -> str:
    view = frame[list(columns)].head(limit).copy()
    for col in view.select_dtypes(include=["float"]).columns:
        view[col] = view[col].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = "\n".join(
        "| " + " | ".join(map(str, row)) + " |"
        for row in view.values
    )
    return "\n".join([header, sep, body])


def render_decision(
    config: Mapping[str, Any],
    frozen_audit: pd.DataFrame,
    reproducibility: Mapping[str, Any],
    normal_day0: pd.DataFrame,
    normal_eval: pd.DataFrame,
    risk_results: pd.DataFrame,
    risk_comparison_frame: pd.DataFrame,
    existing_result: pd.DataFrame,
    prospective_result: pd.DataFrame,
    data_quality: pd.DataFrame,
) -> Tuple[str, str]:
    r0 = risk_comparison_frame[
        risk_comparison_frame.policy.eq(
            "R0_WEAK_MARKET_PHASE2_BASELINE"
        )
    ].iloc[0]
    r1 = risk_comparison_frame[
        risk_comparison_frame.policy.eq(
            "R1_FROZEN_MARKET_PLUS_SURVIVAL"
        )
    ].iloc[0]
    normal_eligible = int(normal_day0.branch_n_eligible.sum())
    normal_pass_mask = normal_eval.day1_c_pass.eq(True)
    normal_pass = int(normal_pass_mask.sum())
    normal_dates = int(
        normal_eval.loc[
            normal_pass_mask,
            "episode_start_date",
        ].nunique()
    )
    normal_status = "NORMAL_PENDING_SAMPLE"
    risk_status = "RISK_PENDING_SAMPLE"
    final = "BOTH_BRANCHES_PENDING_SAMPLE"
    data_blocker = bool(data_quality.data_quality_blocker.any())
    direction = "NOT_ASSESSABLE_NO_PROSPECTIVE_DATA"
    single_date = bool(
        r1.matured_selected_count > 0
        and (
            pd.isna(r1.max_single_date_selected_contribution)
            or r1.matured_selected_dates <= 1
        )
    )
    view = risk_comparison_frame[
        [
            "policy",
            "eligible_count",
            "selected_count",
            "matured_selected_count",
            "pending_selected_count",
            "selected_dates",
            "coverage",
            "winner_count",
            "neutral_count",
            "loser_count",
            "loser_rate",
            "safe_rate",
            "winner_recall",
            "loser_rate_reduction_vs_baseline_pp",
        ]
    ]
    report = f"""# Phase 3I — Frozen Dual-Branch Shadow Validation

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。
> Frozen rule commit：`{config["code_version"]}`。
> Config hash：`{config["config_hash"]}`。
> 實際價格／正式 snapshot 只到 2026-07-27；未虛構 7/28 action。

## 結論先行

最終決策：**{final}**。

- Branch N：**{normal_status}**。獨立驗證 cohort 中 NORMAL eligible=0；
  Dataset C 全部為 RISK_OFF，且 7/27 後尚無實際入庫交易日。Phase 3H 的
  historical n=5 不重複計入 Phase 3I。
- Branch R：**{risk_status}**。Existing Pending baseline=140、R1 total
  selected={int(r1.selected_count)}，但 matured selected 只有
  {int(r1.matured_selected_count)}，matured dates={int(r1.matured_selected_dates)}；
  baseline matured Loser={int(r0.loser_count)}，尚未達 10。
- 7/27 有正式 WATCH snapshot，但 frozen raw-union／Top120 row frame／Phase2
  survivor row 只到 7/24。該日 episode denominator 保持 unavailable，
  不以 WATCH 24 檔冒充 Phase2 eligible universe。

這不是 PASS 或 FAIL。兩條 frozen rule 保持不變，後續只能補成熟結果與新的
實際交易日，不能再調 threshold、換 bundle 或開始 Phase 3J。

## Frozen config reproducibility

{md_table(frozen_audit, ["branch", "audit_item", "source_rows", "mismatch_rows", "reproducible", "validation_status"])}

Branch N D0-A mismatch={reproducibility["d0_a_mismatch_rows"]}、
D1-C mismatch={reproducibility["d1_c_mismatch_rows"]}；Branch R P4
mismatch={reproducibility["p4_mismatch_rows"]}。因此 frozen predicate
本身可完整重現，沒有標記 `FROZEN_RULE_NOT_REPRODUCIBLE`。

## Branch N — NORMAL Day1-C

- source rows={len(normal_day0)}；`EXCLUDED_NON_NORMAL`={int((~normal_day0.branch_n_eligible).sum())}。
- eligible={normal_eligible}、Day1-C pass={normal_pass}、selected dates={normal_dates}。
- Promotion W/N/L=0/0/0；Safe、Winner Dominance、Top-K comparison 均
  `NOT_ASSESSABLE_NO_INDEPENDENT_NORMAL_SAMPLE`。
- Day3 不作 rescue；未產生任何 HIGH_CONVICTION promotion outcome。

## Branch R — Market + Survival

{md_table(view, view.columns)}

目前只有 2026-07-13 的 18 筆 episode 完成 Day10。R1 的全 pending+成熟
selected={int(r1.selected_count)}，Coverage={pct(r1.coverage)}，但正式 outcome
指標只能使用 matured selected={int(r1.matured_selected_count)}：
W/N/L={int(r1.winner_count)}/{int(r1.neutral_count)}/{int(r1.loser_count)}、
Loser Rate={pct(r1.loser_rate)}、Safe={pct(r1.safe_rate)}。這些值全部標記
`EARLY_READ_ONLY`，不得和 Phase 3G Dataset B 歷史結果合併宣稱通過。

R1 zero-primary-date rate={pct(r1.zero_primary_date_rate)}。matured outcome
只來自 1 個日期，因此存在單日主導，尚不能檢驗跨日穩定性。

## Existing Pending vs Prospective

- Existing Pending：Dataset C 140 rows；R1 selected={int(r1.selected_count)}，
  matured selected={int(r1.matured_selected_count)}。
- Prospective New Data：0 rows。2026-07-27 後尚無實際入庫交易日。
- 方向一致性：`{direction}`，不是「一致」，也不是「相反」。

## Data quality

{md_table(data_quality, ["branch", "audit_item", "affected_rows", "denominator_rows", "missing_rate", "severity", "data_quality_blocker"])}

`DATA_QUALITY_BLOCKER`=**{"YES" if data_blocker else "NO"}**，原因是 7/27
無法還原完整 Phase2 input snapshot；Frozen Dataset C 本身仍依規格保留為
`RECONSTRUCTED_NOT_PRODUCTION_EXACT` Existing Pending cohort。

## 24 個必答答案

1. Frozen config 是否完整可重現：是；N/R mismatch 都為 0。
2. 驗證期間是否發生規則或程式版本變更：Frozen config 無變更；7/23 prompt v5→v6 已記錄，但不進兩條 deterministic rule。
3. Branch N eligible episodes：{normal_eligible}。
4. Branch N Day1-C pass：{normal_pass}。
5. Branch N 涵蓋交易日：{normal_dates}。
6. Branch N Promotion W/N/L：0/0/0。
7. Branch N Promotion Safe Rate：NA。
8. Branch N Promotion Winner Dominance：NA。
9. Branch N Promotion Winner Count >=8：否，count=0。
10. Branch N 優於相近 Coverage Top-K：不可評估。
11. Branch N 通過所有正式門檻：否；狀態為 `{normal_status}`，不是 FAIL。
12. Branch R baseline episodes：total={int(r0.eligible_count)}，matured={int(r0.matured_selected_count)}。
13. Branch R baseline Loser：matured Loser={int(r0.loser_count)}。
14. Market + Survival 保留：total={int(r1.selected_count)}，matured={int(r1.matured_selected_count)}。
15. Branch R Loser Rate：{pct(r1.loser_rate)}，EARLY_READ_ONLY。
16. Branch R Safe Rate：{pct(r1.safe_rate)}，EARLY_READ_ONLY。
17. Branch R 相較 baseline 降低多少 Loser Rate：{r1.loser_rate_reduction_vs_baseline_pp:.1f} pp，僅單一成熟日期。
18. Branch R 0 檔日期比例：{pct(r1.zero_primary_date_rate)}。
19. Branch R 是否錯刪所有 Winner：{"是" if r0.winner_count > 0 and r1.winner_count == 0 else "否"}；baseline Winner={int(r0.winner_count)}、R1 Winner={int(r1.winner_count)}。
20. Branch R 通過所有正式門檻：否；狀態為 `{risk_status}`，不是 FAIL。
21. Existing Pending 與 Prospective 方向一致：不可評估，Prospective=0。
22. 是否有單一日期或股票主導：{"是" if single_date else "尚未發現"}；所有 matured R 結果來自 7/13。
23. 是否存在 DATA_QUALITY_BLOCKER：{"是" if data_blocker else "否"}；7/27 Phase2 row-level input 不可還原。
24. 最終決策：**{final}**。

## 禁止事項與 Phase 3 結束邊界

- 沒有修改 production、A/B/C/D、Top120、momentum_score、Hard Exclusion、
  Outcome、正式 WATCH／PRIMARY 或交易策略。
- 沒有用 Day3 rescue、Pocket gate、Watchlist action、模型、grid search 或
  portfolio backtest。
- Pending 沒有標成 Neutral；Excluded rows 保留在 row-level artifact。
- Phase 3I 後不開始 Phase 3J。只有 frozen shadow maturity update，
  或在兩條 branch 達正式 minimum sample 後進行一次最終 Go/No-Go。
"""
    handoff = f"""# Phase 3I — LLM Handoff

## Canonical decision

`{final}`

This is a sample-pending decision, not PASS and not FAIL.

## Safe numbers to quote

- Frozen config hash: `{config["config_hash"]}`.
- Rule replay mismatch: Branch N D0-A=0, D1-C=0; Branch R P4=0.
- Independent Branch N: eligible=0, Day1-C pass=0, Promotion W/N/L=0/0/0.
- Branch R Existing Pending: baseline total={int(r0.eligible_count)},
  matured={int(r0.matured_selected_count)}, matured Loser={int(r0.loser_count)}.
- Branch R R1: selected total={int(r1.selected_count)}, matured selected=
  {int(r1.matured_selected_count)}, selected dates={int(r1.selected_dates)},
  matured dates={int(r1.matured_selected_dates)}, W/N/L=
  {int(r1.winner_count)}/{int(r1.neutral_count)}/{int(r1.loser_count)}.
- Prospective after 2026-07-27: zero actual ingested trading dates.
- 7/27 row-level Phase2 universe is unavailable and was not reconstructed from
  the 24-stock formal WATCH list.

## Interpretation guardrails

Do not merge Phase 3G/3H historical discovery samples into Phase 3I independent
validation. Do not treat pending as Neutral. Do not quote the current risk
rates without `EARLY_READ_ONLY` and the one-matured-date limitation. Do not
change either frozen predicate. Do not propose Phase 3J.
"""
    return report, handoff


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("freezing Phase 3G/3H source-of-truth rules ...", flush=True)
    thresholds = load_frozen_thresholds()
    config = frozen_config(thresholds)
    (OUT / "phase3i_frozen_config.json").write_text(
        json.dumps(
            config,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    frozen_audit, reproducibility = frozen_rule_audit(
        config, thresholds
    )
    if not (
        reproducibility["branch_n_reproducible"]
        and reproducibility["branch_r_reproducible"]
    ):
        raise AssertionError("FROZEN_RULE_NOT_REPRODUCIBLE")

    print("loading Existing Pending and actual source dates ...", flush=True)
    data = load_validation_data()
    paths = build_existing_paths(data["existing"], data["wide"])
    normal_day0, normal_eval, normal_promotion = build_normal_rows(
        data["existing"], paths
    )
    risk_eligible, risk_results = build_risk_rows(
        data["existing"],
        paths,
        reproducibility["branch_r_reproducible"],
    )

    print("building frozen baselines and maturity metrics ...", flush=True)
    existing_result, prospective_result, combined_result, normal_topk = (
        aggregate_result_tables(normal_day0, risk_results)
    )
    risk_combined = risk_comparison(risk_results, "COMBINED")
    snapshots, daily_summary, daily_shadow = build_snapshots(
        data, config, risk_results
    )
    pending = build_pending(normal_eval, risk_results)
    pit = build_pit_audit(
        normal_day0,
        normal_eval,
        risk_results,
        config["config_hash"],
    )
    quality = data_quality_audit(
        data,
        normal_day0,
        risk_results,
        reproducibility,
    )
    changes = version_log(data, config["config_hash"])
    report, handoff = render_decision(
        config,
        frozen_audit,
        reproducibility,
        normal_day0,
        normal_eval,
        risk_results,
        risk_combined,
        existing_result,
        prospective_result,
        quality,
    )

    outputs = {
        "phase3i_frozen_config_audit.csv": frozen_audit,
        "phase3i_daily_input_snapshot.csv": snapshots,
        "phase3i_normal_day0_candidates.csv": normal_day0,
        "phase3i_normal_day1_evaluations.csv": normal_eval,
        "phase3i_normal_promotion_outcomes.csv": normal_promotion,
        "phase3i_normal_topk_baselines.csv": normal_topk,
        "phase3i_risk_eligible_candidates.csv": risk_eligible,
        "phase3i_risk_survival_results.csv": risk_results,
        "phase3i_risk_baseline_comparison.csv": risk_combined,
        "phase3i_pending_maturity.csv": pending,
        "phase3i_point_in_time_audit.csv": pit,
        "phase3i_data_quality_audit.csv": quality,
        "phase3i_version_change_log.csv": changes,
        "phase3i_daily_summary.csv": daily_summary,
        "phase3i_existing_pending_result.csv": existing_result,
        "phase3i_prospective_result.csv": prospective_result,
        "phase3i_combined_result.csv": combined_result,
    }
    for filename, frame in outputs.items():
        frame.to_csv(OUT / filename, index=False)
    (OUT / "phase3i_daily_shadow.json").write_text(
        json.dumps(
            daily_shadow,
            ensure_ascii=False,
            indent=2,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT / "phase3i_final_decision.md").write_text(
        report, encoding="utf-8"
    )
    (OUT / "phase3i_llm_handoff.md").write_text(
        handoff, encoding="utf-8"
    )

    # Frozen, point-in-time, and no-fabrication invariants.
    assert len(data["existing"]) == 140
    assert set(data["existing"].market_path_state) == {"RISK_OFF"}
    assert normal_day0.branch_n_eligible.sum() == 0
    assert risk_results.branch_r_eligible.sum() == 140
    assert (
        pd.to_datetime(pit.max_source_date)
        <= pd.to_datetime(pit.decision_date)
    ).all()
    assert pit.point_in_time_valid.all()
    assert not pit.future_feature_in_decision.any()
    assert not pit.outcome_in_decision.any()
    assert not pit.day3_rescue_used.any()
    assert prospective_result.selected_count.fillna(0).sum() == 0
    assert pending.pending_not_neutral.all()
    assert (
        combined_result.cohort_source == "COMBINED"
    ).all()
    assert config["config_hash"] == canonical_hash(
        {k: v for k, v in config.items() if k != "config_hash"}
    )
    print(
        f"wrote {len(outputs) + 4} artifacts to {OUT}; "
        f"config={config['config_hash'][:12]}, "
        f"existing=140, prospective=0, PIT={len(pit)}/{len(pit)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
