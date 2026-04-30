"""
Persist M23 archive return state at Taipei 20:00.

Rules:
  - Existing tracked stocks with a baseline/return: recalc by today's close.
  - Existing tracked stocks still showing "--": use today's (open+close)/2 as baseline and set return 0.0%.
  - Stocks first added today: keep baseline/return as NULL ("--").
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_ERROR = 2
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
RETURNS_READY_TIME = time(hour=20, minute=0)


def _resolve_target_trade_date() -> date:
    now_tpe = datetime.now(TAIPEI_TZ)
    if now_tpe.time() >= RETURNS_READY_TIME:
        return now_tpe.date()
    return now_tpe.date() - timedelta(days=1)


def _parse_target_trade_date(argv: list[str]) -> date:
    if len(argv) > 1 and argv[1].strip():
        return date.fromisoformat(argv[1].strip())
    return _resolve_target_trade_date()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    argv = argv or sys.argv
    try:
        target_trade_date = _parse_target_trade_date(argv)
    except ValueError as exc:
        logger.error("Invalid target_trade_date argv: %s", exc)
        return EXIT_ERROR
    logger.info("Signal archive return update start: target_trade_date=%s", target_trade_date)

    try:
        from app.database import SessionLocal, engine
        from app.signal_watch_schema import ensure_signal_watch_hit_return_columns
        from app.signals.archive import update_signal_watch_returns
    except Exception:
        logger.exception("Failed to import archive return modules")
        return EXIT_ERROR

    try:
        ensure_signal_watch_hit_return_columns(engine)
        with SessionLocal() as db:
            updated = update_signal_watch_returns(
                db,
                as_of_trade_date=target_trade_date,
            )
    except Exception:
        logger.exception("Signal archive return update failed")
        return EXIT_ERROR

    if updated == 0:
        logger.info("Signal archive return update skipped: no eligible rows")
        return EXIT_NO_DATA

    logger.info(
        "Signal archive return update done: target_trade_date=%s updated=%s",
        target_trade_date,
        updated,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
