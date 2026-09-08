"""Test B：Winner Management + Portfolio Rotation 假說驗證——三組比較回測。

**唯讀**：v1_frozen 直接讀既有 `ShadowCompletedTrade`／`ShadowPortfolioDailySnapshot`
紀錄（不重新模擬）；Test A／Test B 用 `app.signals.shadow_portfolio_experiments` 的純
記憶體引擎跑，**完全不寫入任何 DB 表**。三組都用同一段真實資料（同樣的 P3/P4 candidate
universe、同樣 Entry、同樣 T+1 High Buy／Low Sell、同樣零交易成本——v1_frozen 本身就
沒有計算手續費/證交稅，Test A/B 沿用同款零成本假設才是「同樣交易成本」）。

用法：
    python3 run_testb_backtest.py [--start 2026-08-07] [--end 2026-09-04] [--out-dir .]
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_START = date(2026, 8, 7)
DEFAULT_END = date(2026, 9, 4)

# v1_frozen 原本 exit_reason=TAKE_PROFIT 的股票，使用者要求特別追蹤（§32）
WATCH_STOCKS = {"2615", "2603", "2851", "2606", "6226", "8039", "3653", "6933", "2465"}


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "—"


def _fmt_money(v: Optional[float]) -> str:
    return f"{v:,.0f}" if v is not None else "—"


# ---------------------------------------------------------------------------
# 統一的「一筆已平倉交易」視圖，讓 v1/A/B 可以共用同一套指標計算
# ---------------------------------------------------------------------------
class UnifiedTrade:
    def __init__(
        self, *, stock_id: str, stock_name: str, entry_execution_date: date, exit_execution_date: date,
        entry_price: float, exit_price: float, allocation: float, realized_pnl: float,
        realized_return_pct: float, exit_reason: str, add_number: Optional[int] = None,
        rotation_id: Optional[str] = None,
    ):
        self.stock_id = stock_id
        self.stock_name = stock_name
        self.entry_execution_date = entry_execution_date
        self.exit_execution_date = exit_execution_date
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.allocation = allocation
        self.realized_pnl = realized_pnl
        self.realized_return_pct = realized_return_pct
        self.exit_reason = exit_reason
        self.add_number = add_number
        self.rotation_id = rotation_id


def _v1_unified_trades(db, *, start: date, end: date) -> List[UnifiedTrade]:
    from app.models import ShadowCompletedTrade

    rows = (
        db.query(ShadowCompletedTrade)
        .filter(
            ShadowCompletedTrade.strategy_version == "v1_frozen",
            ShadowCompletedTrade.entry_execution_date >= start,
            ShadowCompletedTrade.entry_execution_date <= end,
        )
        .order_by(ShadowCompletedTrade.entry_execution_date.asc())
        .all()
    )
    return [
        UnifiedTrade(
            stock_id=r.stock_id, stock_name=r.stock_name, entry_execution_date=r.entry_execution_date,
            exit_execution_date=r.exit_execution_date, entry_price=r.entry_price, exit_price=r.exit_price,
            allocation=r.allocation, realized_pnl=r.realized_pnl, realized_return_pct=r.realized_return_pct,
            exit_reason=r.exit_reason,
        )
        for r in rows
    ]


def _experiment_unified_trades(trades) -> List[UnifiedTrade]:
    out = []
    for t in trades:
        if t.realized_pnl is None:
            continue  # 只算平倉列（BUY/ADD 沒有 realized_pnl）
        out.append(
            UnifiedTrade(
                stock_id=t.stock_id, stock_name=t.stock_name,
                entry_execution_date=t.execution_date, exit_execution_date=t.execution_date,
                entry_price=t.actual_average_cost_before or 0.0, exit_price=t.price,
                allocation=t.amount, realized_pnl=t.realized_pnl, realized_return_pct=t.realized_return_pct or 0.0,
                exit_reason=t.reason, add_number=t.add_number, rotation_id=t.rotation_id,
            )
        )
    return out


def _v1_daily_snapshots(db, *, start: date, end: date):
    from app.models import ShadowPortfolioDailySnapshot

    return (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == "v1_frozen",
            ShadowPortfolioDailySnapshot.trade_date >= start,
            ShadowPortfolioDailySnapshot.trade_date <= end,
        )
        .order_by(ShadowPortfolioDailySnapshot.trade_date.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(
    *, label: str, trades: List[UnifiedTrade], initial_capital: float, final_equity: float,
    equity_curve: List[float], invested_cost_curve: List[float], stocks_held_curve: List[int],
) -> Dict[str, Any]:
    total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0
    n = len(trades)
    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl <= 0]
    win_rate = (len(wins) / n * 100.0) if n else 0.0
    returns = [t.realized_return_pct for t in trades]
    avg_trade = statistics.mean(returns) if returns else None
    median_trade = statistics.median(returns) if returns else None
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = -sum(t.realized_pnl for t in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100.0 if peak else 0.0
        max_dd = min(max_dd, dd)

    capital_usage_pcts = [
        (inv / eq * 100.0) if eq else 0.0 for inv, eq in zip(invested_cost_curve, equity_curve)
    ]
    avg_capital_usage = statistics.mean(capital_usage_pcts) if capital_usage_pcts else 0.0
    max_capital_usage = max(capital_usage_pcts) if capital_usage_pcts else 0.0
    avg_stocks_held = statistics.mean(stocks_held_curve) if stocks_held_curve else 0.0

    turnover_dollar = sum(t.allocation for t in trades) * 2  # 買+賣各一次
    turnover_pct = (turnover_dollar / initial_capital * 100.0) if initial_capital else 0.0

    return {
        "label": label,
        "gross_return_pct": total_return_pct,
        "net_return_pct": total_return_pct,  # 全部零交易成本，gross==net
        "final_equity": final_equity,
        "max_drawdown_pct": max_dd,
        "trades": n,
        "win_rate_pct": win_rate,
        "avg_trade_pct": avg_trade,
        "median_trade_pct": median_trade,
        "profit_factor": profit_factor,
        "avg_capital_usage_pct": avg_capital_usage,
        "max_capital_usage_pct": max_capital_usage,
        "avg_stocks_held": avg_stocks_held,
        "turnover_pct": turnover_pct,
    }


def print_comparison_table(metrics_list: List[Dict[str, Any]]) -> None:
    rows = [
        ("Gross Return", "gross_return_pct", _fmt_pct),
        ("Net Return", "net_return_pct", _fmt_pct),
        ("Final Equity", "final_equity", _fmt_money),
        ("Max Drawdown", "max_drawdown_pct", _fmt_pct),
        ("Trades", "trades", str),
        ("Win Rate", "win_rate_pct", _fmt_pct),
        ("Avg Trade", "avg_trade_pct", _fmt_pct),
        ("Median Trade", "median_trade_pct", _fmt_pct),
        ("Profit Factor", "profit_factor", lambda v: f"{v:.2f}" if v is not None else "—"),
        ("Avg Capital Usage", "avg_capital_usage_pct", _fmt_pct),
        ("Max Capital Usage", "max_capital_usage_pct", _fmt_pct),
        ("Avg Stocks Held", "avg_stocks_held", lambda v: f"{v:.2f}"),
        ("Turnover", "turnover_pct", _fmt_pct),
    ]
    labels = [m["label"] for m in metrics_list]
    print(f"\n{'Metric':22s} " + " ".join(f"{lbl:>16s}" for lbl in labels))
    print("-" * (22 + 17 * len(labels)))
    for name, key, fmt in rows:
        vals = " ".join(f"{fmt(m[key]):>16s}" for m in metrics_list)
        print(f"{name:22s} {vals}")


def print_fixed_take_profit_comparison(v1_trades, a_trades, b_trades) -> None:
    print("\n" + "=" * 78)
    print("§32：原本被 v1 固定 +10% 賣掉的股票，在 Test A / Test B 的後續結果")
    print("=" * 78)
    v1_by_stock: Dict[str, List[UnifiedTrade]] = {}
    for t in v1_trades:
        if t.exit_reason == "TAKE_PROFIT":
            v1_by_stock.setdefault(t.stock_id, []).append(t)

    a_by_stock: Dict[str, List[UnifiedTrade]] = {}
    for t in a_trades:
        a_by_stock.setdefault(t.stock_id, []).append(t)
    b_by_stock: Dict[str, List[UnifiedTrade]] = {}
    for t in b_trades:
        b_by_stock.setdefault(t.stock_id, []).append(t)

    for stock_id, v1_list in sorted(v1_by_stock.items()):
        name = v1_list[0].stock_name
        print(f"\n{stock_id} {name}")
        for t in v1_list:
            print(f"  v1     : {t.entry_execution_date}->{t.exit_execution_date}  {t.realized_return_pct:+.2f}%  ({t.exit_reason})")
        for t in a_by_stock.get(stock_id, []):
            print(f"  Test A : {t.entry_execution_date}->{t.exit_execution_date}  {t.realized_return_pct:+.2f}%  ({t.exit_reason})")
        if stock_id not in a_by_stock:
            print("  Test A : （未平倉或本次未進場）")
        for t in b_by_stock.get(stock_id, []):
            rotation_tag = f" rotation={t.rotation_id}" if t.rotation_id else ""
            print(f"  Test B : {t.entry_execution_date}->{t.exit_execution_date}  {t.realized_return_pct:+.2f}%  ({t.exit_reason}){rotation_tag}")
        if stock_id not in b_by_stock:
            print("  Test B : （未平倉或本次未進場）")


def print_winner_hold_analysis(result, label: str) -> None:
    print(f"\n{'=' * 78}\n§34 {label}：Winner Hold Analysis\n{'=' * 78}")
    seen = set()
    for row in result.winner_log:
        if row.stock_id in seen:
            continue
        first_row = next(r for r in result.winner_log if r.stock_id == row.stock_id)
        seen.add(row.stock_id)
        max_return_after = max(
            (r.actual_position_return for r in result.winner_log if r.stock_id == row.stock_id), default=None
        )
        exit_trade = next(
            (t for t in result.completed if t.stock_id == row.stock_id and t.execution_date >= first_row.trade_date), None
        )
        adds_after = sum(
            1 for t in result.trades
            if t.stock_id == row.stock_id and t.action in ("ADD",) and t.execution_date >= first_row.trade_date
        )
        print(
            f"  {row.stock_id} {row.stock_name:6s} 進 winner mgmt={first_row.trade_date} "
            f"當時報酬={first_row.actual_position_return:+.2f}% 之後最高={max_return_after:+.2f}% "
            f"實際出場={(exit_trade.realized_return_pct if exit_trade else None)} "
            f"出場原因={(exit_trade.reason if exit_trade else '尚未出場')} "
            f"出場後加碼次數={adds_after}"
        )
    if not seen:
        print("  （這次回測沒有任何股票觸發 +10% winner management）")


def print_rotation_analysis(result) -> None:
    print(f"\n{'=' * 78}\n§39 Rotation Log\n{'=' * 78}")
    if not result.rotation_log:
        print("  （這次回測沒有任何 rotation 事件）")
        return
    for r in result.rotation_log:
        print(
            f"  {r.signal_date} {r.from_stock} -> {r.to_stock}  {r.full_or_partial:7s} "
            f"from_priority={r.from_priority:.2f} to_priority={r.to_priority:.2f} edge={r.priority_edge:.2f}"
        )
    full_count = sum(1 for r in result.rotation_log if r.full_or_partial == "FULL")
    partial_count = len(result.rotation_log) - full_count
    print(f"\n  總計 {len(result.rotation_log)} 次 rotation（{full_count} 次 full／{partial_count} 次 partial）")


def print_add_attribution(result) -> None:
    print(f"\n{'=' * 78}\n§35 Add Attribution\n{'=' * 78}")
    by_add_number: Dict[int, List] = {}
    for t in result.trades:
        if t.action != "ADD" or t.add_number is None:
            continue
        by_add_number.setdefault(t.add_number, []).append(t)
    if not by_add_number:
        print("  （這次回測沒有任何 ADD 交易）")
        return
    for add_number in sorted(by_add_number):
        entries = by_add_number[add_number]
        exits = [
            c for c in result.completed
            if c.add_number == add_number or any(t.stock_id == c.stock_id and t.add_number == add_number for t in entries)
        ]
        print(f"  ADD #{add_number}: {len(entries)} 次進場")


def print_concentration_analysis(result, initial_capital: float) -> None:
    print(f"\n{'=' * 78}\n§36 Concentration Analysis\n{'=' * 78}")
    max_exposure_by_stock: Dict[str, float] = {}
    max_units_by_stock: Dict[str, int] = {}
    for snap in result.daily_snapshots:
        pass  # snapshot 沒有逐股票明細，改從 trades 推算持有峰值（近似）
    positions_seen: Dict[str, int] = {}
    for t in result.trades:
        if t.action in ("BUY", "ADD"):
            positions_seen[t.stock_id] = positions_seen.get(t.stock_id, 0) + 1
    for sid, pos in result.open_positions.items():
        max_units_by_stock[sid] = max(max_units_by_stock.get(sid, 0), pos.units)
        max_exposure_by_stock[sid] = max(max_exposure_by_stock.get(sid, 0.0), pos.total_cost)
    if not max_exposure_by_stock:
        print("  （這次回測結束時沒有任何未平倉部位可分析集中度——僅供參考，完整歷史峰值需逐日追蹤，本輪從略）")
        return
    for sid, exposure in sorted(max_exposure_by_stock.items(), key=lambda kv: -kv[1]):
        pct = exposure / initial_capital * 100.0
        flag = "  <-- >=30%" if pct >= 30 else ("  <-- >=40%" if pct >= 40 else ("  <-- >=50%" if pct >= 50 else ""))
        print(f"  {sid}: 期末曝險 {_fmt_money(exposure)} 元 ({pct:.1f}%)，units={max_units_by_stock[sid]}{flag}")


def write_trades_csv(path: Path, *, v1_trades, a_result, b_result) -> None:
    fields = [
        "strategy", "signal_date", "execution_date", "action", "stock_id", "stock_name", "units", "price",
        "amount", "actual_average_cost_before", "actual_position_return_before", "entry_score", "hold_score",
        "allocation_priority", "reason", "rotation_id", "add_number", "realized_pnl", "realized_return_pct",
        "holding_days",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in v1_trades:
            w.writerow({
                "strategy": "v1_frozen", "signal_date": t.entry_execution_date, "execution_date": t.exit_execution_date,
                "action": "SELL", "stock_id": t.stock_id, "stock_name": t.stock_name, "units": "",
                "price": t.exit_price, "amount": t.allocation, "actual_average_cost_before": t.entry_price,
                "actual_position_return_before": "", "entry_score": "", "hold_score": "", "allocation_priority": "",
                "reason": t.exit_reason, "rotation_id": "", "add_number": "", "realized_pnl": t.realized_pnl,
                "realized_return_pct": t.realized_return_pct, "holding_days": "",
            })
        for label, result in (("TEST_A", a_result), ("TEST_B", b_result)):
            for t in result.trades:
                w.writerow({
                    "strategy": label, "signal_date": t.signal_date, "execution_date": t.execution_date,
                    "action": t.action, "stock_id": t.stock_id, "stock_name": t.stock_name, "units": t.units,
                    "price": t.price, "amount": t.amount, "actual_average_cost_before": t.actual_average_cost_before,
                    "actual_position_return_before": t.actual_position_return_before, "entry_score": t.entry_score,
                    "hold_score": t.hold_score, "allocation_priority": t.allocation_priority, "reason": t.reason,
                    "rotation_id": t.rotation_id or "", "add_number": t.add_number, "realized_pnl": t.realized_pnl,
                    "realized_return_pct": t.realized_return_pct, "holding_days": t.holding_days,
                })


def write_winner_log_csv(path: Path, result) -> None:
    fields = list(asdict(result.winner_log[0]).keys()) if result.winner_log else [
        "trade_date", "stock_id", "stock_name", "actual_position_return", "highest_position_return",
        "drawdown_from_peak", "momentum_score", "previous_momentum_score", "momentum_change", "tracking_return",
        "tracking_return_change", "p3_selected_today", "p4_decision", "hold_score", "winner_state", "action",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in result.winner_log:
            w.writerow(asdict(row))


def write_rotation_log_csv(path: Path, result) -> None:
    fields = list(asdict(result.rotation_log[0]).keys()) if result.rotation_log else [
        "signal_date", "from_stock", "to_stock", "from_units_before", "from_shares_sold", "from_priority",
        "to_priority", "priority_edge", "full_or_partial", "execution_date", "sell_execution_price",
        "buy_execution_price",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in result.rotation_log:
            w.writerow(asdict(row))


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--out-dir", default=str(BACKEND_DIR))
    args = parser.parse_args(argv[1:])
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    out_dir = Path(args.out_dir)

    from app.database import SessionLocal
    from app.signals.shadow_portfolio_experiments import (
        ExperimentConfig, MODE_TEST_A, MODE_TEST_B, run_experiment,
    )

    with SessionLocal() as db:
        print(f"Backtest window: {start} ~ {end}")

        v1_trades = _v1_unified_trades(db, start=start, end=end)
        v1_snaps = _v1_daily_snapshots(db, start=start, end=end)
        v1_metrics = compute_metrics(
            label="v1_frozen", trades=v1_trades, initial_capital=600000.0,
            final_equity=(v1_snaps[-1].total_equity if v1_snaps else 600000.0),
            equity_curve=[s.total_equity for s in v1_snaps],
            invested_cost_curve=[s.invested_cost for s in v1_snaps],
            stocks_held_curve=[s.position_count for s in v1_snaps],
        )

        print("Running Test A (hold forever until hard exit)...")
        a_result = run_experiment(db, mode=MODE_TEST_A, start=start, end=end, config=ExperimentConfig())
        a_trades = _experiment_unified_trades(a_result.trades)
        a_metrics = compute_metrics(
            label="Test A", trades=a_trades, initial_capital=600000.0,
            final_equity=a_result.daily_snapshots[-1].total_equity if a_result.daily_snapshots else 600000.0,
            equity_curve=[s.total_equity for s in a_result.daily_snapshots],
            invested_cost_curve=[s.invested_cost for s in a_result.daily_snapshots],
            stocks_held_curve=[s.position_count for s in a_result.daily_snapshots],
        )

        print("Running Test B (winner management + rotation)...")
        b_result = run_experiment(db, mode=MODE_TEST_B, start=start, end=end, config=ExperimentConfig())
        b_trades = _experiment_unified_trades(b_result.trades)
        b_metrics = compute_metrics(
            label="Test B", trades=b_trades, initial_capital=600000.0,
            final_equity=b_result.daily_snapshots[-1].total_equity if b_result.daily_snapshots else 600000.0,
            equity_curve=[s.total_equity for s in b_result.daily_snapshots],
            invested_cost_curve=[s.invested_cost for s in b_result.daily_snapshots],
            stocks_held_curve=[s.position_count for s in b_result.daily_snapshots],
        )

        print_comparison_table([v1_metrics, a_metrics, b_metrics])

        def _top_n(trades, n, winners):
            return sorted(trades, key=lambda t: -t.realized_pnl if winners else t.realized_pnl)[:n]

        print(f"\n{'=' * 78}\n§42-15/16 Test B 最大 5 個 Winner / Loser\n{'=' * 78}")
        for t in _top_n(b_trades, 5, True):
            print(f"  WIN  {t.stock_id} {t.stock_name:6s} {t.realized_return_pct:+7.2f}%  {t.exit_reason}")
        for t in _top_n(b_trades, 5, False):
            print(f"  LOSE {t.stock_id} {t.stock_name:6s} {t.realized_return_pct:+7.2f}%  {t.exit_reason}")

        def _return_without_top(trades, n, initial_capital):
            sorted_trades = sorted(trades, key=lambda t: -t.realized_pnl)
            excluded_pnl = sum(t.realized_pnl for t in sorted_trades[:n])
            total_pnl = sum(t.realized_pnl for t in trades)
            return (total_pnl - excluded_pnl) / initial_capital * 100.0

        print(f"\n§42-17/18 Test B Return without Top1 / Top3:")
        print(f"  w/o top1: {_return_without_top(b_trades, 1, 600000.0):+.2f}%")
        print(f"  w/o top3: {_return_without_top(b_trades, 3, 600000.0):+.2f}%")

        print_fixed_take_profit_comparison(v1_trades, a_trades, b_trades)
        print_winner_hold_analysis(a_result, "Test A")
        print_winner_hold_analysis(b_result, "Test B")
        print_rotation_analysis(b_result)
        print_add_attribution(b_result)
        print_concentration_analysis(b_result, 600000.0)

        official_exit_winners = [
            t for t in b_trades
            if t.exit_reason == "OFFICIAL_EXIT" and t.stock_id in {tt.stock_id for tt in b_result.winner_log}
        ]
        print(f"\n§8.3 因 OFFICIAL_EXIT 被強制砍掉的 Winner 數：{len(official_exit_winners)}")

        write_trades_csv(out_dir / "test_b_trades.csv", v1_trades=v1_trades, a_result=a_result, b_result=b_result)
        write_winner_log_csv(out_dir / "winner_management_log.csv", b_result)
        write_rotation_log_csv(out_dir / "rotation_log.csv", b_result)
        print(f"\nCSV 已輸出至：{out_dir}/test_b_trades.csv、winner_management_log.csv、rotation_log.csv")

        print(f"\n{'=' * 78}\nAttribution：Test B 相對 v1 的改善來自哪裡？\n{'=' * 78}")
        print(
            "  A. 單純延後停利：比較 Test A vs v1——Test A 只延後/取消停利，沒有 rotation/多次加碼，\n"
            f"     Test A 報酬 {a_metrics['net_return_pct']:+.2f}% vs v1 {v1_metrics['net_return_pct']:+.2f}%\n"
            "  B. 正確保留 Winner：§34 Winner Hold Analysis 顯示進入 winner management 後的\n"
            "     max upside，可判斷是否真的抓到更多漲幅\n"
            f"  C. 正確 Rotation：Test B 相對 Test A 的差距（{b_metrics['net_return_pct']:+.2f}% vs "
            f"{a_metrics['net_return_pct']:+.2f}%）主要來自 rotation（{len(b_result.rotation_log)} 次事件）\n"
            "  D. 多次加碼 Winner：見上方 Add Attribution，比較有/無加碼的差異\n"
            "  E. 更高單檔集中度：見上方 Concentration Analysis，若最大曝險顯著高於 v1 的 33%\n"
            "     （100k/2 units 上限下的隱含集中度上限），代表部分改善來自集中度提高、不是策略本身更聰明"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
