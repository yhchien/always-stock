"""
一次性回補 `market_stress_indicators`（M27 Market Regime v2）歷史資料。

FinMind 這批 dataset（TX 期貨法人 OI / TXO 法人量／OI / 美股指數 / 商品 / 匯率）
「單一標的 + 區間」查詢會一次回傳完整區間（已驗證，非「只回 start_date」那類
坑），所以整段回補只需要每個資料源各打 1 次 API，配額成本極低。

用法：
    python3 scripts/backfill_market_stress_indicators.py --days 400
    python3 scripts/backfill_market_stress_indicators.py --start 2025-08-01 --end 2026-09-04
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=400, help="往回回補幾個曆日（預設 400）")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD（優先於 --days）")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD，預設今天")
    args = parser.parse_args()

    from app.database import SessionLocal
    from etl.finmind_sdk_client import FinMindSDKClient
    from etl.market_stress_indicators_sdk import (
        fetch_and_upsert_market_stress_indicators,
    )

    token = os.getenv("FINMIND_TOKEN")
    if not token:
        logger.error("FINMIND_TOKEN environment variable not set")
        return 1

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = (
        date.fromisoformat(args.start) if args.start else end_date - timedelta(days=args.days)
    )

    logger.info("Backfilling market_stress_indicators: %s ~ %s", start_date, end_date)

    client = FinMindSDKClient(token)
    db = SessionLocal()
    try:
        result = fetch_and_upsert_market_stress_indicators(db, start_date, end_date, client)
        logger.info("Result: %s", result)
        return 0 if result.get("status") in ("ok", "no_data") else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
