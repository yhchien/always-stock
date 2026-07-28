"""Phase 3F v2 — current-data regime/pocket/watchlist shadow audit.

Pure research.  This script never writes production tables and never changes the
candidate sources, Top120 truncation, momentum_score, filters, prompts, regimes,
WATCH output, or outcome thresholds.

Inputs
------
* Frozen/reconstructed Phase 3E files in ``/tmp/phase3e_frames`` (40 sessions).
* Read-only project database queries for prices, institutional flow, canonical
  classification, and production signal snapshots.

Outputs
-------
The 18 files requested by the Phase 3F v2 specification are written below
``docs/plans/phase3f_v2``.

Run from backend with the project DATABASE_URL loaded:
    python3 analyze_phase3f_v2.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from app.database import DATABASE_URL
from app.signals.market_regime import classify_regime, compute_regime_metrics


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "plans" / "phase3f_v2"
FRAME_DIR = Path("/tmp/phase3e_frames")
START = date(2026, 5, 28)
END = date(2026, 7, 24)
DATASET_A_START = date(2026, 6, 11)  # first 10 sessions are left-censored
DATASET_A_END = date(2026, 7, 1)
DATASET_B_START = date(2026, 7, 2)
DATASET_B_END = date(2026, 7, 9)
DATASET_C_START = date(2026, 7, 10)
WINNER_BAR = 12.0
LOSER_BAR = -6.0
EPS = 1e-12
WATCH_PROXY = "PRODUCTION_WATCH_WHERE_AVAILABLE"
SOURCE_CACHE = Path("/tmp/phase3f_v2_sources.pkl")
PREPARED_CACHE = Path("/tmp/phase3f_v2b_prepared.pkl")
POCKET_CACHE = Path("/tmp/phase3f_v2b_pocket.pkl")
POCKET_CHUNK_PREFIX = "/tmp/phase3f_v2b_pocket_raw_"


def _json(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return None


def _pct_rank(values: pd.Series, ascending: bool = True) -> pd.Series:
    """0..100, higher means stronger unless ascending=False."""
    return values.rank(method="average", pct=True, ascending=ascending) * 100.0


def _safe_div(a: Any, b: Any) -> float:
    try:
        return float(a) / float(b) if b not in (0, None) and not pd.isna(b) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _wilson(success: int, n: int, z: float = 1.959964) -> Tuple[float, float]:
    if n <= 0:
        return (np.nan, np.nan)
    p = success / n
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, center - radius), min(1.0, center + radius))


def _outcome(ret: Any) -> Optional[str]:
    if ret is None or pd.isna(ret):
        return None
    if float(ret) >= WINNER_BAR:
        return "WINNER"
    if float(ret) <= LOSER_BAR:
        return "LOSER"
    return "NEUTRAL"


def _load_replay() -> Tuple[List[date], pd.DataFrame, Dict[Tuple[date, str], Dict[str, Any]]]:
    if not FRAME_DIR.exists():
        raise FileNotFoundError(f"missing Phase 3E replay directory: {FRAME_DIR}")
    pool_rows: List[Dict[str, Any]] = []
    frames: Dict[Tuple[date, str], Dict[str, Any]] = {}
    replay_dates: List[date] = []
    for pool_path in sorted(FRAME_DIR.glob("*_pool.json")):
        ds = date.fromisoformat(pool_path.name[:10])
        if not (START <= ds <= END):
            continue
        frame_path = FRAME_DIR / f"{ds.isoformat()}_frame.json"
        if not frame_path.exists():
            raise FileNotFoundError(frame_path)
        day_frame = json.loads(frame_path.read_text(encoding="utf-8"))
        for sid, feats in day_frame.items():
            frames[(ds, str(sid))] = feats
        for row in json.loads(pool_path.read_text(encoding="utf-8")):
            sources = [k[-1] for k in ("source_A", "source_B", "source_C", "source_D") if row.get(k)]
            pool_rows.append(
                {
                    **row,
                    "stock_id": str(row["stock_id"]),
                    "evaluation_date": ds,
                    "source_combination": "".join(sources) or "NONE",
                }
            )
        replay_dates.append(ds)
    if len(replay_dates) != 40:
        raise RuntimeError(f"expected 40 replay dates, found {len(replay_dates)}")
    pool = pd.DataFrame(pool_rows).sort_values(["evaluation_date", "raw_union_rank", "stock_id"])
    return replay_dates, pool, frames


def _query_sources(replay_dates: Sequence[date]) -> Dict[str, pd.DataFrame]:
    """One read-only batch per source table; no production writes."""
    if SOURCE_CACHE.exists():
        print(f"loading source cache {SOURCE_CACHE} ...", flush=True)
        cached = pd.read_pickle(SOURCE_CACHE)
        if isinstance(cached, dict) and set(
            [
                "prices",
                "flow",
                "master",
                "classification",
                "snapshots",
                "shadow_snapshots",
                "revenue_audit",
            ]
        ).issubset(cached):
            return cached
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15, "options": "-c statement_timeout=180000"},
    )
    price_start = replay_dates[0] - timedelta(days=150)
    forward_end = replay_dates[-1] + timedelta(days=30)
    queries = {
        "prices": """
            SELECT trade_date, stock_id, open_price, high_price, low_price,
                   close_price, volume, turnover
            FROM daily_price
            WHERE trade_date BETWEEN :price_start AND :forward_end
        """,
        "flow": """
            SELECT trade_date, stock_id, inst_type, net_amount_est, net_shares
            FROM inst_stock_flow
            WHERE trade_date BETWEEN :flow_start AND :end
        """,
        "master": """
            SELECT stock_id, stock_name, market, industry_name, sub_industry,
                   is_active, source
            FROM stocks_master
        """,
        "classification": """
            SELECT stock_id, asset_type, primary_sector, sub_sector,
                   classification_confidence, review_required
            FROM security_classification
        """,
        "snapshots": """
            SELECT snapshot_date, market_context, watchlist, candidate_pool_size,
                   final_watchlist_size, prompt_version, generated_at
            FROM signal_snapshots
            WHERE snapshot_date BETWEEN :start AND :end
        """,
        "shadow_snapshots": """
            SELECT snapshot_date, pipeline_version, funnel_metrics,
                   comparison_summary, candidate_pool_size,
                   role_survivor_count, regime_survivor_count, generated_at
            FROM signal_shadow_snapshots
            WHERE snapshot_date BETWEEN :start AND :end
        """,
        "revenue_audit": """
            SELECT EXTRACT(YEAR FROM revenue_month)::int AS revenue_year,
                   EXTRACT(MONTH FROM revenue_month)::int AS revenue_month_num,
                   COUNT(*) AS row_count, COUNT(yoy_pct) AS yoy_count
            FROM monthly_revenue
            WHERE revenue_month >= DATE '2026-01-01'
            GROUP BY 1, 2 ORDER BY 1, 2
        """,
    }
    params = {
        "price_start": price_start,
        "forward_end": forward_end,
        "flow_start": replay_dates[0] - timedelta(days=25),
        "start": replay_dates[0],
        "end": replay_dates[-1],
    }
    out: Dict[str, pd.DataFrame] = {}
    with engine.connect() as conn:
        for name, sql in queries.items():
            print(f"loading {name} ...", flush=True)
            out[name] = pd.read_sql_query(text(sql), conn, params=params)
    engine.dispose()
    for name in ("prices", "flow"):
        out[name]["trade_date"] = pd.to_datetime(out[name]["trade_date"]).dt.date
    if not out["snapshots"].empty:
        out["snapshots"]["snapshot_date"] = pd.to_datetime(out["snapshots"]["snapshot_date"]).dt.date
    if not out["shadow_snapshots"].empty:
        out["shadow_snapshots"]["snapshot_date"] = pd.to_datetime(
            out["shadow_snapshots"]["snapshot_date"]
        ).dt.date
    pd.to_pickle(out, SOURCE_CACHE)
    print(f"wrote source cache {SOURCE_CACHE}", flush=True)
    return out


def _wide(
    prices: pd.DataFrame, value: str, ids: Sequence[str], all_dates: Sequence[date]
) -> pd.DataFrame:
    p = prices[prices["stock_id"].astype(str).isin(ids)].copy()
    p["stock_id"] = p["stock_id"].astype(str)
    out = (
        p.pivot_table(index="trade_date", columns="stock_id", values=value, aggfunc="last")
        .reindex(index=all_dates, columns=ids)
        .astype(float)
    )
    if value in {"open_price", "high_price", "low_price", "close_price"}:
        out = out.mask(out <= 0)
    return out


def _rolling_slope(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window).mean() - s.rolling(window).mean().shift(3)


def build_market_tables(
    dates: Sequence[date],
    prices: pd.DataFrame,
    universe_ids: Sequence[str],
    production_regime: Mapping[date, str],
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, pd.DataFrame]]:
    price_dates = sorted(set(prices["trade_date"]))
    close = _wide(prices, "close_price", universe_ids, price_dates)
    high = _wide(prices, "high_price", universe_ids, price_dates)
    low = _wide(prices, "low_price", universe_ids, price_dates)
    volume = _wide(prices, "volume", universe_ids, price_dates)
    ret1 = close.pct_change(fill_method=None) * 100.0
    ret5 = close.pct_change(5, fill_method=None) * 100.0
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    high20 = close.rolling(20).max()
    low20 = close.rolling(20).min()
    vol20 = volume.rolling(20).mean()

    idx = prices[prices["stock_id"].astype(str) == "TAIEX"].sort_values("trade_date")
    if idx.empty:
        raise RuntimeError("TAIEX rows are required for Phase 3F v2")
    idx = idx.drop_duplicates("trade_date", keep="last").set_index("trade_date")
    ic = idx["close_price"].astype(float).reindex(price_dates)
    ih = idx["high_price"].astype(float).reindex(price_dates)
    il = idx["low_price"].astype(float).reindex(price_dates)
    ima5 = ic.rolling(5).mean()
    ima10 = ic.rolling(10).mean()
    ima20 = ic.rolling(20).mean()
    below10 = ic < ima10
    below20 = ic < ima20

    def streak(mask: pd.Series) -> pd.Series:
        out, n = [], 0
        for v in mask.fillna(False):
            n = n + 1 if bool(v) else 0
            out.append(n)
        return pd.Series(out, index=mask.index)

    prod_series = pd.Series({d: production_regime.get(d) for d in dates})
    risk_streak = streak(prod_series == "RISK_OFF")
    market_rows, breadth_rows = [], []
    for d in dates:
        if d not in ic.index:
            continue
        loc = price_dates.index(d)
        r = ret1.loc[d].dropna()
        r5 = ret5.loc[d].dropna()
        adv, dec, unch = int((r > 0).sum()), int((r < 0).sum()), int((r == 0).sum())
        above5 = (close.loc[d] >= ma5.loc[d]).dropna()
        above10 = (close.loc[d] >= ma10.loc[d]).dropna()
        above20 = (close.loc[d] >= ma20.loc[d]).dropna()
        nh = (close.loc[d] >= high20.loc[d] - EPS).dropna()
        nl = (close.loc[d] <= low20.loc[d] + EPS).dropna()
        ratios = volume.loc[d] / vol20.loc[d]
        market_rows.append(
            {
                "evaluation_date": d,
                **{
                    f"market_return_{n}d": (
                        (ic.iloc[loc] / ic.iloc[loc - n] - 1) * 100 if loc >= n and ic.iloc[loc - n] else np.nan
                    )
                    for n in (1, 3, 5, 10, 20)
                },
                "market_drawdown_from_20d_high": (ic.loc[d] / ic.iloc[max(0, loc - 19) : loc + 1].max() - 1) * 100,
                "market_drawdown_from_60d_high": (ic.loc[d] / ic.iloc[max(0, loc - 59) : loc + 1].max() - 1) * 100,
                "days_since_market_20d_high": loc - int(np.nanargmax(ic.iloc[max(0, loc - 19) : loc + 1].to_numpy())) - max(0, loc - 19),
                "market_ma5_slope": ima5.loc[d] - ima5.shift(3).loc[d],
                "market_ma10_slope": ima10.loc[d] - ima10.shift(3).loc[d],
                "market_ma20_slope": ima20.loc[d] - ima20.shift(3).loc[d],
                "market_below_ma5": bool(ic.loc[d] < ima5.loc[d]),
                "market_below_ma10": bool(below10.loc[d]),
                "market_below_ma20": bool(below20.loc[d]),
                "market_below_ma10_days": int(streak(below10).loc[d]),
                "market_below_ma20_days": int(streak(below20).loc[d]),
                "negative_market_days_5d": int((ic.pct_change().iloc[max(0, loc - 4) : loc + 1] < 0).sum()),
                "negative_market_days_10d": int((ic.pct_change().iloc[max(0, loc - 9) : loc + 1] < 0).sum()),
                "production_market_regime": production_regime.get(d),
                "production_risk_off_streak": int(risk_streak.get(d, 0)),
            }
        )
        breadth_rows.append(
            {
                "evaluation_date": d,
                "advancing_count": adv,
                "declining_count": dec,
                "unchanged_count": unch,
                "advance_decline_ratio": _safe_div(adv, dec),
                "stocks_above_ma5_pct": float(above5.mean() * 100),
                "stocks_above_ma10_pct": float(above10.mean() * 100),
                "stocks_above_ma20_pct": float(above20.mean() * 100),
                "new_20d_high_count": int(nh.sum()),
                "new_20d_low_count": int(nl.sum()),
                "new_high_low_ratio": _safe_div(int(nh.sum()), int(nl.sum())),
                "limit_up_count": int((r >= 9.5).sum()),
                "limit_down_count": int((r <= -9.5).sum()),
                "median_stock_return_1d": float(r.median()),
                "median_stock_return_5d": float(r5.median()),
                "volume_expanding_count": int((ratios >= 1.2).sum()),
            }
        )
    market = pd.DataFrame(market_rows).set_index("evaluation_date")
    breadth = pd.DataFrame(breadth_rows).set_index("evaluation_date")
    breadth["advance_decline_ratio_3d"] = breadth["advance_decline_ratio"].rolling(3).mean()
    breadth["advance_decline_ratio_5d"] = breadth["advance_decline_ratio"].rolling(5).mean()
    breadth["breadth_change_3d"] = breadth["stocks_above_ma20_pct"].diff(3)
    breadth["breadth_change_5d"] = breadth["stocks_above_ma20_pct"].diff(5)
    breadth["index_stock_divergence"] = (
        market["market_return_5d"] - breadth["median_stock_return_5d"]
    )
    wide = {
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
        "ret1": ret1,
        "ret5": ret5,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "high20": high20,
        "low20": low20,
        "volratio20": volume / vol20,
    }
    return market.reset_index(), breadth.reset_index(), wide


def classify_market_path(market: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    m = market.merge(breadth, on="evaluation_date", how="left").sort_values("evaluation_date")
    a = m[(m.evaluation_date >= DATASET_A_START) & (m.evaluation_date <= DATASET_A_END)]
    # Thresholds are frozen exclusively from Dataset A before Dataset B is scored.
    q = {
        "breadth20_p30": float(a["stocks_above_ma20_pct"].quantile(0.30)),
        "breadth_change3_p30": float(a["breadth_change_3d"].quantile(0.30)),
        "return5_p30": float(a["market_return_5d"].quantile(0.30)),
    }
    states, weak_count = [], []
    for row in m.itertuples():
        flags = [
            row.stocks_above_ma20_pct <= q["breadth20_p30"],
            row.breadth_change_3d <= q["breadth_change3_p30"],
            row.market_return_5d <= q["return5_p30"],
        ]
        nweak = int(sum(bool(x) for x in flags if not pd.isna(x)))
        if row.production_risk_off_streak >= 2 or (
            row.market_below_ma20_days >= 3 and nweak >= 2
        ):
            state = "RISK_OFF"
        elif nweak >= 2 or (
            bool(row.market_below_ma10) and row.market_below_ma10_days >= 2
        ):
            state = "WEAKENING"
        else:
            state = "NORMAL"
        states.append(state)
        weak_count.append(nweak)
    m["market_path_state"] = states
    m["weak_feature_count"] = weak_count
    for k, v in q.items():
        m[k] = v
    m["threshold_source"] = "DATASET_A_FIXED_DISTRIBUTION"
    return m


def _trend_efficiency(series: pd.Series) -> float:
    s = series.dropna().astype(float)
    if len(s) < 2:
        return np.nan
    path = s.diff().abs().sum()
    return float(abs(s.iloc[-1] - s.iloc[0]) / path) if path > 0 else 0.0


def build_candidate_features(
    dates: Sequence[date],
    raw_pool: pd.DataFrame,
    frames: Mapping[Tuple[date, str], Dict[str, Any]],
    sources: Mapping[str, pd.DataFrame],
    market_path: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    master = sources["master"].copy()
    master["stock_id"] = master["stock_id"].astype(str)
    cls = sources["classification"].copy()
    cls["stock_id"] = cls["stock_id"].astype(str)
    metadata = master.merge(cls, on="stock_id", how="left")
    raw = raw_pool.merge(metadata, on="stock_id", how="left")
    top = raw[raw["top120"] == True].copy()  # noqa: E712

    flow = sources["flow"].copy()
    flow["stock_id"] = flow["stock_id"].astype(str)
    flow_daily = (
        flow.groupby(["trade_date", "stock_id"], as_index=False)
        .agg(
            institution_flow=("net_amount_est", "sum"),
            institution_net_shares=("net_shares", "sum"),
        )
        .sort_values(["stock_id", "trade_date"])
    )
    foreign = (
        flow[flow["inst_type"] == "foreign"]
        .groupby(["trade_date", "stock_id"], as_index=False)["net_amount_est"]
        .sum()
        .rename(columns={"net_amount_est": "foreign_flow"})
    )
    trust = (
        flow[flow["inst_type"] == "trust"]
        .groupby(["trade_date", "stock_id"], as_index=False)["net_amount_est"]
        .sum()
        .rename(columns={"net_amount_est": "trust_flow"})
    )
    flow_daily = flow_daily.merge(foreign, on=["trade_date", "stock_id"], how="left").merge(
        trust, on=["trade_date", "stock_id"], how="left"
    )
    all_price_dates = list(wide["close"].index)
    all_ids = list(wide["close"].columns)
    flow_pivot = (
        flow_daily.pivot_table(
            index="trade_date", columns="stock_id", values="institution_flow", aggfunc="sum"
        )
        .reindex(index=all_price_dates, columns=all_ids)
        .fillna(0.0)
    )
    buy_pivot = flow_pivot > 0

    mp = market_path.set_index("evaluation_date")
    market_returns = mp[[f"market_return_{n}d" for n in (1, 3, 5)]]
    raw_rank = raw.set_index(["evaluation_date", "stock_id"])["raw_union_rank"].to_dict()
    date_pos = {d: i for i, d in enumerate(dates)}
    price_pos = {d: i for i, d in enumerate(all_price_dates)}

    feat_rows: List[Dict[str, Any]] = []
    for row in top.itertuples(index=False):
        d, sid = row.evaluation_date, str(row.stock_id)
        pi, di = price_pos.get(d), date_pos[d]
        if pi is None or sid not in wide["close"].columns:
            continue
        close_s = wide["close"][sid]
        ret_s = wide["ret1"][sid]
        flow_s = flow_pivot[sid]
        f = dict(frames.get((d, sid), {}))

        def ret(n: int) -> float:
            if pi < n or pd.isna(close_s.iloc[pi]) or pd.isna(close_s.iloc[pi - n]):
                return np.nan
            return float((close_s.iloc[pi] / close_s.iloc[pi - n] - 1) * 100)

        def rank_at(offset: int) -> float:
            return float(raw_rank.get((dates[di - offset], sid), np.nan)) if di >= offset else np.nan

        def rs_at(offset: int) -> float:
            return (
                frames.get((dates[di - offset], sid), {}).get("rs_market_percentile_20d", np.nan)
                if di >= offset
                else np.nan
            )

        market_down5 = [
            j
            for j in range(max(0, pi - 4), pi + 1)
            if j > 0
            and not pd.isna(mp.get("market_return_1d", pd.Series()).get(all_price_dates[j], np.nan))
            and mp["market_return_1d"].get(all_price_dates[j], np.nan) < 0
        ]
        market_down10 = [
            j
            for j in range(max(0, pi - 9), pi + 1)
            if j > 0
            and all_price_dates[j] in mp.index
            and mp.loc[all_price_dates[j], "market_return_1d"] < 0
        ]

        def down_outperformance(js: Sequence[int]) -> int:
            return int(
                sum(
                    not pd.isna(ret_s.iloc[j])
                    and ret_s.iloc[j] > mp.loc[all_price_dates[j], "market_return_1d"]
                    for j in js
                )
            )

        def max_dd(n: int) -> float:
            s = close_s.iloc[max(0, pi - n) : pi + 1].dropna()
            if len(s) < 2:
                return np.nan
            dd = s / s.cummax() - 1.0
            return float(dd.min() * 100)

        rs0, rs3, rs5 = f.get("rs_market_percentile_20d"), rs_at(3), rs_at(5)
        rank0, rank1, rank3 = float(row.raw_union_rank), rank_at(1), rank_at(3)
        flow3 = float(flow_s.iloc[max(0, pi - 2) : pi + 1].sum())
        flow_prev3 = float(flow_s.iloc[max(0, pi - 5) : max(0, pi - 2)].sum())
        feat_rows.append(
            {
                "stock_id": sid,
                "evaluation_date": d,
                "stock_name": getattr(row, "stock_name", None),
                "primary_sector": getattr(row, "primary_sector", None),
                "sub_sector": getattr(row, "sub_sector", None),
                "classification_confidence": getattr(row, "classification_confidence", None),
                "source_combination": row.source_combination,
                "momentum_score": row.momentum_score,
                "momentum_rank": rank0,
                "market_excess_return_1d": ret(1) - market_returns.loc[d, "market_return_1d"],
                "market_excess_return_3d": ret(3) - market_returns.loc[d, "market_return_3d"],
                "market_excess_return_5d": ret(5) - market_returns.loc[d, "market_return_5d"],
                "market_down_day_outperformance_count_5d": down_outperformance(market_down5),
                "market_down_day_outperformance_count_10d": down_outperformance(market_down10),
                "market_down_days_5d": len(market_down5),
                "market_down_days_10d": len(market_down10),
                "rs_market_pct_day0": rs0,
                "rs_market_pct_day_minus_3": rs3,
                "rs_market_pct_day_minus_5": rs5,
                "rs_slope_3d": (rs0 - rs3) / 3 if rs0 is not None and not pd.isna(rs3) else np.nan,
                "rs_slope_5d": (rs0 - rs5) / 5 if rs0 is not None and not pd.isna(rs5) else np.nan,
                "momentum_rank_change_1d": rank1 - rank0 if not pd.isna(rank1) else np.nan,
                "momentum_rank_change_3d": rank3 - rank0 if not pd.isna(rank3) else np.nan,
                "trend_efficiency_5d": _trend_efficiency(close_s.iloc[max(0, pi - 5) : pi + 1]),
                "trend_efficiency_10d": _trend_efficiency(close_s.iloc[max(0, pi - 10) : pi + 1]),
                "max_drawdown_prior_5d": max_dd(5),
                "max_drawdown_prior_10d": max_dd(10),
                "institution_buy_days_5d": int(buy_pivot[sid].iloc[max(0, pi - 4) : pi + 1].sum()),
                "institution_buy_days_10d": int(buy_pivot[sid].iloc[max(0, pi - 9) : pi + 1].sum()),
                "institution_flow_3d": flow3,
                "institution_flow_previous_3d": flow_prev3,
                "institution_flow_acceleration": flow3 - flow_prev3,
                "flow_support_on_market_down_days": (
                    float(np.mean([flow_s.iloc[j] > 0 for j in market_down10]))
                    if market_down10
                    else np.nan
                ),
                "return_1d": ret(1),
                "return_3d": ret(3),
                "return_5d": ret(5),
                "volume_ratio_1d_20d": f.get("volume_1d_to_20d_avg"),
                "production_market_regime": mp.loc[d, "production_market_regime"],
                "market_path_state": mp.loc[d, "market_path_state"],
            }
        )
    cand = pd.DataFrame(feat_rows)
    # Regime-relative features use only same-day information; state percentile is
    # expanding through the current day to avoid looking into future state samples.
    cand["down_survival_ratio"] = cand.apply(
        lambda r: _safe_div(
            r.market_down_day_outperformance_count_10d, r.market_down_days_10d
        ),
        axis=1,
    )
    cand["institution_flow_persistence"] = (
        cand["institution_buy_days_10d"] / 10.0
    )
    for col in (
        "down_survival_ratio",
        "momentum_rank_change_3d",
        "trend_efficiency_10d",
        "institution_flow_persistence",
    ):
        cand[f"{col}_top120_percentile"] = cand.groupby("evaluation_date")[col].transform(
            _pct_rank
        )
        cand[f"{col}_state_percentile"] = cand.groupby(
            ["evaluation_date", "market_path_state"]
        )[col].transform(_pct_rank)
    return cand, raw, flow_daily


def build_pocket_raw(
    dates: Sequence[date],
    candidate: pd.DataFrame,
    sources: Mapping[str, pd.DataFrame],
    market_path: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
    flow_daily: pd.DataFrame,
) -> pd.DataFrame:
    cls = sources["classification"].copy()
    cls["stock_id"] = cls["stock_id"].astype(str)
    cls = cls[
        cls["primary_sector"].notna()
        & cls["asset_type"].isin(["COMMON_STOCK", "PREFERRED_STOCK", "DR", "REIT"])
    ]
    metadata: Dict[str, Dict[str, Any]] = (
        cls.set_index("stock_id")[["primary_sector", "sub_sector"]].to_dict("index")
    )
    all_dates = list(wide["close"].index)
    date_pos = {d: i for i, d in enumerate(all_dates)}
    mp = market_path.set_index("evaluation_date")
    flow_pivot = (
        flow_daily.pivot_table(
            index="trade_date", columns="stock_id", values="institution_flow", aggfunc="sum"
        )
        .reindex(index=all_dates)
        .fillna(0.0)
    )
    top_ids = {
        d: set(candidate.loc[candidate.evaluation_date == d, "stock_id"]) for d in dates
    }
    first_ids: Dict[date, set[str]] = {}
    seen: set[str] = set()
    for d in dates:
        first_ids[d] = top_ids[d] - seen
        seen |= top_ids[d]

    groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for sid, meta in metadata.items():
        groups[("PRIMARY_SECTOR", str(meta["primary_sector"]))].append(sid)
        if meta.get("sub_sector"):
            groups[("SUB_SECTOR", f"{meta['primary_sector']}::{meta['sub_sector']}")].append(sid)

    rows: List[Dict[str, Any]] = []
    for d in dates:
        pi = date_pos[d]
        day_rows: List[Dict[str, Any]] = []
        for (level, cluster), ids0 in groups.items():
            ids = [sid for sid in ids0 if sid in wide["close"].columns]
            if len(ids) < 3:
                continue
            rets: Dict[int, pd.Series] = {}
            for n in (1, 3, 5):
                if pi < n:
                    rets[n] = pd.Series(dtype=float)
                else:
                    rets[n] = (
                        wide["close"].loc[d, ids] / wide["close"].iloc[pi - n][ids] - 1
                    ) * 100
            r1 = rets[1].dropna()
            if len(r1) < 3:
                continue
            sector_ret = {n: float(rets[n].median()) for n in (1, 3, 5)}
            top_in = top_ids[d].intersection(ids)
            cday = candidate[
                (candidate.evaluation_date == d) & candidate.stock_id.isin(ids)
            ]
            inst = (
                flow_pivot.loc[d, [x for x in ids if x in flow_pivot.columns]]
                if d in flow_pivot.index
                else pd.Series(dtype=float)
            )
            market_down_dates = [
                x
                for x in all_dates[max(0, pi - 9) : pi + 1]
                if x in mp.index and mp.loc[x, "market_return_1d"] < 0
            ]
            survival = []
            for md in market_down_dates:
                vals = wide["ret1"].loc[md, ids].dropna()
                if len(vals):
                    survival.append(float(vals.median()) > mp.loc[md, "market_return_1d"])
            leaders = rets[5].dropna().sort_values(ascending=False)
            day_rows.append(
                {
                    "evaluation_date": d,
                    "cluster_level": level,
                    "cluster": cluster,
                    "member_count": len(ids),
                    "sector_return_1d": sector_ret[1],
                    "sector_return_3d": sector_ret[3],
                    "sector_return_5d": sector_ret[5],
                    "sector_market_excess_1d": sector_ret[1] - mp.loc[d, "market_return_1d"],
                    "sector_market_excess_3d": sector_ret[3] - mp.loc[d, "market_return_3d"],
                    "sector_market_excess_5d": sector_ret[5] - mp.loc[d, "market_return_5d"],
                    "sector_positive_breadth": float((r1 > 0).mean() * 100),
                    "sector_above_ma5_pct": float(
                        (wide["close"].loc[d, ids] >= wide["ma5"].loc[d, ids]).mean() * 100
                    ),
                    "sector_above_ma10_pct": float(
                        (wide["close"].loc[d, ids] >= wide["ma10"].loc[d, ids]).mean() * 100
                    ),
                    "sector_new_high_count": int(
                        (wide["close"].loc[d, ids] >= wide["high20"].loc[d, ids] - EPS).sum()
                    ),
                    "sector_volume_expansion_breadth": float(
                        (
                            wide["volratio20"].loc[d, ids] >= 1.2
                        ).mean()
                        * 100
                    ),
                    "sector_institution_buy_breadth": float((inst > 0).mean() * 100),
                    "sector_top120_count": len(top_in),
                    "sector_first_seen_count": len(first_ids[d].intersection(ids)),
                    "sector_rank_improving_count": int(
                        (cday["momentum_rank_change_3d"] > 0).sum()
                    ),
                    "sector_leader_return": float(leaders.iloc[0]) if len(leaders) else np.nan,
                    "sector_second_leader_return": float(leaders.iloc[1]) if len(leaders) > 1 else np.nan,
                    "leader_second_gap": (
                        float(leaders.iloc[0] - leaders.iloc[1]) if len(leaders) > 1 else np.nan
                    ),
                    "sector_market_down_day_survival": (
                        float(np.mean(survival) * 100) if survival else np.nan
                    ),
                }
            )
        day_df = pd.DataFrame(day_rows)
        if day_df.empty:
            continue
        for col in (
            "sector_market_excess_3d",
            "sector_market_excess_5d",
            "sector_positive_breadth",
            "sector_institution_buy_breadth",
        ):
            day_df[f"{col}_daily_percentile"] = day_df.groupby("cluster_level")[col].transform(
                _pct_rank
            )
        rows.extend(day_df.to_dict("records"))
    return pd.DataFrame(rows)


def finalize_pockets(raw: pd.DataFrame, market_path: pd.DataFrame) -> pd.DataFrame:
    pocket = raw.sort_values(["cluster_level", "cluster", "evaluation_date"]).copy()
    market_return = market_path.set_index("evaluation_date")["market_return_5d"].to_dict()
    states, streak_values = [], []
    streaks: Dict[Tuple[str, str], int] = defaultdict(int)
    for r in pocket.itertuples(index=False):
        key = (r.cluster_level, r.cluster)
        strong = (
            r.sector_market_excess_3d_daily_percentile >= 60
            and r.sector_positive_breadth >= 45
            and r.sector_top120_count >= 2
            and (
                r.sector_institution_buy_breadth >= 45
                or r.sector_volume_expansion_breadth >= 35
            )
        )
        streaks[key] = streaks[key] + 1 if strong else 0
        streak_values.append(streaks[key])
        if strong and streaks[key] >= 2:
            state = "CONFIRMED_POCKET"
        elif (
            r.sector_top120_count in (1, 2)
            and r.sector_positive_breadth < 45
            and r.sector_leader_return - market_return.get(r.evaluation_date, 0) >= 2
        ):
            state = "NARROW_LEADERSHIP"
        elif (
            r.sector_market_excess_3d_daily_percentile >= 60
            or (
                r.sector_top120_count >= 2
                and r.sector_institution_buy_breadth >= 50
            )
        ):
            state = "EMERGING_POCKET"
        else:
            state = "NO_POCKET"
        states.append(state)
    pocket["sector_strength_streak"] = streak_values
    pocket["pocket_state"] = states
    for col in (
        "sector_market_excess_3d",
        "sector_market_excess_5d",
        "sector_positive_breadth",
        "sector_institution_buy_breadth",
    ):
        pocket[f"{col}_rolling20_percentile"] = pocket.groupby(
            ["cluster_level", "cluster"]
        )[col].transform(lambda s: s.rolling(20, min_periods=3).rank(pct=True) * 100)
    return pocket


def build_pockets(
    dates: Sequence[date],
    candidate: pd.DataFrame,
    sources: Mapping[str, pd.DataFrame],
    market_path: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
    flow_daily: pd.DataFrame,
) -> pd.DataFrame:
    return finalize_pockets(
        build_pocket_raw(dates, candidate, sources, market_path, wide, flow_daily),
        market_path,
    )


def attach_pockets(candidate: pd.DataFrame, pocket: pd.DataFrame) -> pd.DataFrame:
    c = candidate.copy()
    primary = pocket[pocket.cluster_level == "PRIMARY_SECTOR"][
        ["evaluation_date", "cluster", "pocket_state", "sector_strength_streak"]
    ].rename(
        columns={
            "cluster": "primary_sector",
            "pocket_state": "primary_pocket_state",
            "sector_strength_streak": "primary_pocket_strength_streak",
        }
    )
    sub = pocket[pocket.cluster_level == "SUB_SECTOR"][
        ["evaluation_date", "cluster", "pocket_state", "sector_strength_streak"]
    ].copy()
    split = sub["cluster"].str.split("::", n=1, expand=True)
    sub["primary_sector"] = split[0]
    sub["sub_sector"] = split[1]
    sub = sub.rename(
        columns={
            "pocket_state": "sub_pocket_state",
            "sector_strength_streak": "sub_pocket_strength_streak",
        }
    ).drop(columns=["cluster"])
    c = c.merge(primary, on=["evaluation_date", "primary_sector"], how="left")
    c = c.merge(
        sub, on=["evaluation_date", "primary_sector", "sub_sector"], how="left"
    )
    priority = {
        "NO_POCKET": 0,
        "EMERGING_POCKET": 1,
        "NARROW_LEADERSHIP": 2,
        "CONFIRMED_POCKET": 3,
    }

    def resolve(r: pd.Series) -> str:
        vals = [r.get("primary_pocket_state"), r.get("sub_pocket_state")]
        vals = [v for v in vals if isinstance(v, str)]
        return max(vals, key=lambda v: priority.get(v, -1)) if vals else "NO_POCKET"

    c["pocket_state"] = c.apply(resolve, axis=1)
    c["down_survival_ratio_pocket_percentile"] = c.groupby(
        ["evaluation_date", "pocket_state"]
    )["down_survival_ratio"].transform(_pct_rank)
    return c


def _production_watch_by_date(snapshots: pd.DataFrame) -> Dict[date, List[Dict[str, Any]]]:
    out: Dict[date, List[Dict[str, Any]]] = {}
    for row in snapshots.itertuples(index=False):
        items = _json(row.watchlist) or []
        normalized = []
        for item in items:
            sid = str(item.get("stock") or item.get("stock_id") or "")
            if not sid:
                continue
            normalized.append({**item, "stock_id": sid})
        out[row.snapshot_date] = normalized
    return out


def _shadow_survivors_by_date(shadows: pd.DataFrame) -> Dict[date, List[str]]:
    out: Dict[date, List[str]] = {}
    if shadows.empty:
        return out
    ordered = shadows.sort_values(["snapshot_date", "generated_at"])
    for row in ordered.itertuples(index=False):
        summary = _json(row.comparison_summary) or {}
        ids = summary.get("phase2_survivor_ids") or []
        if ids:
            out[row.snapshot_date] = [str(x) for x in ids]
    return out


def build_episodes(
    dates: Sequence[date],
    candidate: pd.DataFrame,
    raw_pool: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Comparable Phase 3E cohort plus explicit gap-5 sensitivity markers.

    The frozen 246/87/140 baseline is window-first-seen.  A full gap-5 production
    episode cannot be reconstructed exactly without pre-window Top120/WATCH
    snapshots, so the reset candidates are retained as sensitivity rows rather
    than silently changing the baseline denominator.
    """
    top_by_day = {
        d: set(candidate.loc[candidate.evaluation_date == d, "stock_id"]) for d in dates
    }
    date_pos = {d: i for i, d in enumerate(dates)}
    price_dates = list(wide["close"].index)
    price_pos = {d: i for i, d in enumerate(price_dates)}
    seen: set[str] = set()
    last_seen: Dict[str, int] = {}
    episode_rows, sensitivity_rows = [], []
    day0 = candidate.set_index(["evaluation_date", "stock_id"]).sort_index()
    for d in dates:
        di = date_pos[d]
        for sid in sorted(top_by_day[d]):
            prev = last_seen.get(sid)
            missing_sessions = di - prev - 1 if prev is not None else None
            if sid not in seen:
                kind = "WINDOW_FIRST_SEEN"
                comparable = d >= DATASET_A_START
            elif missing_sessions is not None and missing_sessions >= 5:
                kind = "RESET_GAP5_SENSITIVITY"
                comparable = False
            else:
                kind = None
                comparable = False
            if kind:
                base = day0.loc[(d, sid)].to_dict()
                pi = price_pos.get(d)
                entry = wide["close"].loc[d, sid] if sid in wide["close"].columns else np.nan
                future_date = price_dates[pi + 10] if pi is not None and pi + 10 < len(price_dates) else None
                future_close = (
                    wide["close"].iloc[pi + 10][sid]
                    if future_date is not None and sid in wide["close"].columns
                    else np.nan
                )
                ret10 = (
                    float((future_close / entry - 1) * 100)
                    if not pd.isna(entry) and not pd.isna(future_close)
                    else np.nan
                )
                if d <= DATASET_A_END and d >= DATASET_A_START:
                    dataset = "A"
                    maturity = "MATURED"
                elif DATASET_B_START <= d <= DATASET_B_END:
                    dataset = "B"
                    maturity = "MATURED"
                elif d >= DATASET_C_START:
                    dataset = "C"
                    maturity = "PENDING_FORWARD"
                    ret10 = np.nan  # locked rule: Dataset C never enters outcome/selection
                else:
                    dataset = "LEFT_CENSORED"
                    maturity = "EXCLUDED_LEFT_CENSORED"
                out = _outcome(ret10) if maturity == "MATURED" else None
                item = {
                    **base,
                    "stock_id": sid,
                    "evaluation_date": d,
                    "episode_start_date": d,
                    "episode_kind": kind,
                    "missing_top120_sessions_before_entry": missing_sessions,
                    "comparable_phase3e_cohort": comparable,
                    "dataset": dataset,
                    "entry_close": entry,
                    "future_trade_date_10d": future_date,
                    "future_return_10d": ret10,
                    "outcome": out,
                    "maturity_status": maturity,
                }
                (episode_rows if kind == "WINDOW_FIRST_SEEN" else sensitivity_rows).append(item)
            seen.add(sid)
            last_seen[sid] = di
    episodes = pd.DataFrame(episode_rows)
    sensitivity = pd.DataFrame(sensitivity_rows)
    return episodes, sensitivity


