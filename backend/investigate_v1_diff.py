"""一次性 root-cause investigation 腳本（唯讀，不寫入任何 DB，不修改任何策略邏輯）：
比較 fishtail_backtest/daily_data.csv（沙盒凍結資料）vs 目前 production DB（用已修好的
resolve_fishtail_universe/build_daily_evidence 重建），逐 cohort、逐 evidence 欄位 diff。
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

from app.database import SessionLocal
from app.signals.shadow_portfolio import (
    V1_STRATEGY_PARAMS,
    build_daily_evidence,
    filter_out_etfs,
    generate_entry_signal,
    generate_exit_signal,
    resolve_fishtail_universe,
)

START = date(2026, 8, 7)
END = date(2026, 9, 4)
SANDBOX_CSV = Path("/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/daily_data.csv")
OUT_DIR = Path("/tmp")


def load_sandbox_rows():
    with open(SANDBOX_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_current_rows(db):
    """重建目前 production 資料在 [START, END] 每一天、每個 cohort 的 evidence row，
    輸出格式盡量對齊沙盒 daily_data.csv 的欄位，方便直接 diff。"""
    all_cohorts = {}
    d = START
    while d <= END:
        universe = filter_out_etfs(db, resolve_fishtail_universe(db, target_date=d))
        for sid, (sid_, name, fsd) in universe.items():
            all_cohorts[(sid, fsd)] = name
        d += timedelta(days=1)

    rows = []
    for (stock_id, first_seen_date), stock_name in all_cohorts.items():
        d = max(first_seen_date, START)
        while d <= END:
            ev = build_daily_evidence(
                db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date, target_date=d,
            )
            rows.append(
                {
                    "stock_id": stock_id,
                    "stock_name": stock_name,
                    "first_seen_date": first_seen_date.isoformat(),
                    "day_index": ev.day_index,
                    "trade_date": d.isoformat(),
                    "p3_selected_today": ev.p3_selected_today,
                    "hit_count_so_far": ev.hit_count_so_far,
                    "momentum_score": ev.momentum_score,
                    "p4_decision": ev.p4_decision or "",
                    "mark_to_market_return_pct": ev.mark_to_market_return_pct,
                    "is_official_exit_signal_day": ev.is_official_exit_signal_day,
                }
            )
            d += timedelta(days=1)
    return rows


def write_csv(rows, path):
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    sandbox_rows = load_sandbox_rows()
    print(f"sandbox rows: {len(sandbox_rows)}")
    sandbox_cohorts = {(r["stock_id"], r["first_seen_date"]) for r in sandbox_rows}
    print(f"sandbox cohorts: {len(sandbox_cohorts)}")
    print(f"sandbox unique stock_id: {len({r['stock_id'] for r in sandbox_rows})}")

    with SessionLocal() as db:
        current_rows = build_current_rows(db)
    print(f"current rows: {len(current_rows)}")
    current_cohorts = {(r["stock_id"], r["first_seen_date"]) for r in current_rows}
    print(f"current cohorts: {len(current_cohorts)}")
    print(f"current unique stock_id: {len({r['stock_id'] for r in current_rows})}")

    only_sandbox = sandbox_cohorts - current_cohorts
    only_current = current_cohorts - sandbox_cohorts
    both = sandbox_cohorts & current_cohorts

    print(f"\ncohorts only in sandbox: {len(only_sandbox)}")
    print(f"cohorts only in current: {len(only_current)}")
    print(f"cohorts in both: {len(both)}")

    write_csv(current_rows, OUT_DIR / "current_daily_data.csv")
    with open(OUT_DIR / "cohort_diff.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "only_sandbox": sorted(list(only_sandbox)),
                "only_current": sorted(list(only_current)),
                "both_count": len(both),
            },
            f, ensure_ascii=False, indent=2,
        )
    print(f"\nWrote {OUT_DIR / 'current_daily_data.csv'} and {OUT_DIR / 'cohort_diff.json'}")


if __name__ == "__main__":
    main()
