"""Phase 2.7 PART B: ROUND_TRIP_FAILURE Research（2026-07-24）。

**純離線分析**，讀既有 `/tmp/continuation_quality_132_raw.json` +
`/tmp/phase27_roundtrip_matched.json`（18 檔 ROUND_TRIP_FAILURE，各自 MFE-matched
一檔 WINNER）。不查資料庫、不重跑 pipeline、不修改既有規則。

核心問題：「對曾經賺錢的股票，能不能在還沒從 +20% 變成 -10% 以前，辨認它已經
從『正常回檔』轉變成『真正的 ROUND_TRIP_FAILURE』？」

PEAK_DAY：該股票 7 個 observation（Day0/1/2/3/5/7/10）中 current_return 最高
的那個 offset。從 PEAK_DAY 起算 Peak/Peak+1/Peak+2/Peak+3（用 7-offset 陣列
的「相對位置」對齊，不是真正逐日的 peak+1 交易日——這是本輪已知簡化）。

用法：
    python analyze_phase27_roundtrip.py
"""
from __future__ import annotations

import csv
import json
import statistics
from typing import Any, Dict, List, Optional

RAW_PATH = "/tmp/continuation_quality_132_raw.json"
MATCHED_PATH = "/tmp/phase27_roundtrip_matched.json"
OUT_CSV = "/tmp/phase27_roundtrip_matched36.csv"

OFFSETS = (0, 1, 2, 3, 5, 7, 10)
RISK_STATES = ("AT_RISK", "FAILED")


def load_trajectories() -> Dict[str, List[Dict[str, Any]]]:
    rows = json.load(open(RAW_PATH, encoding="utf-8"))
    by_stock: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_stock.setdefault(r["stock_id"], []).append(r)
    for sid in by_stock:
        by_stock[sid].sort(key=lambda x: x["day_offset"])
    return by_stock


def find_peak_index(traj: List[Dict[str, Any]]) -> int:
    returns = [r["current_return"] for r in traj]
    return max(range(len(returns)), key=lambda i: returns[i])