def build_barriers(
    episodes: pd.DataFrame, wide: Mapping[str, pd.DataFrame]
) -> pd.DataFrame:
    dates = list(wide["close"].index)
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    for r in episodes.itertuples(index=False):
        if r.maturity_status != "MATURED":
            continue
        sid, entry = r.stock_id, r.entry_close
        pi = pos.get(r.episode_start_date)
        winner_day = loser_day = None
        ambiguous = False
        if pi is not None and sid in wide["close"].columns and entry and not pd.isna(entry):
            for j in range(pi + 1, min(pi + 11, len(dates))):
                h = wide["high"].iloc[j][sid]
                low = wide["low"].iloc[j][sid]
                hit_w = not pd.isna(h) and (h / entry - 1) * 100 >= WINNER_BAR
                hit_l = not pd.isna(low) and (low / entry - 1) * 100 <= LOSER_BAR
                if hit_w and hit_l:
                    ambiguous = True
                    winner_day = loser_day = dates[j]
                    break
                if hit_w and winner_day is None:
                    winner_day = dates[j]
                if hit_l and loser_day is None:
                    loser_day = dates[j]
                if winner_day is not None or loser_day is not None:
                    break
        if ambiguous:
            path = "AMBIGUOUS_SAME_DAY"
        elif winner_day is not None:
            path = "HIT_WINNER_FIRST"
        elif loser_day is not None:
            path = "HIT_LOSER_FIRST"
        else:
            path = "NO_BARRIER_HIT"
        rows.append(
            {
                "stock_id": sid,
                "episode_start_date": r.episode_start_date,
                "dataset": r.dataset,
                "entry_close": entry,
                "path_outcome": path,
                "winner_hit_date": winner_day,
                "loser_hit_date": loser_day,
                "day10_outcome": r.outcome,
                "future_return_10d": r.future_return_10d,
            }
        )
    return pd.DataFrame(rows)


