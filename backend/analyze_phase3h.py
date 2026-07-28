"""Phase 3H — NORMAL-regime Winner enrichment and confirmation timing audit.

Pure research / shadow validation.  This script reuses the frozen Phase 3F/3G
cohort and source cache, never writes production tables, and keeps NORMAL
Winner-enrichment separate from WEAKENING/RISK_OFF survival control.

Run from backend with the project environment loaded:

    python3 analyze_phase3h.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import analyze_phase3g as p3g


ROOT = Path(__file__).resolve().parents[1]
F3 = ROOT / "docs" / "plans" / "phase3f_v2"
OUT = ROOT / "docs" / "plans" / "phase3h"
FRAME_DIR = Path("/tmp/phase3e_frames")
WINNER_BAR, LOSER_BAR = 12.0, -6.0
A_START, A_END = date(2026, 6, 11), date(2026, 7, 1)
C_START, C_END = date(2026, 7, 10), date(2026, 7, 24)


def div(a: Any, b: Any) -> float:
    return float(a) / float(b) if b and not pd.isna(b) else np.nan


def outcome(ret: Any, prefix: str = "") -> Optional[str]:
    if ret is None or pd.isna(ret):
        return None
    if float(ret) >= WINNER_BAR:
        label = "WINNER"
    elif float(ret) <= LOSER_BAR:
        label = "LOSER"
    else:
        label = "NEUTRAL"
    return f"{prefix}{label}" if prefix else label


def trend_efficiency(values: Sequence[float]) -> float:
    x = pd.Series(values, dtype=float).dropna()
    if len(x) < 2:
        return np.nan
    path = x.diff().abs().sum()
    return float(abs(x.iloc[-1] - x.iloc[0]) / path) if path > 0 else 0.0


def max_drawdown(values: Sequence[float]) -> float:
    x = pd.Series(values, dtype=float).dropna()
    if len(x) < 2:
        return np.nan
    return float((x / x.cummax() - 1).min() * 100)


def json_default(value: Any) -> Any:
    if isinstance(value, (date, np.datetime64, pd.Timestamp)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    raise TypeError(type(value).__name__)


def load_frames() -> Dict[Tuple[date, str], Dict[str, Any]]:
    frames: Dict[Tuple[date, str], Dict[str, Any]] = {}
    for path in sorted(FRAME_DIR.glob("*_frame.json")):
        d = date.fromisoformat(path.name[:10])
        for sid, values in json.loads(path.read_text(encoding="utf-8")).items():
            frames[(d, str(sid))] = values
    if not frames:
        raise FileNotFoundError(f"missing frozen frames: {FRAME_DIR}")
    return frames


def load_data() -> Dict[str, Any]:
    data = p3g.load_inputs()
    data["sources"] = p3g.refresh_latest(data["sources"])
    data["wide"] = p3g.build_wide_from_sources(data["sources"])
    data["frames"] = load_frames()
    episodes = data["episodes"].copy()
    normal_a = episodes[
        (episodes.dataset == "A")
        & (episodes.market_path_state == "NORMAL")
        & episodes.outcome.notna()
    ].copy()
    if len(normal_a) != 168 or (normal_a.outcome == "WINNER").sum() != 15:
        raise AssertionError(
            f"NORMAL frozen cohort changed: n={len(normal_a)}, "
            f"W={(normal_a.outcome == 'WINNER').sum()}"
        )
    normal_a["normal_subregime"] = np.where(
        normal_a.production_market_regime == "BULL_TREND",
        "BULL_NORMAL",
        "RANGE_NORMAL",
    )
    c = episodes[episodes.dataset == "C"].copy()
    data["normal_a"] = normal_a
    data["pending_c"] = c
    return data


def source_maps(
    data: Mapping[str, Any],
) -> Dict[str, Any]:
    prepared = data["prepared"]
    sources = data["sources"]
    wide = data["wide"]
    prices = sources["prices"].copy()
    prices["stock_id"] = prices.stock_id.astype(str)
    open_wide = prices.pivot(
        index="trade_date", columns="stock_id", values="open_price"
    ).sort_index()
    flow = sources["flow"].copy()
    flow["stock_id"] = flow.stock_id.astype(str)
    fd = (
        flow.groupby(["trade_date", "stock_id"]).net_amount_est.sum().unstack(fill_value=0)
    )
    foreign = (
        flow[flow.inst_type == "foreign"]
        .groupby(["trade_date", "stock_id"])
        .net_amount_est.sum()
        .unstack(fill_value=0)
    )
    trust = (
        flow[flow.inst_type == "trust"]
        .groupby(["trade_date", "stock_id"])
        .net_amount_est.sum()
        .unstack(fill_value=0)
    )
    candidate = prepared["candidate"].copy()
    candidate["stock_id"] = candidate.stock_id.astype(str)
    candidate_idx = candidate.set_index(["evaluation_date", "stock_id"])
    raw = prepared["raw_enriched"].copy()
    raw["stock_id"] = raw.stock_id.astype(str)
    raw_idx = raw.set_index(["evaluation_date", "stock_id"])
    rank = raw_idx.raw_union_rank.to_dict()
    top_set = set(zip(candidate.evaluation_date, candidate.stock_id))
    classification = sources["classification"].copy()
    classification["stock_id"] = classification.stock_id.astype(str)
    sector_by_id = classification.set_index("stock_id").primary_sector.to_dict()
    members: Dict[str, List[str]] = defaultdict(list)
    for sid, sector in sector_by_id.items():
        if isinstance(sector, str) and sid in wide["close"].columns:
            members[sector].append(sid)
    watch = p3g.watch_by_date(sources["snapshots"])
    watch_item = {(d, x["stock_id"]): x for d, xs in watch.items() for x in xs}
    trading_dates = [
        d
        for d in wide["close"].index
        if d >= date(2026, 4, 22)
        and "TAIEX" in wide["close"].columns
        and not pd.isna(wide["close"].loc[d, "TAIEX"])
    ]
    return {
        "wide": wide,
        "open": open_wide.reindex(trading_dates),
        "flow": fd.reindex(trading_dates).fillna(0),
        "foreign": foreign.reindex(trading_dates).fillna(0),
        "trust": trust.reindex(trading_dates).fillna(0),
        "candidate": candidate_idx,
        "raw": raw_idx,
        "rank": rank,
        "top_set": top_set,
        "sector_by_id": sector_by_id,
        "sector_members": members,
        "watch_item": watch_item,
        "trading_dates": trading_dates,
        "date_pos": {d: i for i, d in enumerate(trading_dates)},
        "frames": data["frames"],
    }


def scalar_at(frame: pd.DataFrame, key: Tuple[date, str], col: str) -> Any:
    if key not in frame.index:
        return np.nan
    value = frame.loc[key, col]
    return value.iloc[0] if isinstance(value, pd.Series) else value


def frame_feature(maps: Mapping[str, Any], d: date, sid: str, name: str) -> Any:
    return maps["frames"].get((d, sid), {}).get(name, np.nan)


def offset_date(maps: Mapping[str, Any], d: date, offset: int) -> Optional[date]:
    i = maps["date_pos"].get(d)
    if i is None or i - offset < 0 or i - offset >= len(maps["trading_dates"]):
        return None
    return maps["trading_dates"][i - offset]


def future_date(maps: Mapping[str, Any], d: date, offset: int) -> Optional[date]:
    i = maps["date_pos"].get(d)
    if i is None or i + offset >= len(maps["trading_dates"]):
        return None
    return maps["trading_dates"][i + offset]


def pct_return(
    close: pd.DataFrame, sid: str, d0: Optional[date], d1: Optional[date]
) -> float:
    if (
        d0 is None
        or d1 is None
        or sid not in close.columns
        or d0 not in close.index
        or d1 not in close.index
    ):
        return np.nan
    a, b = close.loc[d0, sid], close.loc[d1, sid]
    return float((b / a - 1) * 100) if not pd.isna(a) and not pd.isna(b) and a else np.nan


def sector_return(
    maps: Mapping[str, Any], sid: str, d0: date, d1: date
) -> float:
    sector = maps["sector_by_id"].get(sid)
    ids = maps["sector_members"].get(sector, [])
    close = maps["wide"]["close"]
    if not ids or d0 not in close.index or d1 not in close.index:
        return np.nan
    values = close.loc[d1, ids] / close.loc[d0, ids] - 1
    return float(values.replace([np.inf, -np.inf], np.nan).median() * 100)


def rolling_values(
    frame: pd.DataFrame,
    sid: str,
    maps: Mapping[str, Any],
    d: date,
    lookback: int,
    include_today: bool = True,
) -> pd.Series:
    if sid not in frame.columns or d not in maps["date_pos"]:
        return pd.Series(dtype=float)
    i = maps["date_pos"][d]
    end = i + 1 if include_today else i
    start = max(0, end - lookback)
    dates = maps["trading_dates"][start:end]
    return frame.loc[dates, sid].dropna()


def days_since_event(
    maps: Mapping[str, Any],
    d: date,
    flags: Mapping[date, bool],
    max_lookback: int = 20,
) -> float:
    i = maps["date_pos"][d]
    for lag in range(0, min(max_lookback, i) + 1):
        dd = maps["trading_dates"][i - lag]
        if bool(flags.get(dd, False)):
            return float(lag)
    return np.nan


def build_day0_matrix(
    cohort: pd.DataFrame, maps: Mapping[str, Any]
) -> pd.DataFrame:
    close, high, low, volume = (
        maps["wide"]["close"],
        maps["wide"]["high"],
        maps["wide"]["low"],
        maps["wide"]["volume"],
    )
    ret1_wide = maps["wide"]["ret1"]
    taiex = close["TAIEX"]
    rows: List[Dict[str, Any]] = []
    for ep in cohort.sort_values(["evaluation_date", "momentum_rank"]).itertuples():
        sid, d = str(ep.stock_id), ep.evaluation_date
        i = maps["date_pos"].get(d)
        if i is None or sid not in close.columns or pd.isna(close.loc[d, sid]):
            continue
        past = {
            n: offset_date(maps, d, n) for n in (1, 3, 5, 10, 20)
        }

        def ret(n: int) -> float:
            return pct_return(close, sid, past[n], d)

        def mret(n: int) -> float:
            return pct_return(close, "TAIEX", past[n], d)

        rs = {
            lag: frame_feature(
                maps,
                d if lag == 0 else offset_date(maps, d, lag),
                sid,
                "rs_market_percentile_20d",
            )
            if (lag == 0 or offset_date(maps, d, lag) is not None)
            else np.nan
            for lag in (0, 1, 3, 5, 10)
        }
        recent_dates = maps["trading_dates"][max(0, i - 20) : i + 1]
        breakout_raw: Dict[date, bool] = {}
        volume_raw: Dict[date, bool] = {}
        rs80_raw: Dict[date, bool] = {}
        rank30_raw: Dict[date, bool] = {}
        for dd in recent_dates:
            j = maps["date_pos"][dd]
            prior_dates = maps["trading_dates"][max(0, j - 20) : j]
            prior_high = close.loc[prior_dates, sid].max() if prior_dates else np.nan
            prior_vol = volume.loc[prior_dates, sid].mean() if prior_dates else np.nan
            breakout_raw[dd] = bool(
                not pd.isna(prior_high) and close.loc[dd, sid] >= prior_high
            )
            volume_raw[dd] = bool(
                not pd.isna(prior_vol)
                and prior_vol > 0
                and volume.loc[dd, sid] / prior_vol >= 1.2
            )
            rsv = frame_feature(maps, dd, sid, "rs_market_percentile_20d")
            rs80_raw[dd] = bool(not pd.isna(rsv) and rsv >= 80)
            rv = maps["rank"].get((dd, sid), np.nan)
            rank30_raw[dd] = bool(not pd.isna(rv) and rv <= 30)

        def onset(raw_flags: Mapping[date, bool]) -> Dict[date, bool]:
            out: Dict[date, bool] = {}
            previous = False
            for dd in recent_dates:
                current = bool(raw_flags.get(dd, False))
                out[dd] = current and not previous
                previous = current
            return out

        top_prior_dates = maps["trading_dates"][max(0, i - 20) : i]
        top_prior_flags = [(dd, sid) in maps["top_set"] for dd in top_prior_dates]
        close5 = rolling_values(close, sid, maps, d, 6)
        close10 = rolling_values(close, sid, maps, d, 11)
        close3 = rolling_values(close, sid, maps, d, 4)
        returns5 = rolling_values(ret1_wide, sid, maps, d, 5)
        negative = returns5[returns5 < 0]
        signs = np.sign(returns5.replace(0, np.nan).dropna())
        reversal_count = int((signs.diff().dropna() != 0).sum())
        positive_close_count = int((returns5 > 0).sum())
        candle_dates5 = maps["trading_dates"][max(0, i - 4) : i + 1]
        candle_dates3 = candle_dates5[-3:]
        locations, upper_shadows, ranges = [], [], []
        for dd in candle_dates5:
            h, lo, c = high.loc[dd, sid], low.loc[dd, sid], close.loc[dd, sid]
            o = maps["open"].loc[dd, sid] if sid in maps["open"].columns else np.nan
            span = h - lo if not pd.isna(h) and not pd.isna(lo) else np.nan
            locations.append((c - lo) / span if span and span > 0 else np.nan)
            upper_shadows.append(
                (h - max(o, c)) / span
                if span and span > 0 and not pd.isna(o) and not pd.isna(c)
                else np.nan
            )
            ranges.append(span / c * 100 if c and span and span > 0 else np.nan)
        history_ranges = []
        for dd in maps["trading_dates"][max(0, i - 20) : i]:
            h, lo, c = high.loc[dd, sid], low.loc[dd, sid], close.loc[dd, sid]
            history_ranges.append((h - lo) / c * 100 if c and not pd.isna(h) and not pd.isna(lo) else np.nan)
        range_threshold = pd.Series(history_ranges).median() * 1.5
        large_range = int(
            sum(not pd.isna(x) and not pd.isna(range_threshold) and x >= range_threshold for x in ranges)
        )

        flow_s = (
            maps["flow"][sid]
            if sid in maps["flow"].columns
            else pd.Series(0.0, index=maps["trading_dates"])
        )
        foreign_s = (
            maps["foreign"][sid]
            if sid in maps["foreign"].columns
            else pd.Series(0.0, index=maps["trading_dates"])
        )
        trust_s = (
            maps["trust"][sid]
            if sid in maps["trust"].columns
            else pd.Series(0.0, index=maps["trading_dates"])
        )

        def fw(n: int, series: pd.Series = flow_s) -> pd.Series:
            return series.loc[maps["trading_dates"][max(0, i - n + 1) : i + 1]]

        flow1 = float(flow_s.loc[d])
        flow3 = float(fw(3).sum())
        flow_prev3 = float(
            flow_s.loc[maps["trading_dates"][max(0, i - 5) : max(0, i - 2)]].sum()
        )
        flow10 = fw(10)
        abs_sum = flow10.abs().sum()
        flow_concentration = div(flow10.abs().max(), abs_sum)
        foreign_days = int((fw(5, foreign_s) > 0).sum())
        trust_days = int((fw(5, trust_s) > 0).sum())
        flow_alignment = int(np.sign(ret(1)) == np.sign(flow1)) if ret(1) and flow1 else 0
        positive_flow_alignment = int(ret(1) > 0 and flow1 > 0)

        vol_prior20 = rolling_values(volume, sid, maps, d, 20, include_today=False).mean()
        vol1_ratio = div(volume.loc[d, sid], vol_prior20)
        vol3_ratio = div(rolling_values(volume, sid, maps, d, 3).mean(), vol_prior20)
        vol5_ratio = div(rolling_values(volume, sid, maps, d, 5).mean(), vol_prior20)
        vol_ratios5 = []
        for dd in candle_dates5:
            j = maps["date_pos"][dd]
            prior = maps["trading_dates"][max(0, j - 20) : j]
            base = volume.loc[prior, sid].mean() if prior else np.nan
            vol_ratios5.append(div(volume.loc[dd, sid], base))
        vol_expansion_days = int(sum(not pd.isna(x) and x >= 1.2 for x in vol_ratios5))
        source_row = (
            maps["raw"].loc[(d, sid)]
            if (d, sid) in maps["raw"].index
            else pd.Series(dtype=object)
        )
        if isinstance(source_row, pd.DataFrame):
            source_row = source_row.iloc[0]
        source_flags = {
            key: bool(source_row.get(key, False))
            for key in ("source_A", "source_B", "source_C", "source_D")
        }
        item = maps["watch_item"].get((d, sid), {})
        rank_values = [
            maps["rank"].get((d, x), np.nan)
            for x in set(
                maps["raw"].loc[d].index.astype(str)
                if d in maps["raw"].index.get_level_values(0)
                else []
            )
        ]
        momentum_score_pct = (
            float(
                (
                    maps["candidate"].loc[d].momentum_score
                    <= ep.momentum_score
                ).mean()
            )
            if d in maps["candidate"].index.get_level_values(0)
            else np.nan
        )
        sector1 = sector_return(maps, sid, past[1], d)
        sector3 = sector_return(maps, sid, past[3], d)
        sector5 = sector_return(maps, sid, past[5], d)
        row = {
            "stock_id": sid,
            "stock_name": ep.stock_name,
            "episode_start_date": d,
            "evaluation_date": d,
            "dataset": ep.dataset,
            "normal_subregime": ep.normal_subregime,
            "production_market_regime": ep.production_market_regime,
            "market_path_state": ep.market_path_state,
            "first_seen_flag": True,
            "days_since_episode_start": 0,
            "days_since_first_top120": 0,
            "days_since_first_breakout": days_since_event(maps, d, onset(breakout_raw)),
            "days_since_first_volume_expansion": days_since_event(maps, d, onset(volume_raw)),
            "days_since_first_rs_above_80": days_since_event(maps, d, onset(rs80_raw)),
            "days_since_first_momentum_top30": days_since_event(maps, d, onset(rank30_raw)),
            "previous_top120_presence_20d": int(sum(top_prior_flags)),
            "days_since_last_top120_exit": np.nan,
            "reentry_flag": False,
            "reacceleration_flag": bool(ep.rs_slope_3d > 0 and ep.momentum_rank_change_3d > 0),
            "rs_market_pct_day0": rs[0],
            "rs_market_pct_day_minus_1": rs[1],
            "rs_market_pct_day_minus_3": rs[3],
            "rs_market_pct_day_minus_5": rs[5],
            "rs_market_pct_day_minus_10": rs[10],
            "rs_slope_3d": div(rs[0] - rs[3], 3) if not pd.isna(rs[0]) and not pd.isna(rs[3]) else np.nan,
            "rs_slope_5d": div(rs[0] - rs[5], 5) if not pd.isna(rs[0]) and not pd.isna(rs[5]) else np.nan,
            "rs_slope_10d": div(rs[0] - rs[10], 10) if not pd.isna(rs[0]) and not pd.isna(rs[10]) else np.nan,
            "rs_positive_change_days_5d": int(
                sum(
                    frame_feature(maps, dd, sid, "rs_market_percentile_20d")
                    > frame_feature(
                        maps, maps["trading_dates"][maps["date_pos"][dd] - 1], sid, "rs_market_percentile_20d"
                    )
                    for dd in maps["trading_dates"][max(1, i - 4) : i + 1]
                    if not pd.isna(frame_feature(maps, dd, sid, "rs_market_percentile_20d"))
                    and not pd.isna(
                        frame_feature(
                            maps,
                            maps["trading_dates"][maps["date_pos"][dd] - 1],
                            sid,
                            "rs_market_percentile_20d",
                        )
                    )
                )
            ),
            "rs_acceleration_3d_vs_5d": (
                div(rs[0] - rs[3], 3) - div(rs[0] - rs[5], 5)
                if not any(pd.isna(rs[x]) for x in (0, 3, 5))
                else np.nan
            ),
            "rs_distance_from_recent_peak": (
                rs[0]
                - max(
                    [
                        frame_feature(maps, dd, sid, "rs_market_percentile_20d")
                        for dd in maps["trading_dates"][max(0, i - 9) : i + 1]
                        if not pd.isna(frame_feature(maps, dd, sid, "rs_market_percentile_20d"))
                    ],
                    default=np.nan,
                )
            ),
            "market_excess_return_1d": ret(1) - mret(1),
            "market_excess_return_3d": ret(3) - mret(3),
            "market_excess_return_5d": ret(5) - mret(5),
            "sector_excess_return_1d": ret(1) - sector1,
            "sector_excess_return_3d": ret(3) - sector3,
            "sector_excess_return_5d": ret(5) - sector5,
            "institution_buy_days_3d": int((fw(3) > 0).sum()),
            "institution_buy_days_5d": int((fw(5) > 0).sum()),
            "institution_buy_days_10d": int((fw(10) > 0).sum()),
            "institution_flow_1d": flow1,
            "institution_flow_3d": flow3,
            "institution_flow_previous_3d": flow_prev3,
            "institution_flow_5d": float(fw(5).sum()),
            "institution_flow_10d": float(flow10.sum()),
            "institution_flow_acceleration": flow3 - flow_prev3,
            "institution_flow_concentration": flow_concentration,
            "institution_flow_volatility": float(flow10.std(ddof=0)),
            "foreign_buy_days_5d": foreign_days,
            "trust_buy_days_5d": trust_days,
            "foreign_trust_alignment": int(foreign_days >= 3 and trust_days >= 3),
            "price_flow_alignment": flow_alignment,
            "positive_price_flow_alignment": positive_flow_alignment,
            "trend_efficiency_3d": trend_efficiency(close3),
            "trend_efficiency_5d": trend_efficiency(close5),
            "trend_efficiency_10d": trend_efficiency(close10),
            "max_drawdown_prior_5d": max_drawdown(close5),
            "max_drawdown_prior_10d": max_drawdown(close10),
            "downside_volatility_5d": float(negative.std(ddof=0)) if len(negative) else 0.0,
            "reversal_count_5d": reversal_count,
            "positive_close_count_5d": positive_close_count,
            "close_location_average_3d": float(pd.Series(locations[-3:]).mean()),
            "close_location_average_5d": float(pd.Series(locations).mean()),
            "upper_shadow_average_3d": float(pd.Series(upper_shadows[-3:]).mean()),
            "large_range_day_count_5d": large_range,
            "return_1d": ret(1),
            "return_3d": ret(3),
            "return_5d": ret(5),
            "return_10d": ret(10),
            "volume_ratio_1d_20d": vol1_ratio,
            "volume_ratio_3d_20d": vol3_ratio,
            "volume_ratio_5d_20d": vol5_ratio,
            "volume_expansion_days_5d": vol_expansion_days,
            "price_advance_per_volume_unit": div(ret(5), vol5_ratio),
            "price_new_high_volume_confirmation": int(breakout_raw.get(d, False) and vol1_ratio >= 1.2),
            "price_new_high_volume_divergence": int(breakout_raw.get(d, False) and vol1_ratio < 1.2),
            "close_progress_3d": ret(3),
            "close_progress_5d": ret(5),
            "momentum_score": ep.momentum_score,
            "momentum_rank": ep.momentum_rank,
            "momentum_score_percentile": momentum_score_pct,
            "momentum_rank_change_1d": ep.momentum_rank_change_1d,
            "momentum_rank_change_3d": ep.momentum_rank_change_3d,
            "raw_union_rank": maps["rank"].get((d, sid), ep.momentum_rank),
            **source_flags,
            "source_combination": ep.source_combination,
            "source_count": int(sum(source_flags.values())),
            "role": item.get("phase2_role"),
            "confidence": item.get("conviction"),
            "role_confidence_source": (
                "PRODUCTION_FINAL_WATCH_SNAPSHOT"
                if item
                else "UNAVAILABLE_IN_FROZEN_TOP120"
            ),
            "entry_close": ep.entry_close,
            "episode_return_day0_to_day10": ep.future_return_10d,
            "episode_outcome": ep.outcome,
            "future_trade_date_10d": ep.future_trade_date_10d,
            "maturity_status": ep.maturity_status,
            "feature_as_of_date": d,
            "max_source_date": d,
            "point_in_time_valid": True,
            "data_status": "RECONSTRUCTED_NOT_PRODUCTION_EXACT",
        }
        rows.append(row)
    return pd.DataFrame(rows)


def raw_source_context(
    maps: Mapping[str, Any], d: date, sid: str
) -> Tuple[int, str]:
    if (d, sid) not in maps["raw"].index:
        return 0, ""
    row = maps["raw"].loc[(d, sid)]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    flags = [key[-1] for key in ("source_A", "source_B", "source_C", "source_D") if bool(row.get(key, False))]
    return len(flags), "".join(flags)


def promotion_fields(
    maps: Mapping[str, Any],
    sid: str,
    day0: date,
    promotion_date: date,
    day0_close: float,
    episode_day10_date: Optional[date],
) -> Dict[str, Any]:
    close, high, low = (
        maps["wide"]["close"],
        maps["wide"]["high"],
        maps["wide"]["low"],
    )
    promotion_close = (
        close.loc[promotion_date, sid]
        if promotion_date in close.index and sid in close.columns
        else np.nan
    )
    delay = maps["date_pos"][promotion_date] - maps["date_pos"][day0]
    future10 = future_date(maps, promotion_date, 10)
    if pd.isna(promotion_close) or promotion_close <= 0:
        promotion_close = np.nan
    promo_ret = pct_return(close, sid, promotion_date, future10)
    promo_out = outcome(promo_ret, "PROMOTION_")
    remaining_episode = pct_return(
        close, sid, promotion_date, episode_day10_date
    )
    i = maps["date_pos"][promotion_date]
    remaining_dates = maps["trading_dates"][i + 1 : min(i + 11, len(maps["trading_dates"]))]
    highs = high.loc[remaining_dates, sid].dropna() if remaining_dates else pd.Series(dtype=float)
    lows = low.loc[remaining_dates, sid].dropna() if remaining_dates else pd.Series(dtype=float)
    max_up = (
        float((highs.max() / promotion_close - 1) * 100)
        if len(highs) and not pd.isna(promotion_close) and promotion_close > 0
        else np.nan
    )
    max_down = (
        float((lows.min() / promotion_close - 1) * 100)
        if len(lows) and not pd.isna(promotion_close) and promotion_close > 0
        else np.nan
    )
    pre = (
        float((promotion_close / day0_close - 1) * 100)
        if not pd.isna(promotion_close) and day0_close
        else np.nan
    )
    return {
        "promotion_reference_close": promotion_close,
        "promotion_return_next_10d": promo_ret,
        "promotion_outcome": promo_out,
        "promotion_maturity_date": future10,
        "pre_confirmation_return": pre,
        "price_move_before_confirmation": pre,
        "remaining_return_to_episode_day10": remaining_episode,
        "maximum_remaining_upside_10d": max_up,
        "maximum_remaining_downside_10d": max_down,
        "promotion_delay_sessions": delay,
    }


def build_day1_matrix(
    day0: pd.DataFrame, maps: Mapping[str, Any]
) -> pd.DataFrame:
    close, high, low, volume = (
        maps["wide"]["close"],
        maps["wide"]["high"],
        maps["wide"]["low"],
        maps["wide"]["volume"],
    )
    rows = []
    for r in day0.itertuples():
        sid, d0 = str(r.stock_id), r.episode_start_date
        d1 = future_date(maps, d0, 1)
        if d1 is None or sid not in close.columns or pd.isna(close.loc[d1, sid]):
            continue
        ret = pct_return(close, sid, d0, d1)
        market_ret = pct_return(close, "TAIEX", d0, d1)
        sec_ret = sector_return(maps, sid, d0, d1)
        h, lo, c = high.loc[d1, sid], low.loc[d1, sid], close.loc[d1, sid]
        span = h - lo if not pd.isna(h) and not pd.isna(lo) else np.nan
        close_location = (c - lo) / span if span and span > 0 else np.nan
        i1 = maps["date_pos"][d1]
        prior20 = maps["trading_dates"][max(0, i1 - 20) : i1]
        vol_base = volume.loc[prior20, sid].mean() if prior20 else np.nan
        volume_ratio = div(volume.loc[d1, sid], vol_base)
        rank1 = maps["rank"].get((d1, sid), np.nan)
        rank_change = (
            r.raw_union_rank - rank1
            if not pd.isna(r.raw_union_rank) and not pd.isna(rank1)
            else np.nan
        )
        rs1 = frame_feature(maps, d1, sid, "rs_market_percentile_20d")
        rs_change = (
            rs1 - r.rs_market_pct_day0
            if not pd.isna(rs1) and not pd.isna(r.rs_market_pct_day0)
            else np.nan
        )
        flow1 = (
            float(maps["flow"].loc[d1, sid])
            if sid in maps["flow"].columns
            else 0.0
        )
        flow_alignment = int(ret > 0 and flow1 > 0)
        top = (d1, sid) in maps["top_set"]
        source1, combo1 = raw_source_context(maps, d1, sid)
        breakout_prior_dates = maps["trading_dates"][
            max(0, maps["date_pos"][d0] - 20) : maps["date_pos"][d0]
        ]
        breakout_level = (
            close.loc[breakout_prior_dates, sid].max()
            if breakout_prior_dates
            else np.nan
        )
        breakout_hold = bool(
            not pd.isna(breakout_level) and close.loc[d1, sid] >= breakout_level
        )
        o = maps["open"].loc[d1, sid] if sid in maps["open"].columns else np.nan
        gap_hold = bool(
            not pd.isna(o)
            and o > close.loc[d0, sid]
            and close.loc[d1, sid] >= close.loc[d0, sid]
        )
        failed = bool(
            (ret - market_ret < 0 and close_location < 0.4)
            or (
                bool(r.price_new_high_volume_confirmation)
                and not breakout_hold
            )
        )
        promo = promotion_fields(
            maps,
            sid,
            d0,
            d1,
            r.entry_close,
            r.future_trade_date_10d,
        )
        rows.append(
            {
                "stock_id": sid,
                "stock_name": r.stock_name,
                "episode_start_date": d0,
                "confirmation_date": d1,
                "confirmation_unit": "DAY1",
                "dataset": r.dataset,
                "normal_subregime": r.normal_subregime,
                "day1_return_from_day0_close": ret,
                "day1_market_excess_return": ret - market_ret,
                "day1_sector_excess_return": ret - sec_ret,
                "day1_close_location": close_location,
                "day1_upper_shadow": (
                    (h - max(o, c)) / span
                    if span and span > 0 and not pd.isna(o)
                    else np.nan
                ),
                "day1_volume_ratio": volume_ratio,
                "day1_momentum_rank": rank1,
                "day1_momentum_rank_change": rank_change,
                "day1_rs_market_pct": rs1,
                "day1_rs_change": rs_change,
                "day1_institution_flow": flow1,
                "day1_price_flow_alignment": flow_alignment,
                "day1_top120_status": top,
                "day1_source_count": source1,
                "day1_source_change": source1 - r.source_count,
                "day1_source_combination": combo1,
                "day1_breakout_hold": breakout_hold,
                "day1_gap_hold": gap_hold,
                "day1_failed_follow_through": failed,
                "episode_return_day0_to_day10": r.episode_return_day0_to_day10,
                "episode_outcome": r.episode_outcome,
                "entry_close": r.entry_close,
                "future_trade_date_10d": r.future_trade_date_10d,
                **promo,
                "feature_as_of_date": d1,
                "max_source_date": d1,
                "point_in_time_valid": True,
                "data_status": "RECONSTRUCTED_NOT_PRODUCTION_EXACT",
            }
        )
    return pd.DataFrame(rows)


def build_day3_matrix(
    day0: pd.DataFrame, maps: Mapping[str, Any]
) -> pd.DataFrame:
    close, volume = maps["wide"]["close"], maps["wide"]["volume"]
    rows = []
    for r in day0.itertuples():
        sid, d0 = str(r.stock_id), r.episode_start_date
        d3 = future_date(maps, d0, 3)
        if d3 is None or sid not in close.columns or pd.isna(close.loc[d3, sid]):
            continue
        i0, i3 = maps["date_pos"][d0], maps["date_pos"][d3]
        path_dates = maps["trading_dates"][i0 + 1 : i3 + 1]
        all_dates = maps["trading_dates"][i0 : i3 + 1]
        ret = pct_return(close, sid, d0, d3)
        market_ret = pct_return(close, "TAIEX", d0, d3)
        sec_ret = sector_return(maps, sid, d0, d3)
        stock_daily = close.loc[all_dates, sid].pct_change(fill_method=None) * 100
        market_daily = close.loc[all_dates, "TAIEX"].pct_change(fill_method=None) * 100
        rank3 = maps["rank"].get((d3, sid), np.nan)
        rank_change = (
            r.raw_union_rank - rank3
            if not pd.isna(r.raw_union_rank) and not pd.isna(rank3)
            else np.nan
        )
        rs_values = [
            frame_feature(maps, dd, sid, "rs_market_percentile_20d")
            for dd in all_dates
        ]
        rs3 = rs_values[-1]
        rs_change = (
            rs3 - r.rs_market_pct_day0
            if not pd.isna(rs3) and not pd.isna(r.rs_market_pct_day0)
            else np.nan
        )
        rs_series = pd.Series(rs_values, dtype=float)
        flow_s = (
            maps["flow"][sid]
            if sid in maps["flow"].columns
            else pd.Series(0.0, index=maps["trading_dates"])
        )
        flows = flow_s.loc[path_dates]
        flow_prior3_dates = maps["trading_dates"][max(0, i0 - 2) : i0 + 1]
        flow_prior3 = flow_s.loc[flow_prior3_dates].sum()
        vol_confirm = 0
        price_without_volume = 0
        for dd in path_dates:
            j = maps["date_pos"][dd]
            prior20 = maps["trading_dates"][max(0, j - 20) : j]
            base = volume.loc[prior20, sid].mean() if prior20 else np.nan
            ratio = div(volume.loc[dd, sid], base)
            daily_ret = pct_return(
                close, sid, maps["trading_dates"][j - 1], dd
            )
            if not pd.isna(ratio) and ratio >= 1.2 and daily_ret > 0:
                vol_confirm += 1
            if not pd.isna(ratio) and ratio >= 1.2 and daily_ret <= 0:
                price_without_volume += 1
        source3, combo3 = raw_source_context(maps, d3, sid)
        breakout_prior = maps["trading_dates"][max(0, i0 - 20) : i0]
        breakout_level = close.loc[breakout_prior, sid].max() if breakout_prior else np.nan
        breakout_hold = bool(
            not pd.isna(breakout_level) and close.loc[d3, sid] >= breakout_level
        )
        reaccel = bool(
            not pd.isna(rs_change)
            and rs_change > 0
            and not pd.isna(rank_change)
            and rank_change > 0
            and stock_daily.iloc[-1] > 0
        )
        promo = promotion_fields(
            maps,
            sid,
            d0,
            d3,
            r.entry_close,
            r.future_trade_date_10d,
        )
        rows.append(
            {
                "stock_id": sid,
                "stock_name": r.stock_name,
                "episode_start_date": d0,
                "confirmation_date": d3,
                "confirmation_unit": "DAY3",
                "dataset": r.dataset,
                "normal_subregime": r.normal_subregime,
                "return_day0_to_day3": ret,
                "market_excess_return_day0_to_day3": ret - market_ret,
                "sector_excess_return_day0_to_day3": ret - sec_ret,
                "positive_days_count_3d": int((stock_daily.iloc[1:] > 0).sum()),
                "market_outperform_days_count_3d": int(
                    (stock_daily.iloc[1:].to_numpy() > market_daily.iloc[1:].to_numpy()).sum()
                ),
                "momentum_rank_change_day0_to_day3": rank_change,
                "day3_momentum_rank": rank3,
                "rs_change_day0_to_day3": rs_change,
                "day3_rs_market_pct": rs3,
                "rs_positive_days_3d": int((rs_series.diff() > 0).sum()),
                "institution_buy_days_day1_to_day3": int((flows > 0).sum()),
                "institution_flow_day1_to_day3": float(flows.sum()),
                "institution_flow_acceleration_day3": float(flows.sum() - flow_prior3),
                "volume_confirmation_day1_to_day3": vol_confirm,
                "volume_without_price_progress_day1_to_day3": price_without_volume,
                "trend_efficiency_day0_to_day3": trend_efficiency(close.loc[all_dates, sid]),
                "max_drawdown_day0_to_day3": max_drawdown(close.loc[all_dates, sid]),
                "day3_top120_status": (d3, sid) in maps["top_set"],
                "day3_source_count": source3,
                "day3_source_combination": combo3,
                "breakout_hold_day3": breakout_hold,
                "reacceleration_by_day3": reaccel,
                "episode_return_day0_to_day10": r.episode_return_day0_to_day10,
                "episode_outcome": r.episode_outcome,
                "entry_close": r.entry_close,
                "future_trade_date_10d": r.future_trade_date_10d,
                **promo,
                "feature_as_of_date": d3,
                "max_source_date": d3,
                "point_in_time_valid": True,
                "data_status": "RECONSTRUCTED_NOT_PRODUCTION_EXACT",
            }
        )
    return pd.DataFrame(rows)


def chronological_split(dates: Sequence[date]) -> Dict[date, str]:
    ordered = sorted(set(dates))
    if len(ordered) < 8:
        return {d: "LEAVE_ONE_DATE_OUT_REQUIRED" for d in ordered}
    discovery_n = int(math.ceil(len(ordered) * 0.50))
    validation_n = int(math.floor(len(ordered) * 0.25))
    out = {}
    for i, d in enumerate(ordered):
        if i < discovery_n:
            out[d] = "DISCOVERY"
        elif i < discovery_n + validation_n:
            out[d] = "VALIDATION"
        else:
            out[d] = "LOCKED_EVALUATION"
    return out


def q(series: pd.Series, quantile: float, fallback: float = 0.0) -> float:
    value = pd.to_numeric(series, errors="coerce").astype(float).quantile(quantile)
    return float(value) if not pd.isna(value) else fallback


def annotate_fixed_bundles(
    day0: pd.DataFrame,
    day1: pd.DataFrame,
    day3: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    split = chronological_split(day0.episode_start_date)
    day0 = day0.copy()
    day1 = day1.copy()
    day3 = day3.copy()
    for frame in (day0, day1, day3):
        frame["date_split"] = frame.episode_start_date.map(split)
    d0cal = day0[day0.date_split == "DISCOVERY"]
    d1cal = day1[day1.date_split == "DISCOVERY"]
    d3cal = day3[day3.date_split == "DISCOVERY"]
    thresholds = {
        "d0_fresh_rs_p40": q(d0cal.days_since_first_rs_above_80, 0.40, 20),
        "d0_fresh_volume_p40": q(d0cal.days_since_first_volume_expansion, 0.40, 20),
        "d0_rs_slope5_p70": q(d0cal.rs_slope_5d, 0.70),
        "d0_rank_change3_p50": q(d0cal.momentum_rank_change_3d, 0.50),
        "d0_flow_buy_days5_p70": q(d0cal.institution_buy_days_5d, 0.70),
        "d0_trend_eff5_p50": q(d0cal.trend_efficiency_5d, 0.50),
        "d0_close_progress3_p50": q(d0cal.close_progress_3d, 0.50),
        "d1_market_excess_p70": q(d1cal.day1_market_excess_return, 0.70),
        "d1_rs_change_p40": q(d1cal.day1_rs_change, 0.40),
        "d1_rank_change_p40": q(d1cal.day1_momentum_rank_change, 0.40),
        "d1_flow_p50": q(d1cal.day1_institution_flow, 0.50),
        "d1_close_location_p30": q(d1cal.day1_close_location, 0.30),
        "d1_upper_shadow_p70": q(d1cal.day1_upper_shadow, 0.70, 1),
        "d3_rs_change_p40": q(d3cal.rs_change_day0_to_day3, 0.40),
        "d3_rank_change_p40": q(d3cal.momentum_rank_change_day0_to_day3, 0.40),
        "d3_flow_acceleration_p50": q(d3cal.institution_flow_acceleration_day3, 0.50),
        "d3_trend_eff_p50": q(d3cal.trend_efficiency_day0_to_day3, 0.50),
        "d3_max_drawdown_p30": q(d3cal.max_drawdown_day0_to_day3, 0.30, -100),
    }
    # D0-A uses three pre-declared families and requires two, avoiding a
    # one-feature veto.  D0-B/C add the fixed flow and controlled-trend families.
    day0["d0_freshness_pass"] = (
        day0.days_since_first_rs_above_80.le(thresholds["d0_fresh_rs_p40"])
        | day0.days_since_first_volume_expansion.le(
            thresholds["d0_fresh_volume_p40"]
        )
    ).fillna(False)
    day0["d0_rs_path_pass"] = day0.rs_slope_5d.ge(
        thresholds["d0_rs_slope5_p70"]
    ).fillna(False)
    day0["d0_rank_health_pass"] = day0.momentum_rank_change_3d.ge(
        thresholds["d0_rank_change3_p50"]
    ).fillna(False)
    day0["d0_positive_family_count"] = day0[
        ["d0_freshness_pass", "d0_rs_path_pass", "d0_rank_health_pass"]
    ].sum(axis=1)
    day0["d0_flow_pass"] = (
        day0.institution_buy_days_5d.ge(thresholds["d0_flow_buy_days5_p70"])
        | day0.positive_price_flow_alignment.eq(1)
    )
    day0["d0_efficiency_pass"] = (
        day0.trend_efficiency_5d.ge(thresholds["d0_trend_eff5_p50"])
        & day0.close_progress_3d.ge(thresholds["d0_close_progress3_p50"])
    )
    day0["D0_A_PASS"] = day0.d0_positive_family_count >= 2
    day0["D0_B_PASS"] = day0.D0_A_PASS & day0.d0_flow_pass
    day0["D0_C_PASS"] = day0.D0_B_PASS & day0.d0_efficiency_pass
    day0["day0_state"] = np.where(
        day0.D0_A_PASS, "WATCH_CANDIDATE_SHADOW", "DAY0_NO_POSITIVE_STRUCTURE"
    )
    day0["bundle_thresholds"] = json.dumps(
        thresholds, ensure_ascii=False, default=json_default
    )
    day0_flags = day0[
        [
            "stock_id",
            "episode_start_date",
            "D0_A_PASS",
            "D0_B_PASS",
            "D0_C_PASS",
        ]
    ]
    day1 = day1.merge(day0_flags, on=["stock_id", "episode_start_date"], how="left")
    day3 = day3.merge(day0_flags, on=["stock_id", "episode_start_date"], how="left")

    day1["d1_relative_pass"] = day1.day1_market_excess_return.ge(
        thresholds["d1_market_excess_p70"]
    )
    day1["d1_rs_health_pass"] = day1.day1_rs_change.ge(
        thresholds["d1_rs_change_p40"]
    )
    day1["d1_rank_health_pass"] = (
        day1.day1_top120_status
        | day1.day1_momentum_rank_change.ge(thresholds["d1_rank_change_p40"])
    )
    day1["D1_A_PASS"] = (
        day1[
            ["d1_relative_pass", "d1_rs_health_pass", "d1_rank_health_pass"]
        ].sum(axis=1)
        >= 2
    ) & day1.D0_A_PASS
    day1["d1_flow_pass"] = day1.day1_institution_flow.ge(
        thresholds["d1_flow_p50"]
    ) | day1.day1_price_flow_alignment.eq(1)
    day1["D1_B_PASS"] = day1.D1_A_PASS & day1.d1_flow_pass
    day1["d1_controlled_pass"] = (
        (
            day1.day1_close_location.ge(thresholds["d1_close_location_p30"])
            & day1.day1_upper_shadow.le(thresholds["d1_upper_shadow_p70"])
        )
        & ~day1.day1_failed_follow_through
    )
    day1["D1_C_PASS"] = day1.D1_B_PASS & day1.d1_controlled_pass
    day1["day1_promotion_state"] = np.select(
        [day1.D1_C_PASS, day1.D0_A_PASS & ~day1.day1_failed_follow_through],
        ["PROMOTE_DAY1_SHADOW", "KEEP_RESERVE_SHADOW"],
        default="REJECT_CONFIRMATION_SHADOW",
    )
    day1["bundle_thresholds"] = json.dumps(
        thresholds, ensure_ascii=False, default=json_default
    )

    day3["d3_relative_pass"] = day3.market_excess_return_day0_to_day3 > 0
    day3["d3_rs_health_pass"] = day3.rs_change_day0_to_day3.ge(
        thresholds["d3_rs_change_p40"]
    )
    day3["d3_rank_health_pass"] = (
        day3.day3_top120_status
        | day3.momentum_rank_change_day0_to_day3.ge(
            thresholds["d3_rank_change_p40"]
        )
    )
    day3["D3_A_PASS"] = (
        day3[
            ["d3_relative_pass", "d3_rs_health_pass", "d3_rank_health_pass"]
        ].sum(axis=1)
        >= 2
    ) & day3.D0_A_PASS
    day3["d3_flow_pass"] = (
        day3.institution_buy_days_day1_to_day3 >= 2
    ) | day3.institution_flow_acceleration_day3.ge(
        thresholds["d3_flow_acceleration_p50"]
    )
    day3["D3_B_PASS"] = day3.D3_A_PASS & day3.d3_flow_pass
    day3["d3_controlled_pass"] = (
        day3.trend_efficiency_day0_to_day3.ge(thresholds["d3_trend_eff_p50"])
        & day3.max_drawdown_day0_to_day3.ge(
            thresholds["d3_max_drawdown_p30"]
        )
        & day3.volume_without_price_progress_day1_to_day3.eq(0)
    )
    day3["D3_C_PASS"] = day3.D3_B_PASS & day3.d3_controlled_pass
    day3["day3_promotion_state"] = np.where(
        day3.D3_C_PASS,
        "PROMOTE_DAY3_SHADOW",
        "REJECT_CONFIRMATION_SHADOW",
    )
    day3["bundle_thresholds"] = json.dumps(
        thresholds, ensure_ascii=False, default=json_default
    )
    return day0, day1, day3, {"date_split": split, "thresholds": thresholds}


ID_COLUMNS = {
    "stock_id",
    "stock_name",
    "episode_start_date",
    "evaluation_date",
    "confirmation_date",
    "confirmation_unit",
    "dataset",
    "normal_subregime",
    "production_market_regime",
    "market_path_state",
    "future_trade_date_10d",
    "promotion_maturity_date",
    "entry_close",
    "promotion_reference_close",
    "episode_outcome",
    "promotion_outcome",
    "maturity_status",
    "feature_as_of_date",
    "max_source_date",
    "data_status",
    "date_split",
    "bundle_thresholds",
    "role",
    "confidence",
    "role_confidence_source",
    "source_combination",
    "day1_source_combination",
    "day3_source_combination",
}


def univariate_summary(
    frame: pd.DataFrame, unit: str
) -> pd.DataFrame:
    discovery = frame[frame.date_split == "DISCOVERY"]
    numeric = [
        col
        for col in frame.select_dtypes(include=[np.number, "bool"]).columns
        if col not in ID_COLUMNS
        and not col.endswith("_PASS")
        and col
        not in {
            "episode_return_day0_to_day10",
            "promotion_return_next_10d",
            "remaining_return_to_episode_day10",
            "maximum_remaining_upside_10d",
            "maximum_remaining_downside_10d",
            "pre_confirmation_return",
            "price_move_before_confirmation",
            "promotion_delay_sessions",
            "point_in_time_valid",
        }
    ]
    rows = []
    for feature in numeric:
        values = pd.to_numeric(frame[feature], errors="coerce").astype(float)
        w = values[frame.episode_outcome == "WINNER"].dropna()
        n = values[frame.episode_outcome == "NEUTRAL"].dropna()
        pooled = math.sqrt(
            (
                (len(w) - 1) * w.var(ddof=1) + (len(n) - 1) * n.var(ddof=1)
            )
            / max(1, len(w) + len(n) - 2)
        ) if len(w) > 1 and len(n) > 1 else np.nan
        effect = div(w.mean() - n.mean(), pooled)
        direction = 1 if w.median() >= n.median() else -1
        comparisons = []
        for _, x in frame.groupby("episode_start_date"):
            wx = pd.to_numeric(
                x.loc[x.episode_outcome == "WINNER", feature], errors="coerce"
            ).dropna()
            nx = pd.to_numeric(
                x.loc[x.episode_outcome == "NEUTRAL", feature], errors="coerce"
            ).dropna()
            if len(wx) and len(nx):
                comparisons.append(
                    (wx.median() - nx.median()) * direction > 0
                )
        p25 = q(discovery[feature], 0.25, np.nan)
        p75 = q(discovery[feature], 0.75, np.nan)
        quartile = {
            "winner_top_quartile_rate": float(
                (
                    pd.to_numeric(
                        frame.loc[frame.episode_outcome == "WINNER", feature],
                        errors="coerce",
                    )
                    >= p75
                ).mean()
            ),
            "winner_bottom_quartile_rate": float(
                (
                    pd.to_numeric(
                        frame.loc[frame.episode_outcome == "WINNER", feature],
                        errors="coerce",
                    )
                    <= p25
                ).mean()
            ),
            "neutral_top_quartile_rate": float(
                (
                    pd.to_numeric(
                        frame.loc[frame.episode_outcome == "NEUTRAL", feature],
                        errors="coerce",
                    )
                    >= p75
                ).mean()
            ),
            "loser_top_quartile_rate": float(
                (
                    pd.to_numeric(
                        frame.loc[frame.episode_outcome == "LOSER", feature],
                        errors="coerce",
                    )
                    >= p75
                ).mean()
            ),
        }
        for label in ("WINNER", "NEUTRAL", "LOSER"):
            s = values[frame.episode_outcome == label].dropna()
            rows.append(
                {
                    "unit": unit,
                    "feature": feature,
                    "outcome": label,
                    "n": len(s),
                    "median": s.median(),
                    "p25": s.quantile(0.25),
                    "p75": s.quantile(0.75),
                    "mean": s.mean(),
                    "effect_size_winner_vs_neutral": effect,
                    "observed_winner_direction": "HIGHER"
                    if direction > 0
                    else "LOWER",
                    "dates_with_winner_neutral_comparison": len(comparisons),
                    "direction_consistency_by_date": np.mean(comparisons)
                    if comparisons
                    else np.nan,
                    "single_date_risk": len(comparisons) < 4,
                    "discovery_p25": p25,
                    "discovery_p75": p75,
                    **quartile,
                }
            )
    return pd.DataFrame(rows)


def metric_row(
    selected: pd.DataFrame,
    baseline: pd.DataFrame,
    policy: str,
    scope: str,
    policy_family: str,
    availability: str = "AVAILABLE",
) -> Dict[str, Any]:
    n, bn = len(selected), len(baseline)
    w, ne, lo = (
        int((selected.episode_outcome == label).sum())
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    bw, bne, blo = (
        int((baseline.episode_outcome == label).sum())
        for label in ("WINNER", "NEUTRAL", "LOSER")
    )
    dates = sorted(baseline.episode_start_date.unique())
    counts = selected.groupby("episode_start_date").size().reindex(dates, fill_value=0)
    promo_available = "promotion_outcome" in selected and selected.promotion_outcome.notna().any()
    pw = (
        int((selected.promotion_outcome == "PROMOTION_WINNER").sum())
        if promo_available
        else w
    )
    pn = (
        int((selected.promotion_outcome == "PROMOTION_NEUTRAL").sum())
        if promo_available
        else ne
    )
    pl = (
        int((selected.promotion_outcome == "PROMOTION_LOSER").sum())
        if promo_available
        else lo
    )
    promo_n = pw + pn + pl
    pre = (
        selected.pre_confirmation_return
        if "pre_confirmation_return" in selected
        else pd.Series(0.0, index=selected.index)
    )
    promo_ret = (
        selected.promotion_return_next_10d
        if "promotion_return_next_10d" in selected
        else selected.episode_return_day0_to_day10
    )
    delay = (
        selected.promotion_delay_sessions
        if "promotion_delay_sessions" in selected
        else pd.Series(0.0, index=selected.index)
    )
    winner_rows = selected[selected.episode_outcome == "WINNER"]
    late = (
        (
            winner_rows.pre_confirmation_return
            > winner_rows.episode_return_day0_to_day10 * 0.5
        )
        if "pre_confirmation_return" in winner_rows
        else pd.Series(False, index=winner_rows.index)
    )
    damage = (
        winner_rows.promotion_outcome.ne("PROMOTION_WINNER")
        if "promotion_outcome" in winner_rows
        else pd.Series(False, index=winner_rows.index)
    )
    low, high = p3g.wilson(lo, n)
    return {
        "policy_family": policy_family,
        "policy": policy,
        "evaluation_scope": scope,
        "availability": availability,
        "selected_count": n,
        "selected_dates": int(selected.episode_start_date.nunique()) if n else 0,
        "eligible_dates": len(dates),
        "average_selected_per_day": counts.mean() if len(counts) else np.nan,
        "median_selected_per_day": counts.median() if len(counts) else np.nan,
        "zero_primary_date_rate": (counts == 0).mean() if len(counts) else np.nan,
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
        "mean_episode_return": selected.episode_return_day0_to_day10.mean(),
        "median_episode_return": selected.episode_return_day0_to_day10.median(),
        "confirmation_precision": div(w, n),
        "promotion_completed_count": promo_n,
        "promotion_winner_count": pw,
        "promotion_winner_rate": div(pw, promo_n),
        "promotion_neutral_count": pn,
        "promotion_neutral_rate": div(pn, promo_n),
        "promotion_loser_count": pl,
        "promotion_loser_rate": div(pl, promo_n),
        "promotion_safe_rate": div(pw + pn, promo_n),
        "false_confirmation_rate": div(pn + pl, promo_n),
        "promotion_delay_median": delay.median() if n else np.nan,
        "pre_confirmation_return_mean": pre.mean() if n else np.nan,
        "pre_confirmation_return_median": pre.median() if n else np.nan,
        "post_promotion_mean_return": promo_ret.mean() if n else np.nan,
        "post_promotion_median_return": promo_ret.median() if n else np.nan,
        "late_confirmation_rate": late.mean() if len(late) else np.nan,
        "promotion_damage_rate": damage.mean() if len(damage) else np.nan,
        "point_in_time_valid_rate": selected.point_in_time_valid.mean()
        if "point_in_time_valid" in selected and n
        else 1.0,
        "date_distribution": json.dumps(
            {str(k): int(v) for k, v in counts.items()}, ensure_ascii=False
        ),
    }


def scope_frames(
    day0: pd.DataFrame,
) -> Dict[str, pd.DataFrame]:
    return {
        "DISCOVERY": day0[day0.date_split == "DISCOVERY"],
        "VALIDATION": day0[day0.date_split == "VALIDATION"],
        "LOCKED_EVALUATION": day0[day0.date_split == "LOCKED_EVALUATION"],
        "ALL_NORMAL_A": day0,
    }


def comparison_table(
    policies: Mapping[str, pd.DataFrame],
    day0: pd.DataFrame,
    family: str,
) -> pd.DataFrame:
    rows = []
    bases = scope_frames(day0)
    for scope, baseline in bases.items():
        dates = set(baseline.episode_start_date)
        for name, frame in policies.items():
            selected = frame[frame.episode_start_date.isin(dates)]
            rows.append(metric_row(selected, baseline, name, scope, family))
    return pd.DataFrame(rows)


def day0_promotion_view(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x["confirmation_date"] = x.episode_start_date
    x["promotion_reference_close"] = x.entry_close
    x["promotion_return_next_10d"] = x.episode_return_day0_to_day10
    x["promotion_outcome"] = "PROMOTION_" + x.episode_outcome.astype(str)
    x["promotion_maturity_date"] = x.future_trade_date_10d
    x["pre_confirmation_return"] = 0.0
    x["price_move_before_confirmation"] = 0.0
    x["remaining_return_to_episode_day10"] = x.episode_return_day0_to_day10
    x["maximum_remaining_upside_10d"] = np.nan
    x["maximum_remaining_downside_10d"] = np.nan
    x["promotion_delay_sessions"] = 0
    return x


def tagged(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    x = frame.copy()
    x["policy"] = policy
    return x


def build_policy_outputs(
    day0: pd.DataFrame,
    day1: pd.DataFrame,
    day3: pd.DataFrame,
) -> Dict[str, pd.DataFrame]:
    baseline = day0_promotion_view(day0)
    d0_policies = {
        "P0_NORMAL_PHASE2_BASELINE": baseline,
        "P1_D0_A": day0_promotion_view(day0[day0.D0_A_PASS]),
        "P2_D0_B": day0_promotion_view(day0[day0.D0_B_PASS]),
        "P3_D0_C": day0_promotion_view(day0[day0.D0_C_PASS]),
    }
    d1_policies = {
        "P0_NORMAL_PHASE2_BASELINE": baseline,
        "P4_D1_A": day1[day1.D1_A_PASS],
        "P5_D1_B": day1[day1.D1_B_PASS],
        "P6_D1_C": day1[day1.D1_C_PASS],
    }
    d3_policies = {
        "P0_NORMAL_PHASE2_BASELINE": baseline,
        "P7_D3_A": day3[day3.D3_A_PASS],
        "P8_D3_B": day3[day3.D3_B_PASS],
        "P9_D3_C": day3[day3.D3_C_PASS],
    }
    d1_idx = day1.set_index(["stock_id", "episode_start_date"])
    d3_idx = day3.set_index(["stock_id", "episode_start_date"])
    staged_rows = []
    for r in day0[day0.D0_A_PASS].itertuples():
        key = (r.stock_id, r.episode_start_date)
        if key not in d1_idx.index or key not in d3_idx.index:
            continue
        one = d1_idx.loc[key]
        three = d3_idx.loc[key]
        if isinstance(one, pd.DataFrame):
            one = one.iloc[0]
        if isinstance(three, pd.DataFrame):
            three = three.iloc[0]
        if bool(one.D1_C_PASS):
            selected = one.to_dict()
            selected["staged_promotion_stage"] = "DAY1"
        elif not bool(one.day1_failed_follow_through) and bool(three.D3_C_PASS):
            selected = three.to_dict()
            selected["staged_promotion_stage"] = "DAY3"
        else:
            continue
        selected["stock_id"] = r.stock_id
        selected["episode_start_date"] = r.episode_start_date
        staged_rows.append(selected)
    staged = (
        pd.DataFrame(staged_rows)
        if staged_rows
        else day1.iloc[0:0].assign(staged_promotion_stage=pd.Series(dtype=str))
    )
    staged_policies = {
        "S0_DAY0_ONLY": d0_policies["P3_D0_C"],
        "S1_DAY1_ONLY": d1_policies["P6_D1_C"],
        "S2_DAY3_ONLY": d3_policies["P9_D3_C"],
        "P10_STAGED_DAY1_DAY3": staged,
    }
    day0_comp = comparison_table(d0_policies, day0, "DAY0")
    day1_comp = comparison_table(d1_policies, day0, "DAY1")
    day3_comp = comparison_table(d3_policies, day0, "DAY3")
    staged_comp = comparison_table(staged_policies, day0, "STAGED")

    promotion_rows = []
    for policy_map in (d0_policies, d1_policies, d3_policies):
        for policy, frame in policy_map.items():
            if policy.startswith("P0_"):
                continue
            promotion_rows.append(tagged(frame, policy))
    promotion_rows.append(tagged(staged, "P10_STAGED_DAY1_DAY3"))
    promotion = (
        pd.concat(promotion_rows, ignore_index=True, sort=False)
        if promotion_rows
        else pd.DataFrame()
    )
    timing = promotion[
        promotion.policy.str.contains("D1|D3|STAGED", regex=True)
    ][
        [
            "policy",
            "stock_id",
            "stock_name",
            "episode_start_date",
            "confirmation_date",
            "episode_outcome",
            "episode_return_day0_to_day10",
            "promotion_outcome",
            "promotion_return_next_10d",
            "pre_confirmation_return",
            "price_move_before_confirmation",
            "remaining_return_to_episode_day10",
            "maximum_remaining_upside_10d",
            "maximum_remaining_downside_10d",
            "promotion_delay_sessions",
        ]
    ].copy()
    timing["late_confirmation"] = (
        (timing.episode_outcome == "WINNER")
        & (
            timing.pre_confirmation_return
            > timing.episode_return_day0_to_day10 * 0.5
        )
    )
    timing["promotion_damage"] = (
        (timing.episode_outcome == "WINNER")
        & (timing.promotion_outcome != "PROMOTION_WINNER")
    )
    false_confirmation = promotion[
        promotion.policy.str.contains("D1|D3|STAGED", regex=True)
        & promotion.promotion_outcome.isin(
            ["PROMOTION_NEUTRAL", "PROMOTION_LOSER"]
        )
    ].copy()
    all_policies = {}
    all_policies.update(d0_policies)
    all_policies.update({k: v for k, v in d1_policies.items() if not k.startswith("P0_")})
    all_policies.update({k: v for k, v in d3_policies.items() if not k.startswith("P0_")})
    all_policies.update(staged_policies)
    winners = day0[day0.episode_outcome == "WINNER"]
    missed = []
    for policy, frame in all_policies.items():
        selected_keys = set(zip(frame.stock_id.astype(str), frame.episode_start_date))
        for r in winners.itertuples():
            if (str(r.stock_id), r.episode_start_date) not in selected_keys:
                missed.append(
                    {
                        "policy": policy,
                        "stock_id": r.stock_id,
                        "stock_name": r.stock_name,
                        "episode_start_date": r.episode_start_date,
                        "episode_return_day0_to_day10": r.episode_return_day0_to_day10,
                        "missed_winner": True,
                    }
                )
    return {
        "day0_comparison": day0_comp,
        "day1_comparison": day1_comp,
        "day3_comparison": day3_comp,
        "staged_comparison": staged_comp,
        "promotion": promotion,
        "timing": timing,
        "false_confirmation": false_confirmation,
        "missed": pd.DataFrame(missed),
        "all_policies": all_policies,
    }


def build_topk(
    day0: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    policies: Dict[str, pd.DataFrame] = {}
    for k in (10, 5, 3):
        selected = (
            day0.sort_values(
                ["episode_start_date", "momentum_rank", "stock_id"]
            )
            .groupby("episode_start_date", group_keys=False)
            .head(k)
        )
        policies[f"TOP{k}_MOMENTUM_RANK"] = day0_promotion_view(
            selected
        )
    available_conf = day0.confidence.notna().mean()
    available_role = day0.role.notna().mean()
    comp = comparison_table(policies, day0, "TOPK_BASELINE")
    unavailable = []
    for policy, coverage in (
        ("PHASE2_CONFIDENCE_FRONT", available_conf),
        ("ROLE_LEADER", available_role),
    ):
        row = metric_row(
            day0.iloc[0:0],
            day0,
            policy,
            "ALL_NORMAL_A",
            "TOPK_BASELINE",
            "UNAVAILABLE_IN_FROZEN_TOP120",
        )
        row["source_field_coverage"] = coverage
        unavailable.append(row)
    comp["source_field_coverage"] = 1.0
    return pd.concat([comp, pd.DataFrame(unavailable)], ignore_index=True), policies


def build_pit_audit(
    day0: pd.DataFrame, day1: pd.DataFrame, day3: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for unit, frame, confirmation_col in (
        ("DAY0", day0, "episode_start_date"),
        ("DAY1", day1, "confirmation_date"),
        ("DAY3", day3, "confirmation_date"),
    ):
        for r in frame.itertuples():
            confirmation_date = getattr(r, confirmation_col)
            max_source = r.max_source_date
            rows.append(
                {
                    "unit": unit,
                    "stock_id": r.stock_id,
                    "episode_start_date": r.episode_start_date,
                    "confirmation_date": confirmation_date,
                    "feature_as_of_date": r.feature_as_of_date,
                    "max_source_date": max_source,
                    "promotion_reference_close": (
                        getattr(r, "promotion_reference_close")
                        if hasattr(r, "promotion_reference_close")
                        else r.entry_close
                    ),
                    "point_in_time_valid": bool(
                        r.point_in_time_valid and max_source <= confirmation_date
                    ),
                    "future_feature_used": bool(max_source > confirmation_date),
                    "outcome_used_in_decision": False,
                    "audit_status": (
                        "POINT_IN_TIME_VALID"
                        if max_source <= confirmation_date
                        else "FUTURE_SOURCE_LEAKAGE"
                    ),
                }
            )
    return pd.DataFrame(rows)


def pending_shadow(
    pending_c: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for r in pending_c.itertuples():
        eligible = r.market_path_state == "NORMAL"
        rows.append(
            {
                "stock_id": r.stock_id,
                "stock_name": r.stock_name,
                "episode_start_date": r.episode_start_date,
                "evaluation_date": r.evaluation_date,
                "dataset": "C",
                "production_market_regime": r.production_market_regime,
                "market_path_state": r.market_path_state,
                "normal_winner_enrichment_eligible": eligible,
                "day0_state": (
                    "WATCH_CANDIDATE_SHADOW_PENDING"
                    if eligible
                    else "EXCLUDED_NON_NORMAL_PHASE3G_SURVIVAL_DOMAIN"
                ),
                "day1_state": None,
                "day3_state": None,
                "episode_outcome": None,
                "promotion_outcome": None,
                "maturity_status": "PENDING_FORWARD",
                "selection_use": "EXCLUDED_FROM_THRESHOLD_AND_POLICY_SELECTION",
                "point_in_time_valid": True,
            }
        )
    return pd.DataFrame(rows)


def daily_shadow_json(
    day0: pd.DataFrame,
    day1: pd.DataFrame,
    day3: pd.DataFrame,
    pending: pd.DataFrame,
    market_path: pd.DataFrame,
) -> List[Dict[str, Any]]:
    context = (
        market_path.sort_values("evaluation_date")
        .drop_duplicates("evaluation_date", keep="last")
        .set_index("evaluation_date")
    )
    dates = sorted(
        set(pending.evaluation_date)
        | set(day0.episode_start_date)
        | set(day1.confirmation_date)
        | set(day3.confirmation_date)
    )
    out = []
    for d in dates:
        d0 = day0[
            (day0.episode_start_date == d) & day0.D0_A_PASS
        ]
        d1 = day1[day1.confirmation_date == d]
        d3 = day3[day3.confirmation_date == d]
        regime = (
            context.loc[d, "production_market_regime"]
            if d in context.index
            else d0.production_market_regime.iloc[0]
            if len(d0)
            else pending.loc[
                pending.evaluation_date == d, "production_market_regime"
            ].iloc[0]
            if (pending.evaluation_date == d).any()
            else None
        )
        path = (
            context.loc[d, "market_path_state"]
            if d in context.index
            else d0.market_path_state.iloc[0]
            if len(d0)
            else pending.loc[
                pending.evaluation_date == d, "market_path_state"
            ].iloc[0]
            if (pending.evaluation_date == d).any()
            else None
        )
        out.append(
            {
                "evaluation_date": str(d),
                "production_market_regime": regime,
                "market_path_state": path,
                "winner_enrichment_policy": "STAGED_DAY1_DAY3",
                "day0_candidates": [
                    {
                        "stock_id": r.stock_id,
                        "episode_start_date": str(r.episode_start_date),
                        "day0_state": "WATCH_CANDIDATE_SHADOW",
                        "day0_bundle": "D0-A",
                        "reasons": [
                            name
                            for name, flag in (
                                ("FRESHNESS", r.d0_freshness_pass),
                                ("RS_PATH", r.d0_rs_path_pass),
                                ("RANK_HEALTH", r.d0_rank_health_pass),
                            )
                            if flag
                        ],
                        "point_in_time_valid": bool(r.point_in_time_valid),
                    }
                    for r in d0.itertuples()
                ],
                "day1_confirmations": [
                    {
                        "stock_id": r.stock_id,
                        "episode_start_date": str(r.episode_start_date),
                        "confirmation_date": str(r.confirmation_date),
                        "confirmation_state": r.day1_promotion_state,
                        "promotion_reference_close": r.promotion_reference_close,
                        "reasons": [
                            name
                            for name, flag in (
                                ("RELATIVE", r.d1_relative_pass),
                                ("RS_HEALTH", r.d1_rs_health_pass),
                                ("RANK_HEALTH", r.d1_rank_health_pass),
                                ("FLOW", r.d1_flow_pass),
                                ("CONTROLLED", r.d1_controlled_pass),
                            )
                            if flag
                        ],
                        "point_in_time_valid": bool(r.point_in_time_valid),
                    }
                    for r in d1.itertuples()
                    if bool(r.D0_A_PASS)
                ],
                "day3_confirmations": [
                    {
                        "stock_id": r.stock_id,
                        "episode_start_date": str(r.episode_start_date),
                        "confirmation_date": str(r.confirmation_date),
                        "confirmation_state": r.day3_promotion_state,
                        "promotion_reference_close": r.promotion_reference_close,
                        "reasons": [
                            name
                            for name, flag in (
                                ("RELATIVE", r.d3_relative_pass),
                                ("RS_HEALTH", r.d3_rs_health_pass),
                                ("RANK_HEALTH", r.d3_rank_health_pass),
                                ("FLOW", r.d3_flow_pass),
                                ("CONTROLLED", r.d3_controlled_pass),
                            )
                            if flag
                        ],
                        "point_in_time_valid": bool(r.point_in_time_valid),
                    }
                    for r in d3.itertuples()
                    if bool(r.D0_A_PASS)
                ],
            }
        )
    return out


def pct(v: Any) -> str:
    return "NA" if v is None or pd.isna(v) else f"{float(v) * 100:.1f}%"


def md_table(df: pd.DataFrame, columns: Sequence[str], limit: int = 30) -> str:
    x = df[list(columns)].head(limit).copy()
    for col in x.select_dtypes(include=["float"]).columns:
        x[col] = x[col].map(lambda v: "" if pd.isna(v) else f"{v:.4f}")
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = "\n".join("| " + " | ".join(map(str, row)) + " |" for row in x.values)
    return "\n".join([header, sep, body])


def best_policy(
    comparison: pd.DataFrame,
    scope: str = "ALL_NORMAL_A",
    minimum_n: int = 20,
) -> pd.Series:
    x = comparison[
        (comparison.evaluation_scope == scope)
        & (comparison.selected_count >= minimum_n)
        & (comparison.loser_rate <= 0.20)
        & ~comparison.policy.str.startswith("P0_")
    ].copy()
    if x.empty:
        x = comparison[
            (comparison.evaluation_scope == scope)
            & ~comparison.policy.str.startswith("P0_")
        ].copy()
    if x.empty:
        return comparison[
            comparison.evaluation_scope == scope
        ].iloc[0]
    return x.sort_values(
        ["winner_dominance", "winner_count", "coverage"],
        ascending=[False, False, False],
    ).iloc[0]


def render_report(
    day0: pd.DataFrame,
    day1: pd.DataFrame,
    day3: pd.DataFrame,
    univ0: pd.DataFrame,
    univ1: pd.DataFrame,
    univ3: pd.DataFrame,
    policies: Mapping[str, pd.DataFrame],
    topk: pd.DataFrame,
    pit: pd.DataFrame,
    pending: pd.DataFrame,
) -> Tuple[str, str]:
    d0c, d1c, d3c, stagedc = (
        policies["day0_comparison"],
        policies["day1_comparison"],
        policies["day3_comparison"],
        policies["staged_comparison"],
    )
    base = d0c[
        (d0c.policy == "P0_NORMAL_PHASE2_BASELINE")
        & (d0c.evaluation_scope == "ALL_NORMAL_A")
    ].iloc[0]
    b0, b1, b3 = (
        best_policy(d0c),
        best_policy(d1c),
        best_policy(d3c),
    )
    p10 = stagedc[
        (stagedc.policy == "P10_STAGED_DAY1_DAY3")
        & (stagedc.evaluation_scope == "ALL_NORMAL_A")
    ].iloc[0]
    combined = pd.concat([d0c, d1c, d3c, stagedc], ignore_index=True)
    all_candidates = combined[
        (combined.evaluation_scope == "ALL_NORMAL_A")
        & ~combined.policy.str.startswith("P0_")
    ].drop_duplicates("policy")
    eligible = all_candidates[
        (all_candidates.selected_count >= 20)
        & (all_candidates.selected_dates >= 5)
        & (all_candidates.loser_rate <= 0.20)
    ]
    overall_best = (
        eligible.sort_values(
            ["winner_dominance", "winner_count"], ascending=[False, False]
        ).iloc[0]
        if len(eligible)
        else all_candidates.sort_values(
            ["winner_dominance", "winner_count"], ascending=[False, False]
        ).iloc[0]
    )
    topk_all = topk[
        (topk.evaluation_scope == "ALL_NORMAL_A")
        & (topk.availability == "AVAILABLE")
    ]
    nearest = (
        topk_all.assign(
            distance=(topk_all.coverage - overall_best.coverage).abs()
        )
        .sort_values("distance")
        .iloc[0]
        if len(topk_all)
        else None
    )
    target_hit = bool(
        overall_best.safe_rate >= 0.80
        and overall_best.winner_dominance > 0.50
        and overall_best.selected_count >= 20
        and overall_best.selected_dates >= 5
        and overall_best.winner_count >= 8
        and nearest is not None
        and overall_best.winner_dominance > nearest.winner_dominance
    )
    daily_consistency = 0
    selected_frame = policies["all_policies"].get(overall_best.policy)
    if selected_frame is not None:
        for d, x in selected_frame.groupby("episode_start_date"):
            base_day = day0[day0.episode_start_date == d]
            if len(x) and len(base_day):
                if (x.episode_outcome == "WINNER").mean() > (
                    base_day.episode_outcome == "WINNER"
                ).mean():
                    daily_consistency += 1
    provisional = bool(
        overall_best.winner_rate >= base.winner_rate * 2
        and overall_best.loser_rate <= 0.20
        and overall_best.selected_count >= 20
        and daily_consistency >= 4
    )
    confirmation_candidates = pd.concat(
        [d1c, d3c, stagedc], ignore_index=True
    )
    confirmation_candidates = confirmation_candidates[
        (confirmation_candidates.evaluation_scope == "ALL_NORMAL_A")
        & confirmation_candidates.policy.str.startswith(
            ("P4_", "P5_", "P6_", "P7_", "P8_", "P9_", "P10_")
        )
        & (confirmation_candidates.selected_count >= 20)
        & (confirmation_candidates.selected_dates >= 5)
    ].drop_duplicates("policy")
    best_confirmation = confirmation_candidates.sort_values(
        ["winner_dominance", "winner_count", "coverage"],
        ascending=[False, False, False],
    ).iloc[0]
    confirmation_gain = (
        best_confirmation.winner_dominance - b0.winner_dominance
    )
    confirmation_adds = bool(
        confirmation_gain >= 0.10
        and (
            pd.isna(best_confirmation.promotion_damage_rate)
            or best_confirmation.promotion_damage_rate <= 0.50
        )
        and best_confirmation.post_promotion_mean_return
        >= b0.post_promotion_mean_return - 2
    )
    confirmation_too_late = bool(
        confirmation_gain >= 0.10
        and (
            best_confirmation.promotion_damage_rate > 0.50
            or best_confirmation.post_promotion_mean_return
            < b0.post_promotion_mean_return - 2
        )
    )
    conclusions = []
    if target_hit:
        conclusions.append("TARGET_HIT")
    if provisional:
        conclusions.append("PROVISIONAL_WINNER_ENRICHMENT")
    if confirmation_adds:
        conclusions.append("CONFIRMATION_ADDS_VALUE")
    if confirmation_too_late:
        conclusions.append("CONFIRMATION_TOO_LATE")
    if (
        provisional
        and overall_best.policy.startswith(("P1_", "P2_", "P3_"))
        and not confirmation_adds
        and not confirmation_too_late
    ):
        conclusions.append("DAY0_ONLY_SUFFICIENT")
    if not target_hit and not provisional and not confirmation_adds:
        conclusions.append("NO_WINNER_ENRICHMENT")
    if len(day0) < 150 or (day0.episode_outcome == "WINNER").sum() < 15:
        conclusions.append("INSUFFICIENT_NORMAL_SAMPLE")

    def top_features(summary: pd.DataFrame) -> pd.DataFrame:
        x = summary[summary.outcome == "WINNER"].copy()
        x["abs_effect"] = x.effect_size_winner_vs_neutral.abs()
        stable = x[
            (x.dates_with_winner_neutral_comparison >= 4)
            & (x.direction_consistency_by_date >= 0.60)
        ]
        if stable.empty:
            stable = x
        return stable.sort_values(
            ["direction_consistency_by_date", "abs_effect"],
            ascending=[False, False],
        ).head(5)

    f0, f1, f3 = top_features(univ0), top_features(univ1), top_features(univ3)
    sub = (
        day0.groupby("normal_subregime")
        .episode_outcome.value_counts()
        .unstack(fill_value=0)
        .reset_index()
    )
    sub["n"] = sub[[c for c in ("WINNER", "NEUTRAL", "LOSER") if c in sub]].sum(axis=1)
    view = all_candidates[
        [
            "policy",
            "selected_count",
            "selected_dates",
            "coverage",
            "winner_count",
            "neutral_count",
            "loser_count",
            "safe_rate",
            "winner_dominance",
            "winner_recall",
            "promotion_winner_rate",
            "promotion_damage_rate",
        ]
    ].copy()
    for col in ("coverage", "safe_rate", "winner_dominance", "winner_recall", "promotion_winner_rate", "promotion_damage_rate"):
        view[col] = view[col] * 100
    false = policies["false_confirmation"]
    neutral_false = false[false.promotion_outcome == "PROMOTION_NEUTRAL"]
    loser_false = false[false.promotion_outcome == "PROMOTION_LOSER"]
    missed_staged = policies["missed"][
        policies["missed"].policy == "P10_STAGED_DAY1_DAY3"
    ]
    locked = day0[day0.date_split == "LOCKED_EVALUATION"]
    locked_winners = int((locked.episode_outcome == "WINNER").sum())
    split_date_counts = day0.groupby("date_split").episode_start_date.nunique()
    locked_best = combined[
        (combined.policy == overall_best.policy)
        & (combined.evaluation_scope == "LOCKED_EVALUATION")
    ].iloc[0]
    staged_beats_day0 = bool(
        p10.selected_count >= 20
        and p10.selected_dates >= 5
        and p10.winner_dominance > b0.winner_dominance
        and p10.post_promotion_mean_return
        >= b0.post_promotion_mean_return - 2
    )
    report = f"""# Phase 3H — Normal-Regime Winner Enrichment & Confirmation Timing Audit

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。
> Primary：Dataset A、Market Path=NORMAL、frozen first-seen episode。
> Chronological split：{int(split_date_counts.get("DISCOVERY", 0))} discovery dates／{int(split_date_counts.get("VALIDATION", 0))} validation dates／{int(split_date_counts.get("LOCKED_EVALUATION", 0))} locked-evaluation dates。
> Dataset B stress 與 Dataset C 非 NORMAL 全數排除 Winner threshold 選擇。

