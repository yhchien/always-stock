"""Phase 2.7 PART A: Continuation Persistence Research（2026-07-24）。

**純離線分析**，直接讀既有 `/tmp/continuation_quality_132_raw.json`（Phase 2.6
132-stock continuation quality 資料，已含 7 個 observation offset 的
`continuation_quality_state`），不重新查資料庫、不重跑 pipeline、不修改既有
`continuation_quality_state` 規則或門檻。

核心問題：「單日 AT_RISK/FAILED 警報」跟「持續性的惡化」，兩者的區分力有沒有
差異？

新增 4 個 research-only 衍生欄位（純粹從既有 7-offset 狀態序列推算，不查任何
新資料）：
    at_risk_plus_persistence / failed_persistence / recovery_speed /
    risk_episode_count（state_recurrence）

用法：
    python analyze_phase27_persistence.py
"""
from __future__ import annotations

import csv
import json
import statistics
from typing import Any, Dict, List, Optional

RAW_PATH = "/tmp/continuation_quality_132_raw.json"
OUT_CSV = "/tmp/phase27_persistence_132.csv"

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


def compute_persistence_metrics(traj: List[Dict[str, Any]]) -> Dict[str, Any]:
    states = [r["continuation_quality_state"] for r in traj]
    n = len(states)

    # ---- A. at_risk_plus_persistence：第一次進入 AT_RISK/FAILED 後，連續幾個
    # observation 仍在 {AT_RISK, FAILED}（含首次那一筆）----
    first_risk_idx = next((i for i, s in enumerate(states) if s in RISK_STATES), None)
    at_risk_persistence = 0
    if first_risk_idx is not None:
        at_risk_persistence = 1
        for i in range(first_risk_idx + 1, n):
            if states[i] in RISK_STATES:
                at_risk_persistence += 1
            else:
                break

    # ---- B. failed_persistence：第一次進入 FAILED 後，連續幾個 observation
    # 仍是 FAILED（嚴格，掉回 AT_RISK 也算中斷）----
    first_failed_idx = next((i for i, s in enumerate(states) if s == "FAILED"), None)
    failed_persistence = 0
    if first_failed_idx is not None:
        failed_persistence = 1
        for i in range(first_failed_idx + 1, n):
            if states[i] == "FAILED":
                failed_persistence += 1
            else:
                break

    # ---- C. recovery_speed：第一次進入 AT_RISK/FAILED 之後，幾個 observation
    # 才回到 HEALTHY/CAUTION ----
    recovery_speed = "N/A"  # 從未進入風險狀態
    if first_risk_idx is not None:
        recovered_at = next((i for i in range(first_risk_idx + 1, n) if states[i] in ("HEALTHY", "CAUTION")), None)
        if recovered_at is None:
            recovery_speed = "NOT_RECOVERED_BY_DAY10"
        else:
            gap = recovered_at - first_risk_idx
            if gap == 1:
                recovery_speed = "RECOVERED_NEXT"
            elif gap == 2:
                recovery_speed = "RECOVERED_WITHIN_2"
            else:
                recovery_speed = "RECOVERED_LATER"

    # ---- D. risk_episode_count：風險狀態反覆出現次數（中間須有 HEALTHY/CAUTION
    # 隔開才算新一輪 episode）----
    episodes = 0
    in_episode = False
    for s in states:
        if s in RISK_STATES:
            if not in_episode:
                episodes += 1
                in_episode = True
        else:
            in_episode = False

    return {
        "at_risk_plus_persistence": at_risk_persistence,
        "failed_persistence": failed_persistence,
        "recovery_speed": recovery_speed,
        "risk_episode_count": episodes,
        "first_risk_offset": OFFSETS[first_risk_idx] if first_risk_idx is not None else None,
        "first_risk_return": traj[first_risk_idx]["current_return"] if first_risk_idx is not None else None,
        "first_failed_offset": OFFSETS[first_failed_idx] if first_failed_idx is not None else None,
        "first_failed_return": traj[first_failed_idx]["current_return"] if first_failed_idx is not None else None,
    }


