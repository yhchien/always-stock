"""
一次性 backfill：對 2026-08-07~2026-09-04（fishtail_backtest/ 沙盒驗證過的同一段真實魚尾
歷史資料，21 個交易日）逐日重跑 Shadow Portfolio v1 策略，驗證 production port 是否忠實
複製沙盒結果（+12.05% 無成本／28 筆交易／勝率 46.4%）。

寫進**正式** `strategy_version="v1_frozen"` 的表（這批歷史資料本來就是沙盒 CSV 的原始
production 來源，SignalWatchHit/SignalObservation/SignalObservationReview 早已存在，
不需要重跑 FinMind ETL 或任何 LLM 呼叫）。

如實比對結果，不因為對不上就調整判斷邏輯（v1 門檻凍結）——若有落差，回頭檢查是不是
production 資料的邊界情況跟沙盒 CSV 匯出當下的假設不同（例如某天 daily_price 缺筆、
或某檔股票的 P4 episode 起訖日跟 CSV 匯出時的認定有出入）。

用法：
    python3 backfill_shadow_portfolio_replay.py            # dry-run，只印會跑幾天
    python3 backfill_shadow_portfolio_replay.py --execute   # 真的寫入 DB
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

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
                    trades.append(
                        {
                            "stock_id": stock_id,
                            "entry_date": entry.scheduled_execution_date,
                            "exit_date": o.scheduled_execution_date,
                            "return_pct": return_pct,
                            "exit_reason": o.reason,
                        }
                    )
                open_entries = []
    return trades


def _reset_shadow_portfolio_state(session_factory, strategy_version: str, logger) -> None:
    """每次 `--execute` 一律先清空這個 strategy_version 的所有 shadow 表再重跑。

    **絕對不能假設「重跑整支 script」對已存在的 portfolio 狀態是安全的**——
    `ShadowVirtualPosition`／`ShadowVirtualPortfolio.cash` 是「目前最新狀態」，不是
    逐日 append-only 紀錄。若上一輪跑到一半失敗（例如遠端連線中斷）沒清空就重跑，
    重新從第一天開始的迴圈會拿「已經跑到後面某天累積出來的持倉/現金」去跟「第一天的
    證據」做判斷，等於把未來的狀態誤植回過去，整個回放會全部錯亂（這是真的撞過的
    bug，不是假設性風險）。`ShadowStrategyDailyDecision` 的 idempotency 只保證「同一天
    不會重複決策」，保證不了「整個 replay 從頭安全重跑」。
    """
    from app.models import (
        ShadowPortfolioDailySnapshot, ShadowPositionLot, ShadowStrategyDailyDecision,
        ShadowStrategyOrder, ShadowVirtualPortfolio, ShadowVirtualPosition,
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
        db.query(ShadowVirtualPortfolio).filter(ShadowVirtualPortfolio.strategy_version == strategy_version).delete(
            synchronize_session=False
        )
        db.commit()
    logger.info("Reset all shadow portfolio state for strategy_version=%s before replay", strategy_version)


def main(argv: list) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    execute = "--execute" in argv

    from app.database import SessionLocal, engine
    from app.models import DailyPrice
    from app.signals import shadow_portfolio as sp

    sp.ensure_shadow_portfolio_tables(engine)

    if execute:
        _reset_shadow_portfolio_state(SessionLocal, sp.STRATEGY_VERSION, logger)

    with SessionLocal() as db:
        trade_dates = sorted(
            d[0]
            for d in db.query(DailyPrice.trade_date)
            .filter(DailyPrice.trade_date >= REPLAY_START, DailyPrice.trade_date <= REPLAY_END)
            .distinct()
            .all()
        )

    if not trade_dates:
        logger.error("No trading days found in DB for %s ~ %s", REPLAY_START, REPLAY_END)
        return 1

    logger.info(
        "Replay window: %s ~ %s (%d trading days), strategy_version=%s, execute=%s",
        trade_dates[0], trade_dates[-1], len(trade_dates), sp.STRATEGY_VERSION, execute,
    )

    if not execute:
        logger.info("Dry-run only — pass --execute to actually write to DB")
        for d in trade_dates:
            logger.info("  would replay: %s", d)
        return 0

    from app.models import ShadowStrategyDailyDecision, ShadowStrategyOrder

    print(f"\n{'='*78}\nSTRATEGY_VERSION={sp.STRATEGY_VERSION}  REPLAY {trade_dates[0]} ~ {trade_dates[-1]}\n{'='*78}")

    for d in trade_dates:
        with SessionLocal() as db:
            executed = sp.execute_pending_strategy_orders(db, target_date=d)
            db.commit()
            # 今天真正成交的訂單（用 T-1 的訊號，用今天的 high/low 成交）
            filled_today = (
                db.query(ShadowStrategyOrder)
                .filter(
                    ShadowStrategyOrder.strategy_version == sp.STRATEGY_VERSION,
                    ShadowStrategyOrder.status == sp.ORDER_STATUS_EXECUTED,
                    ShadowStrategyOrder.scheduled_execution_date == d,
                )
                .all()
            )

        with SessionLocal() as db:
            decided = sp.run_daily_trading_strategy(db, target_date=d)
            db.commit()
            # 今天新產生、明天要執行的訊號
            new_signals = (
                db.query(ShadowStrategyDailyDecision)
                .filter(
                    ShadowStrategyDailyDecision.strategy_version == sp.STRATEGY_VERSION,
                    ShadowStrategyDailyDecision.trade_date == d,
                    ShadowStrategyDailyDecision.action.in_([sp.ACTION_BUY, sp.ACTION_ADD, sp.ACTION_SELL]),
                )
                .all()
            )

        with SessionLocal() as db:
            snapshot = sp.create_portfolio_daily_snapshot(db, target_date=d)
            db.commit()

        print(f"\n--- {d} ---")
        if filled_today:
            print("  [今日成交]")
            for o in filled_today:
                print(f"    {o.action:4s} {o.stock_id:8s} {o.stock_name:6s} @ {o.execution_price:.2f}  ({o.reason})")
        else:
            print("  [今日成交] 無")

        if new_signals:
            print("  [今日訊號，明日待執行]")
            for s in new_signals:
                print(f"    {s.action:4s} {s.stock_id:8s} {s.stock_name:6s}  {s.action_reason}")
        else:
            print("  [今日訊號] 無")

        print(
            f"  持有={decided.get('hold', 0)} 觀察={decided.get('watch', 0)} "
            f"容量不足跳過={decided.get('skipped_capacity', 0)}"
        )
        print(f"  >> 權益={snapshot.total_equity:,.0f}  累積報酬={snapshot.total_return_pct:+.2f}%")

        logger.info(
            "%s executed=%s decided=%s equity=%.0f return=%.2f%%",
            d, executed, decided, snapshot.total_equity, snapshot.total_return_pct,
        )

    with SessionLocal() as db:
        from app.models import ShadowPortfolioDailySnapshot

        final_snapshot = (
            db.query(ShadowPortfolioDailySnapshot)
            .filter(ShadowPortfolioDailySnapshot.strategy_version == sp.STRATEGY_VERSION)
            .order_by(ShadowPortfolioDailySnapshot.trade_date.desc())
            .first()
        )
        trades = _reconstruct_trades(db, sp.STRATEGY_VERSION)

    trade_count = len(trades)
    win_count = sum(1 for t in trades if t["return_pct"] > 0)
    win_rate_pct = (win_count / trade_count * 100.0) if trade_count else 0.0

    print("\n" + "=" * 70)
    print("SHADOW PORTFOLIO PRODUCTION PORT vs FISHTAIL_BACKTEST SANDBOX")
    print("=" * 70)
    print(f"{'Metric':30s} {'Sandbox v1_frozen':>20s} {'Production port':>20s}")
    print(
        f"{'Total return %':30s} {SANDBOX_BENCHMARK['total_return_pct']:>20.2f} "
        f"{final_snapshot.total_return_pct if final_snapshot else float('nan'):>20.2f}"
    )
    print(f"{'Trade count':30s} {SANDBOX_BENCHMARK['trade_count']:>20d} {trade_count:>20d}")
    print(f"{'Win rate %':30s} {SANDBOX_BENCHMARK['win_rate_pct']:>20.1f} {win_rate_pct:>20.1f}")
    print("=" * 70)

    print("\n--- Reconstructed trades ---")
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        print(
            f"  {t['stock_id']:6s} entry={t['entry_date']} exit={t['exit_date']} "
            f"return={t['return_pct']:+7.2f}% reason={t['exit_reason']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
