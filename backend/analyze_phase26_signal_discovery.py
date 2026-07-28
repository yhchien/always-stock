"""Phase 2.6 Signal Discovery：離線分析腳本（純讀 `/tmp/phase26_snapshots.json`，
不查資料庫、不呼叫 OpenAI）。

產出：
    /tmp/phase26_extended_3d_success_vs_failure.csv
    /tmp/phase26_winner_vs_big_loser_trajectory.csv
    /tmp/phase26_named_case_trajectory.csv

用法：
    python analyze_phase26_signal_discovery.py
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from typing import Any, Dict, List, Optional

SNAPSHOT_PATH = "/tmp/phase26_snapshots.json"

NAMED_WINNERS = ("6505", "8039", "6414", "1810")
NAMED_LOSERS = ("7610", "8033", "6226")

DAY0_FEATURE_COLUMNS = [
    "momentum_score", "momentum_phase",
    "rs_market_percentile_20d", "rs_industry_percentile_20d",
    "peer_rs_percentile_20d", "sector_strength_percentile_20d", "sector_context_quality",
    "peer_scope_used", "primary_sector_stock_count", "sub_sector_stock_count",
    "price_change_1d", "price_change_3d", "price_change_5d", "price_change_10d",
    "return_20d", "return_60d",
    "rs_rank_improvement_5d",
    "volume_5d_to_60d_ratio", "volume_1d_to_5d_ratio", "volume_1d_to_20d_avg",
    "trend_efficiency_20d", "atr_pct_14d", "distance_to_20d_high", "distance_to_ma20",
    "total_institution_flow_1d", "total_institution_flow_3d", "total_institution_flow_5d",
    "institution_flow_momentum", "sector_rotation_status", "sector_cluster_state",
    "role", "entry_state", "pullback_atr_multiple", "tracking_state",
    "momentum_freshness", "excess_return_vs_market_1d", "excess_return_vs_market_3d",
    "close_location_value", "relative_volume_signed",
]


def _group(ret: Optional[float]) -> str:
    if ret is None:
        return "UNKNOWN"
    if ret >= 10:
        return "A>=+10%"
    if ret >= 0:
        return "B 0~+10%"
    if ret >= -5:
        return "C 0~-5%"
    if ret >= -10:
        return "D -5~-10%"
    return "E<=-10%"


def load_snapshots() -> Dict[str, Any]:
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_extended_3d_csv(snapshots: Dict[str, Any], out_path: str) -> List[Dict[str, Any]]:
    rows = []
    for sid, entry in snapshots.items():
        day0 = entry.get("days", {}).get("day0")
        if not day0:
            continue
        risk = entry.get("risk_warnings_day0") or []
        if "EXTENDED_3D" not in risk:
            continue
        ret = entry.get("forward_return_pct_10d")
        row = {
            "stock_id": sid,
            "catch_date": entry.get("catch_date"),
            "forward_return_pct_10d": ret,
            "outcome_group": _group(ret),
        }
        for k in DAY0_FEATURE_COLUMNS:
            row[k] = day0.get(k)
        rows.append(row)

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print(f"extended_3d subset: {len(rows)} stocks -> {out_path}")
    return rows


def write_trajectory_csv(snapshots: Dict[str, Any], stock_ids: List[str], out_path: str) -> List[Dict[str, Any]]:
    rows = []
    for sid in stock_ids:
        entry = snapshots.get(sid)
        if not entry:
            continue
        ret = entry.get("forward_return_pct_10d")
        for day_label, day_data in sorted(entry.get("days", {}).items(), key=lambda kv: int(kv[0][3:])):
            row = {
                "stock_id": sid,
                "day_offset": int(day_label[3:]),
                "date": day_data.get("date"),
                "catch_date": entry.get("catch_date"),
                "forward_return_pct_10d": ret,
                "outcome_group": _group(ret),
            }
            for k in DAY0_FEATURE_COLUMNS:
                row[k] = day_data.get(k)
            rows.append(row)

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print(f"trajectory rows: {len(rows)} -> {out_path}")
    return rows


def main() -> None:
    snapshots = load_snapshots()
    print(f"loaded {len(snapshots)} stock snapshots")

    write_extended_3d_csv(snapshots, "/tmp/phase26_extended_3d_success_vs_failure.csv")

    winners = [sid for sid, e in snapshots.items() if (e.get("forward_return_pct_10d") or -999) >= 10]
    big_losers = [sid for sid, e in snapshots.items() if (e.get("forward_return_pct_10d") or 999) <= -10]
    print(f"winners (>=+10%): {len(winners)}, big losers (<=-10%): {len(big_losers)}")
    write_trajectory_csv(snapshots, winners + big_losers, "/tmp/phase26_winner_vs_big_loser_trajectory.csv")

    named = [sid for sid in (NAMED_WINNERS + NAMED_LOSERS) if sid in snapshots]
    missing = [sid for sid in (NAMED_WINNERS + NAMED_LOSERS) if sid not in snapshots]
    if missing:
        print(f"WARNING: named cases missing from window: {missing}")
    write_trajectory_csv(snapshots, named, "/tmp/phase26_named_case_trajectory.csv")


if __name__ == "__main__":
    main()
