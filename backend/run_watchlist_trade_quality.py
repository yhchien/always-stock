"""
Daily watchlist trade quality refresh (M25).

執行流程：
1. resolve_snapshot_trade_date(db) — 看 DB 最近交易日（同 signal archive 規則）
2. SELECT DISTINCT user_id, stock_id, buy_date FROM user_watchlist
3. 對每組 (user_id, stock_id, buy_date)：
   - 若 (user_id, stock_id, buy_date, snapshot_trade_date) 已有 ok 快照 → skip
   - 否則跑 trade quality（透過 routers/analysis.run_trade_quality_for_user）→ 寫快照表（source='cron'）
   - 個別檔失敗 → 寫一筆 status='failed' 紀錄 + 繼續下一檔（不中斷整個 job）
4. exit code: 0 ok / 1 partial（有 ok 但有 failed）/ 2 全失敗 / 5 holiday（DB 無交易日資料）

使用方式：
    python3 run_watchlist_trade_quality.py                    # 自動 resolve
    python3 run_watchlist_trade_quality.py --date 2026-04-30  # 手動指定 snapshot_trade_date
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_HOLIDAY = 5


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily watchlist trade quality refresh (M25)")
    parser.add_argument(
        "--date",
        dest="snapshot_date",
        type=str,
        default=None,
        help="手動指定 snapshot_trade_date（YYYY-MM-DD）；省略則由 resolve_snapshot_trade_date 自動推算",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出會跑哪些 (user, stock, buy_date)，不實際呼叫 OpenAI",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        from app.database import SessionLocal
        from app.models import UserWatchlist, WatchlistTradeQualitySnapshot
        from app.routers.analysis import run_trade_quality_for_user
        from app.trade_quality_cache import (
            load_snapshot,
            resolve_snapshot_trade_date,
            save_snapshot_failed,
        )
    except Exception:
        logger.exception("Failed to import M25 modules")
        return EXIT_ALL_FAILED

    db = SessionLocal()
    try:
        # 1. resolve snapshot_trade_date
        if args.snapshot_date:
            try:
                snapshot_trade_date = date.fromisoformat(args.snapshot_date)
            except ValueError:
                logger.error("--date must be YYYY-MM-DD: %s", args.snapshot_date)
                return EXIT_ALL_FAILED
        else:
            snapshot_trade_date = resolve_snapshot_trade_date(db)
            if snapshot_trade_date is None:
                logger.warning("DB 無交易日資料，視為 holiday 跳過")
                return EXIT_HOLIDAY

        logger.info("M25 watchlist trade quality refresh start: snapshot_date=%s", snapshot_trade_date)

        # 2. 取所有 watchlist 條目
        entries = (
            db.query(UserWatchlist)
            .order_by(UserWatchlist.user_id.asc(), UserWatchlist.created_at.asc())
            .all()
        )
        if not entries:
            logger.info("No watchlist entries; nothing to refresh")
            return EXIT_OK

        # 取得每位使用者完整 ORM（runner 需要 user 物件）
        user_ids = sorted({e.user_id for e in entries})
        from app.models import User

        users_by_id = {
            u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
        }
        logger.info("Watchlist entries: %d (users=%d)", len(entries), len(users_by_id))

        if args.dry_run:
            for e in entries:
                logger.info(
                    "[DRY] user=%s stock=%s buy_date=%s",
                    e.user_id, e.stock_id, e.buy_date,
                )
            return EXIT_OK

        # 3. 對每筆：cache 命中 skip / 否則跑 + 寫
        ok_count = 0
        skip_count = 0
        failed_count = 0

        for entry in entries:
            user = users_by_id.get(entry.user_id)
            if user is None or not user.is_active:
                logger.debug(
                    "skip inactive/missing user=%s stock=%s",
                    entry.user_id, entry.stock_id,
                )
                continue

            existing = load_snapshot(
                db,
                user_id=user.id,
                stock_id=entry.stock_id,
                buy_date=entry.buy_date,
                snapshot_trade_date=snapshot_trade_date,
            )
            if existing is not None and existing.status == "ok":
                skip_count += 1
                continue

            try:
                run_trade_quality_for_user(
                    db,
                    user=user,
                    stock_id=entry.stock_id,
                    buy_date_input=entry.buy_date,
                    persist_source="cron",
                )
                ok_count += 1
                logger.info(
                    "ok user=%s stock=%s buy_date=%s",
                    user.id, entry.stock_id, entry.buy_date,
                )
            except Exception as exc:
                failed_count += 1
                logger.exception(
                    "Failed user=%s stock=%s buy_date=%s",
                    user.id, entry.stock_id, entry.buy_date,
                )
                try:
                    save_snapshot_failed(
                        db,
                        user_id=user.id,
                        stock_id=entry.stock_id,
                        buy_date=entry.buy_date,
                        snapshot_trade_date=snapshot_trade_date,
                        error_message=f"{type(exc).__name__}: {exc}",
                        source="cron",
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist failure marker user=%s stock=%s",
                        user.id, entry.stock_id,
                    )

        logger.info(
            "M25 done: snapshot_date=%s ok=%d failed=%d skip=%d",
            snapshot_trade_date, ok_count, failed_count, skip_count,
        )
        # exit code 規則
        if failed_count == 0:
            return EXIT_OK
        if ok_count == 0 and failed_count > 0:
            return EXIT_ALL_FAILED
        return EXIT_PARTIAL

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