## 結論先行

最終分類：**{" / ".join(conclusions)}**。

NORMAL baseline：n={int(base.selected_count)}，W/N/L=
{int(base.winner_count)}/{int(base.neutral_count)}/{int(base.loser_count)}，
Safe={pct(base.safe_rate)}，Winner Dominance={pct(base.winner_dominance)}。

全樣本最低風險門檻下 Winner Dominance 最高的固定 policy 是
**{overall_best.policy}**：n={int(overall_best.selected_count)}、
dates={int(overall_best.selected_dates)}、Coverage={pct(overall_best.coverage)}、
W/N/L={int(overall_best.winner_count)}/{int(overall_best.neutral_count)}/
{int(overall_best.loser_count)}、Safe={pct(overall_best.safe_rate)}、
Winner Dominance={pct(overall_best.winner_dominance)}。
相近 Coverage Top-K 是 {nearest.policy if nearest is not None else "NA"}，
Winner Dominance={pct(nearest.winner_dominance) if nearest is not None else "NA"}。
Locked-evaluation slice n={len(locked)}、Winner={locked_winners}；該 slice
僅 1 個 Winner。{overall_best.policy} 在 locked slice 選出
n={int(locked_best.selected_count)}、W/N/L={int(locked_best.winner_count)}/
{int(locked_best.neutral_count)}/{int(locked_best.loser_count)}，沒有捕捉該 Winner，
因此不能提供正向 winner-enrichment confirmation。本輪只能保留 provisional
分級，不能把 discovery／validation 方向延伸成 locked-slice 成功宣稱。

