"""
魚尾每日模擬交易（Shadow Portfolio）Phase 1 入口。

**必須排在 `signal_archive_returns` 之後執行**（見 app/signals/shadow_portfolio.py 檔頭
說明）——v1 策略的 mark_to_market_return_pct 依賴 `archive.update_signal_watch_returns()`
先跑過，這個函式由獨立的 `run_signal_archive_returns.py`（`signal_archive_returns.yml`
workflow，鏈在 daily_signals 完成之後）呼叫。Phase 1 尚未接 GitHub Actions，先手動執行。

用法：
    # 抓最新交易日（比照 archive.resolve_archive_as_of_trade_date）
    python run_shadow_portfolio.py

    # 手動指定日期（YYYY-MM-DD）
    python run_shadow_portfolio.py 2026-09-04

Exit code（比照 run_daily_signals.py 慣例）：
    0 = ok
    1 = no_data（DB 無該 target_date 的交易資料，或找不到任何最新交易日）
    3 = db_error（DB 連線 / commit 失敗等其他例外）
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


def _parse_target_date_from_argv(argv: list, db) -> "date | None":
    from app.signals import archive

    if len(argv) > 1 and argv[1].strip():
        return date.fromisoformat(argv[1].strip())
    return archive.resolve_archive_as_of_trade_date(db)


def main(argv: list) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        from app.database import SessionLocal, engine
        from app.models import DailyPrice
        from app.signals import shadow_portfolio as sp
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
            has_trade_data = (
                db.query(DailyPrice.id)
                .filter(DailyPrice.trade_date == target_date)
                .first()
                is not None
            )
    except Exception:
        logger.exception("Failed to check trading day for target_date=%s", target_date)
        return EXIT_DB_ERROR

    if not has_trade_data:
        logger.info("target_date=%s has no daily_price rows; treating as non-trading day, skip", target_date)
        return EXIT_NO_DATA

    logger.info("Shadow portfolio run start: target_date=%s strategy_version=%s", target_date, sp.STRATEGY_VERSION)

    try:
        with SessionLocal() as db:
            executed = sp.execute_pending_strategy_orders(db, target_date=target_date)
            db.commit()
            logger.info("execute_pending_strategy_orders: %s", executed)

        with SessionLocal() as db:
            decided = sp.run_daily_trading_strategy(db, target_date=target_date)
            db.commit()
            logger.info("run_daily_trading_strategy: %s", decided)

        with SessionLocal() as db:
            snapshot = sp.create_portfolio_daily_snapshot(db, target_date=target_date)
            db.commit()
            logger.info(
                "create_portfolio_daily_snapshot: equity=%.2f return_pct=%.2f%% positions=%d",
                snapshot.total_equity, snapshot.total_return_pct, snapshot.position_count,
            )
    except Exception:
        logger.exception("Shadow portfolio run failed: target_date=%s", target_date)
        return EXIT_DB_ERROR

    logger.info("Shadow portfolio run done: target_date=%s", target_date)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
