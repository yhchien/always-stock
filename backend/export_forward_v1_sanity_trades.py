"""輸出 `forward_v1_sanity_trades.csv`（spec Part 26），供人工 spot-check（Part 27~30）。

唯讀查詢，逐筆列出一個 `strategy_version` 的所有已成交訂單（BUY/ADD/SELL），欄位對齊
spec §26 要求（signal_date/execution_date/action/price/shares/cash 前後/均價前後/
actual_position_return/tracking_return/p4_decision/realized_pnl）。

用法：
    python3 export_forward_v1_sanity_trades.py --strategy-version=FORWARD_V1_202609 \
        --out=../forward_v1_sanity_trades.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list) -> tuple[str, str]:
    strategy_version = None
    out_path = None
    for arg in argv:
        if arg.startswith("--strategy-version="):
            strategy_version = arg.split("=", 1)[1].strip()
        elif arg.startswith("--out="):
            out_path = arg.split("=", 1)[1].strip()
    if not strategy_version:
        raise SystemExit("必須帶 --strategy-version=XXX")
    if not out_path:
        out_path = f"forward_v1_sanity_trades_{strategy_version}.csv"
    return strategy_version, out_path


def main(argv: list) -> int:
    strategy_version, out_path = _parse_args(argv)

    from app.database import SessionLocal
    from app.models import ShadowStrategyDailyDecision, ShadowStrategyOrder

    rows = []
    running_cash = None
    with SessionLocal() as db:
        orders = (
            db.query(ShadowStrategyOrder)
            .filter(
                ShadowStrategyOrder.strategy_version == strategy_version,
                ShadowStrategyOrder.status == "EXECUTED",
            )
            .order_by(ShadowStrategyOrder.scheduled_execution_date.asc(), ShadowStrategyOrder.id.asc())
            .all()
        )

        # cash_before/cash_after 逐筆對照 ShadowPortfolioDailySnapshot（成交當天收盤後的
        # 快照值）——同一天可能有多筆成交，這裡只能給「當天收盤後」的 cash，無法逐筆重建
        # 盤中每一步驟的現金軌跡；人工 spot-check 時請以 daily snapshot 為準對照。
        from app.models import ShadowPortfolioDailySnapshot

        snapshot_cash_by_date = {
            s.trade_date: s.cash
            for s in db.query(ShadowPortfolioDailySnapshot)
            .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
            .all()
        }

        # 對應同一天、同一檔股票的決策紀錄，取得 actual_position_return（成交當下的
        # 決策快照，不是事後回算）。
        decisions_by_key = {
            (d.stock_id, d.trade_date): d
            for d in db.query(ShadowStrategyDailyDecision)
            .filter(ShadowStrategyDailyDecision.strategy_version == strategy_version)
            .all()
        }

        for o in orders:
            decision = decisions_by_key.get((o.stock_id, o.signal_date))
            snapshot = o.signal_snapshot or {}
            rows.append(
                {
                    "signal_date": o.signal_date.isoformat(),
                    "execution_date": o.scheduled_execution_date.isoformat(),
                    "stock_id": o.stock_id,
                    "stock_name": o.stock_name,
                    "action": o.action,
                    "signal_reason": o.reason,
                    "entry_pattern": o.entry_pattern,
                    "execution_price": o.execution_price,
                    "planned_amount": o.planned_amount,
                    "units": o.units,
                    "day_index": snapshot.get("day_index"),
                    "hit_count_so_far": snapshot.get("hit_count_so_far"),
                    "momentum_score": snapshot.get("momentum_score") or (decision.momentum_score if decision else None),
                    "tracking_return_pct": snapshot.get("mark_to_market_return_pct")
                    or (decision.mark_to_market_return_pct if decision else None),
                    "p4_decision": snapshot.get("p4_decision") or (decision.p4_decision if decision else None),
                    "actual_position_return_on_signal_day": decision.actual_position_return if decision else None,
                    "cash_after_execution_date": snapshot_cash_by_date.get(o.scheduled_execution_date),
                }
            )

    if not rows:
        print(f"strategy_version={strategy_version} 沒有任何已成交訂單", file=sys.stderr)
        return 1

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"已輸出 {len(rows)} 筆到 {out_path}")
    buy_count = sum(1 for r in rows if r["action"] == "BUY")
    add_count = sum(1 for r in rows if r["action"] == "ADD")
    sell_count = sum(1 for r in rows if r["action"] == "SELL")
    print(f"  BUY={buy_count} ADD={add_count} SELL={sell_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