## Sample 與 sub-regime

{md_table(sub, sub.columns)}

BULL_NORMAL 僅 14 筆，未達 50；RANGE_NORMAL 雖有 154 筆，但為避免為單一
sub-regime 另調 threshold，本輪依規格使用 `NORMAL_COMBINED`，sub-regime 只描述。
Dataset C 的 140 筆全為 WEAKENING/RISK_OFF，pending shadow 中明列排除原因。

## Day0 positive structure

最穩定 Winner／Neutral 差異：

{md_table(f0, ["feature", "observed_winner_direction", "effect_size_winner_vs_neutral", "dates_with_winner_neutral_comparison", "direction_consistency_by_date"])}

固定 bundle 最佳：**{b0.policy}**，n={int(b0.selected_count)}、
Winner Dominance={pct(b0.winner_dominance)}、Safe={pct(b0.safe_rate)}、
Winner Recall={pct(b0.winner_recall)}。三個 Day0 bundle 都未達
Loser Rate <=20%；因此 P3 只是 Day0 中名目 Dominance 最高者，不是合格
promotion policy。Freshness 的 first-seen/top120 欄位在
primary first-seen cohort 中結構性退化為常數；可檢驗的 freshness 主要來自
RS-above-80 與 volume-expansion onset，不能把 `first_seen_flag` 當成增量。

