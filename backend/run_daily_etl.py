"""
每日 ETL 主流程。

執行順序（單日）:
  1. fetch_stock_master   — 更新股票基本資料（含 Fugle 子產業）
  2. fetch_daily_price    — 抓當日收盤價
  3. fetch_inst_flow      — 抓三大法人買賣超（金額估計依賴 step 2）
  4. aggregate_industry   — 彙整到 industry_daily_flow

使用方式:
  # 跑今天
  python run_daily_etl.py

  # 指定日期
  python run_daily_etl.py --date 2025-04-01

  # 歷史 backfill（往前 N 個日曆天，跳過非交易日）
  python run_daily_etl.py --backfill-days 30

  # 指定 Fugle mapping
  python run_daily_etl.py --date 2025-04-01 \\
      --fugle-mapping ../tools/output/fugle_industry_mapping.csv

  # 不更新 stock master（backfill 時加速）
  python run_daily_etl.py --backfill-days 30 --skip-master
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from typing import Optional

from logging_config import setup_logging
from app.database import SessionLocal
from init_db import init_db
from etl.fetch_stock_master import fetch_and_upsert_stock_master
from etl.fetch_daily_price import fetch_and_upsert_daily_price
from etl.fetch_inst_flow import fetch_and_upsert_inst_flow
from etl.aggregate_industry_flow import aggregate_industry_flow

logger = logging.getLogger(__name__)


def run_one_day(
    trade_date: date,
    fugle_mapping_path: Optional[str],
    skip_master: bool,
    token: str,
) -> bool:
    """
    跑單日 ETL。回傳 True 表示有資料成功入庫，False 表示非交易日或失敗。
    """
    db = SessionLocal()
    try:
        logger.info("── %s ─────────────────────────────", trade_date)

        if not skip_master:
            n = fetch_and_upsert_stock_master(db, token=token, fugle_mapping_path=fugle_mapping_path)
            logger.info("[1/4] stock_master: %d stocks", n)

        price_count = fetch_and_upsert_daily_price(db, trade_date)
        logger.info("[2/4] daily_price: %d records", price_count)

        if price_count == 0:
            logger.info("Non-trading day, skipping inst_flow and aggregation")
            return False

        flow_count = fetch_and_upsert_inst_flow(db, trade_date)
        logger.info("[3/4] inst_flow: %d records", flow_count)

        agg_count = aggregate_industry_flow(db, trade_date)
        logger.info("[4/4] industry_flow: %d industries", agg_count)

        return True
    except Exception:
        logger.exception("ETL failed for %s", trade_date)
        return False
    finally:
        db.close()


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="tw-stock-dashboard daily ETL")
    parser.add_argument(
        "--date", type=str, default=None,
        help="交易日期 YYYY-MM-DD，預設為今天",
    )
    parser.add_argument(
        "--backfill-days", type=int, default=0,
        help="往前 N 個日曆天做 backfill（--date 會被忽略）",
    )
    parser.add_argument(
        "--fugle-mapping", type=str, default=None,
        dest="fugle_mapping",
        help="Fugle 子產業 CSV 路徑（預設: ../tools/output/fugle_industry_mapping.csv）",
    )
    parser.add_argument(
        "--skip-master", action="store_true",
        help="不更新 stock_master（backfill 時加速）",
    )
    parser.add_argument(
        "--token", type=str, default="",
        help="FinMind API token（免費額度可省略）",
    )
    args = parser.parse_args()

    # 解析 Fugle mapping 路徑
    import os
    fugle_mapping = args.fugle_mapping
    if fugle_mapping is None:
        default_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "output", "fugle_industry_mapping.csv"
        )
        if os.path.exists(default_path):
            fugle_mapping = os.path.normpath(default_path)
            logger.info("Using default Fugle mapping: %s", fugle_mapping)

    # 初始化 DB（idempotent）
    init_db()

    # 決定要跑哪幾天
    if args.backfill_days > 0:
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.backfill_days - 1, -1, -1)]
        logger.info("Backfill mode: %d days (%s → %s)", len(dates), dates[0], dates[-1])
    else:
        target = date.fromisoformat(args.date) if args.date else date.today()
        dates = [target]

    success = 0
    skipped = 0
    for d in dates:
        result = run_one_day(
            trade_date=d,
            fugle_mapping_path=fugle_mapping,
            skip_master=args.skip_master,
            token=args.token,
        )
        if result:
            success += 1
        else:
            skipped += 1

    logger.info("Done. success=%d, skipped(non-trading)=%d", success, skipped)


if __name__ == "__main__":
    main()