def build_peak_aligned_rows(sid: str, role: str, traj: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    peak_idx = find_peak_index(traj)
    rows_out = []
    for rel_off in (0, 1, 2, 3):
        idx = peak_idx + rel_off
        if idx >= len(traj):
            break
        r = traj[idx]
        rows_out.append({
            "stock_id": sid,
            "role": role,
            "future_return_10d": traj[0]["future_return_10d"],
            "peak_day_offset": traj[peak_idx]["day_offset"],
            "peak_return": traj[peak_idx]["current_return"],
            "rel_offset": f"PEAK+{rel_off}" if rel_off else "PEAK",
            "actual_day_offset": r["day_offset"],
            "current_return": r["current_return"],
            "drawdown_from_max": r["drawdown_from_max"],
            "excess_return_vs_market_3d": r["excess_return_vs_market_3d"],
            "momentum_freshness": r["momentum_freshness"],
            "tracking_state": r["tracking_state"],
            "continuation_quality_state": r["continuation_quality_state"],
        })
    return rows_out


def main() -> None:
    by_stock = load_trajectories()
    matched = json.load(open(MATCHED_PATH, encoding="utf-8"))
    rt_ids = matched["round_trip_losers"]
    w_ids = matched["matched_winners"]

    all_rows = []
    for rt_sid, w_sid in zip(rt_ids, w_ids):
        all_rows.extend(build_peak_aligned_rows(rt_sid, "ROUND_TRIP_FAILURE", by_stock[rt_sid]))
        all_rows.extend(build_peak_aligned_rows(w_sid, "MATCHED_WINNER", by_stock[w_sid]))

    columns = [
        "stock_id", "role", "future_return_10d", "peak_day_offset", "peak_return",
        "rel_offset", "actual_day_offset", "current_return", "drawdown_from_max",
        "excess_return_vs_market_3d", "momentum_freshness", "tracking_state",
        "continuation_quality_state",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"wrote {len(all_rows)} rows -> {OUT_CSV}")

    # ---- descriptive stats by rel_offset ----
    by_rel = {}
    for r in all_rows:
        by_rel.setdefault((r["role"], r["rel_offset"]), []).append(r)

    print(f"\n{'role':20s} {'rel_offset':10s} {'n':>3s} {'med_return':>11s} {'med_drawdown':>13s} "
          f"{'med_excess3d':>13s} {'DETERIORATING%':>15s} {'AT_RISK+%':>10s} {'FAILED%':>8s}")
    for role in ("ROUND_TRIP_FAILURE", "MATCHED_WINNER"):
        for rel in ("PEAK", "PEAK+1", "PEAK+2", "PEAK+3"):
            rows = by_rel.get((role, rel), [])
            if not rows:
                continue
            rets = [r["current_return"] for r in rows]
            dds = [r["drawdown_from_max"] for r in rows]
            excess = [r["excess_return_vs_market_3d"] for r in rows if r["excess_return_vs_market_3d"] is not None]
            det_rate = 100 * sum(1 for r in rows if r["momentum_freshness"] in ("STALE", "DETERIORATING")) / len(rows)
            atrisk_rate = 100 * sum(1 for r in rows if r["continuation_quality_state"] in RISK_STATES) / len(rows)
            failed_rate = 100 * sum(1 for r in rows if r["continuation_quality_state"] == "FAILED") / len(rows)
            print(f"{role:20s} {rel:10s} {len(rows):>3d} {statistics.median(rets):>11.2f} "
                  f"{statistics.median(dds):>13.2f} "
                  f"{(statistics.median(excess) if excess else float('nan')):>13.2f} "
                  f"{det_rate:>15.1f} {atrisk_rate:>10.1f} {failed_rate:>8.1f}")

    # ---- earliest divergence point: 找哪個 rel_offset 兩組差異開始穩定拉開 ----
    print("\n=== group gap by rel_offset (ROUND_TRIP median - WINNER median) ===")
    for rel in ("PEAK", "PEAK+1", "PEAK+2", "PEAK+3"):
        rt_rows = by_rel.get(("ROUND_TRIP_FAILURE", rel), [])
        w_rows = by_rel.get(("MATCHED_WINNER", rel), [])
        if not rt_rows or not w_rows:
            continue
        rt_dd = statistics.median([r["drawdown_from_max"] for r in rt_rows])
        w_dd = statistics.median([r["drawdown_from_max"] for r in w_rows])
        rt_atrisk = 100 * sum(1 for r in rt_rows if r["continuation_quality_state"] in RISK_STATES) / len(rt_rows)
        w_atrisk = 100 * sum(1 for r in w_rows if r["continuation_quality_state"] in RISK_STATES) / len(w_rows)
        print(f"{rel}: drawdown gap={rt_dd - w_dd:.2f}pp, AT_RISK+ rate gap={rt_atrisk - w_atrisk:.1f}pp "
              f"(RT={rt_atrisk:.1f}% W={w_atrisk:.1f}%)")

    # ---- PROFIT_PATH 特別分析：ROUND_TRIP 是否更常命中 drawdown<=-8% (profit_path_bad 的其中一條件) ----
    print("\n=== PROFIT_PATH deterioration (drawdown_from_max<=-8%, matching既有門檻) ===")
    for role in ("ROUND_TRIP_FAILURE", "MATCHED_WINNER"):
        for rel in ("PEAK", "PEAK+1", "PEAK+2", "PEAK+3"):
            rows = by_rel.get((role, rel), [])
            if not rows:
                continue
            hit = sum(1 for r in rows if r["drawdown_from_max"] <= -8.0)
            print(f"{role:20s} {rel:10s}: {hit}/{len(rows)} = {100*hit/len(rows):.1f}% hit drawdown<=-8%")

    # 第一次命中 drawdown<=-8% 時，still holding 多少浮盈？
    print("\n=== ROUND_TRIP_FAILURE: first drawdown<=-8% hit, remaining profit at that point ===")
    for rt_sid in rt_ids:
        traj = by_stock[rt_sid]
        peak_idx = find_peak_index(traj)
        hit_idx = None
        for i in range(peak_idx, len(traj)):
            if traj[i]["drawdown_from_max"] <= -8.0:
                hit_idx = i
                break
        if hit_idx is not None:
            print(f"{rt_sid}: peak_return={traj[peak_idx]['current_return']:.2f}% "
                  f"first_dd8_at=day{traj[hit_idx]['day_offset']} "
                  f"return_at_that_point={traj[hit_idx]['current_return']:.2f}%")
        else:
            print(f"{rt_sid}: never hit drawdown<=-8% within observed window")


if __name__ == "__main__":
    main()