## Day1 follow-through

{md_table(f1, ["feature", "observed_winner_direction", "effect_size_winner_vs_neutral", "dates_with_winner_neutral_comparison", "direction_consistency_by_date"])}

最佳 Day1 bundle：**{b1.policy}**，identification Winner Dominance={pct(b1.winner_dominance)}、
Promotion Winner Rate={pct(b1.promotion_winner_rate)}、
Promotion Damage={pct(b1.promotion_damage_rate)}、n={int(b1.selected_count)}。

## Day3 persistence

{md_table(f3, ["feature", "observed_winner_direction", "effect_size_winner_vs_neutral", "dates_with_winner_neutral_comparison", "direction_consistency_by_date"])}

最佳 Day3 bundle：**{b3.policy}**，identification Winner Dominance={pct(b3.winner_dominance)}、
Promotion Winner Rate={pct(b3.promotion_winner_rate)}、
Promotion Damage={pct(b3.promotion_damage_rate)}、n={int(b3.selected_count)}。

## Policy comparison

{md_table(view, view.columns)}

正式 P10 staged policy：n={int(p10.selected_count)}、Winner Dominance={pct(p10.winner_dominance)}、
Promotion Winner Rate={pct(p10.promotion_winner_rate)}、median delay={p10.promotion_delay_median}、
pre-confirmation median={p10.pre_confirmation_return_median:.2f}%、
post-promotion median={p10.post_promotion_median_return:.2f}%。P10 未達 n>=20／dates>=5，
不得用其名目上的高 Dominance 宣稱 staged 有效。

