"""
FinMind ETL 協調器
統一管理所有 FinMind ETL，支援平行執行、錯誤處理、日誌記錄

使用方式：
    # 執行當日 ETL
    python run_finmind_etl.py --date 2026-04-13
    
    # 執行完整 backfill（2021-06-30 到今日）
    python run_finmind_etl.py --backfill
"""

import logging
import sys
import os
from datetime import datetime, date, timedelta
from typing import Dict, Any, List
import json
import argparse

logger = logging.getLogger(__name__)


class FinMindETLOrchestrator:
    """FinMind ETL 協調器"""

    def __init__(self, client: Any = None):
        """
        初始化協調器

        Args:
            client: FinMind HTTP 客戶端（若為 None 會自動建立）
        """
        from etl.http_client import FinMindHTTPClient

        if client is None:
            token = os.getenv("FINMIND_TOKEN")
            if not token:
                raise ValueError("FINMIND_TOKEN environment variable not set")
            self.client = FinMindHTTPClient(token)
        else:
            self.client = client

        self.results = {}

    def run_daily_etl(
        self,
        trade_date: date,
        db: Any = None,
        parallel: bool = False,
    ) -> Dict[str, Any]:
        """
        執行當日完整 ETL

        Args:
            trade_date: 交易日期
            db: SQLAlchemy session（若為 None 會自動建立）
            parallel: 是否並行執行（超時考量，建議設為 False）

        Returns:
            {
                "date": date,
                "status": "ok" | "partial" | "error",
                "results": {
                    "daily_price": {...},
                    "inst_flow": {...},
                    "broker_trade": {...},
                    "daily_valuation": {...},
                },
                "start_time": datetime,
                "end_time": datetime,
                "duration_seconds": float,
            }
        """
        from app.database import SessionLocal
        from etl.finmind_daily_price import fetch_and_upsert_daily_price_finmind
        from etl.finmind_inst_flow import fetch_and_upsert_inst_flow_finmind
        from etl.finmind_broker_trade import fetch_and_upsert_broker_trade_finmind
        from etl.finmind_daily_valuation import fetch_and_upsert_daily_valuation_finmind

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        start_time = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"Starting FinMind daily ETL for {trade_date}")
        logger.info("=" * 80)

        result = {
            "date": trade_date,
            "status": "ok",
            "results": {},
            "start_time": start_time,
            "end_time": None,
            "duration_seconds": 0,
        }

        try:
            # 1. 每日股價
            logger.info("\n[1/4] Fetching daily prices...")
            try:
                price_result = fetch_and_upsert_daily_price_finmind(db, trade_date, self.client)
                result["results"]["daily_price"] = price_result
                logger.info(f"✓ Daily prices: {price_result['status']}")
            except Exception as e:
                logger.error(f"✗ Daily price ETL failed: {e}")
                result["results"]["daily_price"] = {"status": "error", "error": str(e)}

            # 2. 三大法人買賣超
            logger.info("\n[2/4] Fetching institutional flows...")
            try:
                inst_result = fetch_and_upsert_inst_flow_finmind(db, trade_date, self.client)
                result["results"]["inst_flow"] = inst_result
                logger.info(f"✓ Institutional flows: {inst_result['status']}")
            except Exception as e:
                logger.error(f"✗ Institutional flow ETL failed: {e}")
                result["results"]["inst_flow"] = {"status": "error", "error": str(e)}

            # 3. 券商交易資料（Sponsor 限制）
            logger.info("\n[3/4] Fetching broker trade data...")
            try:
                broker_result = fetch_and_upsert_broker_trade_finmind(db, trade_date, self.client)
                result["results"]["broker_trade"] = broker_result
                logger.info(f"✓ Broker trades: {broker_result['status']}")
            except Exception as e:
                logger.error(f"✗ Broker trade ETL failed: {e}")
                result["results"]["broker_trade"] = {"status": "error", "error": str(e)}

            # 4. 日常估值（Sponsor 限制）
            logger.info("\n[4/4] Fetching daily valuation...")
            try:
                valuation_result = fetch_and_upsert_daily_valuation_finmind(db, trade_date, self.client)
                result["results"]["daily_valuation"] = valuation_result
                logger.info(f"✓ Daily valuation: {valuation_result['status']}")
            except Exception as e:
                logger.error(f"✗ Daily valuation ETL failed: {e}")
                result["results"]["daily_valuation"] = {"status": "error", "error": str(e)}

            # 判定整體狀態
            statuses = [r.get("status") for r in result["results"].values()]
            if any(s == "error" for s in statuses):
                result["status"] = "error"
            elif any(s == "partial" for s in statuses):
                result["status"] = "partial"
            elif any(s == "sponsor_only" for s in statuses):
                result["status"] = "sponsor_only"
            else:
                result["status"] = "ok"

        except Exception as e:
            logger.error(f"Unexpected error during daily ETL: {e}")
            result["status"] = "error"

        finally:
            end_time = datetime.utcnow()
            result["end_time"] = end_time
            result["duration_seconds"] = (end_time - start_time).total_seconds()

            logger.info("\n" + "=" * 80)
            logger.info(f"ETL Summary: {result['status'].upper()}")
            logger.info(f"Duration: {result['duration_seconds']:.1f}s")
            logger.info("=" * 80)

            if close_db:
                db.close()

        return result

    def save_etl_log(self, result: Dict[str, Any], log_dir: str = "backend/logs") -> str:
        """
        保存 ETL 執行日誌

        Args:
            result: ETL 結果字典
            log_dir: 日誌目錄

        Returns:
            日誌檔路徑
        """
        os.makedirs(log_dir, exist_ok=True)

        # 轉換 datetime 為字串
        result_json = json.loads(
            json.dumps(result, default=str)
        )

        date_str = result_json["date"]
        log_file = os.path.join(
            log_dir,
            f"finmind_etl_log_{date_str}.json"
        )

        with open(log_file, "w") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)

        logger.info(f"Log saved to {log_file}")
        return log_file


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="Run FinMind ETL")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="交易日期 (YYYY-MM-DD，預設為昨天)"
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="執行完整 backfill（2021-06-30 到今日）"
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

    # 建立協調器
    orchestrator = FinMindETLOrchestrator()

    if args.backfill:
        logger.info("Backfill mode: this will take a long time")
        # TODO：實裝 backfill 邏輯
        logger.error("Backfill not yet implemented")
        return 1

    else:
        # 單日執行
        if args.date:
            try:
                target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                logger.error(f"Invalid date format: {args.date}")
                return 1
        else:
            # 預設為昨天
            target_date = (datetime.now().date() - timedelta(days=1))

        logger.info(f"Running ETL for {target_date}")

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
