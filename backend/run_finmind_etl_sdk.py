"""
FinMind ETL 協調器 (SDK 版本)
統一管理所有 FinMind SDK ETL，支援 batch 執行、配額管理、日誌記錄

使用方式：
    # 執行當日 ETL
    python run_finmind_etl_sdk.py --date 2026-04-13
    
    # 執行日期區間 ETL（推薦用於 backfill）
    python run_finmind_etl_sdk.py --start-date 2026-01-01 --end-date 2026-04-13
"""

import logging
import sys
import os
from datetime import datetime, date, timedelta
from typing import Dict, Any, List
import json
import argparse

logger = logging.getLogger(__name__)


class FinMindETLOrchestratorSDK:
    """FinMind SDK ETL 協調器"""

    def __init__(self, token: str):
        """
        初始化協調器

        Args:
            token: FinMind API token
        """
        from etl.finmind_sdk_client import FinMindSDKClient

        self.client = FinMindSDKClient(token)
        self.results = {}

    def get_stock_ids(self, db: Any) -> List[str]:
        """取得上市公司股票代碼清單"""
        from app.models import StockMaster

        stocks = db.query(StockMaster.stock_id).filter(
            StockMaster.market == "twse",
            StockMaster.is_active == True,
        ).all()

        return [s[0] for s in stocks]

    def run_daily_etl(
        self,
        trade_date: date,
        db: Any = None,
    ) -> Dict[str, Any]:
        """
        執行當日完整 ETL（實際上是 1 天區間）

        Args:
            trade_date: 交易日期
            db: SQLAlchemy session

        Returns:
            {
                "date": date,
                "status": "ok" | "partial" | "error",
                "results": {...},
                "duration_seconds": float,
            }
        """
        return self.run_etl_range(trade_date, trade_date, db)

    def run_etl_range(
        self,
        start_date: date,
        end_date: date,
        db: Any = None,
    ) -> Dict[str, Any]:
        """
        執行日期區間 ETL（推薦用於大量級 backfill）

        主要改進：
        - 一次 batch 查詢多天多股
        - SDK 內部自動並行（async）
        - API 呼叫次數大幅減少

        Args:
            start_date: 開始日期
            end_date: 結束日期
            db: SQLAlchemy session

        Returns:
            {
                "start_date": date,
                "end_date": date,
                "status": "ok" | "partial" | "error" | "insufficient_quota",
                "results": {
                    "daily_price": {...},
                    "inst_flow": {...},
                    "daily_valuation": {...},
                },
                "duration_seconds": float,
            }
        """
        from app.database import SessionLocal
        from etl.finmind_daily_price_sdk import fetch_and_upsert_daily_price_finmind_sdk
        from etl.finmind_inst_flow_sdk import fetch_and_upsert_inst_flow_finmind_sdk
        from etl.finmind_daily_valuation_sdk import fetch_and_upsert_daily_valuation_finmind_sdk

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        start_time = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"Starting FinMind SDK ETL from {start_date} to {end_date}")
        logger.info("=" * 80)

        result = {
            "start_date": start_date,
            "end_date": end_date,
            "status": "ok",
            "results": {},
            "duration_seconds": 0,
        }

        try:
            # 取得股票清單
            stock_ids = self.get_stock_ids(db)
            logger.info(f"Total stocks to process: {len(stock_ids)}")

            # 檢查配額
            quota = self.client.get_quota()
            logger.info(
                f"Quota status: {quota.get('status')}, "
                f"remaining: {quota.get('remaining', 'N/A')}"
            )

            if quota.get("status") == "critical":
                logger.error("Quota critical, aborting ETL")
                result["status"] = "insufficient_quota"
                return result

            # 1. 每日股價（一次 batch fetch）
            logger.info("\n[1/3] Fetching daily prices (SDK batch)...")
            try:
                price_result = fetch_and_upsert_daily_price_finmind_sdk(
                    db, stock_ids, start_date, end_date, self.client
                )
                result["results"]["daily_price"] = price_result
                logger.info(f"✓ Daily prices: {price_result['status']}")
            except Exception as e:
                logger.error(f"✗ Daily price ETL failed: {e}")
                result["results"]["daily_price"] = {"status": "error", "error": str(e)}

            # 2. 三大法人買賣超（一次 batch fetch）
            logger.info("\n[2/3] Fetching institutional flows (SDK batch)...")
            try:
                inst_result = fetch_and_upsert_inst_flow_finmind_sdk(
                    db, stock_ids, start_date, end_date, self.client
                )
                result["results"]["inst_flow"] = inst_result
                logger.info(f"✓ Institutional flows: {inst_result['status']}")
            except Exception as e:
                logger.error(f"✗ Institutional flow ETL failed: {e}")
                result["results"]["inst_flow"] = {"status": "error", "error": str(e)}

            # 3. 日常估值（一次 batch fetch）
            logger.info("\n[3/3] Fetching daily valuation (SDK batch)...")
            try:
                valuation_result = fetch_and_upsert_daily_valuation_finmind_sdk(
                    db, stock_ids, start_date, end_date, self.client
                )
                result["results"]["daily_valuation"] = valuation_result
                logger.info(f"✓ Daily valuation: {valuation_result['status']}")
            except Exception as e:
                logger.error(f"✗ Daily valuation ETL failed: {e}")
                result["results"]["daily_valuation"] = {"status": "error", "error": str(e)}

            # 判定整體狀態
            statuses = [r.get("status") for r in result["results"].values() if r]
            if any(s == "error" for s in statuses):
                result["status"] = "error"
            elif any(s == "partial" for s in statuses):
                result["status"] = "partial"
            elif any(s == "insufficient_quota" for s in statuses):
                result["status"] = "insufficient_quota"
            else:
                result["status"] = "ok"

        except Exception as e:
            logger.error(f"Unexpected error during ETL: {e}")
            result["status"] = "error"

        finally:
            end_time = datetime.utcnow()
            result["duration_seconds"] = (end_time - start_time).total_seconds()

            logger.info("\n" + "=" * 80)
            logger.info(f"ETL Summary: {result['status'].upper()}")
            logger.info(f"Duration: {result['duration_seconds']:.1f}s")
            logger.info("=" * 80)

            if close_db:
                db.close()

        return result

    def save_etl_log(self, result: Dict[str, Any], log_dir: str = "backend/logs") -> str:
        """保存 ETL 執行日誌"""
        os.makedirs(log_dir, exist_ok=True)

        # 轉換 datetime 為字串
        result_json = json.loads(json.dumps(result, default=str))

        start_date_str = result_json["start_date"]
        end_date_str = result_json["end_date"]
        log_file = os.path.join(
            log_dir,
            f"finmind_sdk_etl_{start_date_str}_to_{end_date_str}.json"
        )

        with open(log_file, "w") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)

        logger.info(f"Log saved to {log_file}")
        return log_file


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="Run FinMind SDK ETL")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="單日執行 (YYYY-MM-DD，預設為昨天)"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="日期區間開始 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="日期區間結束 (YYYY-MM-DD，預設為今天)"
    )
    parser.add_argument(
        "--skip-log",
        action="store_true",
        help="跳過日誌保存"
    )

    args = parser.parse_args()

    # 設定日誌
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 取得 token
    token = os.getenv("FINMIND_TOKEN")
    if not token:
        logger.error("FINMIND_TOKEN environment variable not set")
        return 1

    # 建立協調器
    orchestrator = FinMindETLOrchestratorSDK(token)

    if args.date:
        # 單日模式
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {args.date}")
            return 1

        logger.info(f"Running single-day ETL for {target_date}")
        result = orchestrator.run_daily_etl(target_date)

    elif args.start_date:
        # 日期區間模式
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
            end_date = (
                datetime.strptime(args.end_date, "%Y-%m-%d").date()
                if args.end_date
                else date.today()
            )
        except ValueError as e:
            logger.error(f"Invalid date format: {e}")
            return 1

        logger.info(f"Running range ETL from {start_date} to {end_date}")
        result = orchestrator.run_etl_range(start_date, end_date)

    else:
        # 預設：昨天
        target_date = date.today() - timedelta(days=1)
        logger.info(f"Running default ETL for {target_date}")
        result = orchestrator.run_daily_etl(target_date)

    if not args.skip_log:
        orchestrator.save_etl_log(result)

    # 回傳狀態碼
    if result["status"] == "ok":
        return 0
    elif result["status"] == "partial":
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