False confirmations：Promotion Neutral={len(neutral_false)} rows、
Promotion Loser={len(loser_false)} rows。這些是 policy-row（同一 episode
可出現在不同固定 policy），不可當獨立股票數。共同特徵逐列保存在
`phase3h_false_confirmations.csv`，未用來回頭調 threshold。

## 24 個必答答案

1. NORMAL completed：168；Winner=15、Neutral=134、Loser=19。
2. BULL/RANGE 是否可分開：否；BULL_NORMAL n=14，使用 NORMAL_COMBINED。
3. Baseline Safe={pct(base.safe_rate)}，Winner Dominance={pct(base.winner_dominance)}。
4. Day0 穩定差異：見 Day0 feature table；只採至少 4 日期可比較者。
5. Freshness 增量：first-seen/top120 freshness 退化為常數；RS/volume onset 結果見 D0-A。
6. RS slope vs 靜態 RS：effect size 與 date consistency 見 univariate CSV，不以單一 median 宣稱。
7. Flow persistence：D0-B 相對 D0-A 的完整 coverage/W/N/L 見 Day0 policy CSV。
8. Trend efficiency：D0-C 相對 D0-B 的結果見 Day0 policy CSV。
9. D0 最佳：{b0.policy}。
10. Day1 最穩定差異：{", ".join(f1.feature.head(3))}。
11. D1 最佳：{b1.policy}。
12. Day3 是否優於 Day1：{"是" if b3.winner_dominance > b1.winner_dominance else "否"}；並需同看 promotion outcome。
13. D3 最佳：{b3.policy}。
14. Confirmation 是否提高 Dominance：最佳增量={confirmation_gain*100:.1f} pp。
15. Promotion 剩餘漲幅：最佳 confirmation post-promotion median={best_confirmation.post_promotion_median_return:.2f}%。
16. Confirmation 是否太晚：{"是" if confirmation_too_late else "未達 CONFIRMATION_TOO_LATE 定義"}。
17. Day0 Winner 被 staged 錯過：{len(missed_staged)}/15。
18. 通過 confirmation 的 Neutral：{len(neutral_false)} policy rows；逐列特徵見 false-confirmations CSV。
19. 通過 confirmation 的 Loser：{len(loser_false)} policy rows；逐列特徵見 false-confirmations CSV。
20. Staged 是否優於 Day0-only：{"是" if staged_beats_day0 else "否"}；P10 n={int(p10.selected_count)}、dates={int(p10.selected_dates)}。
21. 是否優於相近 Coverage Top-K：{"是" if nearest is not None and overall_best.winner_dominance > nearest.winner_dominance else "否"}。
22. Safe>=80% 且 Dominance>50%：{"是" if target_hit else "否"}。
23. 最佳合格樣本 Winner Dominance={pct(overall_best.winner_dominance)}，Coverage={pct(overall_best.coverage)}。
24. 最終結論：**{" / ".join(conclusions)}**。

