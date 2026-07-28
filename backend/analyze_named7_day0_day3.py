"""7 檔具名案例 Day0~Day3 CSV 產出（純離線分析，讀 /tmp/phase26_named7_snapshots.json）。

用法：
    python analyze_named7_day0_day3.py
"""
from __future__ import annotations

import csv
import json

SNAPSHOT_PATH = "/tmp/phase26_named7_snapshots.json"
OUT_CSV = "/tmp/named_7_stocks_day0_day3.csv"

COLUMNS = [
    "date", "stock_id", "day_offset", "outcome",
    "close", "return_1d",
    "rs_market_percentile_20d", "peer_rs_percentile_20d", "rs_rank_change_5d",
    "institution_flow_1d", "institution_flow_3d",
    "volume_ratio",
    "internal_role", "momentum_freshness",
]

WINNERS = {"8039", "6505", "6414", "1810"}
LOSERS = {"7610", "8033", "6226"}


def main() -> None:
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshots = json.load(f)

    rows = []
    for sid, entry in snapshots.items():
        outcome = "WINNER" if sid in WINNERS else ("LOSER" if sid in LOSERS else "?")
        for day_label, day in sorted(entry.get("days", {}).items(), key=lambda kv: int(kv[0][3:])):
            rows.append({
                "date": day.get("date"),
                "stock_id": sid,
                "day_offset": int(day_label[3:]),
                "outcome": outcome,
                "close": day.get("close_1d"),
                "return_1d": day.get("price_change_1d"),
                "rs_market_percentile_20d": day.get("rs_market_percentile_20d"),
                "peer_rs_percentile_20d": day.get("peer_rs_percentile_20d"),
                "rs_rank_change_5d": day.get("rs_rank_improvement_5d"),
                "institution_flow_1d": day.get("total_institution_flow_1d"),
                "institution_flow_3d": day.get("total_institution_flow_3d"),
                "volume_ratio": day.get("volume_1d_to_5d_ratio"),
                "internal_role": day.get("role"),
                "momentum_freshness": day.get("momentum_freshness"),
            })

    rows.sort(key=lambda r: (r["stock_id"], r["day_offset"]))

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"wrote {len(rows)} rows -> {OUT_CSV}")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
