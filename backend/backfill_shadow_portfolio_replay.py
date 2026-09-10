"""
一次性 backfill：逐日重跑 Shadow Portfolio v1 策略，驗證 production port（用魚尾 first_seen_
date，取代原本誤用 P4 SignalObservation.started_signal_date 的版本，見 2026-09-08 修復）
在真實資料上的行為是否合理。

原始驗證窗口是 2026-08-07~2026-09-04（fishtail_backtest/ 沙盒驗證過的同一段真實魚尾歷史
資料，21 個交易日，凍結 benchmark +12.05% 無成本／28 筆交易／勝率 46.4%）；`REPLAY_START`／
`REPLAY_END` 可調整為更長區間做延伸驗證——**窗口一旦變寬，`SANDBOX_BENCHMARK` 的三個數字
不再是同一批交易日的逐位元組對照，只能當參考基準，不是嚴格相等的驗證目標**。

寫進**正式** `strategy_version="v1_frozen"` 的表（這批歷史資料本來就是沙盒 CSV 的原始
production 來源，SignalWatchHit/SignalObservation/SignalObservationReview 早已存在，
不需要重跑 FinMind ETL 或任何 LLM 呼叫）。

如實比對結果，不因為對不上就調整判斷邏輯（v1 門檻凍結）——若有落差，回頭檢查是不是
production 資料的邊界情況跟沙盒 CSV 匯出當下的假設不同（例如某天 daily_price 缺筆、
或某檔股票的 P4 episode 起訖日跟 CSV 匯出時的認定有出入）。

**2026-09-09：v1_frozen 直接改版為 Dual-Engine Strategy**，使用者明確要求「直接覆蓋
v1_frozen 本身，不建立新版本，允許 DELETE/RESET 舊有 Shadow Portfolio 紀錄後以新 Rule
重新產生」——原本這支腳本對 `strategy_version==v1_frozen` 的 `--execute` 硬性拒絕（見
下方已移除的舊註解）已經**不再適用**，`v1_frozen` 現在正是這支腳本主要的重跑對象。
`SANDBOX_BENCHMARK` 三個數字（+12.05%／28 筆／46.4%）是**舊版**（單一 pullback 規則）
v1_frozen 的歷史基準，跟新版 Dual-Engine 是完全不同的策略邏輯，**不是**這次重跑的比較
目標，只保留在輸出裡當「這裡曾經是什麼」的歷史紀錄。

用法：
    python3 backfill_shadow_portfolio_replay.py                       # dry-run
    python3 backfill_shadow_portfolio_replay.py --execute              # 真的寫入 DB
    python3 backfill_shadow_portfolio_replay.py --execute \\
        --start=2026-08-01 --end=2026-09-07                            # 指定回測窗口
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

REPLAY_START = date(2026, 8, 7)
REPLAY_END = date(2026, 9, 4)

SANDBOX_BENCHMARK = {
    "total_return_pct": 12.05,
    "trade_count": 28,
    "win_rate_pct": 46.4,
}


def _reconstruct_trades(db, strategy_version: str):
    """從 ShadowStrategyOrder 的完整成交歷史重建「哪次進場對應哪次出場」，算出每一筆
    realized 交易的報酬率。SELL 永遠全部出清，所以一筆 SELL 會關掉自上次 SELL 以來所有
    未平倉的 BUY/ADD entry orders（可能是 1 筆或 2 筆，對應 1~2 個 lot）。
    """
    from app.models import ShadowStrategyOrder

    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == "EXECUTED",
        )
        .order_by(ShadowStrategyOrder.scheduled_execution_date.asc(), ShadowStrategyOrder.id.asc())
        .all()
    )

    by_stock = defaultdict(list)
    for o in orders:
        by_stock[o.stock_id].append(o)

    trades = []
    for stock_id, stock_orders in by_stock.items():
        open_entries = []
        for o in stock_orders:
            if o.action in ("BUY", "ADD"):
                open_entries.append(o)
            elif o.action == "SELL" and o.execution_price is not None:
                for entry in open_entries:
                    if entry.execution_price is None:
                        continue
                    return_pct = (o.execution_price - entry.execution_price) / entry.execution_price * 100.0
                    # $ 損益：entry.planned_amount 是這個 lot 的成本（BUY/ADD 建立時就有
                    # 記錄），shares = planned_amount / entry_price，pnl = shares *
                    # (exit_price - entry_price) = planned_amount * return_pct / 100。
                    pnl_amount = (
                        entry.planned_amount * return_pct / 100.0 if entry.planned_amount is not None else None
                    )
                    holding_days = (o.scheduled_execution_date - entry.scheduled_execution_date).days
                    trades.append(
                        {
                            "stock_id": stock_id,
                            "entry_date": entry.scheduled_execution_date,
                            "exit_date": o.scheduled_execution_date,
                            "return_pct": return_pct,
                            "pnl_amount": pnl_amount,
                            "holding_days": holding_days,
                            "exit_reason": o.reason,
                        }
                    )
                open_entries = []
    return trades


def compute_full_metrics(db, *, strategy_version: str, initial_capital: float) -> dict:
    """Part 11 比較表需要的完整指標集合：Gross Return / Final Equity / Trades /
    Win Rate / Profit Factor / Max Drawdown / Avg Trade / Median Trade /
    Avg Holding Days / Largest Winner / Largest Loser / Return w/o Top1 / w/o Top3。

    純讀取、不寫入——sanity replay 跑完後、5 Gates 驗證腳本、以及任何要產這張表的
    報告都呼叫這個函式，避免同一組指標在多個地方各自重算一次容易對不起來。
    """
    from app.models import ShadowPortfolioDailySnapshot

    trades = _reconstruct_trades(db, strategy_version)
    trade_count = len(trades)

    final_snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
        .order_by(ShadowPortfolioDailySnapshot.trade_date.desc())
        .first()
    )
    final_equity = final_snapshot.total_equity if final_snapshot else initial_capital
    gross_return_pct = (final_equity - initial_capital) / initial_capital * 100.0

    # Max drawdown：從逐日權益曲線算 peak-to-trough
    equity_curve = [
        row[0]
        for row in db.query(ShadowPortfolioDailySnapshot.total_equity)
        .filter(ShadowPortfolioDailySnapshot.strategy_version == strategy_version)
        .order_by(ShadowPortfolioDailySnapshot.trade_date.asc())
        .all()
    ]
    max_drawdown_pct = 0.0
    peak = initial_capital
    for equity in equity_curve:
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak * 100.0
        max_drawdown_pct = min(max_drawdown_pct, drawdown)

    if trade_count == 0:
        return {
            "strategy_version": strategy_version, "gross_return_pct": gross_return_pct,
            "final_equity": final_equity, "trade_count": 0, "win_rate_pct": 0.0,
            "profit_factor": None, "max_drawdown_pct": max_drawdown_pct,
            "avg_trade_pct": None, "median_trade_pct": None, "avg_holding_days": None,
            "largest_winner_pct": None, "largest_loser_pct": None,
            "return_pct_without_top1": gross_return_pct, "return_pct_without_top3": gross_return_pct,
            "trades": [],
        }

    returns = sorted(t["return_pct"] for t in trades)
    win_trades = [t for t in trades if t["return_pct"] > 0]
    loss_trades = [t for t in trades if t["return_pct"] < 0]
    win_rate_pct = len(win_trades) / trade_count * 100.0

    gross_profit = sum(t["pnl_amount"] for t in win_trades if t["pnl_amount"] is not None)
    gross_loss = sum(t["pnl_amount"] for t in loss_trades if t["pnl_amount"] is not None)
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else None

    n = len(returns)
    median_trade_pct = returns[n // 2] if n % 2 == 1 else (returns[n // 2 - 1] + returns[n // 2]) / 2.0

    holding_days_values = [t["holding_days"] for t in trades if t["holding_days"] is not None]

    # Return w/o Top1 / Top3：拿掉「$ 損益最大的 1（或 3）筆交易」後，最終權益會變成
    # 多少（不是拿掉報酬率最高的筆——一筆小倉位漲 50% 對總報酬貢獻可能遠小於一筆大倉位
    # 漲 15%，用 $ 損益排序才是「這筆交易對總報酬的真實貢獻」）。
    trades_by_pnl_desc = sorted(
        (t for t in trades if t["pnl_amount"] is not None), key=lambda t: t["pnl_amount"], reverse=True
    )
    top1_pnl = sum(t["pnl_amount"] for t in trades_by_pnl_desc[:1])
    top3_pnl = sum(t["pnl_amount"] for t in trades_by_pnl_desc[:3])
    return_pct_without_top1 = (final_equity - top1_pnl - initial_capital) / initial_capital * 100.0
    return_pct_without_top3 = (final_equity - top3_pnl - initial_capital) / initial_capital * 100.0

    return {
        "strategy_version": strategy_version,
        "gross_return_pct": gross_return_pct,
        "final_equity": final_equity,
        "trade_count": trade_count,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "avg_trade_pct": sum(returns) / n,
        "median_trade_pct": median_trade_pct,
        "avg_holding_days": (sum(holding_days_values) / len(holding_days_values)) if holding_days_values else None,
        "largest_winner_pct": max(returns),
        "largest_loser_pct": min(returns),
        "return_pct_without_top1": return_pct_without_top1,
        "return_pct_without_top3": return_pct_without_top3,
        "trades": trades,
    }


def _reset_shadow_portfolio_state(session_factory, strategy_version: str, logger) -> None:
    """每次 `--execute` 一律先清空這個 strategy_version 的所有 shadow 表再重跑。

    **絕對不能假設「重跑整支 script」對已存在的 portfolio 狀態是安全的**——
    `ShadowVirtualPosition`／`ShadowVirtualPortfolio.cash` 是「目前最新狀態」，不是
    逐日 append-only 紀錄。若上一輪跑到一半失敗（例如遠端連線中斷）沒清空就重跑，
    重新從第一天開始的迴圈會拿「已經跑到後面某天累積出來的持倉/現金」去跟「第一天的
    證據」做判斷，等於把未來的狀態誤植回過去，整個回放會全部錯亂（這是真的撞過的
    bug，不是假設性風險）。`ShadowStrategyDailyDecision` 的 idempotency 只保證「同一天
    不會重複決策」，保證不了「整個 replay 從頭安全重跑」。
    **2026-09-08 修正**：原本漏清 `ShadowCompletedTrade`——那張表的 model docstring
    寫「不受 35 交易日循環強制重置影響」，講的是 `check_and_apply_cycle_reset`（策略
    正常運作時的循環重置，永久紀錄本來就該保留），跟這支 backfill script 的「整個重跑」
    是完全不同的情境；backfill 重跑必須連已平倉的永久紀錄都清空重建，不然每次重新驗證
    都會在這張表疊加重複列（真的撞過：同一個 8/7~9/4 窗口 4 次 backfill 疊出 63 筆，
    但實際只有 24 筆不重複的交易，直接汙染了正式站「交易紀錄」頁面顯示的資料）。

    **2026-09（本輪）再犯同一種錯誤，順手修正**：新增 `ShadowMissedCandidate`／
    `ShadowWinnerTracking`（FORWARD_V1_202609 專用）時，忘記把這兩張新表也加進這裡
    一起清空——第二次重跑 FORWARD_V1_202609（補 winner tracking 接線）時，
    `ShadowMissedCandidate` 撞到上一輪殘留資料的 unique constraint 直接整支腳本
    crash，crash 當下 `ShadowVirtualPosition`/`ShadowStrategyOrder` 等表已經被清空
    重建到一半（第一次成功執行過的部分早被清光，第二次跑到一半又中斷），DB 處於
    「兩次執行混在一起」的破碎狀態，必須整個重跑一次才能恢復乾淨。教訓：**任何新增
    的 `strategy_version`-scoped 表，只要會被這支 backfill script 寫入，就必須同步
    加進這個函式**，不能只在新增 model 的當下記得，事後很容易忘記回頭補。
    """
    from app.models import (
        ShadowCompletedTrade, ShadowMissedCandidate, ShadowPortfolioDailySnapshot, ShadowPositionLot,
        ShadowStrategyDailyDecision, ShadowStrategyOrder, ShadowVirtualPortfolio, ShadowVirtualPosition,
        ShadowWinnerTracking,
    )

    with session_factory() as db:
        position_ids = [
            row[0]
            for row in db.query(ShadowVirtualPosition.id)
            .filter(ShadowVirtualPosition.strategy_version == strategy_version)
            .all()
        ]
        if position_ids:
            db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id.in_(position_ids)).delete(
                synchronize_session=False
            )
        db.query(ShadowVirtualPosition).filter(ShadowVirtualPosition.strategy_version == strategy_version).delete(
            synchronize_session=False
        )
        db.query(ShadowStrategyOrder).filter(ShadowStrategyOrder.strategy_version == strategy_version).delete(
            synchronize_session=False
        )
        db.query(ShadowStrategyDailyDecision).filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version
        ).delete(synchronize_session=False)
        db.query(ShadowPortfolioDailySnapshot).filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version
        ).delete(synchronize_session=False)
        db.query(ShadowCompletedTrade).filter(
            ShadowCompletedTrade.strategy_version == strategy_version
        ).delete(synchronize_session=False)
        db.query(ShadowMissedCandidate).filter(
            ShadowMissedCandidate.strategy_version == strategy_version
        ).delete(synchronize_session=False)
        db.query(ShadowWinnerTracking).filter(
            ShadowWinnerTracking.strategy_version == strategy_version
        ).delete(synchronize_session=False)
        db.query(ShadowVirtualPortfolio).filter(ShadowVirtualPortfolio.strategy_version == strategy_version).delete(
            synchronize_session=False
        )
        db.commit()
    logger.info("Reset all shadow portfolio state for strategy_version=%s before replay", strategy_version)


def _parse_strategy_version(argv: list, default: str) -> str:
    """`--strategy-version=XXX`；未帶時回傳 `default`（呼叫端傳 `sp.STRATEGY_VERSION`）。"""
    for arg in argv:
        if arg.startswith("--strategy-version="):
            return arg.split("=", 1)[1].strip()
    return default


def _parse_date_override(argv: list, flag: str, default: date) -> date:
    """`--start=YYYY-MM-DD`／`--end=YYYY-MM-DD`；未帶時回傳模組層級的
    `REPLAY_START`／`REPLAY_END` 預設值（維持既有呼叫方式向後相容）。"""
    for arg in argv:
        if arg.startswith(flag):
            return date.fromisoformat(arg.split("=", 1)[1].strip())
    return default


def main(argv: list) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    execute = "--execute" in argv

    from app.database import SessionLocal, engine
    from app.models import DailyPrice
    from app.signals import shadow_portfolio as sp

    strategy_version = _parse_strategy_version(argv, sp.STRATEGY_VERSION)
    if strategy_version not in sp.STRATEGY_PARAMS_BY_VERSION:
        logger.error(
            "Unknown --strategy-version=%s (known: %s)",
            strategy_version, sorted(sp.STRATEGY_PARAMS_BY_VERSION.keys()),
        )
        return 1

    replay_start = _parse_date_override(argv, "--start=", REPLAY_START)
    replay_end = _parse_date_override(argv, "--end=", REPLAY_END)

    # 2026-09-09 起 v1_frozen 已改版為 Dual-Engine（見本檔案頂部說明），使用者明確
    # 授權對它執行 --execute（DELETE/RESET 舊紀錄後以新 Rule 重新產生）——這裡**不再**
    # 拒絕 v1_frozen，這支腳本現在正是這次改版的主要重跑工具。

    sp.ensure_shadow_portfolio_tables(engine)

    if execute:
        _reset_shadow_portfolio_state(SessionLocal, strategy_version, logger)

    with SessionLocal() as db:
        trade_dates = sorted(
            d[0]
            for d in db.query(DailyPrice.trade_date)
            .filter(DailyPrice.trade_date >= replay_start, DailyPrice.trade_date <= replay_end)
            .distinct()
            .all()
        )

    if not trade_dates:
        logger.error("No trading days found in DB for %s ~ %s", replay_start, replay_end)
        return 1

    logger.info(
        "Replay window: %s ~ %s (%d trading days), strategy_version=%s, execute=%s",
        trade_dates[0], trade_dates[-1], len(trade_dates), strategy_version, execute,
    )

    if not execute:
        logger.info("Dry-run only — pass --execute to actually write to DB")
        for d in trade_dates:
            logger.info("  would replay: %s", d)
        return 0

    from app.models import ShadowStrategyDailyDecision, ShadowStrategyOrder

    def _with_retry(step_fn, *, retries: int = 4, delay_seconds: float = 3.0):
        """遠端 Postgres 對長時間佔用連線的既有已知不穩定（見
        `backfill_p4_momentum_scores.py` 同類事故）——`step_fn` 是一個會自己開
        `SessionLocal()`／commit／讀值回傳的零參數函式，任何一步撞到
        `OperationalError`（連線斷線）就整步重來（每一步本身都是 idempotent 的
        deterministic 計算，重來不會產生重複副作用）。"""
        from sqlalchemy.exc import OperationalError

        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                return step_fn()
            except OperationalError as exc:
                last_exc = exc
                logger.warning(
                    "OperationalError on attempt %d/%d (%s) — retrying in %.0fs",
                    attempt, retries, type(exc).__name__, delay_seconds,
                )
                if attempt < retries:
                    time.sleep(delay_seconds)
        raise last_exc

    print(f"\n{'='*78}\nSTRATEGY_VERSION={strategy_version}  REPLAY {trade_dates[0]} ~ {trade_dates[-1]}\n{'='*78}")

    for d in trade_dates:
        # 每個 with 區塊內就把要印的欄位讀成 plain tuple——session 一離開 with
        # 就 close，ORM 物件在區塊外變成 detached/expired，屬性存取會觸發對已
        # 關閉 session 的 lazy-load 而炸掉（DetachedInstanceError，真的撞過，
        # 撞到的當下讓整支 script 在第一天就當機）。
        def _step_execute_pending():
            with SessionLocal() as db:
                executed = sp.execute_pending_strategy_orders(db, target_date=d, strategy_version=strategy_version)
                db.commit()
                filled_today = [
                    (o.action, o.stock_id, o.stock_name, o.execution_price, o.reason)
                    for o in db.query(ShadowStrategyOrder).filter(
                        ShadowStrategyOrder.strategy_version == strategy_version,
                        ShadowStrategyOrder.status == sp.ORDER_STATUS_EXECUTED,
                        ShadowStrategyOrder.scheduled_execution_date == d,
                    )
                ]
                return executed, filled_today

        executed, filled_today = _with_retry(_step_execute_pending)

        def _step_run_strategy():
            with SessionLocal() as db:
                decided = sp.run_daily_trading_strategy(db, target_date=d, strategy_version=strategy_version)
                db.commit()
                new_signals = [
                    (s.action, s.stock_id, s.stock_name, s.action_reason)
                    for s in db.query(ShadowStrategyDailyDecision).filter(
                        ShadowStrategyDailyDecision.strategy_version == strategy_version,
                        ShadowStrategyDailyDecision.trade_date == d,
                        ShadowStrategyDailyDecision.action.in_([sp.ACTION_BUY, sp.ACTION_ADD, sp.ACTION_SELL]),
                    )
                ]
                return decided, new_signals

        decided, new_signals = _with_retry(_step_run_strategy)

        def _step_snapshot():
            with SessionLocal() as db:
                snapshot = sp.create_portfolio_daily_snapshot(db, target_date=d, strategy_version=strategy_version)
                db.commit()
                return snapshot.total_equity, snapshot.total_return_pct

        snapshot_equity, snapshot_return_pct = _with_retry(_step_snapshot)

        def _step_cycle_reset():
            with SessionLocal() as db:
                reset_triggered = sp.check_and_apply_cycle_reset(db, target_date=d, strategy_version=strategy_version)
                db.commit()
                return reset_triggered

        reset_triggered = _with_retry(_step_cycle_reset)

        def _step_winner_tracking():
            if not sp.STRATEGY_PARAMS_BY_VERSION[strategy_version].get("track_winners"):
                return 0
            with SessionLocal() as db:
                updated = sp.update_winner_tracking(db, target_date=d, strategy_version=strategy_version)
                db.commit()
                return updated

        winner_rows_updated = _with_retry(_step_winner_tracking)

        print(f"\n--- {d} ---")
        if reset_triggered:
            print("  *** 35 個交易日循環結束，portfolio 已強制重置 ***")
        if filled_today:
            print("  [今日成交]")
            for action, stock_id, stock_name, execution_price, reason in filled_today:
                print(f"    {action:4s} {stock_id:8s} {stock_name:6s} @ {execution_price:.2f}  ({reason})")
        else:
            print("  [今日成交] 無")

        if new_signals:
            print("  [今日訊號，明日待執行]")
            for action, stock_id, stock_name, action_reason in new_signals:
                print(f"    {action:4s} {stock_id:8s} {stock_name:6s}  {action_reason}")
        else:
            print("  [今日訊號] 無")

        print(
            f"  持有={decided.get('hold', 0)} 觀察={decided.get('watch', 0)} "
            f"容量不足跳過={decided.get('skipped_capacity', 0)}"
        )
        print(f"  >> 權益={snapshot_equity:,.0f}  累積報酬={snapshot_return_pct:+.2f}%")
        if winner_rows_updated:
            print(f"  [Winner Tracking] 今日更新 {winner_rows_updated} 檔（actual_position_return 曾經 >= +10%）")

        logger.info(
            "%s executed=%s decided=%s equity=%.0f return=%.2f%%",
            d, executed, decided, snapshot_equity, snapshot_return_pct,
        )

    initial_capital = sp.STRATEGY_PARAMS_BY_VERSION[strategy_version]["initial_capital"]
    with SessionLocal() as db:
        metrics = compute_full_metrics(db, strategy_version=strategy_version, initial_capital=initial_capital)
    trades = metrics["trades"]

    print("\n" + "=" * 70)
    print(f"SHADOW PORTFOLIO REPLAY SUMMARY — strategy_version={strategy_version}")
    print("=" * 70)

    def _fmt(value, spec="+.2f"):
        return format(value, spec) if value is not None else "N/A"

    print(f"{'Gross Return %':30s} {_fmt(metrics['gross_return_pct']):>15s}")
    print(f"{'Final Equity':30s} {metrics['final_equity']:>15,.0f}")
    print(f"{'Trades':30s} {metrics['trade_count']:>15d}")
    print(f"{'Win Rate %':30s} {_fmt(metrics['win_rate_pct'], '.1f'):>15s}")
    print(f"{'Profit Factor':30s} {_fmt(metrics['profit_factor'], '.2f'):>15s}")
    print(f"{'Max Drawdown %':30s} {_fmt(metrics['max_drawdown_pct']):>15s}")
    print(f"{'Avg Trade %':30s} {_fmt(metrics['avg_trade_pct']):>15s}")
    print(f"{'Median Trade %':30s} {_fmt(metrics['median_trade_pct']):>15s}")
    print(f"{'Avg Holding Days':30s} {_fmt(metrics['avg_holding_days'], '.1f'):>15s}")
    print(f"{'Largest Winner %':30s} {_fmt(metrics['largest_winner_pct']):>15s}")
    print(f"{'Largest Loser %':30s} {_fmt(metrics['largest_loser_pct']):>15s}")
    print(f"{'Return w/o Top1 %':30s} {_fmt(metrics['return_pct_without_top1']):>15s}")
    print(f"{'Return w/o Top3 %':30s} {_fmt(metrics['return_pct_without_top3']):>15s}")
    print("=" * 70)

    if strategy_version == sp.STRATEGY_VERSION:
        print(
            "\n(以上是 2026-09-09 改版後的 Dual-Engine v1_frozen 結果。舊版單一 pullback 規則的"
            " v1_frozen 歷史基準——僅供對照『這裡曾經是什麼』，不是這次的比較目標，見 "
            f"canonical_v1_manifest.json：total_return_pct={SANDBOX_BENCHMARK['total_return_pct']}%, "
            f"trade_count={SANDBOX_BENCHMARK['trade_count']}, win_rate_pct={SANDBOX_BENCHMARK['win_rate_pct']}%)"
        )

    print("\n--- Reconstructed trades ---")
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        print(
            f"  {t['stock_id']:6s} entry={t['entry_date']} exit={t['exit_date']} "
            f"return={t['return_pct']:+7.2f}% pnl={_fmt(t['pnl_amount'], ',.0f')} "
            f"holding_days={t['holding_days']} reason={t['exit_reason']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