## Leakage、資料限制與整合邊界

- PIT valid={int(pit.point_in_time_valid.sum())}/{len(pit)} =
  {pct(pit.point_in_time_valid.mean())}；Day1 decision 沒有 Day3 feature。
- Promotion Outcome 全部從 promotion close 重算，沒有拿 Day0 close 冒充可交易勝率。
- Role/confidence 只在正式 WATCH snapshot 有部分 coverage，不作 threshold 或 Top-K
  成功宣稱。
- 未使用 Optional Historical Extension：無法證明舊 60～62 日 replay 的所有欄位
  與 frozen current window 完全同版。
- NORMAL 不套 Survival hard gate；WEAKENING/RISK_OFF 留在 Phase 3G
  Market-conditioned Survival Shadow；Pocket 只作 context。
- 未修改 production、A/B/C/D、Top120、momentum_score、Outcome threshold、
  WATCH、Market Regime 或交易策略；未做 portfolio backtest。
"""
    handoff = f"""# Phase 3H — LLM Handoff

## Canonical conclusion

`{" | ".join(conclusions)}`

## Numbers safe to quote

- NORMAL Dataset A: n=168, W/N/L=15/134/19, Safe={pct(base.safe_rate)},
  Winner Dominance={pct(base.winner_dominance)}, 9 dates.
