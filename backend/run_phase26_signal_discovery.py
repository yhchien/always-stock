"""Phase 2.6 Signal Discovery（2026-07-23）：Day0~Day+5 trajectory 研究腳本。

**本次只做 research，不修改任何 production rule / threshold / Hard Exclusion。**

沿用 Phase 2.5 replay 已產出的 617 檔去重 candidate（`/tmp/phase25_replay_60d.json`）
當目標股票池，針對每一檔在其 first_seen（catch_date）之後的 Day0/+1/+2/+3/+5（交易日）
重建 deterministic 特徵快照——**不透過 `candidate_pool.build_candidate_pool()` 的候選池
篩選/擴散邏輯，也不透過 `pipeline_v2.run_phase2_pipeline()` 的 regime gate**，而是直接
呼叫底層 pure function（`momentum.compute_market_momentum_frame` /
`sector_context.compute_sector_context` / `entry_state.compute_entry_state` /
`deterministic_signals.build_deterministic_signals` / `roles.classify_roles` /
`tracking_state.compute_tracking_state` / `momentum_freshness.compute_momentum_freshness`）
——因為研究目的是「這檔股票不論當天是否還被候選池選中，特徵長什麼樣子」，Day+2/+3
的失敗股很可能已經跌出候選池，若透過候選池邏輯會直接看不到它。

輸出：
    /tmp/phase26_snapshots.json —— {stock_id: {trading_date_offset_label: {features...}}}
    供後續 `analyze_phase26_signal_discovery.py`（另一支離線分析腳本）產出：
        - extended_3d_success_vs_failure.csv
        - winner_vs_big_loser_trajectory.csv
        - named_case_trajectory.csv
        - phase26_signal_discovery_report.md

用法：
    python run_phase26_signal_discovery.py --smoke 5   # 只跑前 5 檔股票驗證正確性
    python run_phase26_signal_discovery.py              # 全量 617 檔
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice
from app.signals import candidate_pool
from app.signals import deterministic_signals as det_signals
from app.signals import filters as legacy_filters
from app.signals import momentum
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import momentum_freshness as freshness_mod
from app.signals.phase2 import pipeline_v2
from app.signals.phase2 import roles as roles_mod
from app.signals.phase2 import sector_cluster as cluster_mod
from app.signals.phase2 import sector_context as sector_ctx_mod
from app.signals.phase2 import tracking_state as tracking_mod

REPLAY_SOURCE = "/tmp/phase25_replay_60d.json"
OUT_PATH = "/tmp/phase26_snapshots.json"
FORWARD_OFFSETS = (0, 1, 2, 3, 5)  # 交易日 offset（0=first_seen 當天）

FEATURE_KEYS = [
    "name", "industry", "sub_industry",
    "price_change_1d", "price_change_3d", "price_change_5d", "price_change_10d",
    "return_20d", "return_60d",
    "momentum_score", "momentum_phase",
    "rs_market_percentile_20d", "rs_industry_percentile_20d",
    "rs_rank_improvement_5d",
    "distance_to_20d_high", "distance_to_ma20", "trend_efficiency_20d", "atr_pct_14d",
    "volume_5d_to_60d_ratio", "volume_1d_to_5d_ratio", "volume_1d_to_20d_avg",
    "total_institution_flow_1d", "total_institution_flow_3d", "total_institution_flow_5d",
    "consecutive_buy_days_3d",
    "close_1d", "high_1d", "low_1d", "open_1d",
    "role", "entry_state", "pullback_atr_multiple", "tracking_state",
    "is_tracked", "hit_count", "failed_follow_through",
]


def _load_cohort_targets() -> Dict[str, Dict[str, Any]]:
    with open(REPLAY_SOURCE, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return first_seen


def _build_trading_day_index(db, anchor_end: date, lookback: int) -> List[date]:
    return get_recent_trade_dates(db, anchor_end, lookback)


def _load_taiex_returns(db, all_days: List[date]) -> Dict[date, Dict[str, Optional[float]]]:
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(DailyPrice.stock_id == "TAIEX", DailyPrice.trade_date.in_(all_days))
        .all()
    )
    by_date = {d: float(c) for d, c in rows if c is not None}
    ordered = [d for d in all_days if d in by_date]
    idx = {d: i for i, d in enumerate(ordered)}
    out: Dict[date, Dict[str, Optional[float]]] = {}
    for d in ordered:
        i = idx[d]
        r1 = (by_date[d] / by_date[ordered[i - 1]] - 1.0) * 100.0 if i >= 1 and by_date[ordered[i - 1]] else None
        r3 = (by_date[d] / by_date[ordered[i - 3]] - 1.0) * 100.0 if i >= 3 and by_date[ordered[i - 3]] else None
        out[d] = {"return_1d_pct": r1, "return_3d_pct": r3}
    return out


def _snapshot_day(
    db,
    target_date: date,
    target_ids: List[str],
    taiex_ret: Dict[str, Optional[float]],
) -> Dict[str, Dict[str, Any]]:
    """對 target_ids（該天需要快照的股票）建立 rich deterministic 特徵快照。

    不透過候選池篩選；直接用底層 pure function 組裝，任何股票只要還在
    stocks_master + 有 daily_price 資料就能拿到特徵，即使它已經跌出候選池。
    """
    ingestion = candidate_pool.ingest_data(db, target_date)
    masters = ingestion.get("stocks_master") or {}
    if not masters:
        return {}

    frame = momentum.compute_market_momentum_frame(db, target_date, masters)
    if not frame:
        return {}

    classifications = pipeline_v2.load_classifications(db, list(frame.keys()))
    sector_ctx_by_id = sector_ctx_mod.compute_sector_context(frame, classifications)

    present_ids = [sid for sid in target_ids if sid in frame and sid in masters]
    if not present_ids:
        return {}

    metrics = candidate_pool._compute_pool_metrics(db, ingestion, present_ids)
    tracking_status = candidate_pool._load_tracking_status(db, present_ids, target_date)
    industry_flow_totals = candidate_pool._load_industry_flow_totals(db, ingestion)

    cands: Dict[str, Dict[str, Any]] = {}
    for sid in present_ids:
        master = masters[sid]
        m = metrics.get(sid) or candidate_pool._empty_metrics()
        ts = tracking_status.get(sid) or candidate_pool._empty_tracking_status()
        mf = frame.get(sid) or momentum.empty_momentum_features()
        ind_flow = industry_flow_totals.get(
            candidate_pool._normalized_industry(master.industry_name)
        ) or {"industry_flow_1d": None, "industry_flow_3d": None}
        c: Dict[str, Any] = {
            "stock_id": sid,
            "name": master.stock_name,
            "industry": master.industry_name,
            "sub_industry": master.sub_industry,
            **m,
            **{k: v for k, v in mf.items() if not k.startswith("_")},
            **ts,
            **ind_flow,
        }
        c.update(momentum.compute_momentum_score(c))
        cands[sid] = c

    for sid, c in cands.items():
        c["soft_hints"] = legacy_filters._detect_soft_hints(c)
    for sid, c in cands.items():
        c["deterministic_signals"] = det_signals.build_deterministic_signals(c)
    for sid, c in cands.items():
        er = entry_state_mod.compute_entry_state(c)
        c["entry_state"] = er["entry_state"]
        c["pullback_atr_multiple"] = er["pullback_atr_multiple"]
    for sid, c in cands.items():
        c["tracking_state"] = tracking_mod.compute_tracking_state(c)

    new_discovery = [c for c in cands.values() if c.get("tracking_state") is None]
    # sector cluster 近似：只用目標股票本身當 cluster 樣本（非全市場候選池），
    # 是研究腳本的已知簡化（見 report 說明），因為全市場 momentum_score 計算成本過高。
    clusters = cluster_mod.compute_sector_clusters(list(cands.values()), sector_ctx_by_id)
    role_results = roles_mod.classify_roles(new_discovery, sector_ctx_by_id, clusters)
    for sid, c in cands.items():
        c["role"] = role_results.get(sid, {}).get("role")

    out: Dict[str, Dict[str, Any]] = {}
    for sid, c in cands.items():
        ctx = sector_ctx_by_id.get(sid) or {}
        cluster_state = cluster_mod.get_cluster_state(ctx.get("primary_sector"), clusters)
        fresh = freshness_mod.compute_momentum_freshness(
            c, taiex_return_1d_pct=taiex_ret.get("return_1d_pct")
        )
        row = {k: c.get(k) for k in FEATURE_KEYS}
        row.update({
            "peer_rs_percentile_20d": ctx.get("peer_rs_percentile_20d"),
            "sector_strength_percentile_20d": ctx.get("sector_strength_percentile_20d"),
            "sector_context_quality": ctx.get("sector_context_quality"),
            "peer_scope_used": ctx.get("peer_scope_used"),
            "primary_sector_stock_count": ctx.get("primary_sector_stock_count"),
            "sub_sector_stock_count": ctx.get("sub_sector_stock_count"),
            "sector_cluster_state": cluster_state,
            "institution_flow_momentum": c["deterministic_signals"].get("institution_flow_momentum"),
            "sector_rotation_status": c["deterministic_signals"].get("sector_rotation_status"),
            "risk_flags": c["deterministic_signals"].get("risk_flags"),
            "momentum_freshness": fresh["momentum_freshness"],
            "excess_return_vs_market_1d": fresh["excess_return_vs_market_1d"],
            "close_location_value": fresh["close_location_value"],
            "relative_volume_signed": fresh["relative_volume_signed"],
            "taiex_return_1d_pct": taiex_ret.get("return_1d_pct"),
            "taiex_return_3d_pct": taiex_ret.get("return_3d_pct"),
            "excess_return_vs_market_3d": (
                c.get("price_change_3d") - taiex_ret["return_3d_pct"]
                if c.get("price_change_3d") is not None and taiex_ret.get("return_3d_pct") is not None
                else None
            ),
        })
        out[sid] = row
    return out


def run(
    smoke_n: Optional[int],
    only_stocks: Optional[List[str]] = None,
    offsets_override: Optional[List[int]] = None,
    out_path: str = OUT_PATH,
) -> None:
    db = SessionLocal()
    try:
        cohort = _load_cohort_targets()
        if only_stocks:
            stock_ids = [sid for sid in only_stocks if sid in cohort]
            missing = [sid for sid in only_stocks if sid not in cohort]
            if missing:
                print(f"WARNING: not found in 60-day replay window (skipped): {missing}")
        else:
            stock_ids = sorted(cohort.keys())
            if smoke_n:
                stock_ids = stock_ids[:smoke_n]
        print(f"target stocks: {len(stock_ids)} -> {stock_ids}")

        offsets = offsets_override or list(FORWARD_OFFSETS)

        # 建立涵蓋整個 replay 視窗 + forward buffer 的完整交易日序列
        anchor_end = date(2026, 7, 22)
        all_days = _build_trading_day_index(db, anchor_end, 200)
        day_index = {d: i for i, d in enumerate(all_days)}

        # 對每檔股票算出它需要的交易日 offset 對應的實際日期
        needed_dates_by_stock: Dict[str, Dict[int, date]] = {}
        all_needed_dates = set()
        for sid in stock_ids:
            catch_date = date.fromisoformat(cohort[sid]["catch_date"])
            if catch_date not in day_index:
                continue
            i0 = day_index[catch_date]
            day_offsets = {}
            for off in offsets:
                j = i0 + off
                if j < len(all_days):
                    day_offsets[off] = all_days[j]
                    all_needed_dates.add(all_days[j])
            needed_dates_by_stock[sid] = day_offsets

        print(f"unique dates needed: {len(all_needed_dates)} "
              f"({min(all_needed_dates)} ~ {max(all_needed_dates)})")

        taiex_ret_all = _load_taiex_returns(db, sorted(all_needed_dates))

        # 反查：每個日期 -> 當天需要快照的 stock_id 清單
        stocks_by_date: Dict[date, List[str]] = {}
        for sid, offsets in needed_dates_by_stock.items():
            for off, d in offsets.items():
                stocks_by_date.setdefault(d, []).append(sid)

        results: Dict[str, Dict[str, Any]] = {sid: {} for sid in stock_ids}
        sorted_dates = sorted(stocks_by_date.keys())
        for i, d in enumerate(sorted_dates):
            target_ids_today = stocks_by_date[d]
            taiex_ret = taiex_ret_all.get(d, {"return_1d_pct": None, "return_3d_pct": None})
            snap = _snapshot_day(db, d, target_ids_today, taiex_ret)
            for sid, row in snap.items():
                results[sid][d.isoformat()] = row
            if (i + 1) % 5 == 0 or i == len(sorted_dates) - 1:
                print(f"  progress {i + 1}/{len(sorted_dates)} dates done")

        # 組裝最終輸出：stock_id -> {offset_label: {date, features...}}
        final: Dict[str, Any] = {}
        for sid in stock_ids:
            offsets = needed_dates_by_stock.get(sid, {})
            entry = {
                "catch_date": cohort[sid]["catch_date"],
                "forward_return_pct_10d": cohort[sid].get("forward_return_pct"),
                "risk_warnings_day0": cohort[sid].get("risk_warnings"),
                "watch_quality_state_day0": cohort[sid].get("watch_quality_state"),
                "momentum_freshness_day0_original": cohort[sid].get("momentum_freshness"),
                "days": {},
            }
            for off, d in offsets.items():
                row = results[sid].get(d.isoformat())
                if row is not None:
                    entry["days"][f"day{off}"] = {"date": d.isoformat(), **row}
            final[sid] = entry

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(final, f, ensure_ascii=False, indent=2, default=str)
        print(f"written {out_path}, stocks={len(final)}")
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", type=int, default=None, help="只跑前 N 檔股票驗證正確性")
    parser.add_argument(
        "--stocks", type=str, default=None,
        help="只跑指定股票代號（逗號分隔），例如 --stocks 8039,6505,6414,1810,7610,8033,6226",
    )
    parser.add_argument(
        "--offsets", type=str, default=None,
        help="覆寫要抓的交易日 offset（逗號分隔整數），例如 --offsets 0,1,2,3",
    )
    parser.add_argument("--out", type=str, default=OUT_PATH)
    args = parser.parse_args()
    only_stocks = args.stocks.split(",") if args.stocks else None
    offsets_override = [int(x) for x in args.offsets.split(",")] if args.offsets else None
    run(args.smoke, only_stocks=only_stocks, offsets_override=offsets_override, out_path=args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