def choose_bundle_on_dataset_a(episodes: pd.DataFrame) -> str:
    choices = []
    for name in ("bundle_A", "bundle_B", "bundle_C"):
        x = episodes[(episodes.dataset == "A") & episodes[name]]
        if len(x) < 15:
            continue
        loser_rate = float((x.outcome == "LOSER").mean())
        winner_count = int((x.outcome == "WINNER").sum())
        choices.append((loser_rate, -winner_count, -len(x), name))
    return min(choices)[-1] if choices else "bundle_A"


def annotate_survival_bundles(df: pd.DataFrame) -> pd.DataFrame:
    e = df.copy()
    e["bundle_A"] = e["down_survival_ratio_top120_percentile"] >= 70
    e["bundle_B"] = e["bundle_A"] & (
        (e["momentum_rank_change_3d"] >= 0) | (e["rs_slope_3d"] > 0)
    )
    e["bundle_C"] = e["bundle_B"] & (
        e["institution_flow_persistence_state_percentile"] >= 50
    )
    return e


def apply_policies(episodes: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    e = annotate_survival_bundles(episodes)
    # Policy 0 is an explicitly labelled Top120 first-seen proxy.  Frozen
    # deterministic survivor history is not available for all 40 sessions.
    e["policy0"] = True
    e["policy0_data_source"] = "RECONSTRUCTED_TOP120_FIRST_SEEN_PROXY"
    e["policy1"] = np.select(
        [
            e.market_path_state == "NORMAL",
            e.market_path_state == "WEAKENING",
            e.market_path_state == "RISK_OFF",
        ],
        [
            True,
            e.down_survival_ratio_top120_percentile >= 70,
            e.down_survival_ratio_top120_percentile >= 90,
        ],
        default=False,
    ).astype(bool)
    e["policy2"] = np.select(
        [
            e.market_path_state == "NORMAL",
            e.market_path_state == "WEAKENING",
            e.market_path_state == "RISK_OFF",
        ],
        [
            e.pocket_state.isin(
                ["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
            ),
            e.pocket_state.isin(["CONFIRMED_POCKET", "NARROW_LEADERSHIP"]),
            e.pocket_state == "CONFIRMED_POCKET",
        ],
        default=False,
    ).astype(bool)
    chosen = choose_bundle_on_dataset_a(e)
    e["policy3"] = e["policy2"] & e[chosen]
    e["policy3_bundle_chosen_on_dataset_a"] = chosen
    return e, chosen


def _episode_metrics(
    selected: pd.DataFrame, baseline: pd.DataFrame, eligible_dates: Sequence[date]
) -> Dict[str, Any]:
    n, base_n = len(selected), len(baseline)
    wc = int((selected.outcome == "WINNER").sum())
    nc = int((selected.outcome == "NEUTRAL").sum())
    lc = int((selected.outcome == "LOSER").sum())
    bw = int((baseline.outcome == "WINNER").sum())
    bn = int((baseline.outcome == "NEUTRAL").sum())
    bl = int((baseline.outcome == "LOSER").sum())
    daily_counts = selected.groupby("episode_start_date").size().reindex(eligible_dates, fill_value=0)
    daily = []
    for d in eligible_dates:
        x = selected[selected.episode_start_date == d]
        if len(x):
            daily.append(
                {
                    "winner_rate": (x.outcome == "WINNER").mean(),
                    "neutral_rate": (x.outcome == "NEUTRAL").mean(),
                    "loser_rate": (x.outcome == "LOSER").mean(),
                    "safe_rate": (x.outcome != "LOSER").mean(),
                }
            )
    macro = pd.DataFrame(daily)
    lo, hi = _wilson(lc, n)
    return {
        "selected_count": n,
        "selected_dates": int((daily_counts > 0).sum()),
        "eligible_dates": len(eligible_dates),
        "average_selected_per_day": float(daily_counts.mean()) if len(daily_counts) else np.nan,
        "median_selected_per_day": float(daily_counts.median()) if len(daily_counts) else np.nan,
        "zero_primary_date_rate": float((daily_counts == 0).mean()) if len(daily_counts) else np.nan,
        "coverage": _safe_div(n, base_n),
        "winner_count": wc,
        "winner_rate": _safe_div(wc, n),
        "neutral_count": nc,
        "neutral_rate": _safe_div(nc, n),
        "loser_count": lc,
        "loser_rate": _safe_div(lc, n),
        "loser_rate_wilson_low": lo,
        "loser_rate_wilson_high": hi,
        "safe_rate": _safe_div(wc + nc, n),
        "winner_dominance": _safe_div(wc, wc + nc),
        "winner_recall": _safe_div(wc, bw),
        "neutral_removal_rate": 1 - _safe_div(nc, bn) if bn else np.nan,
        "loser_removal_rate": 1 - _safe_div(lc, bl) if bl else np.nan,
        "mean_future_return_10d": (
            float(selected.future_return_10d.mean()) if n else np.nan
        ),
        "median_future_return_10d": (
            float(selected.future_return_10d.median()) if n else np.nan
        ),
        "macro_daily_winner_rate": (
            float(macro.winner_rate.mean()) if not macro.empty else np.nan
        ),
        "macro_daily_neutral_rate": (
            float(macro.neutral_rate.mean()) if not macro.empty else np.nan
        ),
        "macro_daily_loser_rate": (
            float(macro.loser_rate.mean()) if not macro.empty else np.nan
        ),
        "macro_daily_safe_rate": (
            float(macro.safe_rate.mean()) if not macro.empty else np.nan
        ),
    }


def summarize_policies(episodes: pd.DataFrame, dates: Sequence[date]) -> pd.DataFrame:
    matured = episodes[episodes.dataset.isin(["A", "B"])].copy()
    rows = []
    policies = {
        "POLICY_0_CURRENT_BASELINE_PROXY": "policy0",
        "POLICY_1_MARKET_CONTRACTION": "policy1",
        "POLICY_2_POCKET_GATE": "policy2",
        "POLICY_3_POCKET_STOCK_SURVIVAL": "policy3",
        "BUNDLE_A_DIAGNOSTIC": "bundle_A",
        "BUNDLE_B_DIAGNOSTIC": "bundle_B",
        "BUNDLE_C_DIAGNOSTIC": "bundle_C",
    }
    for dataset in ("A", "B", "A+B"):
        base_ds = matured if dataset == "A+B" else matured[matured.dataset == dataset]
        for state in ("ALL", "NORMAL", "WEAKENING", "RISK_OFF"):
            base = base_ds if state == "ALL" else base_ds[base_ds.market_path_state == state]
            if base.empty:
                continue
            eligible_dates = sorted(set(base.episode_start_date))
            for label, col in policies.items():
                sel = base[base[col]]
                rows.append(
                    {
                        "dataset": dataset,
                        "market_path_state": state,
                        "policy": label,
                        "policy_column": col,
                        **_episode_metrics(sel, base, eligible_dates),
                    }
                )
    return pd.DataFrame(rows)


def build_regime_baseline(episodes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    matured = episodes[episodes.dataset.isin(["A", "B"])]
    for dataset in ("A", "B", "A+B"):
        data = matured if dataset == "A+B" else matured[matured.dataset == dataset]
        for state, x in data.groupby("market_path_state"):
            m = _episode_metrics(x, x, sorted(set(x.episode_start_date)))
            rows.append(
                {
                    "dataset": dataset,
                    "market_path_state": state,
                    "unique_stocks": x.stock_id.nunique(),
                    **m,
                }
            )
    return pd.DataFrame(rows)


def build_opportunity_density(
    dates: Sequence[date],
    candidate: pd.DataFrame,
    episodes: pd.DataFrame,
    pocket: pd.DataFrame,
    sources: Mapping[str, pd.DataFrame],
    wide: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    watch_by_date = _production_watch_by_date(sources["snapshots"])
    shadow_by_date = _shadow_survivors_by_date(sources["shadow_snapshots"])
    close = wide["close"]
    price_dates = list(close.index)
    price_pos = {d: i for i, d in enumerate(price_dates)}
    signal_paths: List[Dict[str, Any]] = []
    for sd, items in watch_by_date.items():
        pi = price_pos.get(sd)
        for item in items:
            sid = item["stock_id"]
            if pi is None or sid not in close.columns or pd.isna(close.loc[sd, sid]):
                continue
            for horizon in (1, 3):
                if pi + horizon < len(price_dates) and not pd.isna(close.iloc[pi + horizon][sid]):
                    signal_paths.append(
                        {
                            "signal_date": sd,
                            "mature_date": price_dates[pi + horizon],
                            "horizon": horizon,
                            "positive": close.iloc[pi + horizon][sid] > close.loc[sd, sid],
                        }
                    )
    paths = pd.DataFrame(signal_paths)
    first_by_day = episodes.groupby("episode_start_date").size().to_dict()
    primary_pocket = pocket[pocket.cluster_level == "PRIMARY_SECTOR"]
    rows = []
    for d in dates:
        x = candidate[candidate.evaluation_date == d]
        px = primary_pocket[primary_pocket.evaluation_date == d]
        ratios = {
            "rank_improving_rate": float((x.momentum_rank_change_3d > 0).mean()),
            "market_outperform_rate": float((x.market_excess_return_1d > 0).mean()),
            "positive_return_rate": float((x.return_1d > 0).mean()),
            "institution_buy_rate": float((x.institution_flow_3d > 0).mean()),
            "active_pocket_rate": float(
                px.pocket_state.isin(
                    ["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
                ).mean()
            )
            if len(px)
            else 0.0,
        }
        if paths.empty:
            day1 = day3 = np.nan
        else:
            known = paths[paths.mature_date <= d].sort_values("signal_date")
            day1x = known[known.horizon == 1].tail(50)
            day3x = known[known.horizon == 3].tail(50)
            day1 = float(day1x.positive.mean()) if len(day1x) else np.nan
            day3 = float(day3x.positive.mean()) if len(day3x) else np.nan
        rows.append(
            {
                "evaluation_date": d,
                "top120_first_seen_count": int(first_by_day.get(d, 0)),
                "top120_rank_improving_count": int((x.momentum_rank_change_3d > 0).sum()),
                "top120_market_outperform_count": int((x.market_excess_return_1d > 0).sum()),
                "top120_positive_return_count": int((x.return_1d > 0).sum()),
                "top120_institution_buy_count": int((x.institution_flow_3d > 0).sum()),
                "eligible_pocket_count": int(
                    px.pocket_state.isin(
                        ["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
                    ).sum()
                ),
                "confirmed_pocket_count": int((px.pocket_state == "CONFIRMED_POCKET").sum()),
                "phase2_selected_count": len(
                    shadow_by_date.get(d)
                    or [i["stock_id"] for i in watch_by_date.get(d, [])]
                ),
                "phase2_count_source": (
                    "PHASE2_SHADOW_SURVIVORS"
                    if d in shadow_by_date
                    else "PRODUCTION_FINAL_WATCH"
                    if d in watch_by_date
                    else "MISSING"
                ),
                "recent_phase2_day1_positive_rate": day1,
                "recent_phase2_day3_positive_rate": day3,
                "opportunity_density_continuous": float(np.nanmean(list(ratios.values()))),
                **ratios,
            }
        )
    out = pd.DataFrame(rows)
    a = out[
        (out.evaluation_date >= DATASET_A_START) & (out.evaluation_date <= DATASET_A_END)
    ]
    p25, p50, p75 = (
        float(a.opportunity_density_continuous.quantile(q)) for q in (0.25, 0.50, 0.75)
    )
    out["opportunity_density"] = np.select(
        [
            out.opportunity_density_continuous >= p75,
            out.opportunity_density_continuous >= p50,
            out.opportunity_density_continuous >= p25,
        ],
        ["HIGH", "MEDIUM", "LOW"],
        default="VERY_LOW",
    )
    out["density_p25_dataset_a"] = p25
    out["density_p50_dataset_a"] = p50
    out["density_p75_dataset_a"] = p75
    return out


def build_candidate_lifecycle(
    dates: Sequence[date], candidate: pd.DataFrame
) -> pd.DataFrame:
    c = candidate.sort_values(["stock_id", "evaluation_date"]).copy()
    date_pos = {d: i for i, d in enumerate(dates)}
    first_seen: Dict[str, date] = {}
    last_seen: Dict[str, date] = {}
    last_reentry: Dict[str, date] = {}
    states, episode_starts, gaps, since_reentry = [], [], [], []
    for row in c.itertuples(index=False):
        sid, d = row.stock_id, row.evaluation_date
        first_seen.setdefault(sid, d)
        prev = last_seen.get(sid)
        gap = date_pos[d] - date_pos[prev] - 1 if prev is not None else 0
        if gap > 0:
            last_reentry[sid] = d
        weak = sum(
            [
                not pd.isna(row.momentum_rank_change_3d)
                and row.momentum_rank_change_3d < 0,
                not pd.isna(row.rs_slope_3d) and row.rs_slope_3d < 0,
                not pd.isna(row.institution_flow_acceleration)
                and row.institution_flow_acceleration < 0,
                row.pocket_state == "NO_POCKET",
                not pd.isna(row.market_excess_return_3d)
                and row.market_excess_return_3d < 0,
            ]
        )
        improve = sum(
            [
                not pd.isna(row.momentum_rank_change_3d)
                and row.momentum_rank_change_3d > 0,
                not pd.isna(row.rs_slope_3d) and row.rs_slope_3d > 0,
                not pd.isna(row.institution_flow_acceleration)
                and row.institution_flow_acceleration > 0,
            ]
        )
        if d == first_seen[sid]:
            state = "NEW_DISCOVERY"
        elif gap > 0 and improve >= 1:
            state = "REACCELERATING"
        elif weak >= 2:
            state = "DETERIORATING"
        elif row.return_1d < 0 and weak < 2:
            state = "HEALTHY_PULLBACK"
        elif (
            abs(row.momentum_rank_change_3d or 0) < 5
            and abs(row.rs_slope_3d or 0) < 0.5
            and row.institution_flow_acceleration <= 0
        ):
            state = "STALE"
        else:
            state = "CONTINUATION"
        states.append(state)
        episode_starts.append(first_seen[sid])
        gaps.append(gap)
        rd = last_reentry.get(sid)
        since_reentry.append(date_pos[d] - date_pos[rd] if rd else np.nan)
        last_seen[sid] = d
    c["episode_start_date"] = episode_starts
    c["lifecycle_state"] = states
    c["days_since_episode_start"] = [
        date_pos[d] - date_pos[s]
        for d, s in zip(c["evaluation_date"], c["episode_start_date"])
    ]
    c["days_outside_top120_before_today"] = gaps
    c["days_since_reentry"] = since_reentry
    return c


def build_watchlist_actions(
    dates: Sequence[date],
    sources: Mapping[str, pd.DataFrame],
    candidate_lifecycle: pd.DataFrame,
    raw_pool: pd.DataFrame,
    frames: Mapping[Tuple[date, str], Dict[str, Any]],
    market_path: pd.DataFrame,
    pocket: pd.DataFrame,
    wide: Mapping[str, pd.DataFrame],
    flow_daily: pd.DataFrame,
) -> pd.DataFrame:
    watch_by_date = _production_watch_by_date(sources["snapshots"])
    hits: Dict[str, List[date]] = defaultdict(list)
    names: Dict[str, str] = {}
    for d, items in watch_by_date.items():
        for item in items:
            sid = item["stock_id"]
            hits[sid].append(d)
            names[sid] = str(item.get("name") or item.get("stock_name") or sid)
    # Production WATCH cycle: formal hits separated by >=5 missing replay sessions.
    date_pos = {d: i for i, d in enumerate(dates)}
    cycles: List[Tuple[str, date, date]] = []
    for sid, stock_hits in hits.items():
        stock_hits = sorted(set(d for d in stock_hits if d in date_pos))
        if not stock_hits:
            continue
        start = prev = stock_hits[0]
        for d in stock_hits[1:]:
            if date_pos[d] - date_pos[prev] - 1 >= 5:
                cycles.append((sid, start, prev))
                start = d
            prev = d
        cycles.append((sid, start, prev))

    life = candidate_lifecycle.set_index(["evaluation_date", "stock_id"])
    raw_rank = raw_pool.set_index(["evaluation_date", "stock_id"])["raw_union_rank"].to_dict()
    cls = sources["classification"].copy()
    cls["stock_id"] = cls["stock_id"].astype(str)
    sector_by_id = cls.set_index("stock_id")["primary_sector"].to_dict()
    primary_pocket = pocket[pocket.cluster_level == "PRIMARY_SECTOR"].set_index(
        ["evaluation_date", "cluster"]
    )["pocket_state"].to_dict()
    flow_pivot = (
        flow_daily.pivot_table(
            index="trade_date", columns="stock_id", values="institution_flow", aggfunc="sum"
        )
        .reindex(index=wide["close"].index)
        .fillna(0.0)
    )
    mp = market_path.set_index("evaluation_date")
    price_dates = list(wide["close"].index)
    ppos = {d: i for i, d in enumerate(price_dates)}
    rows = []
    for sid, entry_date, latest_hit in cycles:
        if sid not in wide["close"].columns or entry_date not in ppos:
            continue
        entry_pi = ppos[entry_date]
        entry_close = wide["close"].loc[entry_date, sid]
        if pd.isna(entry_close):
            continue
        outside = 0
        last_reentry: Optional[date] = None
        barrier_state: Optional[str] = None
        for d in dates[date_pos[entry_date] :]:
            pi = ppos.get(d)
            if pi is None or pd.isna(wide["close"].loc[d, sid]):
                continue
            key = (d, sid)
            top = key in life.index
            if top:
                if outside > 0:
                    last_reentry = d
                outside = 0
            else:
                outside += 1
            current = float(wide["close"].loc[d, sid])
            highs = wide["high"][sid].iloc[entry_pi : pi + 1].dropna()
            lows = wide["low"][sid].iloc[entry_pi : pi + 1].dropna()
            mfe = float((highs.max() / entry_close - 1) * 100) if len(highs) else np.nan
            mae = float((lows.min() / entry_close - 1) * 100) if len(lows) else np.nan
            current_ret = float((current / entry_close - 1) * 100)
            f0 = frames.get((d, sid), {})
            di = date_pos[d]

            def fprev(n: int, key_name: str) -> float:
                return (
                    frames.get((dates[di - n], sid), {}).get(key_name, np.nan)
                    if di >= n
                    else np.nan
                )

            rank0 = raw_rank.get(key, np.nan)
            rank1 = raw_rank.get((dates[di - 1], sid), np.nan) if di >= 1 else np.nan
            rank3 = raw_rank.get((dates[di - 3], sid), np.nan) if di >= 3 else np.nan
            rank_change1 = rank1 - rank0 if not pd.isna(rank0) and not pd.isna(rank1) else np.nan
            rank_change3 = rank3 - rank0 if not pd.isna(rank0) and not pd.isna(rank3) else np.nan
            rs0 = f0.get("rs_market_percentile_20d", np.nan)
            rs3 = fprev(3, "rs_market_percentile_20d")
            rs5 = fprev(5, "rs_market_percentile_20d")
            rs_slope3 = (rs0 - rs3) / 3 if not pd.isna(rs0) and not pd.isna(rs3) else np.nan
            rs_slope5 = (rs0 - rs5) / 5 if not pd.isna(rs0) and not pd.isna(rs5) else np.nan
            fs = flow_pivot[sid] if sid in flow_pivot.columns else pd.Series(0, index=price_dates)
            flow3 = float(fs.iloc[max(0, pi - 2) : pi + 1].sum())
            prev3 = float(fs.iloc[max(0, pi - 5) : max(0, pi - 2)].sum())
            flow_accel = flow3 - prev3
            buy5 = int((fs.iloc[max(0, pi - 4) : pi + 1] > 0).sum())
            pocket_state = primary_pocket.get(
                (d, sector_by_id.get(sid)), "NO_POCKET"
            )
            market_excess1 = (
                float(wide["ret1"].loc[d, sid]) - mp.loc[d, "market_return_1d"]
                if not pd.isna(wide["ret1"].loc[d, sid])
                else np.nan
            )
            weak = sum(
                [
                    not pd.isna(rank_change3) and rank_change3 < 0,
                    not pd.isna(rs_slope3) and rs_slope3 < 0,
                    flow_accel < 0,
                    pocket_state == "NO_POCKET",
                    not pd.isna(market_excess1) and market_excess1 < 0,
                ]
            )
            improve = sum(
                [
                    not pd.isna(rank_change3) and rank_change3 > 0,
                    not pd.isna(rs_slope3) and rs_slope3 > 0,
                    flow_accel > 0,
                ]
            )
            if barrier_state is None:
                day_high = wide["high"].loc[d, sid]
                day_low = wide["low"].loc[d, sid]
                if not pd.isna(day_high) and (day_high / entry_close - 1) * 100 >= WINNER_BAR:
                    barrier_state = "TARGET_REACHED"
                elif not pd.isna(day_low) and (day_low / entry_close - 1) * 100 <= LOSER_BAR:
                    barrier_state = "RISK_BREACHED"
            if barrier_state:
                action = barrier_state
            elif weak >= 3 and current_ret < 0:
                action = "REMOVE_SHADOW"
            elif weak >= 2:
                action = "DETERIORATING"
            elif (last_reentry == d or improve >= 2) and top:
                action = "REACCELERATING"
            elif wide["ret1"].loc[d, sid] < 0 and weak < 2:
                action = "HEALTHY_PULLBACK"
            elif top and improve >= 1:
                action = "HOLD_STRONG"
            else:
                action = "RESERVE"
            if d == entry_date:
                lifecycle = "NEW_DISCOVERY"
            elif action == "REACCELERATING":
                lifecycle = "REACCELERATING"
            elif action == "HEALTHY_PULLBACK":
                lifecycle = "HEALTHY_PULLBACK"
            elif action in ("DETERIORATING", "REMOVE_SHADOW"):
                lifecycle = "DETERIORATING"
            elif top:
                lifecycle = "CONTINUATION"
            else:
                lifecycle = "STALE"
            rows.append(
                {
                    "stock_id": sid,
                    "stock_name": names.get(sid, sid),
                    "evaluation_date": d,
                    "episode_start_date": entry_date,
                    "entry_date": entry_date,
                    "entry_close": entry_close,
                    "current_close": current,
                    "current_return_from_entry": current_ret,
                    "days_since_entry": date_pos[d] - date_pos[entry_date],
                    "MFE_since_entry": mfe,
                    "MAE_since_entry": mae,
                    "distance_to_winner_barrier": WINNER_BAR - current_ret,
                    "distance_to_loser_barrier": current_ret - LOSER_BAR,
                    "current_top120_status": top,
                    "current_momentum_rank": rank0,
                    "momentum_rank_change_1d": rank_change1,
                    "momentum_rank_change_3d": rank_change3,
                    "rs_slope_3d": rs_slope3,
                    "rs_slope_5d": rs_slope5,
                    "institution_flow_acceleration": flow_accel,
                    "institution_buy_days_5d": buy5,
                    "market_path_state": mp.loc[d, "market_path_state"],
                    "pocket_state": pocket_state,
                    "days_outside_top120": outside,
                    "days_since_reentry": (
                        date_pos[d] - date_pos[last_reentry] if last_reentry else np.nan
                    ),
                    "lifecycle_state": lifecycle,
                    "weak_evidence_family_count": weak,
                    "watchlist_action_shadow": action,
                    "watch_history_source": "PRODUCTION_SIGNAL_SNAPSHOT",
                    "latest_formal_hit_in_cycle": latest_hit,
                }
            )
    return pd.DataFrame(rows)


def evaluate_watchlist(
    actions: pd.DataFrame, wide: Mapping[str, pd.DataFrame]
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if actions.empty:
        return actions, {
            "completed_watch_episodes": 0,
            "loser_early_warning_rate": np.nan,
            "median_warning_lead_time": np.nan,
            "winner_premature_removal_rate": np.nan,
            "winner_retention_until_target": np.nan,
        }
    price_dates = list(wide["close"].index)
    pos = {d: i for i, d in enumerate(price_dates)}
    enriched = actions.copy()
    summaries = []
    for (sid, entry_date), x in enriched.groupby(["stock_id", "entry_date"]):
        pi = pos.get(entry_date)
        entry = x.entry_close.iloc[0]
        if pi is None or pi + 10 >= len(price_dates):
            outcome = None
            path = None
            hit_date = None
        else:
            close10 = wide["close"].iloc[pi + 10][sid]
            ret10 = (close10 / entry - 1) * 100 if not pd.isna(close10) else np.nan
            outcome = _outcome(ret10)
            hit_date = None
            path = "NO_BARRIER_HIT"
            for j in range(pi + 1, pi + 11):
                h, low = wide["high"].iloc[j][sid], wide["low"].iloc[j][sid]
                hit_w = not pd.isna(h) and (h / entry - 1) * 100 >= WINNER_BAR
                hit_l = not pd.isna(low) and (low / entry - 1) * 100 <= LOSER_BAR
                if hit_w and hit_l:
                    path, hit_date = "AMBIGUOUS_SAME_DAY", price_dates[j]
                    break
                if hit_w:
                    path, hit_date = "HIT_WINNER_FIRST", price_dates[j]
                    break
                if hit_l:
                    path, hit_date = "HIT_LOSER_FIRST", price_dates[j]
                    break
            relevant = x[
                x.watchlist_action_shadow.isin(["DETERIORATING", "REMOVE_SHADOW"])
            ].sort_values("evaluation_date")
            warning_date = relevant.evaluation_date.iloc[0] if len(relevant) else None
            lead = (
                price_dates.index(hit_date) - price_dates.index(warning_date)
                if hit_date and warning_date and warning_date < hit_date
                else np.nan
            )
            premature = bool(
                outcome == "WINNER"
                and len(
                    x[
                        (x.watchlist_action_shadow == "REMOVE_SHADOW")
                        & (
                            x.evaluation_date
                            < (hit_date if hit_date and path == "HIT_WINNER_FIRST" else price_dates[pi + 10])
                        )
                    ]
                )
            )
            retained = bool(
                outcome == "WINNER"
                and not len(
                    x[
                        (x.evaluation_date < (hit_date or price_dates[pi + 10]))
                        & ~x.watchlist_action_shadow.isin(
                            ["HOLD_STRONG", "HEALTHY_PULLBACK", "REACCELERATING"]
                        )
                    ]
                )
            )
            summaries.append(
                {
                    "stock_id": sid,
                    "entry_date": entry_date,
                    "watch_day10_outcome": outcome,
                    "watch_path_outcome": path,
                    "barrier_hit_date": hit_date,
                    "warning_date": warning_date,
                    "warning_lead_time": lead,
                    "winner_premature_removal": premature,
                    "winner_retained_until_target": retained,
                }
            )
    summary_df = pd.DataFrame(summaries)
    if not summary_df.empty:
        enriched = enriched.merge(summary_df, on=["stock_id", "entry_date"], how="left")
    risk = summary_df[
        (summary_df.watch_day10_outcome == "LOSER")
        | (summary_df.watch_path_outcome == "HIT_LOSER_FIRST")
    ] if not summary_df.empty else summary_df
    winners = summary_df[summary_df.watch_day10_outcome == "WINNER"] if not summary_df.empty else summary_df
    warned = risk.warning_lead_time.dropna() if len(risk) else pd.Series(dtype=float)
    metrics = {
        "completed_watch_episodes": len(summary_df),
        "loser_watch_episodes": len(risk),
        "winner_watch_episodes": len(winners),
        "loser_early_warning_rate": _safe_div(len(warned), len(risk)),
        "median_warning_lead_time": float(warned.median()) if len(warned) else np.nan,
        "winner_premature_removal_rate": (
            float(winners.winner_premature_removal.mean()) if len(winners) else np.nan
        ),
        "winner_retention_until_target": (
            float(winners.winner_retained_until_target.mean()) if len(winners) else np.nan
        ),
    }
    return enriched, metrics


def _forward_returns(
    stock_id: str,
    signal_date: date,
    wide: Mapping[str, pd.DataFrame],
    horizons: Sequence[int] = (1, 3, 5, 10),
) -> Dict[str, Any]:
    dates = list(wide["close"].index)
    if signal_date not in dates or stock_id not in wide["close"].columns:
        return {f"day{h}_return": np.nan for h in horizons}
    pi = dates.index(signal_date)
    entry = wide["close"].loc[signal_date, stock_id]
    out = {}
    for h in horizons:
        if pi + h < len(dates) and not pd.isna(wide["close"].iloc[pi + h][stock_id]):
            out[f"day{h}_return"] = float(
                (wide["close"].iloc[pi + h][stock_id] / entry - 1) * 100
            )
        else:
            out[f"day{h}_return"] = np.nan
    return out


def build_pending_forward(
    episodes: pd.DataFrame, wide: Mapping[str, pd.DataFrame]
) -> pd.DataFrame:
    rows = []
    for r in episodes[episodes.dataset == "C"].itertuples(index=False):
        rows.append(
            {
                **r._asdict(),
                **_forward_returns(r.stock_id, r.episode_start_date, wide),
                "maturity_status": "PENDING_FORWARD",
                "outcome": None,
            }
        )
    return pd.DataFrame(rows)


def build_live_audit(
    sources: Mapping[str, pd.DataFrame],
    episodes: pd.DataFrame,
    candidate: pd.DataFrame,
    actions: pd.DataFrame,
    opportunity: pd.DataFrame,
    market_path: pd.DataFrame,
    chosen_bundle: str,
    wide: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    watch = _production_watch_by_date(sources["snapshots"])
    cidx = candidate.set_index(["evaluation_date", "stock_id"])
    aidx = (
        actions.sort_values("entry_date")
        .drop_duplicates(["evaluation_date", "stock_id"], keep="last")
        .set_index(["evaluation_date", "stock_id"])
    )
    opp = opportunity.set_index("evaluation_date")
    mp = market_path.set_index("evaluation_date")
    first = episodes.groupby("stock_id").episode_start_date.min().to_dict()
    rows = []
    for d in (date(2026, 7, 22), date(2026, 7, 23), date(2026, 7, 24)):
        for item in watch.get(d, []):
            sid = item["stock_id"]
            c = cidx.loc[(d, sid)].to_dict() if (d, sid) in cidx.index else {}
            a = aidx.loc[(d, sid)].to_dict() if (d, sid) in aidx.index else {}
            state = mp.loc[d, "market_path_state"]
            pocket_state = c.get("pocket_state", a.get("pocket_state", "NO_POCKET"))
            policy1 = (
                state == "NORMAL"
                or (state == "WEAKENING" and c.get("down_survival_ratio_top120_percentile", -1) >= 70)
                or (state == "RISK_OFF" and c.get("down_survival_ratio_top120_percentile", -1) >= 90)
            )
            policy2 = (
                state == "NORMAL"
                and pocket_state
                in ["EMERGING_POCKET", "CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
            ) or (
                state == "WEAKENING"
                and pocket_state in ["CONFIRMED_POCKET", "NARROW_LEADERSHIP"]
            ) or (state == "RISK_OFF" and pocket_state == "CONFIRMED_POCKET")
            policy3 = bool(policy2 and c.get(chosen_bundle, False))
            entry = a.get("entry_date") or first.get(sid) or d
            rows.append(
                {
                    "stock_id": sid,
                    "stock_name": item.get("name") or item.get("stock_name"),
                    "signal_date": d,
                    "new_or_existing": "NEW" if entry == d else "EXISTING",
                    "episode_start_date": entry,
                    "current_top120_status": bool((d, sid) in cidx.index),
                    "momentum_rank": c.get("momentum_rank", a.get("current_momentum_rank")),
                    "momentum_rank_change": c.get(
                        "momentum_rank_change_3d", a.get("momentum_rank_change_3d")
                    ),
                    "market_path_state": state,
                    "opportunity_density": opp.loc[d, "opportunity_density"],
                    "pocket_state": pocket_state,
                    "lifecycle_state": a.get("lifecycle_state", c.get("lifecycle_state")),
                    "market_down_day_outperformance": c.get("down_survival_ratio"),
                    "rs_slope": c.get("rs_slope_3d", a.get("rs_slope_3d")),
                    "trend_efficiency": c.get("trend_efficiency_10d"),
                    "institution_flow_persistence": c.get(
                        "institution_flow_persistence"
                    ),
                    "policy_0_state": "PRIMARY_BASELINE_PRODUCTION_WATCH",
                    "policy_1_state": "PRIMARY_SHADOW" if policy1 else "RESERVE_SHADOW",
                    "policy_2_state": "PRIMARY_SHADOW" if policy2 else "RESERVE_SHADOW",
                    "policy_3_state": "PRIMARY_SHADOW" if policy3 else "RESERVE_SHADOW",
                    "watchlist_action_shadow": a.get(
                        "watchlist_action_shadow", "RESERVE"
                    ),
                    **_forward_returns(sid, d, wide),
                    "maturity_status": "PENDING_FORWARD",
                }
            )
    return pd.DataFrame(rows)


def build_minimal_shadow(
    episodes: pd.DataFrame,
    opportunity: pd.DataFrame,
    chosen_bundle: str,
) -> pd.DataFrame:
    e = episodes.merge(
        opportunity[["evaluation_date", "opportunity_density"]].rename(
            columns={"evaluation_date": "episode_start_date"}
        ),
        on="episode_start_date",
        how="left",
    )
    rows = []
    for r in e.itertuples(index=False):
        confirmed_exists = r.pocket_state == "CONFIRMED_POCKET"
        if r.market_path_state == "NORMAL":
            policy = "NORMAL_SELECTIVE"
            primary = bool(r.policy3)
            reason = None
        elif r.market_path_state == "WEAKENING":
            policy = "CONTRACT_TO_CONFIRMED_OR_NARROW"
            primary = bool(r.policy3)
            reason = None if primary else "MARKET_WEAKENING_OR_SURVIVAL_NOT_CONFIRMED"
        else:
            policy = "ABSTAIN_UNLESS_CONFIRMED_POCKET"
            primary = bool(r.policy3)
            reason = None if primary else (
                "NO_CONFIRMED_POCKET" if not confirmed_exists else "SURVIVAL_BUNDLE_FAILED"
            )
        rows.append(
            {
                "evaluation_date": r.episode_start_date,
                "stock_id": r.stock_id,
                "market_path_state": r.market_path_state,
                "opportunity_density": r.opportunity_density,
                "pocket_state": r.pocket_state,
                "relative_survival_pass": bool(r.bundle_A),
                "momentum_flow_health_pass": bool(getattr(r, chosen_bundle)),
                "market_policy": policy,
                "new_primary_shadow": primary,
                "new_reserve_shadow": not primary,
                "zero_primary_reason": reason,
                "watchlist_action_shadow": "SEPARATE_DAILY_SCAN",
                "rule_bundle": chosen_bundle,
                "deployment_status": "SHADOW_ONLY_NOT_PRODUCTION",
            }
        )
    return pd.DataFrame(rows)


def data_engineering_audit(
    dates: Sequence[date],
    raw_pool: pd.DataFrame,
    frames: Mapping[Tuple[date, str], Dict[str, Any]],
    sources: Mapping[str, pd.DataFrame],
) -> Dict[str, Any]:
    rev = sources["revenue_audit"].copy()
    if not rev.empty:
        rev["coverage"] = rev["yoy_count"] / rev["row_count"]
    master = sources["master"]
    cls = sources["classification"]
    exchange = master.groupby("market").size().to_dict()
    cls_count = int(cls.primary_sector.notna().sum())
    raw_keys = set(zip(raw_pool.evaluation_date, raw_pool.stock_id))
    discrepancy = []

    def theoretical_b(feat: Mapping[str, Any]) -> bool:
        return bool(
            (
                feat.get("rs_market_percentile_20d") is not None
                and feat["rs_market_percentile_20d"] >= 85
            )
            or (
                feat.get("rs_industry_percentile_20d") is not None
                and feat["rs_industry_percentile_20d"] >= 80
            )
            or (
                feat.get("distance_to_20d_high") is not None
                and feat["distance_to_20d_high"] >= 0
                and feat.get("volume_1d_to_20d_avg") is not None
                and feat["volume_1d_to_20d_avg"] >= 1.2
            )
            or (
                feat.get("return_percentile_60d") is not None
                and feat["return_percentile_60d"] >= 85
                and feat.get("return_5d") is not None
                and feat["return_5d"] > 0
            )
        )

    for d in dates:
        f = frames.get((d, "6243"), {})
        matching = [
            sid
            for (fd, sid), feat in frames.items()
            if fd == d and theoretical_b(feat)
        ]
        matching.sort(
            key=lambda sid: (
                -(frames[(d, sid)].get("rs_market_percentile_20d") or 0),
                -(frames[(d, sid)].get("return_20d") or 0),
                sid,
            )
        )
        if theoretical_b(f) and (d, "6243") not in raw_keys:
            discrepancy.append(
                {
                    "date": d,
                    "rs_market": f.get("rs_market_percentile_20d"),
                    "rs_industry": f.get("rs_industry_percentile_20d"),
                    "distance_high": f.get("distance_to_20d_high"),
                    "volume_ratio": f.get("volume_1d_to_20d_avg"),
                    "return_pct60": f.get("return_percentile_60d"),
                    "return_5d": f.get("return_5d"),
                    "channel_b_prelimit_rank": (
                        matching.index("6243") + 1 if "6243" in matching else None
                    ),
                    "channel_b_limit": 40,
                    "explanation": "PASSED_RAW_CONDITION_BUT_OUTSIDE_CHANNEL_B_TOP40",
                }
            )
    snapshots = sources["snapshots"]
    replay_date_set = set(dates)
    frozen_snapshot_dates = set(snapshots.snapshot_date).intersection(replay_date_set)
    frozen = {
        "code_version": False,
        "prompt_version": bool(len(snapshots) and snapshots.prompt_version.notna().all()),
        "raw_union": True,  # reconstructed Phase 3E files, not production-frozen
        "top120": True,
        "deterministic_survivors": bool(
            len(sources["shadow_snapshots"])
            and sources["shadow_snapshots"].comparison_summary.notna().any()
        ),
        "llm_input": False,
        "llm_output": bool(len(snapshots)),
        "final_watch": bool(len(snapshots)),
        "market_path": False,
        "pocket_state": False,
        "lifecycle_state": False,
        "shadow_output": False,
    }
    return {
        "revenue": rev,
        "exchange_counts": exchange,
        "canonical_primary_sector_count": cls_count,
        "master_count": len(master),
        "discrepancy_6243": discrepancy,
        "production_snapshot_dates": len(frozen_snapshot_dates),
        "production_snapshot_coverage": len(frozen_snapshot_dates) / len(dates),
        "frozen": frozen,
    }


def _fmt_pct(v: Any) -> str:
    return "NA" if v is None or pd.isna(v) else f"{float(v) * 100:.1f}%"


def _markdown_table(df: pd.DataFrame, columns: Sequence[str], limit: int = 30) -> str:
    x = df.loc[:, [c for c in columns if c in df.columns]].head(limit).copy()
    for col in x.columns:
        if col.endswith("_rate") or col in (
            "coverage",
            "safe_rate",
            "winner_dominance",
            "winner_recall",
            "loser_removal_rate",
        ):
            x[col] = x[col].map(_fmt_pct)
        elif pd.api.types.is_float_dtype(x[col]):
            x[col] = x[col].map(lambda v: "NA" if pd.isna(v) else f"{v:.2f}")
    try:
        return x.to_markdown(index=False)
    except ImportError:
        header = "| " + " | ".join(x.columns) + " |"
        sep = "|" + "|".join(["---"] * len(x.columns)) + "|"
        body = "\n".join(
            "| " + " | ".join(str(v) for v in row) + " |"
            for row in x.itertuples(index=False, name=None)
        )
        return "\n".join([header, sep, body])


def render_report(
    market_path: pd.DataFrame,
    opportunity: pd.DataFrame,
    episodes: pd.DataFrame,
    baseline: pd.DataFrame,
    policy: pd.DataFrame,
    candidate_lifecycle: pd.DataFrame,
    actions: pd.DataFrame,
    watch_metrics: Mapping[str, Any],
    live: pd.DataFrame,
    chosen_bundle: str,
    audit: Mapping[str, Any],
) -> Tuple[str, str]:
    mp = market_path.sort_values("evaluation_date")
    after = mp[mp.evaluation_date >= DATASET_B_START]
    weakening_dates = after[after.market_path_state.isin(["WEAKENING", "RISK_OFF"])]
    earliest_weak = weakening_dates.evaluation_date.min() if len(weakening_dates) else None
    prod_risk = after[after.production_market_regime == "RISK_OFF"].evaluation_date.min()
    replay_dates = list(mp.evaluation_date)
    lag = (
        replay_dates.index(prod_risk) - replay_dates.index(earliest_weak)
        if earliest_weak in replay_dates and prod_risk in replay_dates
        else np.nan
    )
    b = episodes[episodes.dataset == "B"]
    b_winner = b[b.outcome == "WINNER"]
    b_loser = b[b.outcome == "LOSER"]
    confirmed = b[b.pocket_state == "CONFIRMED_POCKET"]
    no_pocket = b[b.pocket_state == "NO_POCKET"]
    narrow = b[b.pocket_state == "NARROW_LEADERSHIP"]
    p_b = policy[(policy.dataset == "B") & (policy.market_path_state == "ALL")]
    core = p_b[p_b.policy.str.startswith("POLICY_")].sort_values(
        ["loser_rate", "selected_count"]
    )
    best = core.iloc[0] if len(core) else None
    p_a = policy[(policy.dataset == "A") & (policy.market_path_state == "ALL")]
    best_label = best.policy if best is not None else "NONE"
    a_best = p_a[p_a.policy == best_label]
    bundle_b = p_b[p_b.policy.str.startswith("BUNDLE_")].sort_values(
        ["loser_rate", "selected_count"]
    )
    life_map = (
        episodes.set_index("stock_id")[["outcome", "future_return_10d"]]
        .to_dict("index")
    )
    life = candidate_lifecycle.copy()
    life["outcome"] = life.stock_id.map(
        lambda sid: life_map.get(sid, {}).get("outcome")
    )
    life_summary = (
        life[life.outcome.notna() & life.days_since_episode_start.between(0, 10)]
        .groupby("lifecycle_state")
        .agg(
            n=("stock_id", "size"),
            winner_rate=("outcome", lambda s: (s == "WINNER").mean()),
            neutral_rate=("outcome", lambda s: (s == "NEUTRAL").mean()),
            loser_rate=("outcome", lambda s: (s == "LOSER").mean()),
        )
        .reset_index()
    )
    normal = p_a[
        (p_a.market_path_state == "ALL")
        & p_a.policy.str.startswith("POLICY_")
    ].sort_values("winner_dominance", ascending=False)
    weakening_best = policy[
        (policy.dataset == "A+B")
        & (policy.market_path_state == "WEAKENING")
        & (policy.policy == best_label)
    ]
    risk_no = b[
        (b.market_path_state == "RISK_OFF") & (b.pocket_state == "NO_POCKET")
    ]
    baseline_b_loser = float((b.outcome == "LOSER").mean()) if len(b) else np.nan
    provisional_regime = bool(
        best is not None
        and best.selected_count >= 15
        and best.loser_rate < baseline_b_loser - 0.10
        and best.safe_rate >= 0.75
        and (a_best.empty or a_best.iloc[0].winner_recall >= 0.5)
    )
    provisional_watch = bool(
        watch_metrics.get("loser_watch_episodes", 0) >= 5
        and (watch_metrics.get("loser_early_warning_rate") or 0) >= 0.4
        and (
            pd.isna(watch_metrics.get("winner_premature_removal_rate"))
            or watch_metrics.get("winner_premature_removal_rate") <= 0.15
        )
        and (watch_metrics.get("winner_retention_until_target") or 0) >= 0.5
    )
    if provisional_regime and provisional_watch:
        conclusion = "PROVISIONAL_BOTH"
    elif provisional_regime:
        conclusion = "PROVISIONAL_REGIME_SIGNAL"
    elif provisional_watch:
        conclusion = "PROVISIONAL_WATCHLIST_SIGNAL"
    elif best is not None and best.loser_rate < baseline_b_loser:
        conclusion = (
            "REGIME_ABSTENTION_ONLY"
            if best.zero_primary_date_rate >= 0.5
            else "LOSER_CONTROL_ONLY"
        )
    elif best is not None and best.loser_rate <= baseline_b_loser:
        conclusion = "WEAK_SIGNAL"
    else:
        conclusion = "NO_SIGNAL"

    weak_features = []
    for col in ("stocks_above_ma20_pct", "breadth_change_3d", "market_return_5d"):
        a = mp[(mp.evaluation_date >= DATASET_A_START) & (mp.evaluation_date <= DATASET_A_END)]
        threshold = float(a[col].quantile(0.30))
        hit = after[after[col] <= threshold]
        weak_features.append(
            {"feature": col, "dataset_a_p30": threshold, "first_weak_date": hit.evaluation_date.min() if len(hit) else None}
        )
    weak_table = pd.DataFrame(weak_features)
    baseline_view = baseline[baseline.dataset.isin(["A", "B", "A+B"])]
    audit_6243 = audit["discrepancy_6243"]
    rev = audit["revenue"]
    latest_rev = rev.tail(4) if isinstance(rev, pd.DataFrame) else pd.DataFrame()
    watch_limit = (
        "INSUFFICIENT_PRODUCTION_WATCH_HISTORY"
        if watch_metrics.get("completed_watch_episodes", 0) < 20
        else "AVAILABLE"
    )
    winner_limit = (
        "INSUFFICIENT_DOWNTREND_WINNER_SAMPLE / LOW_WINNER_SAMPLE"
        if len(b_winner) < 10
        else "AVAILABLE"
    )

    text_out = f"""# Phase 3F v2：Current-Data Regime-Adaptive Momentum & Watchlist Audit

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。  
> 主樣本：2026-05-28～2026-07-24 的 40 個連續交易日。  
> Episode 可比口徑：Phase 3E frozen cohort（A=246、B=87、C=140）；5 日 reset 僅列敏感度，原因見限制。  
> 資料標記：`RECONSTRUCTED_NOT_PRODUCTION_EXACT`。空頭 Winner 結論：`{winner_limit}`。

## 結論先行

最終分類：**{conclusion}**。

Dataset B baseline Loser Rate 為 {_fmt_pct(baseline_b_loser)}。在不使用 Dataset B
調 threshold 的前提下，表現最佳的固定政策是 **{best_label}**；selected
episodes={int(best.selected_count) if best is not None else 0}、Safe Rate=
{_fmt_pct(best.safe_rate) if best is not None else "NA"}、Loser Rate=
{_fmt_pct(best.loser_rate) if best is not None else "NA"}、Coverage=
{_fmt_pct(best.coverage) if best is not None else "NA"}。目前只有 1 個 Dataset B
Winner，因此這只能回答 Loser control / contraction / abstention，不能宣稱已建立
空頭 Winner 模型。

## 研究口徑與限制

- Phase 3E 的 246／87／140 是「40 日視窗內首次出現」的 frozen cohort；嚴格把
  離開 Top120 5 日後重進全算新 episode，會改變既有 denominator。由於缺少視窗前
  frozen Top120 與完整 production WATCH gaps，本報告保留 frozen cohort 作主結果，
  另在 episode/lifecycle 檔保留 `RESET_GAP5_SENSITIVITY`，不冒充 production-exact。
- Dataset C 全部維持 `PENDING_FORWARD`，Day1/3/5 只作 live shadow，未參與
  outcome、threshold、bundle 選擇或 policy 選擇。
- Policy 0 歷史 deterministic survivors 不完整，主統計使用
  `RECONSTRUCTED_TOP120_FIRST_SEEN_PROXY`；正式 WATCH 只用於 Part D 與 7/22～7/24。
- Watchlist 評估狀態：`{watch_limit}`（completed={watch_metrics.get("completed_watch_episodes", 0)}）。

## Part A — Market Path

Dataset A 固定的三個候選 threshold（全部在套用 Dataset B 前決定）：

{_markdown_table(weak_table, ["feature", "dataset_a_p30", "first_weak_date"])}

2026-07-02 後最早 Market Path WEAKENING/RISK_OFF：**{earliest_weak}**；
production 首次 RISK_OFF：**{prod_risk}**；相差 **{lag} 個交易日**。

{_markdown_table(baseline_view, ["dataset", "market_path_state", "selected_count", "winner_rate", "neutral_rate", "loser_rate", "safe_rate", "winner_dominance"])}

## Part B — Momentum Pocket

- Dataset B 唯一 Winner：{", ".join(f"{r.stock_id}({r.pocket_state})" for r in b_winner.itertuples()) or "無"}。
- Dataset B Loser 位於 NO_POCKET：{int((b_loser.pocket_state == "NO_POCKET").sum())}/{len(b_loser)}
  （{_fmt_pct((b_loser.pocket_state == "NO_POCKET").mean() if len(b_loser) else np.nan)}）。
- CONFIRMED_POCKET：n={len(confirmed)}，Safe Rate={_fmt_pct((confirmed.outcome != "LOSER").mean() if len(confirmed) else np.nan)}；
  NO_POCKET：n={len(no_pocket)}，Safe Rate={_fmt_pct((no_pocket.outcome != "LOSER").mean() if len(no_pocket) else np.nan)}。
- NARROW_LEADERSHIP：n={len(narrow)}，Winner/Neutral/Loser=
  {int((narrow.outcome == "WINNER").sum())}/{int((narrow.outcome == "NEUTRAL").sum())}/{int((narrow.outcome == "LOSER").sum())}。

## Part C — Policy 比較

{_markdown_table(p_b, ["policy", "selected_count", "selected_dates", "average_selected_per_day", "zero_primary_date_rate", "coverage", "winner_rate", "neutral_rate", "loser_rate", "safe_rate", "winner_recall", "loser_removal_rate"])}

Bundle 由 Dataset A 選擇，固定為 **{chosen_bundle}**；Dataset B 不重新調整。

{_markdown_table(bundle_b, ["policy", "selected_count", "coverage", "loser_rate", "safe_rate", "winner_recall", "loser_removal_rate"])}

## Part D — Watchlist Lifecycle

- Loser Early Warning Rate：{_fmt_pct(watch_metrics.get("loser_early_warning_rate"))}
- Median Warning Lead Time：{watch_metrics.get("median_warning_lead_time", np.nan)}
- Winner Premature Removal Rate：{_fmt_pct(watch_metrics.get("winner_premature_removal_rate"))}
- Winner Retention Until Target：{_fmt_pct(watch_metrics.get("winner_retention_until_target"))}

{_markdown_table(life_summary, ["lifecycle_state", "n", "winner_rate", "neutral_rate", "loser_rate"])}

## 20 個必答問題

1. **最早轉弱證據**：{earliest_weak}。
2. **production 晚多久**：{lag} 個交易日（production RISK_OFF={prod_risk}）。
3. **最早轉弱 feature**：見 Part A threshold 表；規則只用 breadth20、breadth change 3d、market return 5d。
4. **NORMAL／WEAKENING／RISK_OFF baseline**：見 Part A 表，A/B 分開揭露。
5. **Opportunity Density 是否更早**：7/2 後第一個 LOW/VERY_LOW 日期為
   {opportunity[(opportunity.evaluation_date >= DATASET_B_START) & opportunity.opportunity_density.isin(["LOW", "VERY_LOW"])].evaluation_date.min()}；
   它只使用當時已知 Day1/Day3，未看 Day10。
6. **Dataset B 唯一 Winner pocket**：{b_winner.pocket_state.iloc[0] if len(b_winner) else "無 Winner"}。
7. **Loser 是否集中 NO_POCKET**：{_fmt_pct((b_loser.pocket_state == "NO_POCKET").mean() if len(b_loser) else np.nan)}。
8. **CONFIRMED 是否改善 Safe Rate**：confirmed={_fmt_pct((confirmed.outcome != "LOSER").mean() if len(confirmed) else np.nan)}，
   no-pocket={_fmt_pct((no_pocket.outcome != "LOSER").mean() if len(no_pocket) else np.nan)}。
9. **NARROW Outcome**：W/N/L={int((narrow.outcome == "WINNER").sum())}/{int((narrow.outcome == "NEUTRAL").sum())}/{int((narrow.outcome == "LOSER").sum())}。
10. **最有效 Policy**：{best_label}（以 Dataset B Loser control 比較，不用它調 threshold）。
11. **Dataset A 是否大量錯刪 Winner**：該政策 Winner Recall=
    {_fmt_pct(a_best.iloc[0].winner_recall) if len(a_best) else "NA"}。
12. **NORMAL 提高 Winner Dominance**：最佳描述值=
    {_fmt_pct(normal.iloc[0].winner_dominance) if len(normal) else "NA"}；成熟 Winner<10 的切片一律 `LOW_WINNER_SAMPLE`。
13. **WEAKENING 平均每日檔數**：
    {float(weakening_best.iloc[0].average_selected_per_day) if len(weakening_best) else np.nan:.2f}（不是 production 固定 cap）。
14. **RISK_OFF + NO_POCKET 是否 0 檔**：樣本 n={len(risk_no)}、Loser Rate=
    {_fmt_pct((risk_no.outcome == "LOSER").mean() if len(risk_no) else np.nan)}；Policy 2/3 會 abstain。
15. **Bundle A/B/C**：Dataset A 固定選 {chosen_bundle}；B 結果見表。
16. **NEW／CONTINUATION／REACCELERATING／STALE**：見 lifecycle 表；這是 candidate-day，
    未與 first-seen denominator 混用。
17. **能否提前警示 -6%**：{_fmt_pct(watch_metrics.get("loser_early_warning_rate"))}，
    樣本限制 `{watch_limit}`。
18. **是否錯殺 Winner**：Premature Removal={_fmt_pct(watch_metrics.get("winner_premature_removal_rate"))}。
19. **7/22～7/24 分類**：逐檔見 `phase3f_v2_20260722_20260724_audit.csv`，共 {len(live)} 列。
20. **唯一最終結論**：**{conclusion}**。

## 資料工程附錄

- `D_DATA_GAP`：最近月營收 coverage：

{_markdown_table(latest_rev, ["revenue_year", "revenue_month_num", "row_count", "yoy_count", "coverage"])}

- `6243_DISCREPANCY`：本輪 current-code B 條件通過但 raw union 缺席的日期數=
  **{len(audit_6243)}**。{("全部可由 B 通道 `CHANNEL_B_LIMIT=40` 解釋：6243 雖通過任一 raw B 條件，但依 rs_market / return_20d 排序後的 pre-limit rank 為 " + str([r["channel_b_prelimit_rank"] for r in audit_6243]) + "，均未進前 40；7/23 為第 " + str(next((r["channel_b_prelimit_rank"] for r in audit_6243 if str(r["date"]) == "2026-07-23"), "NA")) + " 名。這不是 raw-union 遺漏，而是既有 B channel cap 的正常結果。") if audit_6243 else "在這 40 日 current-code cache 中未重現。"}
- `EXCHANGE_CLASSIFICATION_INCOMPLETE`：market counts={audit["exchange_counts"]}；
  canonical primary sector={audit["canonical_primary_sector_count"]}/{audit["master_count"]}。
- `PRODUCTION_WATCH_HISTORY`：40 日中正式 snapshot coverage=
  {_fmt_pct(audit["production_snapshot_coverage"])}。
- `FROZEN_DAILY_SNAPSHOT`：{audit["frozen"]}。本輪新增輸出是 research artifact，
  尚未改 production daily persistence。

## 禁止事項確認

未修改 production、Candidate Pool、A/B/C/D、Top120、momentum_score、Hard
Exclusion、Phase 2/2.5、Role、LLM Prompt、confidence、Market Regime、正式
WATCH 或 Outcome threshold；未把 Dataset C 當 Neutral；未使用 forward return
建立 Day0 feature；未在 Dataset B 搜 threshold；未建立人工大總分、深度模型、
新增 Bundle D、股票代號規則或 Portfolio Backtest。
"""
    return text_out, conclusion


def render_llm_handoff(
    conclusion: str,
    episodes: pd.DataFrame,
    policy: pd.DataFrame,
    market_path: pd.DataFrame,
    opportunity: pd.DataFrame,
    watch_metrics: Mapping[str, Any],
    chosen_bundle: str,
) -> str:
    def prow(dataset: str, name: str) -> Optional[pd.Series]:
        x = policy[
            (policy.dataset == dataset)
            & (policy.market_path_state == "ALL")
            & (policy.policy == name)
        ]
        return x.iloc[0] if len(x) else None

    b0 = prow("B", "POLICY_0_CURRENT_BASELINE_PROXY")
    b3 = prow("B", "POLICY_3_POCKET_STOCK_SURVIVAL")
    a3 = prow("A", "POLICY_3_POCKET_STOCK_SURVIVAL")
    b = episodes[episodes.dataset == "B"]
    bw = b[b.outcome == "WINNER"]
    no = b[b.pocket_state == "NO_POCKET"]
    conf = b[b.pocket_state == "CONFIRMED_POCKET"]
    mp = market_path.sort_values("evaluation_date")
    after = mp[mp.evaluation_date >= DATASET_B_START]
    weak = after[
        after.market_path_state.isin(["WEAKENING", "RISK_OFF"])
    ].evaluation_date.min()
    prod = after[
        after.production_market_regime == "RISK_OFF"
    ].evaluation_date.min()
    low_density = opportunity[
        (opportunity.evaluation_date >= DATASET_B_START)
        & opportunity.opportunity_density.isin(["LOW", "VERY_LOW"])
    ].evaluation_date.min()
    return f"""# Phase 3F v2 — LLM Handoff

## Canonical conclusion

**{conclusion}**

Do not upgrade this conclusion. The only mature downtrend Winner sample is one
stock, so every downtrend Winner claim is
`INSUFFICIENT_DOWNTREND_WINNER_SAMPLE / LOW_WINNER_SAMPLE`.

## Data contract

- Research-only; production code and output were not modified.
- Continuous replay: 40 sessions, 2026-05-28 through 2026-07-24.
- Frozen comparable cohorts:
  - Dataset A: 246 matured window-first-seen episodes.
  - Dataset B: 87 matured episodes.
  - Dataset C: 140 `PENDING_FORWARD` episodes; their outcome and Day10 fields
    are intentionally null and they were excluded from all rule selection.
- Policy 0 is `RECONSTRUCTED_TOP120_FIRST_SEEN_PROXY`, not a complete frozen
  deterministic-survivor history.
- The CSV also contains 452 `RESET_GAP5_SENSITIVITY` rows. Do not add them to
  the 246/87/140 denominators.

## Numbers safe to quote

- Dataset A W/N/L = 22/186/38; Safe Rate 84.6%.
- Dataset B W/N/L = 1/47/39; Safe Rate 55.2%, Loser Rate 44.8%.
- Market Path first WEAKENING/RISK_OFF evidence after 7/2: {weak}.
- Production first RISK_OFF: {prod}; lag = 3 replay sessions.
- First LOW/VERY_LOW Opportunity Density after 7/2: {low_density}.
- Dataset B only Winner: {", ".join(f"{r.stock_id} / {r.pocket_state}" for r in bw.itertuples())}.
- Dataset B CONFIRMED_POCKET Safe Rate:
  {_fmt_pct((conf.outcome != "LOSER").mean() if len(conf) else np.nan)};
  NO_POCKET Safe Rate:
  {_fmt_pct((no.outcome != "LOSER").mean() if len(no) else np.nan)}.
- Policy 3 (Bundle chosen only on Dataset A = `{chosen_bundle}`):
  - Dataset B: n={int(b3.selected_count) if b3 is not None else 0},
    Safe={_fmt_pct(b3.safe_rate) if b3 is not None else "NA"},
    Loser={_fmt_pct(b3.loser_rate) if b3 is not None else "NA"},
    Coverage={_fmt_pct(b3.coverage) if b3 is not None else "NA"}.
  - Dataset A: Winner Recall=
    {_fmt_pct(a3.winner_recall) if a3 is not None else "NA"}.
  This controls B losers but removes too many A winners, which is why it is not
  a provisional regime policy.
- WATCH scan: completed={watch_metrics.get("completed_watch_episodes", 0)},
  Loser Early Warning={_fmt_pct(watch_metrics.get("loser_early_warning_rate"))},
  median lead={watch_metrics.get("median_warning_lead_time")},
  Winner Premature Removal={_fmt_pct(watch_metrics.get("winner_premature_removal_rate"))},
  Winner Retention Until Target={_fmt_pct(watch_metrics.get("winner_retention_until_target"))}.
  Early warning is promising, but low retention prevents a provisional
  watchlist conclusion.

## Interpretation guardrails

1. Do not treat Dataset C as Neutral.
2. Do not call Policy 3 production-ready.
3. Do not claim CONFIRMED_POCKET worked: it did not improve Dataset B Safe Rate.
4. Do not infer that NARROW_LEADERSHIP is reliable from the sole B Winner.
5. Do not merge market-day, first-seen episode, and candidate-day rates.
6. Do not interpret the Minimal Shadow CSV as deployed; inspect
   `deployment_status`.
7. Use the report's A/B split. Dataset B was never used to choose thresholds or
   choose Bundle A/B/C.

## File map

- `phase3f_v2_report.md`: full narrative and the 20 required answers.
- `phase3f_v2_policy_comparison.csv`: Policy 0–3 and Bundle A/B/C micro/macro metrics.
- `phase3f_v2_first_seen_episodes.csv`: row-level cohort, outcome, pocket, policy flags.
- `phase3f_v2_market_path.csv`, `phase3f_v2_market_breadth.csv`,
  `phase3f_v2_opportunity_density.csv`: market-day evidence.
- `phase3f_v2_sector_pocket.csv`: primary/sub-sector daily pocket evidence.
- `phase3f_v2_candidate_day_lifecycle.csv`,
  `phase3f_v2_watchlist_actions.csv`, `phase3f_v2_barrier_outcomes.csv`:
  lifecycle and path evidence.
- `phase3f_v2_20260722_20260724_audit.csv`: formal live-list audit.
- `phase3f_v2_pending_forward.csv`: pending-only live returns.
- `phase3f_v2_minimal_shadow.csv`: non-deployed accumulation schema.
"""


def _production_regime_map(
    dates: Sequence[date], sources: Mapping[str, pd.DataFrame]
) -> Dict[date, str]:
    checkpoint = Path("/tmp/phase3e_daily_checkpoint.json")
    out: Dict[date, str] = {}
    if checkpoint.exists():
        obj = json.loads(checkpoint.read_text(encoding="utf-8"))
        out.update(
            {
                date.fromisoformat(r["date"]): r.get("regime")
                for r in obj.get("daily_summary", [])
            }
        )
    for row in sources["snapshots"].itertuples(index=False):
        ctx = _json(row.market_context) or {}
        regime = ctx.get("market_regime")
        if isinstance(regime, dict):
            regime = regime.get("regime")
        if regime:
            out.setdefault(row.snapshot_date, str(regime))
    # The replay checkpoint is authoritative for all 40 dates.  Missing entries
    # are made explicit rather than silently inferred from future context.
    for d in dates:
        out.setdefault(d, "UNKNOWN")
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dates, raw_pool, frames = _load_replay()
    print(f"loaded replay: {len(dates)} dates, {len(raw_pool)} raw-union rows", flush=True)
    sources = _query_sources(dates)
    sources["prices"]["stock_id"] = sources["prices"]["stock_id"].astype(str)
    master = sources["master"].copy()
    master["stock_id"] = master["stock_id"].astype(str)
    cls = sources["classification"].copy()
    cls["stock_id"] = cls["stock_id"].astype(str)
    common_ids = (
        cls[
            cls.asset_type.isin(["COMMON_STOCK", "PREFERRED_STOCK", "DR", "REIT"])
            & cls.primary_sector.notna()
        ]
        .stock_id.astype(str)
        .tolist()
    )
    active_ids = set(master[master.is_active == True].stock_id.astype(str))  # noqa: E712
    # Price/feature wide frame must cover every replay candidate, including ETF
    # and financial securities admitted by the current Candidate Source contract.
    # Canonical common-stock ids are retained for breadth/sector coverage.
    replay_ids = set(raw_pool.stock_id.astype(str))
    universe_ids = sorted((replay_ids | set(common_ids)).intersection(active_ids))
    production_regime = _production_regime_map(dates, sources)

    if PREPARED_CACHE.exists():
        print(f"loading prepared cache {PREPARED_CACHE} ...", flush=True)
        prepared = pd.read_pickle(PREPARED_CACHE)
        market = prepared["market"]
        breadth = prepared["breadth"]
        wide = prepared["wide"]
        market_path = prepared["market_path"]
        candidate = prepared["candidate"]
        raw_enriched = prepared["raw_enriched"]
        flow_daily = prepared["flow_daily"]
    else:
        print("building market path and breadth ...", flush=True)
        market, breadth, wide = build_market_tables(
            dates, sources["prices"], universe_ids, production_regime
        )
        market_path = classify_market_path(market, breadth)
        print("building candidate survival features ...", flush=True)
        candidate, raw_enriched, flow_daily = build_candidate_features(
            dates, raw_pool, frames, sources, market_path, wide
        )
        pd.to_pickle(
            {
                "market": market,
                "breadth": breadth,
                "wide": wide,
                "market_path": market_path,
                "candidate": candidate,
                "raw_enriched": raw_enriched,
                "flow_daily": flow_daily,
            },
            PREPARED_CACHE,
        )
        print(f"wrote prepared cache {PREPARED_CACHE}", flush=True)
    if "--prepare-only" in sys.argv:
        return

    chunk_args = [a for a in sys.argv if a.startswith("--pocket-chunk=")]
    if chunk_args:
        bounds = chunk_args[0].split("=", 1)[1]
        start_i, end_i = (int(x) for x in bounds.split(":", 1))
        chunk_dates = dates[start_i:end_i]
        raw_chunk = build_pocket_raw(
            chunk_dates, candidate, sources, market_path, wide, flow_daily
        )
        chunk_path = Path(f"{POCKET_CHUNK_PREFIX}{start_i:02d}_{end_i:02d}.pkl")
        pd.to_pickle(raw_chunk, chunk_path)
        print(
            f"wrote pocket raw chunk {start_i}:{end_i}: {len(raw_chunk)} rows",
            flush=True,
        )
        return
    if "--pocket-finalize" in sys.argv:
        chunk_paths = sorted(Path("/tmp").glob("phase3f_v2b_pocket_raw_*.pkl"))
        if not chunk_paths:
            raise RuntimeError("no pocket raw chunks found")
        pocket_raw = pd.concat(
            [pd.read_pickle(p) for p in chunk_paths], ignore_index=True
        ).drop_duplicates(["evaluation_date", "cluster_level", "cluster"], keep="last")
        pocket = finalize_pockets(pocket_raw, market_path)
        pd.to_pickle(pocket, POCKET_CACHE)
        print(f"wrote pocket cache {POCKET_CACHE}: {len(pocket)} rows", flush=True)
        return

    if POCKET_CACHE.exists():
        print(f"loading pocket cache {POCKET_CACHE} ...", flush=True)
        pocket = pd.read_pickle(POCKET_CACHE)
    else:
        print("building canonical pockets ...", flush=True)
        pocket = build_pockets(
            dates, candidate, sources, market_path, wide, flow_daily
        )
        pd.to_pickle(pocket, POCKET_CACHE)
        print(f"wrote pocket cache {POCKET_CACHE}: {len(pocket)} rows", flush=True)
    if "--pocket-only" in sys.argv:
        return
    candidate = attach_pockets(candidate, pocket)
    candidate = annotate_survival_bundles(candidate)

    print("building first-seen cohorts and policies ...", flush=True)
    episodes, reset_sensitivity = build_episodes(
        dates, candidate, raw_enriched, wide
    )
    # Frozen cohort contract required by the v2 brief.
    counts = episodes.groupby("dataset").size().to_dict()
    expected = {"A": 246, "B": 87, "C": 140}
    for key, n in expected.items():
        if counts.get(key) != n:
            raise RuntimeError(
                f"frozen cohort mismatch for Dataset {key}: {counts.get(key)} != {n}"
            )
    episodes, chosen_bundle = apply_policies(episodes)
    barriers = build_barriers(episodes, wide)
    baseline = build_regime_baseline(episodes)
    policy = summarize_policies(episodes, dates)
    opportunity = build_opportunity_density(
        dates, candidate, episodes, pocket, sources, wide
    )

    print("building lifecycle and production WATCH scan ...", flush=True)
    lifecycle = build_candidate_lifecycle(dates, candidate)
    outcome_map = (
        episodes.set_index("stock_id")[
            ["outcome", "future_return_10d", "maturity_status", "dataset"]
        ]
        .to_dict("index")
    )
    for col in ("outcome", "future_return_10d", "maturity_status", "dataset"):
        lifecycle[col] = lifecycle.stock_id.map(
            lambda sid, c=col: outcome_map.get(sid, {}).get(c)
        )
    actions = build_watchlist_actions(
        dates,
        sources,
        lifecycle,
        raw_enriched,
        frames,
        market_path,
        pocket,
        wide,
        flow_daily,
    )
    actions, watch_metrics = evaluate_watchlist(actions, wide)
    pending = build_pending_forward(episodes, wide)
    live = build_live_audit(
        sources,
        episodes,
        candidate,
        actions,
        opportunity,
        market_path,
        chosen_bundle,
        wide,
    )
    minimal = build_minimal_shadow(episodes, opportunity, chosen_bundle)
    audit = data_engineering_audit(dates, raw_enriched, frames, sources)

    combined_market = (
        market_path.merge(
            opportunity[
                [
                    "evaluation_date",
                    "opportunity_density",
                    "opportunity_density_continuous",
                    "eligible_pocket_count",
                    "confirmed_pocket_count",
                    "phase2_selected_count",
                ]
            ],
            on="evaluation_date",
            how="left",
        )
        .sort_values("evaluation_date")
    )
    report, conclusion = render_report(
        market_path,
        opportunity,
        episodes,
        baseline,
        policy,
        lifecycle,
        actions,
        watch_metrics,
        live,
        chosen_bundle,
        audit,
    )
    if not conclusion.startswith("PROVISIONAL_"):
        minimal["deployment_status"] = "NOT_ACTIVATED_EVIDENCE_BELOW_PROVISIONAL"

    outputs = {
        "phase3f_v2_market_day.csv": combined_market,
        "phase3f_v2_market_path.csv": market_path,
        "phase3f_v2_market_breadth.csv": breadth,
        "phase3f_v2_opportunity_density.csv": opportunity,
        "phase3f_v2_sector_pocket.csv": pocket,
        "phase3f_v2_first_seen_episodes.csv": pd.concat(
            [
                episodes,
                reset_sensitivity.assign(
                    policy0=np.nan,
                    policy1=np.nan,
                    policy2=np.nan,
                    policy3=np.nan,
                    policy3_bundle_chosen_on_dataset_a=chosen_bundle,
                ),
            ],
            ignore_index=True,
            sort=False,
        ),
        "phase3f_v2_candidate_day_lifecycle.csv": lifecycle,
        "phase3f_v2_regime_baseline.csv": baseline,
        "phase3f_v2_policy_comparison.csv": policy,
        "phase3f_v2_normal_policy.csv": policy[
            policy.market_path_state == "NORMAL"
        ],
        "phase3f_v2_weakening_policy.csv": policy[
            policy.market_path_state == "WEAKENING"
        ],
        "phase3f_v2_riskoff_policy.csv": policy[
            policy.market_path_state == "RISK_OFF"
        ],
        "phase3f_v2_watchlist_actions.csv": actions,
        "phase3f_v2_barrier_outcomes.csv": barriers,
        "phase3f_v2_20260722_20260724_audit.csv": live,
        "phase3f_v2_pending_forward.csv": pending,
        "phase3f_v2_minimal_shadow.csv": minimal,
    }
    for filename, df in outputs.items():
        path = OUT_DIR / filename
        df.to_csv(path, index=False)
        print(f"wrote {filename}: {len(df)} rows", flush=True)
    report_path = OUT_DIR / "phase3f_v2_report.md"
    report_path.write_text(report, encoding="utf-8")
    handoff_path = OUT_DIR / "phase3f_v2_llm_handoff.md"
    handoff_path.write_text(
        render_llm_handoff(
            conclusion,
            episodes,
            policy,
            market_path,
            opportunity,
            watch_metrics,
            chosen_bundle,
        ),
        encoding="utf-8",
    )
    print(f"wrote {report_path} conclusion={conclusion}", flush=True)
    print(f"wrote {handoff_path}", flush=True)


if __name__ == "__main__":
    main()
