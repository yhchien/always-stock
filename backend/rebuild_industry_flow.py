"""
全面重建 industry_daily_flow。

執行順序：
  1. 用 FinMind TaiwanStockIndustryChain 更新 stocks_master.industry_name
  2. 清空 industry_daily_flow（或指定起始日期後清空）
  3. 從 inst_stock_flow 取所有唯一交易日
  4. 對每個交易日重跑 aggregate_industry_flow()

用法：
  python rebuild_industry_flow.py                  # 全量重建（清空後重跑所有日期）
  python rebuild_industry_flow.py --dry-run        # 只印出計畫，不寫入
  python rebuild_industry_flow.py --skip-master    # 跳過更新 stocks_master（產業分類已更新過）
  python rebuild_industry_flow.py --from 2024-01-01  # 只重建指定日期起的資料（不清空舊資料）
  python rebuild_industry_flow.py --checkpoint backend/logs/rebuild_checkpoint.txt
"""
import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

# 確保可以 import app/etl 模組
sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal
from etl.aggregate_industry_flow import aggregate_industry_flow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rebuild_industry_flow")


def get_all_trade_dates(db, from_date: Optional[date] = None) -> List[date]:
    """從 inst_stock_flow 取所有 unique trade_date（升序）。"""
    from sqlalchemy import text

    if from_date:
        rows = db.execute(
            text("SELECT DISTINCT trade_date FROM inst_stock_flow WHERE trade_date >= :d ORDER BY trade_date"),
            {"d": from_date},
        ).fetchall()
    else:
        rows = db.execute(
            text("SELECT DISTINCT trade_date FROM inst_stock_flow ORDER BY trade_date")
        ).fetchall()

    results = []
    for r in rows:
        d = r[0]
        if isinstance(d, str):
            d = date.fromisoformat(d)
        results.append(d)
    return results


def clear_industry_daily_flow(db, from_date: Optional[date] = None, dry_run: bool = False) -> int:
    """清空 industry_daily_flow（或只清指定日期起的資料）。"""
    from sqlalchemy import text

    if from_date:
        sql = "DELETE FROM industry_daily_flow WHERE trade_date >= :d"
        params = {"d": from_date}
        desc = f"trade_date >= {from_date}"
    else:
        sql = "DELETE FROM industry_daily_flow"
        params = {}
        desc = "all rows"

    if dry_run:
        count_sql = sql.replace("DELETE FROM", "SELECT COUNT(*) FROM")
        count = db.execute(text(count_sql), params).scalar()
        logger.info("[dry-run] Would delete %d rows from industry_daily_flow (%s)", count, desc)
        return count

    result = db.execute(text(sql), params)
    db.commit()
    deleted = result.rowcount
    logger.info("Deleted %d rows from industry_daily_flow (%s)", deleted, desc)
    return deleted


def load_checkpoint(path: str) -> Optional[date]:
    """讀取 checkpoint：回傳上次成功完成的最後一天（下次從隔天開始）。"""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        line = f.read().strip()
    if line:
        try:
            return date.fromisoformat(line)
        except ValueError:
            logger.warning("Invalid checkpoint date: %r, ignoring", line)
    return None


def save_checkpoint(path: str, d: date) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(d.isoformat())


def main():
    parser = argparse.ArgumentParser(description="Rebuild industry_daily_flow from scratch")
    parser.add_argument("--dry-run", action="store_true", help="印出計畫，不寫入 DB")
    parser.add_argument("--skip-master", action="store_true", help="跳過更新 stocks_master 產業分類")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="只重建此日期起的資料（不清空更早的資料）")
    parser.add_argument("--checkpoint", metavar="FILE", default="",
                        help="checkpoint 檔案路徑，支援斷點續跑")
    args = parser.parse_args()

    from_date: Optional[date] = None
    if args.from_date:
        from_date = date.fromisoformat(args.from_date)

    token = os.environ.get("FINMIND_TOKEN", "")

    db = SessionLocal()
    try:
        # ─────────────────────────────────────────────────────────
        # Step 1: 更新 stocks_master 產業分類
        # ─────────────────────────────────────────────────────────
        if not args.skip_master:
            if not token:
                if args.dry_run:
                    logger.warning("FINMIND_TOKEN not set — Step 1 skipped in dry-run mode.")
                    logger.warning("Set FINMIND_TOKEN to preview industry name changes.")
                    logger.info("=== Step 1/3: Skipped (no token in dry-run) ===")
                else:
                    logger.error("FINMIND_TOKEN not set. Cannot fetch TaiwanStockIndustryChain.")
                    logger.error("Set FINMIND_TOKEN or use --skip-master to skip this step.")
                    sys.exit(1)
            else:
                from etl.finmind_industry_chain_sdk import update_stocks_master_industry
                logger.info("=== Step 1/3: Updating stocks_master industry from FinMind ===")
                stats = update_stocks_master_industry(db, token, dry_run=args.dry_run)
                logger.info(
                    "stocks_master: updated=%d, unchanged=%d, not_in_master=%d",
                    stats["updated"], stats["skipped"], stats["not_in_master"],
                )
        else:
            logger.info("=== Step 1/3: Skipped (--skip-master) ===")

        # ─────────────────────────────────────────────────────────
        # Step 2: 清空 industry_daily_flow
        # ─────────────────────────────────────────────────────────
        logger.info("=== Step 2/3: Clearing industry_daily_flow ===")
        clear_from = from_date  # None → 清全部；有值 → 只清該日期起
        clear_industry_daily_flow(db, from_date=clear_from, dry_run=args.dry_run)

        # ─────────────────────────────────────────────────────────
        # Step 3: 逐日重跑 aggregate_industry_flow
        # ─────────────────────────────────────────────────────────
        logger.info("=== Step 3/3: Re-aggregating industry_daily_flow ===")

        # checkpoint 支援
        checkpoint_path = args.checkpoint
        resume_after: Optional[date] = None
        if checkpoint_path:
            resume_after = load_checkpoint(checkpoint_path)
            if resume_after:
                logger.info("Checkpoint found: resuming after %s", resume_after)

        trade_dates = get_all_trade_dates(db, from_date=from_date)
        logger.info("Total trade dates to process: %d", len(trade_dates))

        processed = 0
        skipped_checkpoint = 0
        RECONNECT_EVERY = 50  # 每 50 天換一次 session，避免 Render idle timeout 斷線

        for i, d in enumerate(trade_dates):
            # checkpoint 恢復：跳過已完成的日期
            if resume_after and d <= resume_after:
                skipped_checkpoint += 1
                continue

            if args.dry_run:
                logger.info("[dry-run] Would aggregate %s", d)
                processed += 1
                continue

            # 每 RECONNECT_EVERY 天換一個新 session，防止長時間連線被 Render 切斷
            if processed > 0 and processed % RECONNECT_EVERY == 0:
                db.close()
                db = SessionLocal()
                logger.debug("Reconnected DB session at date %s", d)

            count = aggregate_industry_flow(db, d)
            processed += 1

            if checkpoint_path:
                save_checkpoint(checkpoint_path, d)

            if processed % 50 == 0:
                logger.info("Progress: %d / %d dates done (latest: %s)", processed, len(trade_dates), d)

        logger.info(
            "Done. Processed %d dates, skipped %d (checkpoint), total %d",
            processed, skipped_checkpoint, len(trade_dates),
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
