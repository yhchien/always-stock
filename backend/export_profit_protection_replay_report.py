"""Export isolated profit-protection replay operations and comparison metrics."""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import (  # noqa: E402
    ShadowCompletedTrade,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
)
from app.signals.shadow_portfolio import (  # noqa: E402
    STRATEGY_VERSION_PROFIT_PROTECTION_202610,
)
from backfill_shadow_portfolio_replay import compute_full_metrics  # noqa: E402


CONTROL_VERSION = "v1_frozen"
EXPERIMENT_VERSION = STRATEGY_VERSION_PROFIT_PROTECTION_202610
REPLAY_DB_ROOT = Path(os.getenv("PROFIT_REPLAY_DB_ROOT", "/private/tmp/profit-protection-202610"))
WINDOWS = {
    "history_2026-08-01_2026-09-07": (
        date(2026, 8, 1),
        date(2026, 9, 7),
        REPLAY_DB_ROOT / "history.db",
    ),
    "forward_2026-09-07_2026-10-01": (
        date(2026, 9, 7),
        date(2026, 10, 1),
        REPLAY_DB_ROOT / "forward.db",
    ),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if value is not None else ""


def _write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> None:
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _session_for(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path.resolve()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    return sessionmaker(bind=engine)()


def main() -> int:
    project_root = BACKEND_DIR.parent
    output_dir = project_root / "docs" / "reports" / "profit_protection_202610"
    output_dir.mkdir(parents=True, exist_ok=True)

    operation_rows: List[Dict[str, Any]] = []
    order_rows: List[Dict[str, Any]] = []
    trade_rows: List[Dict[str, Any]] = []
    comparison_rows: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    for period, (start_date, end_date, relative_db_path) in WINDOWS.items():
        db = _session_for(project_root / relative_db_path)
        try:
            for strategy_label, strategy_version in (
                ("control", CONTROL_VERSION),
                ("profit_protection", EXPERIMENT_VERSION),
            ):
                decisions = (
                    db.query(ShadowStrategyDailyDecision)
                    .filter(
                        ShadowStrategyDailyDecision.strategy_version == strategy_version,
                        ShadowStrategyDailyDecision.trade_date >= start_date,
                        ShadowStrategyDailyDecision.trade_date <= end_date,
                    )
                    .order_by(
                        ShadowStrategyDailyDecision.trade_date.asc(),
                        ShadowStrategyDailyDecision.stock_id.asc(),
                    )
                    .all()
                )
                for row in decisions:
                    evidence = row.continuation_evidence or {}
                    operation_rows.append(
                        {
                            "period": period,
                            "strategy": strategy_label,
                            "strategy_version": strategy_version,
                            "trade_date": row.trade_date,
                            "stock_id": row.stock_id,
                            "stock_name": row.stock_name,
                            "action": row.action,
                            "action_reason": row.action_reason,
                            "scheduled_execution_date": row.scheduled_execution_date,
                            "actual_position_return_pct": row.actual_position_return,
                            "momentum_score": row.momentum_score,
                            "p4_decision": row.p4_decision,
                            "p3_selected_today": row.p3_selected_today,
                            "hit_count": row.hit_count,
                            "episode_price_return_pct": row.episode_price_return_pct,
                            "episode_low_return_pct": row.episode_low_return_pct,
                            "continuation_evidence_count": row.continuation_evidence_count,
                            "continuation_phase": row.continuation_phase,
                            "continuation_rank": row.continuation_rank,
                            "profit_protection": _json(evidence.get("profit_protection")),
                        }
                    )

                orders = (
                    db.query(ShadowStrategyOrder)
                    .filter(
                        ShadowStrategyOrder.strategy_version == strategy_version,
                        ShadowStrategyOrder.signal_date >= start_date,
                        ShadowStrategyOrder.signal_date <= end_date,
                    )
                    .order_by(
                        ShadowStrategyOrder.signal_date.asc(),
                        ShadowStrategyOrder.id.asc(),
                    )
                    .all()
                )
                for row in orders:
                    order_rows.append(
                        {
                            "period": period,
                            "strategy": strategy_label,
                            "strategy_version": strategy_version,
                            "order_id": row.id,
                            "signal_date": row.signal_date,
                            "scheduled_execution_date": row.scheduled_execution_date,
                            "status": row.status,
                            "action": row.action,
                            "stock_id": row.stock_id,
                            "stock_name": row.stock_name,
                            "reason": row.reason,
                            "entry_pattern": row.entry_pattern,
                            "units": row.units,
                            "planned_amount": row.planned_amount,
                            "execution_price": row.execution_price,
                            "signal_snapshot": _json(row.signal_snapshot),
                        }
                    )

                trades = (
                    db.query(ShadowCompletedTrade)
                    .filter(
                        ShadowCompletedTrade.strategy_version == strategy_version,
                        ShadowCompletedTrade.exit_execution_date >= start_date,
                        ShadowCompletedTrade.exit_execution_date <= end_date,
                    )
                    .order_by(
                        ShadowCompletedTrade.exit_execution_date.asc(),
                        ShadowCompletedTrade.id.asc(),
                    )
                    .all()
                )
                for row in trades:
                    trade_rows.append(
                        {
                            "period": period,
                            "strategy": strategy_label,
                            "strategy_version": strategy_version,
                            "trade_id": row.id,
                            "stock_id": row.stock_id,
                            "stock_name": row.stock_name,
                            "entry_type": row.entry_type,
                            "entry_signal_date": row.entry_signal_date,
                            "entry_execution_date": row.entry_execution_date,
                            "entry_price": row.entry_price,
                            "exit_reason": row.exit_reason,
                            "exit_signal_date": row.exit_signal_date,
                            "exit_execution_date": row.exit_execution_date,
                            "exit_price": row.exit_price,
                            "realized_return_pct": row.realized_return_pct,
                            "realized_pnl": row.realized_pnl,
                            "holding_days": row.holding_days,
                            "followed_by_rotation": row.followed_by_rotation,
                        }
                    )

                metrics = compute_full_metrics(
                    db,
                    strategy_version=strategy_version,
                    initial_capital=600000.0,
                )
                comparison_rows.append(
                    {
                        "period": period,
                        "strategy": strategy_label,
                        "strategy_version": strategy_version,
                        "gross_return_pct": metrics["gross_return_pct"],
                        "final_equity": metrics["final_equity"],
                        "trade_count": metrics["trade_count"],
                        "win_rate_pct": metrics["win_rate_pct"],
                        "profit_factor": metrics["profit_factor"],
                        "max_drawdown_pct": metrics["max_drawdown_pct"],
                        "avg_trade_pct": metrics["avg_trade_pct"],
                        "largest_winner_pct": metrics["largest_winner_pct"],
                        "largest_loser_pct": metrics["largest_loser_pct"],
                    }
                )
                summary.setdefault(period, {})[strategy_label] = comparison_rows[-1]
        finally:
            db.close()

    _write_csv(
        output_dir / "daily_decisions_all.csv",
        operation_rows,
        [
            "period", "strategy", "strategy_version", "trade_date", "stock_id", "stock_name",
            "action", "action_reason", "scheduled_execution_date",
            "actual_position_return_pct", "momentum_score", "p4_decision",
            "p3_selected_today", "hit_count", "episode_price_return_pct",
            "episode_low_return_pct", "continuation_evidence_count",
            "continuation_phase", "continuation_rank", "profit_protection",
        ],
    )
    _write_csv(output_dir / "orders_all.csv", order_rows, list(order_rows[0].keys()))
    _write_csv(output_dir / "completed_trades_all.csv", trade_rows, list(trade_rows[0].keys()))
    _write_csv(output_dir / "comparison.csv", comparison_rows, list(comparison_rows[0].keys()))
    _write_csv(
        output_dir / "profit_protection_daily_decisions.csv",
        [row for row in operation_rows if row["strategy"] == "profit_protection"],
        [
            "period", "strategy", "strategy_version", "trade_date", "stock_id", "stock_name",
            "action", "action_reason", "scheduled_execution_date",
            "actual_position_return_pct", "momentum_score", "p4_decision",
            "p3_selected_today", "hit_count", "episode_price_return_pct",
            "episode_low_return_pct", "continuation_evidence_count",
            "continuation_phase", "continuation_rank", "profit_protection",
        ],
    )
    _write_csv(
        output_dir / "profit_protection_orders.csv",
        [row for row in order_rows if row["strategy"] == "profit_protection"],
        list(order_rows[0].keys()),
    )
    _write_csv(
        output_dir / "profit_protection_trades.csv",
        [row for row in trade_rows if row["strategy"] == "profit_protection"],
        list(trade_rows[0].keys()),
    )
    (output_dir / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        """# Profit Protection 2026-10 Replay

資料來自隔離 SQLite 回放，不是正式資料庫。

- daily_decisions_all.csv：兩段期間、控制組與停利版的每日每檔決策。
- orders_all.csv：所有 BUY/ADD/SELL 訊號及其成交狀態。
- completed_trades_all.csv：已完成交易、進出場價格與實現損益。
- comparison.csv / comparison.json：兩段回測績效比較。
- profit_protection 欄位保存停利評估的獲利與弱化訊號。
""",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
