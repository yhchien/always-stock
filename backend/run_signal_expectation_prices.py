"""M23 後續：每日 expectation price cron 入口。

行為：
  1. update_hit_targets(target_date)   — 對所有 active row 比對當日收盤 → 標 hit_*_at
  2. generate_for_new_signals(snapshot_date=target_date, source="cron")
     — 對「今日新抓到」（first_seen_date == target_date）的股票逐檔產生預測

用法：
    # cron 觸發（target_date 依台北 19:00 分界自動解析，同 run_daily_signals.py）
    python run_signal_expectation_prices.py

    # 手動指定日期（YYYY-MM-DD）
    python run_signal_expectation_prices.py 2026-05-26

Exit code（對齊 run_daily_signals.py 慣例）：
    0 = ok（所有新股都跑成功，或當日無新股可跑）
    1 = no_data（DB 無候選 / target date 無交易資料）
    2 = partial（部分新股失敗，但 hit detection 仍跑完）
    3 = error（DB 連線 / pipeline crash）
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_PARTIAL = 2
EXIT_ERROR = 3

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
EXPECTATION_READY_TIME = time(hour=19, minute=0)


def _resolve_target_date_from_now() -> date:
    """與 run_daily_signals.py 一致的台北 19:00 分界。"""
    now_tpe = datetime.now(TAIPEI_TZ)
    if now_tpe.time() >= EXPECTATION_READY_TIME:
        return now_tpe.date()
    return now_tpe.date() - timedelta(days=1)


def _parse_target_date_from_argv(argv: List[str]) -> date:
    if len(argv) > 1 and argv[1].strip():
        return date.fromisoformat(argv[1].strip())
    return _resolve_target_date_from_now()


def main(argv: List[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        target_date = _parse_target_date_from_argv(argv)
    except ValueError as exc:
        logger.error("Invalid target_date argv: %s", exc)
        return EXIT_ERROR

    logger.info("Expectation price cron start: target_date=%s", target_date)

    try:
        from app.database import SessionLocal
        from app.signals import expectation_price as svc
    except Exception:
        logger.exception("Failed to import expectation_price modules")
        return EXIT_ERROR

    overall_status = EXIT_OK

    # Step 1：hit detection
    try:
        with SessionLocal() as db:
            hit_result = svc.update_hit_targets(db, target_date)
        logger.info(
            "Hit detection done: conservative=%s dream=%s",
            hit_result["conservative_hits"],
            hit_result["dream_hits"],
        )
    except Exception:
        logger.exception("update_hit_targets failed")
        overall_status = EXIT_ERROR

    # Step 2：對新進股跑預測
    try:
        with SessionLocal() as db:
            result = svc.generate_for_new_signals(
                db,
                target_date,
                source="cron",
            )
        logger.info(
            "Expectation price generation: target=%s total=%s ok=%s failed=%s",
            target_date,
            result["total"],
            result["ok"],
            len(result["failed"]),
        )
        if result["total"] == 0:
            if overall_status == EXIT_OK:
                overall_status = EXIT_NO_DATA
        elif result["failed"]:
            if overall_status == EXIT_OK:
                overall_status = EXIT_PARTIAL
    except Exception:
        logger.exception("generate_for_new_signals crashed")
        return EXIT_ERROR

    logger.info(
        "Expectation price cron done: target=%s exit_code=%s",
        target_date,
        overall_status,
    )
    return overall_status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