def main() -> None:
    by_stock = load_trajectories()
    rows_out = []
    for sid, traj in by_stock.items():
        metrics = compute_persistence_metrics(traj)
        rows_out.append({
            "stock_id": sid,
            "outcome_group": traj[0]["outcome_group"],
            "future_return_10d": traj[0]["future_return_10d"],
            **metrics,
        })

    columns = [
        "stock_id", "outcome_group", "future_return_10d",
        "first_risk_offset", "first_risk_return",
        "at_risk_plus_persistence",
        "first_failed_offset", "first_failed_return", "failed_persistence",
        "recovery_speed", "risk_episode_count",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print(f"wrote {len(rows_out)} rows -> {OUT_CSV}")

    losers = [r for r in rows_out if r["outcome_group"] == "BIG_LOSER"]
    winners = [r for r in rows_out if r["outcome_group"] == "WINNER"]
    n_l, n_w = len(losers), len(winners)

    def pct(cond, rows):
        c = sum(1 for r in rows if cond(r))
        return c, len(rows), round(100 * c / len(rows), 1) if rows else None

    print(f"\n{'metric':45s} {'BIG_LOSER':>18s} {'WINNER':>18s}")

    def row(label, cond):
        cl, nl, pl = pct(cond, losers)
        cw, nw, pw = pct(cond, winners)
        print(f"{label:45s} {cl:>3d}/{nl:<3d} ({pl:>5.1f}%) {cw:>3d}/{nw:<3d} ({pw:>5.1f}%)")

    row("ever AT_RISK+", lambda r: r["at_risk_plus_persistence"] >= 1)
    row("AT_RISK+ persistence >=2", lambda r: r["at_risk_plus_persistence"] >= 2)
    row("AT_RISK+ persistence >=3", lambda r: r["at_risk_plus_persistence"] >= 3)
    row("ever FAILED", lambda r: r["failed_persistence"] >= 1)
    row("FAILED persistence >=2", lambda r: r["failed_persistence"] >= 2)
    row("FAILED persistence >=3", lambda r: r["failed_persistence"] >= 3)
    row("recovered next observation", lambda r: r["recovery_speed"] == "RECOVERED_NEXT")
    row("recovered within 2 observations", lambda r: r["recovery_speed"] in ("RECOVERED_NEXT", "RECOVERED_WITHIN_2"))
    row("not recovered by Day10", lambda r: r["recovery_speed"] == "NOT_RECOVERED_BY_DAY10")
    row("multiple risk episodes (>=2)", lambda r: r["risk_episode_count"] >= 2)

    print("\n=== BIG_LOSER: average return at various detection points ===")
    for label, key_offset, key_return, min_persist in (
        ("first AT_RISK+ (persistence>=1)", "first_risk_offset", "first_risk_return", 1),
        ("first FAILED (persistence>=1)", "first_failed_offset", "first_failed_return", 1),
    ):
        vals = [r[key_return] for r in losers if r[key_return] is not None]
        print(f"{label}: n={len(vals)} mean_return={statistics.mean(vals):.2f}%" if vals else f"{label}: n=0")

    # persistence>=2 / >=3 的觸發報酬（用 first_risk_return 為準，若 persistence 不足則不列入）
    for min_p in (2, 3):
        vals = [r["first_risk_return"] for r in losers if r["at_risk_plus_persistence"] >= min_p and r["first_risk_return"] is not None]
        n_detected_before_neg10 = sum(1 for v in vals if v > -10)
        print(f"AT_RISK+ persistence>={min_p}: n={len(vals)}/{n_l}, mean_return={statistics.mean(vals):.2f}%" if vals else f"AT_RISK+ persistence>={min_p}: n=0")
        if vals:
            print(f"  detected before -10%: {n_detected_before_neg10}/{len(vals)} = {100*n_detected_before_neg10/len(vals):.1f}%")

    with open("/tmp/phase27_persistence_raw.json", "w", encoding="utf-8") as f:
        json.dump(rows_out, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