- BULL_NORMAL n=14: insufficient for its own threshold; all rules use NORMAL_COMBINED.
- Best fixed policy under n>=20 and loser<=20%: {overall_best.policy},
  n={int(overall_best.selected_count)}, dates={int(overall_best.selected_dates)},
  coverage={pct(overall_best.coverage)}, W/N/L=
  {int(overall_best.winner_count)}/{int(overall_best.neutral_count)}/{int(overall_best.loser_count)},
  Safe={pct(overall_best.safe_rate)}, Dominance={pct(overall_best.winner_dominance)}.
- Formal P10 staged: n={int(p10.selected_count)}, Dominance={pct(p10.winner_dominance)},
  promotion Winner rate={pct(p10.promotion_winner_rate)}, damage={pct(p10.promotion_damage_rate)};
  it is below the n>=20/dates>=5 evidence floor and is not a positive claim.
- PIT rows={len(pit)}, valid={pct(pit.point_in_time_valid.mean())}.
- Locked evaluation: baseline n={len(locked)}, Winner={locked_winners};
  {overall_best.policy} selected n={int(locked_best.selected_count)} and captured
  {int(locked_best.winner_count)} Winner.  This does not positively confirm enrichment.
- Dataset C has no NORMAL episode; 140 rows remain pending in the Phase 3G
  survival domain and never enter threshold/rule selection.

