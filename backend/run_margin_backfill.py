"""
融資融券 backfill 專用入口

背景：daily_etl_update workflow 在台北 18:00 cron 跑時，FinMind
TaiwanStockMarginPurchaseShortSale dataset 多半尚未同步當日餘額
（券商 ~21:00 才公告），導致 margin step 經常 no_data，DB 大量交易日 0 rows。

本 script 由獨立 workflow margin_trade_backfill.yml 在台北 22:30 跑，
專門補抓 margin/short 資料；不動其他 step、不影響 daily_etl 既有時程。

用法：
    # 預設行為：掃描最近 LOOKBACK_TRADE_DAYS 個交易日，補抓 margin_trade
    # 缺漏（margin_trade 該日 row 數 < daily_price 該日 row 數 × MIN_COVERAGE_RATIO）
    # 的日子。
    python run_margin_backfill.py

    # 指定單日
    python run_margin_backfill.py --date 2026-05-22

    # 指定區間（仍只補缺漏日，已存在資料的日子跳過）
    python run_margin_backfill.py --start-date 2026-05-12 --end-date 2026-05-22

    # 強制重抓（不檢查現有資料）
    python run_margin_backfill.py --date 2026-05-22 --force

退出碼：
    0  全部成功（或無待補資料）
    1  部分日子失敗（partial）
    2  全部失敗
    5  整體假日（target 範圍內 daily_price 都無資料）
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 預設掃描最近 N 個交易日（涵蓋兩週 + buffer）
DEFAULT_LOOKBACK_TRADE_DAYS = 14

# 視為「該日資料完整」的最低門檻：margin_trade row 數 / daily_price row 數
# 0.85 預留一些彈性給 ETF / 無融資資格的標的
MIN_COVERAGE_RATIO = 0.85


def _yesterday_taipei() -> date:
    return (datetime.now(TAIPEI_TZ) - timedelta(days=1)).date()


def _find_target_trade_dates(
    db,
    *,
    start_date: Optional[date],
    end_date: Optional[date],
    lookback: int,
    force: bool,
) -> List[date]:
    """找出要 backfill 的交易日清單。

    規則：
    - 若指定 start_date / end_date，範圍內 daily_price 有資料的日子全部候選
    - 否則，取最近 `lookback` 個交易日（從 daily_price 看）
    - 若 force=False，過濾掉 margin_trade 已有資料（>= MIN_COVERAGE_RATIO）的日子
    """
    if start_date and end_date:
        rows = db.execute(
            text(
                "SELECT DISTINCT trade_date FROM daily_price "
                "WHERE trade_date BETWEEN :s AND :e "
                "ORDER BY trade_date"
            ),
            {"s": start_date, "e": end_date},
        ).all()
    else:
        rows = db.execute(
            text(
                "SELECT trade_date FROM ("
                "  SELECT DISTINCT trade_date FROM daily_price "
                "  ORDER BY trade_date DESC LIMIT :n"
                ") sub ORDER BY trade_date"
            ),
            {"n": lookback},
        ).all()

    candidate_dates = [r[0] for r in rows]
    if not candidate_dates:
        return []

    if force:
        return candidate_dates

    # 候選日數固定且小，用兩個獨立 GROUP BY 並在 Python 合併
    start = candidate_dates[0]
    end = candidate_dates[-1]
    price_counts = dict(
        db.execute(
            text(
                "SELECT trade_date, COUNT(DISTINCT stock_id) "
                "FROM daily_price WHERE trade_date BETWEEN :s AND :e "
                "GROUP BY trade_date"
            ),
            {"s": start, "e": end},
        ).all()
    )
    margin_counts = dict(
        db.execute(
            text(
                "SELECT trade_date, COUNT(*) "
                "FROM margin_trade WHERE trade_date BETWEEN :s AND :e "
                "GROUP BY trade_date"
            ),
            {"s": start, "e": end},
        ).all()
    )

    missing: List[date] = []
    for td in candidate_dates:
        price_rows = price_counts.get(td, 0)
        margin_rows = margin_counts.get(td, 0)
        if price_rows == 0:
            continue
        ratio = margin_rows / price_rows
        if ratio < MIN_COVERAGE_RATIO:
            missing.append(td)
            logger.info(
                "缺漏：%s margin_rows=%d / price_rows=%d (%.0f%%) → 待補",
                td, margin_rows, price_rows, ratio * 100,
            )
        else:
            logger.info(
                "已完整：%s margin_rows=%d / price_rows=%d (%.0f%%) → skip",
                td, margin_rows, price_rows, ratio * 100,
            )

    return missing


def _backfill_one_day(
    db,
    trade_date: date,
    client,
    stock_ids: List[str],
) -> str:
    """補抓單一交易日；回傳 status string。"""
    from etl.finmind_margin_trade_sdk import fetch_and_upsert_margin_trade_finmind_sdk

    logger.info("=" * 60)
    logger.info("Backfilling margin_trade for %s ...", trade_date)
    result = fetch_and_upsert_margin_trade_finmind_sdk(
        db, stock_ids, trade_date, trade_date, client,
    )
    status = result.get("status", "unknown")
    upserted = result.get("upserted", 0)
    logger.info("→ %s status=%s upserted=%d", trade_date, status, upserted)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="補抓 FinMind 融資融券每日餘額（margin_trade）",
    )
    parser.add_argument("--date", help="單日 backfill（YYYY-MM-DD），預設昨日台北")
    parser.add_argument("--start-date", help="區間起日（YYYY-MM-DD）")
    parser.add_argument("--end-date", help="區間迄日（YYYY-MM-DD）")
    parser.add_argument(
        "--lookback",
        type=int,
        default=DEFAULT_LOOKBACK_TRADE_DAYS,
        help=f"未帶日期時掃描的交易日數（預設 {DEFAULT_LOOKBACK_TRADE_DAYS}）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="不檢查現有資料，強制重抓所有日子",
    )
    args = parser.parse_args()

    token = os.getenv("FINMIND_TOKEN") or os.getenv("FINMIND_API_TOKEN")
    if not token:
        logger.error("環境變數 FINMIND_TOKEN 未設定")
        return 2

    # 決定日期範圍
    if args.date:
        try:
            single = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError as e:
            logger.error("--date 格式錯誤：%s", e)
            return 2
        start_date = end_date = single
    elif args.start_date and args.end_date:
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError as e:
            logger.error("--start-date / --end-date 格式錯誤：%s", e)
            return 2
        if start_date > end_date:
            logger.error("--start-date 必須 <= --end-date")
            return 2
    else:
        start_date = end_date = None  # 走 lookback 模式

    from app.database import SessionLocal
    from app.models import StockMaster
    from etl.finmind_sdk_client import FinMindSDKClient

    client = FinMindSDKClient(token)
    db = SessionLocal()
    try:
        target_dates = _find_target_trade_dates(
            db,
            start_date=start_date,
            end_date=end_date,
            lookback=args.lookback,
            force=args.force,
        )

        if not target_dates:
            logger.info("沒有需要 backfill 的交易日。")
            return 5 if (start_date or end_date) else 0

        stock_ids = [
            row[0]
            for row in db.query(StockMaster.stock_id)
            .filter(StockMaster.market == "twse", StockMaster.is_active == True)
            .all()
        ]
        logger.info("Active stocks: %d；待補交易日：%d", len(stock_ids), len(target_dates))

        ok_count = 0
        fail_count = 0
        for td in target_dates:
            try:
                status = _backfill_one_day(db, td, client, stock_ids)
                if status == "ok":
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.exception("補抓 %s 失敗：%s", td, e)
                fail_count += 1

        logger.info("=" * 60)
        logger.info("完成：成功 %d / 失敗 %d / 共 %d 個交易日",
                    ok_count, fail_count, len(target_dates))

        if fail_count == 0:
            return 0
        if ok_count == 0:
            return 2
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
