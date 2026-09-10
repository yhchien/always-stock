"""
魚尾每日模擬交易（Shadow Portfolio）入口——每個真實交易日跑一次，驅動所有「應該在這天
運作」的 strategy_version。

**必須排在 `signal_archive_returns` 之後執行**（見 app/signals/shadow_portfolio.py 檔頭
說明）——v1 策略的 mark_to_market_return_pct 依賴 `archive.update_signal_watch_returns()`
先跑過，這個函式由獨立的 `run_signal_archive_returns.py`（`signal_archive_returns.yml`
workflow，鏈在 daily_signals 完成之後）呼叫；`.github/workflows/shadow_portfolio.yml` 則是
`workflow_run` 鏈在 `Signal Archive Returns Update` 完成之後——也就是這支腳本被觸發時，
當天的市場資料、P3（Global Selector）、P4（每日觀察複核）都已經跑完，資料齊備。

用法：
    # 抓最新交易日（比照 archive.resolve_archive_as_of_trade_date）
    python run_shadow_portfolio.py

    # 手動指定日期（YYYY-MM-DD）
    python run_shadow_portfolio.py 2026-09-04

Exit code（比照 run_daily_signals.py 慣例）：
    0 = ok（至少一個 strategy_version 成功跑完；個別版本失敗會記在 log，不會讓整支腳本
        因為某一版本出錯就連帶讓其他版本也不跑——見 `_run_one_strategy_version` 的
        try/except 邊界）
    1 = no_data（DB 無該 target_date 的交易資料，或找不到任何最新交易日）
    3 = db_error（全部 strategy_version 都失敗，或 DB 連線/commit 等更基礎的例外）
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_DB_ERROR = 3

# v1_frozen 新週期從這一天（含）起才參與每日模擬交易，與 Forward 策略的週期對齊。
V1_FROZEN_START_DATE = date(2026, 9, 8)

# FORWARD_V1_202609 從這一天（含）起才真正參與每日模擬交易——這個日期是
# 2026-09-08 Freeze 決策當下，DB 裡「最新有真實交易資料」的下一個交易日
# （2026-09-08 收盤資料已存在，2026-09-09 完全沒有任何資料，是真正未見的未來）。
# 早於這個日期的 target_date，即使 self-healing 補跑到，也絕對不會對 FORWARD_V1_202609
# 產生任何決策/訂單——這是 spec Part 40「不要把過去幾天 backfill 當成 Forward Day」
# 的程式碼層保證，不只是操作流程上記得別這樣做。
FORWARD_V1_START_DATE = date(2026, 9, 9)

# 2026-09-09：v1_frozen 本身改版為 Dual-Engine，使用者明確要求它是「目前這套 Shadow
# Portfolio 的主要 active strategy」，不要讓兩套策略同時對同一批候選各自產生
# BUY/ADD/SELL 決策（即使兩者各自有獨立的虛擬資金池、不會真的搶同一筆錢，並存仍會讓
# 「現在到底在跑哪一套」變得含糊）。因此每日 runner 停止自動觸發 `FORWARD_V1_202609`——
# 它的既有歷史資料（`ShadowVirtualPortfolio`／`ShadowCompletedTrade` 等）完全不受影響，
# 只是不會再有新的一天被自動加進去；未來要恢復只需要把這個 flag 改回 True。
_FORWARD_V1_ENABLED = False


def _parse_target_date_from_argv(argv: list, db) -> "date | None":
    from app.signals import archive

    if len(argv) > 1 and argv[1].strip():
        return date.fromisoformat(argv[1].strip())
    return archive.resolve_archive_as_of_trade_date(db)


def _strategy_versions_for_date(target_date: date) -> list[str]:
    """今天應該跑哪些 strategy_version。

    `v1_frozen` 的目前週期從 2026-09-08 開始；更早日期只保留給明確指定
    replay/backfill 的歷史分析，不會被每日 production runner 自動補進目前時間線。
    `FORWARD_V1_202609` 目前仍停用，不再自動加入每日 runner。
    """
    from app.signals import shadow_portfolio as sp

    versions = [sp.STRATEGY_VERSION] if target_date >= V1_FROZEN_START_DATE else []
    if _FORWARD_V1_ENABLED and target_date >= FORWARD_V1_START_DATE:
        versions.append(sp.STRATEGY_VERSION_FORWARD_V1)
    return versions


def _run_one_strategy_version(SessionLocal, sp, *, target_date: date, strategy_version: str) -> bool:
    """對單一 strategy_version 跑完整套每日流程。回傳是否成功；例外會被這裡吞掉
    （記 log），讓呼叫端可以繼續處理其他 strategy_version，不會因為某一版本出錯
    就讓整支腳本直接中止、連帶其他版本當天完全沒有機會執行。"""
    try:
        with SessionLocal() as db:
            executed = sp.execute_pending_strategy_orders(db, target_date=target_date, strategy_version=strategy_version)
            db.commit()
            logger.info("[%s] execute_pending_strategy_orders: %s", strategy_version, executed)

        with SessionLocal() as db:
            decided = sp.run_daily_trading_strategy(db, target_date=target_date, strategy_version=strategy_version)
            db.commit()
            logger.info("[%s] run_daily_trading_strategy: %s", strategy_version, decided)

        with SessionLocal() as db:
            snapshot = sp.create_portfolio_daily_snapshot(db, target_date=target_date, strategy_version=strategy_version)
            db.commit()
            logger.info(
                "[%s] create_portfolio_daily_snapshot: equity=%.2f return_pct=%.2f%% positions=%d",
                strategy_version, snapshot.total_equity, snapshot.total_return_pct, snapshot.position_count,
            )

        with SessionLocal() as db:
            reset_triggered = sp.check_and_apply_cycle_reset(db, target_date=target_date, strategy_version=strategy_version)
            db.commit()
            if reset_triggered:
                logger.info("[%s] cycle completed; portfolio has been reset for a new cycle", strategy_version)

        params = sp.STRATEGY_PARAMS_BY_VERSION[strategy_version]
        if params.get("track_winners"):
            with SessionLocal() as db:
                updated = sp.update_winner_tracking(db, target_date=target_date, strategy_version=strategy_version)
                db.commit()
                if updated:
                    logger.info("[%s] update_winner_tracking: %d rows", strategy_version, updated)

        return True
    except Exception:
        logger.exception("[%s] Shadow portfolio run failed: target_date=%s", strategy_version, target_date)
        return False


def main(argv: list) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        from app.database import SessionLocal, engine
        from app.signals import shadow_portfolio as sp
        from app.trading_calendar import is_trading_day
    except Exception:
        logger.exception("Failed to import shadow_portfolio modules")
        return EXIT_DB_ERROR

    try:
        sp.ensure_shadow_portfolio_tables(engine)
    except Exception:
        logger.exception("Failed to ensure shadow portfolio tables")
        return EXIT_DB_ERROR

    try:
        with SessionLocal() as db:
            target_date = _parse_target_date_from_argv(argv, db)
    except ValueError as exc:
        logger.error("Invalid target_date argv: %s", exc)
        return EXIT_DB_ERROR
    except Exception:
        logger.exception("Failed to resolve target_date")
        return EXIT_DB_ERROR

    if target_date is None:
        logger.info("No trading day resolvable from daily_price; nothing to do")
        return EXIT_NO_DATA

    try:
        with SessionLocal() as db:
            has_trade_data = is_trading_day(db, target_date)
    except Exception:
        logger.exception("Failed to check trading day for target_date=%s", target_date)
        return EXIT_DB_ERROR

    if not has_trade_data:
        logger.info("target_date=%s has no daily_price rows; treating as non-trading day, skip", target_date)
        return EXIT_NO_DATA

    versions = _strategy_versions_for_date(target_date)
    logger.info("Shadow portfolio run start: target_date=%s strategy_versions=%s", target_date, versions)

    if not versions:
        logger.info("No active strategy version for target_date=%s; skip", target_date)
        return EXIT_NO_DATA

    results = {v: _run_one_strategy_version(SessionLocal, sp, target_date=target_date, strategy_version=v) for v in versions}

    logger.info("Shadow portfolio run done: target_date=%s results=%s", target_date, results)
    if not any(results.values()):
        return EXIT_DB_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
