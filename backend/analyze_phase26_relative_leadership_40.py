"""Phase 2.6 Relative Leadership 40-stock validation（2026-07-24）。

延續 20-stock 驗證（`analyze_phase26_relative_leadership.py`），擴大到 20 WINNER +
20 BIG_LOSER（matched sampling，`/tmp/phase26_matched_40.json`）。重用同一套
peer scope 解析（canonical taxonomy hierarchy）與 ranking metric（20 日報酬），
門檻完全不變。**只做研究，不修改 production。**

新增：對所有 `peer_rank_direction=IMPROVING` 的股票，比較
`peer_rank_percentile_day_minus_4` → `peer_rank_percentile_day0`，descriptive
分類 ESTABLISHED_ACCELERATION（Day-4 已在前段）vs SUDDEN_RISER（Day-4 不在前段）。

用法：
    python analyze_phase26_relative_leadership_40.py
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from datetime import date
from typing import Any, Dict, List

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import SecurityClassification

from analyze_phase26_relative_leadership import (
    REPLAY_617_PATH,
    RETURN_LOOKBACK_DAYS,
    TOP20_PCT_THRESHOLD,
    classify_direction,
    compute_peer_ranks_for_window,
    load_cohort,
    resolve_peer_group,
)

MATCHED_40_PATH = "/tmp/phase26_matched_40.json"
OUT_CSV = "/tmp/phase26_relative_leadership_matched40.csv"

# ESTABLISHED_ACCELERATION vs SUDDEN_RISER 分界（descriptive only，不接 production）
ESTABLISHED_DAY_MINUS_4_PCT_MAX = 0.20  # Day-4 percentile <= 此值 → 5 天前已在 peer 前段


def classify_improving_subtype(pct_day_minus_4, pct_day0) -> str:
    if pct_day_minus_4 is None or pct_day0 is None:
        return "UNKNOWN"
    if pct_day_minus_4 <= ESTABLISHED_DAY_MINUS_4_PCT_MAX:
        return "ESTABLISHED_ACCELERATION"
    return "SUDDEN_RISER"


def main() -> None:
    cohort = load_cohort()
    with open(MATCHED_40_PATH, encoding="utf-8") as f:
        matched = json.load(f)
    all_stock_ids = matched["losers"] + matched["winners"]
    outcome = {sid: "BIG_LOSER" for sid in matched["losers"]}
    outcome.update({sid: "WINNER" for sid in matched["winners"]})
    print(f"validating {len(matched['losers'])} BIG_LOSER + {len(matched['winners'])} WINNER = {len(all_stock_ids)} stocks")

    db = SessionLocal()
    try:
        anchor_end = date(2026, 7, 22)
        all_days = get_recent_trade_dates(db, anchor_end, 130)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")

        rows_out = []
        for sid in all_stock_ids:
            rec = cohort[sid]
            day0 = date.fromisoformat(rec["catch_date"])
            peer_info = resolve_peer_group(db, sid)
            peer_ranks = {}
            if peer_info["peer_scope"] != "UNAVAILABLE":
                peer_ranks = compute_peer_ranks_for_window(
                    db, sid, peer_info["peer_ids"], day0, all_days, day_index
                )

            ranks_5d = [peer_ranks.get(off, {}).get("peer_rank") for off in (-4, -3, -2, -1, 0)]
            pct_5d = [peer_ranks.get(off, {}).get("peer_rank_percentile") for off in (-4, -3, -2, -1, 0)]
            valid_pct = [p for p in pct_5d if p is not None]
            direction = classify_direction(ranks_5d)
            improving_subtype = (
                classify_improving_subtype(pct_5d[0], pct_5d[4]) if direction == "IMPROVING" else "N/A"
            )

            row = {
                "stock_id": sid,
                "outcome_group": outcome[sid],
                "future_return_10d": rec["forward_return_pct"],
                "peer_scope": peer_info["peer_scope"],
                "peer_count": peer_ranks.get(0, {}).get("peer_count"),
                "peer_rank_percentile_day0": pct_5d[4],
                "peer_rank_percentile_day_minus_4": pct_5d[0],
                "peer_top20_days_5d": sum(1 for p in valid_pct if p <= TOP20_PCT_THRESHOLD),
                "peer_rank_median_5d": statistics.median([r for r in ranks_5d if r is not None]) if any(r is not None for r in ranks_5d) else None,
                "peer_rank_direction": direction,
                "improving_subtype": improving_subtype,
            }
            rows_out.append(row)
            print(f"{sid} ({outcome[sid]}, ret={rec['forward_return_pct']:.1f}%) scope={peer_info['peer_scope']} "
                  f"pct(day-4={pct_5d[0]}, day0={pct_5d[4]}) direction={direction} subtype={improving_subtype}")
    finally:
        db.close()

    columns = [
        "stock_id", "outcome_group", "future_return_10d", "peer_scope", "peer_count",
        "peer_rank_percentile_day0", "peer_rank_percentile_day_minus_4",
        "peer_top20_days_5d", "peer_rank_median_5d", "peer_rank_direction", "improving_subtype",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print(f"\nwrote {len(rows_out)} rows -> {OUT_CSV}")

    # ---- summary stats ----
    def valid(rows, key):
        return [r[key] for r in rows if r[key] is not None]

    losers = [r for r in rows_out if r["outcome_group"] == "BIG_LOSER"]
    winners = [r for r in rows_out if r["outcome_group"] == "WINNER"]
    winners_valid = [r for r in winners if r["peer_scope"] != "UNAVAILABLE"]
    losers_valid = [r for r in losers if r["peer_scope"] != "UNAVAILABLE"]

    print("\n===== SUMMARY =====")
    print(f"BIG_LOSER n={len(losers)} (valid peer scope={len(losers_valid)})")
    print(f"WINNER n={len(winners)} (valid peer scope={len(winners_valid)})")

    for key in ("peer_rank_percentile_day0", "peer_top20_days_5d", "peer_rank_median_5d"):
        l_vals = valid(losers_valid, key)
        w_vals = valid(winners_valid, key)
        print(f"{key}: LOSER median={statistics.median(l_vals):.3f} (n={len(l_vals)}) | "
              f"WINNER median={statistics.median(w_vals):.3f} (n={len(w_vals)})")

    print("\ndirection distribution:")
    print("  LOSER:", dict(Counter(r["peer_rank_direction"] for r in losers)))
    print("  WINNER:", dict(Counter(r["peer_rank_direction"] for r in winners)))

    print("\nimproving subtype distribution (only within IMPROVING stocks):")
    loser_improving = [r for r in losers if r["peer_rank_direction"] == "IMPROVING"]
    winner_improving = [r for r in winners if r["peer_rank_direction"] == "IMPROVING"]
    print(f"  LOSER (n={len(loser_improving)}):", dict(Counter(r["improving_subtype"] for r in loser_improving)))
    print(f"  WINNER (n={len(winner_improving)}):", dict(Counter(r["improving_subtype"] for r in winner_improving)))


if __name__ == "__main__":
    main()