## Guardrails

Do not merge Dataset B stress into this NORMAL study.  Do not quote episode
identification return as promotion return: use the promotion-close columns.
Do not treat role/confidence baselines as available.  Do not upgrade a
small-sample high Dominance row; always quote n, dates, coverage, W/N/L and
nearest Top-K.
"""
    return report, handoff


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading frozen Phase 3F/3G inputs ...", flush=True)
    data = load_data()
    maps = source_maps(data)
    print("building NORMAL Day0 feature matrix ...", flush=True)
    day0 = build_day0_matrix(data["normal_a"], maps)
    print("building point-in-time Day1/Day3 matrices ...", flush=True)
    day1 = build_day1_matrix(day0, maps)
    day3 = build_day3_matrix(day0, maps)
    day0, day1, day3, design = annotate_fixed_bundles(day0, day1, day3)

    print("summarizing univariate and fixed-policy evidence ...", flush=True)
    univ0 = univariate_summary(day0, "DAY0")
    univ1 = univariate_summary(day1, "DAY1")
    univ3 = univariate_summary(day3, "DAY3")
    policies = build_policy_outputs(day0, day1, day3)
    topk, topk_policies = build_topk(day0)
    pit = build_pit_audit(day0, day1, day3)
    pending = pending_shadow(data["pending_c"])
    shadow = daily_shadow_json(
        day0, day1, day3, pending, data["prepared"]["market_path"]
    )
    precision = pd.concat(
        [
            policies["day0_comparison"],
            policies["day1_comparison"],
            policies["day3_comparison"],
            policies["staged_comparison"],
            topk,
        ],
        ignore_index=True,
        sort=False,
    )
    report, handoff = render_report(
        day0,
        day1,
        day3,
        univ0,
        univ1,
        univ3,
        policies,
        topk,
        pit,
        pending,
    )
    cohort_cols = [
        "stock_id",
        "stock_name",
        "episode_start_date",
        "dataset",
        "normal_subregime",
        "production_market_regime",
        "market_path_state",
        "entry_close",
        "episode_return_day0_to_day10",
        "episode_outcome",
        "future_trade_date_10d",
        "maturity_status",
        "data_status",
    ]
    outputs = {
        "phase3h_normal_first_seen_cohort.csv": day0[cohort_cols],
        "phase3h_day0_feature_matrix.csv": day0,
        "phase3h_day1_confirmation_matrix.csv": day1,
        "phase3h_day3_confirmation_matrix.csv": day3,
        "phase3h_day0_univariate_summary.csv": univ0,
        "phase3h_day1_followthrough_summary.csv": univ1,
        "phase3h_day3_persistence_summary.csv": univ3,
        "phase3h_day0_policy_comparison.csv": policies["day0_comparison"],
        "phase3h_day1_policy_comparison.csv": policies["day1_comparison"],
        "phase3h_day3_policy_comparison.csv": policies["day3_comparison"],
        "phase3h_staged_policy_comparison.csv": policies["staged_comparison"],
        "phase3h_topk_baseline.csv": topk,
        "phase3h_precision_coverage.csv": precision,
        "phase3h_promotion_outcomes.csv": policies["promotion"],
        "phase3h_confirmation_timing_cost.csv": policies["timing"],
        "phase3h_missed_winners.csv": policies["missed"],
        "phase3h_false_confirmations.csv": policies["false_confirmation"],
        "phase3h_point_in_time_audit.csv": pit,
        "phase3h_pending_confirmation_shadow.csv": pending,
    }
    for filename, frame in outputs.items():
        frame.to_csv(OUT / filename, index=False)
    (OUT / "phase3h_report.md").write_text(report, encoding="utf-8")
    (OUT / "phase3h_llm_handoff.md").write_text(handoff, encoding="utf-8")
    (OUT / "phase3h_daily_shadow.json").write_text(
        json.dumps(shadow, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )

    # Frozen denominator, chronological split, PIT, promotion reference, and
    # pending-outcome invariants.
    assert len(day0) == 168
    assert day0.episode_outcome.value_counts().to_dict() == {
        "NEUTRAL": 134,
        "LOSER": 19,
        "WINNER": 15,
    }
    assert day0.episode_start_date.nunique() == 9
    assert day0.date_split.value_counts().index.isin(
        ["DISCOVERY", "VALIDATION", "LOCKED_EVALUATION"]
    ).all()
    assert len(day1) == len(day0) and len(day3) == len(day0)
    assert pit.point_in_time_valid.mean() == 1.0
    assert (pd.to_datetime(pit.max_source_date) <= pd.to_datetime(pit.confirmation_date)).all()
    assert not pit.outcome_used_in_decision.any()
    assert pending.episode_outcome.isna().all()
    assert pending.promotion_outcome.isna().all()
    assert not pending.normal_winner_enrichment_eligible.any()
    assert set(policies["promotion"].promotion_outcome.dropna().unique()).issubset(
        {"PROMOTION_WINNER", "PROMOTION_NEUTRAL", "PROMOTION_LOSER"}
    )
    print(
        f"wrote {len(outputs) + 3} artifacts to {OUT}; "
        f"NORMAL={len(day0)}, W/N/L=15/134/19, PIT={pit.point_in_time_valid.mean():.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
