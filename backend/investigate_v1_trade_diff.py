"""Root-cause investigation：OLD sandbox v1 (28 trades) vs CURRENT production v1_frozen
(23 trades) 逐筆 outer join diff + TAKE_PROFIT 異常清單。唯讀，不寫入任何 DB，不修改任何
策略邏輯/參數。
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from app.database import SessionLocal
from app.models import DailyPrice, ShadowCompletedTrade
from app.signals.shadow_portfolio import build_daily_evidence

OUT_DIR = Path("/tmp")
SANDBOX_TRADES_CSV = Path(
    "/Users/brian.yh.chien/.gstack/projects/always-stock/fishtail_backtest/output/trades_v1_no_cost.csv"
)


def load_old_trades():
    with open(SANDBOX_TRADES_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_new_trades(db):
    rows = (
        db.query(ShadowCompletedTrade)
        .filter(ShadowCompletedTrade.strategy_version == "v1_frozen")
        .order_by(ShadowCompletedTrade.entry_execution_date.asc())
        .all()
    )
    out = []
    for r in rows:
        out.append(
            {
                "stock_id": r.stock_id, "stock_name": r.stock_name,
                "entry_signal_date": r.entry_signal_date.isoformat(),
                "entry_execution_date": r.entry_execution_date.isoformat(),
                "exit_signal_date": r.exit_signal_date.isoformat(),
                "exit_execution_date": r.exit_execution_date.isoformat(),
                "exit_reason": r.exit_reason,
                "entry_price": r.entry_price, "exit_price": r.exit_price,
                "realized_return_pct": r.realized_return_pct,
            }
        )
    return out


def key_of(t):
    return (t["stock_id"], t["entry_signal_date"])


def main():
    old = load_old_trades()
    with SessionLocal() as db:
        new = load_new_trades(db)

    old_by_key = {}
    for t in old:
        old_by_key.setdefault(key_of(t), []).append(t)
    new_by_key = {}
    for t in new:
        new_by_key.setdefault(key_of(t), []).append(t)

    old_keys = set(old_by_key.keys())
    new_keys = set(new_by_key.keys())
    only_old = old_keys - new_keys
    only_new = new_keys - old_keys
    both = old_keys & new_keys

    print(f"OLD trades: {len(old)}  NEW trades: {len(new)}")
    print(f"only_old keys: {len(only_old)}  only_new keys: {len(only_new)}  both keys: {len(both)}")

    diverged = []
    identical = []
    for k in sorted(both):
        o = old_by_key[k][0]
        n = new_by_key[k][0]
        same = (
            o["exit_signal_date"] == n["exit_signal_date"]
            and o["exit_reason"] == n["exit_reason"]
            and abs(float(o["realized_return_pct"]) - float(n["realized_return_pct"])) < 0.01
        )
        (identical if same else diverged).append((k, o, n))

    print(f"\nmatched entry, IDENTICAL result: {len(identical)}")
    print(f"matched entry, DIVERGED result: {len(diverged)}")
    for k, o, n in diverged:
        print(f"  {k}: OLD exit_sig={o['exit_signal_date']} reason={o['exit_reason']} return={o['realized_return_pct']}")
        print(f"       NEW exit_sig={n['exit_signal_date']} reason={n['exit_reason']} return={n['realized_return_pct']}")

    # ---- 針對 only_old / only_new，用目前修好的 evidence 引擎重建當天的完整欄位 ----
    def enrich(key, db):
        stock_id, signal_date_str = key
        signal_date = date.fromisoformat(signal_date_str)
        # 嘗試找出這個 stock 在這個時間點附近，目前資料庫認定的 first_seen_date
        from app.signals.shadow_portfolio import resolve_fishtail_universe
        universe = resolve_fishtail_universe(db, target_date=signal_date)
        first_seen = universe.get(stock_id, (None, None, None))[2]
        if first_seen is None:
            return {"stock_id": stock_id, "signal_date": signal_date_str, "note": "NOT_IN_CURRENT_UNIVERSE"}
        ev = build_daily_evidence(
            db, stock_id=stock_id, stock_name=universe[stock_id][1], first_seen_date=first_seen,
            target_date=signal_date,
        )
        return {
            "stock_id": stock_id, "signal_date": signal_date_str, "current_first_seen_date": first_seen.isoformat(),
            "day_index": ev.day_index, "hit_count_so_far": ev.hit_count_so_far, "momentum_score": ev.momentum_score,
            "p4_decision": ev.p4_decision, "mark_to_market_return_pct": ev.mark_to_market_return_pct,
        }

    print("\n=== only_old (in sandbox 28, missing from current 23) ===")
    only_old_report = []
    with SessionLocal() as db:
        for k in sorted(only_old):
            o = old_by_key[k][0]
            enriched = enrich(k, db)
            row = {
                "stock_id": o["stock_id"], "stock_name": o["stock_name"],
                "old_first_seen_date": o["signal_first_seen_date"], "old_entry_signal_date": o["entry_signal_date"],
                "old_day_index": o["entry_day_index"], "old_hit_count": o["entry_hit_count"],
                "old_momentum": o["entry_momentum"], "old_mark_to_market": o["entry_mark_to_market_return"],
                "old_p4_decision": o["entry_p4"], "old_entry_pattern": o["entry_type"],
                **{f"now_{k2}": v2 for k2, v2 in enriched.items() if k2 not in ("stock_id", "signal_date")},
            }
            only_old_report.append(row)
            print(f"  {o['stock_id']:6s} {o['stock_name']:6s} old_entry_sig={o['entry_signal_date']} "
                  f"old(day={o['entry_day_index']},hit={o['entry_hit_count']},mom={o['entry_momentum']},"
                  f"mtm={o['entry_mark_to_market_return']},p4={o['entry_p4']}) -> "
                  f"NOW(first_seen={enriched.get('current_first_seen_date','?')},"
                  f"day={enriched.get('day_index','?')},hit={enriched.get('hit_count_so_far','?')},"
                  f"mom={enriched.get('momentum_score','?')},mtm={enriched.get('mark_to_market_return_pct','?')},"
                  f"p4={enriched.get('p4_decision','?')}) {enriched.get('note','')}")

    print("\n=== only_new (in current 23, not in sandbox 28) ===")
    only_new_report = []
    with SessionLocal() as db:
        for k in sorted(only_new):
            n = new_by_key[k][0]
            row = {
                "stock_id": n["stock_id"], "stock_name": n["stock_name"],
                "entry_signal_date": n["entry_signal_date"], "entry_execution_date": n["entry_execution_date"],
                "exit_reason": n["exit_reason"], "realized_return_pct": n["realized_return_pct"],
            }
            only_new_report.append(row)
            print(f"  {n['stock_id']:6s} {n['stock_name']:6s} entry_sig={n['entry_signal_date']} "
                  f"exit_reason={n['exit_reason']} return={n['realized_return_pct']:.2f}%")

    with open(OUT_DIR / "v1_trade_diff.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "only_old": only_old_report, "only_new": only_new_report,
                "diverged": [{"key": list(k), "old": o, "new": n} for k, o, n in diverged],
                "identical_count": len(identical),
            },
            f, ensure_ascii=False, indent=2, default=str,
        )
    print(f"\nWrote {OUT_DIR / 'v1_trade_diff.json'}")


if __name__ == "__main__":
    main()
