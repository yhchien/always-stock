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
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Callable, Dict, Any, List
import json
import argparse

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

CRITICAL_STEPS = {"daily_price", "inst_flow"}
# no_data 視為可延續狀態（非 critical step 的空資料是正常情境，例如月營收只有每月 10 日才有新值）
RESUMEABLE_STEP_STATUSES = {"ok", "partial", "skipped", "empty", "no_data"}

# CRITICAL step 遇到 no_data 時的重試間隔（秒），對齊 FinMind 慢同步 dataset
NO_DATA_RETRY_SCHEDULE = [600, 1200]  # 10 min, 20 min


def determine_overall_status(step_results: Dict[str, Any]) -> str:
    """依各步驟結果判定整體 ETL 狀態。"""
    if any(
        step_result and step_result.get("status") == "insufficient_quota"
        for step_result in step_results.values()
    ):
        return "insufficient_quota"

    # CRITICAL step 若為 error 或最終仍 no_data（retry 用盡）→ 整包 error
    if any(
        step_name in CRITICAL_STEPS
        and step_result
        and step_result.get("status") in ("error", "no_data")
        for step_name, step_result in step_results.items()
    ):
        return "error"

    if any(
        step_result
        and step_result.get("status") not in RESUMEABLE_STEP_STATUSES
        for step_result in step_results.values()
    ):
        return "partial"

    return "ok"


def _run_critical_step_with_retry(
    step_name: str,
    step_func: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    CRITICAL step 若回 no_data，依 NO_DATA_RETRY_SCHEDULE 重試。

    只對 no_data 重試（FinMind 尚未同步完成）；真正 exception 的 error 不重試。
    """
    try:
        result = step_func()
    except Exception as e:
        logger.error(f"[{step_name}] first attempt raised: {e}")
        return {"status": "error", "error": str(e)}

    for attempt, delay in enumerate(NO_DATA_RETRY_SCHEDULE, start=1):
        if result.get("status") != "no_data":
            return result
        logger.warning(
            f"[{step_name}] returned no_data, retry #{attempt}/"
            f"{len(NO_DATA_RETRY_SCHEDULE)} in {delay}s"
        )
        time.sleep(delay)
        try:
            result = step_func()
            logger.info(
                f"[{step_name}] retry #{attempt} result: {result.get('status')}"
            )
        except Exception as e:
            logger.error(f"[{step_name}] retry #{attempt} raised: {e}")
            return {"status": "error", "error": str(e)}

    return result


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
        steps: List[str] = None,
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
        from etl.fetch_stock_master import fetch_and_upsert_stock_master
        from etl.finmind_daily_price_sdk import fetch_and_upsert_daily_price_finmind_sdk
        from etl.finmind_inst_flow_sdk import fetch_and_upsert_inst_flow_finmind_sdk
        from etl.finmind_daily_valuation_sdk import fetch_and_upsert_daily_valuation_finmind_sdk
        from etl.finmind_monthly_revenue_sdk import fetch_and_upsert_monthly_revenue_sdk
        from etl.finmind_financial_statement_sdk import fetch_and_upsert_financial_statement_sdk
        from etl.finmind_broker_trade_sdk import fetch_and_upsert_broker_trade_agg_sdk

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        # 決定要執行的步驟（None 代表全部）
        ALL_STEPS = ["stocks_master", "daily_price", "inst_flow", "daily_valuation",
                     "monthly_revenue", "financial_statement", "broker_trade_agg"]
        active_steps = set(steps) if steps else set(ALL_STEPS)

        start_time = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"Starting FinMind SDK ETL from {start_date} to {end_date}")
        if steps:
            logger.info(f"Running steps: {', '.join(steps)}")
        logger.info("=" * 80)

        result = {
            "start_date": start_date,
            "end_date": end_date,
            "status": "ok",
            "results": {},
            "duration_seconds": 0,
        }

        try:
            # 0. stocks_master 更新（FinMind TaiwanStockInfo + TaiwanStockIndustryChain）
            #    非 CRITICAL：失敗只 warn，不影響後續 price/flow 寫入
            if "stocks_master" in active_steps:
                logger.info("\n[0/7] Updating stocks_master (FinMind industry chain)...")
                try:
                    n = fetch_and_upsert_stock_master(db, token=self.client.token)
                    result["results"]["stocks_master"] = {"status": "ok", "count": n}
                    logger.info(f"✓ stocks_master: {n} stocks")
                except Exception as e:
                    logger.warning(f"⚠ stocks_master update failed (non-critical): {e}")
                    result["results"]["stocks_master"] = {"status": "error", "error": str(e)}
            else:
                logger.info("\n[0/7] stocks_master: skipped")
                result["results"]["stocks_master"] = {"status": "skipped"}

            # 取得股票清單（此時 stocks_master 若剛更新完，會拿到最新名單）
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

            # 1. 每日股價（一次 batch fetch，CRITICAL → no_data 時重試）
            if "daily_price" in active_steps:
                logger.info("\n[1/7] Fetching daily prices (SDK batch)...")
                price_result = _run_critical_step_with_retry(
                    "daily_price",
                    lambda: fetch_and_upsert_daily_price_finmind_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    ),
                )
                result["results"]["daily_price"] = price_result
                logger.info(f"✓ Daily prices: {price_result['status']}")
            else:
                logger.info("\n[1/7] Daily prices: skipped")
                result["results"]["daily_price"] = {"status": "skipped"}

            # 2. 三大法人買賣超（一次 batch fetch，CRITICAL → no_data 時重試）
            if "inst_flow" in active_steps:
                logger.info("\n[2/7] Fetching institutional flows (SDK batch)...")
                inst_result = _run_critical_step_with_retry(
                    "inst_flow",
                    lambda: fetch_and_upsert_inst_flow_finmind_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    ),
                )
                result["results"]["inst_flow"] = inst_result
                logger.info(f"✓ Institutional flows: {inst_result['status']}")
            else:
                logger.info("\n[2/7] Institutional flows: skipped")
                result["results"]["inst_flow"] = {"status": "skipped"}

            # 3. 日常估值（一次 batch fetch）
            if "daily_valuation" in active_steps:
                logger.info("\n[3/7] Fetching daily valuation (SDK batch)...")
                try:
                    valuation_result = fetch_and_upsert_daily_valuation_finmind_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    )
                    result["results"]["daily_valuation"] = valuation_result
                    logger.info(f"✓ Daily valuation: {valuation_result['status']}")
                except Exception as e:
                    logger.error(f"✗ Daily valuation ETL failed: {e}")
                    result["results"]["daily_valuation"] = {"status": "error", "error": str(e)}
            else:
                logger.info("\n[3/7] Daily valuation: skipped")
                result["results"]["daily_valuation"] = {"status": "skipped"}

            # 4. 月營收
            if "monthly_revenue" in active_steps:
                logger.info("\n[4/7] Fetching monthly revenue (SDK batch)...")
                try:
                    rev_result = fetch_and_upsert_monthly_revenue_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    )
                    result["results"]["monthly_revenue"] = rev_result
                    logger.info(f"✓ Monthly revenue: {rev_result['status']}")
                except Exception as e:
                    logger.error(f"✗ Monthly revenue ETL failed: {e}")
                    result["results"]["monthly_revenue"] = {"status": "error", "error": str(e)}
            else:
                logger.info("\n[4/7] Monthly revenue: skipped")
                result["results"]["monthly_revenue"] = {"status": "skipped"}

            # 5. 財報（季報）
            if "financial_statement" in active_steps:
                logger.info("\n[5/7] Fetching financial statements (SDK batch)...")
                try:
                    fs_result = fetch_and_upsert_financial_statement_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    )
                    result["results"]["financial_statement"] = fs_result
                    logger.info(f"✓ Financial statements: {fs_result['status']}")
                except Exception as e:
                    logger.error(f"✗ Financial statement ETL failed: {e}")
                    result["results"]["financial_statement"] = {"status": "error", "error": str(e)}
            else:
                logger.info("\n[5/7] Financial statements: skipped")
                result["results"]["financial_statement"] = {"status": "skipped"}

            # 6. 券商分點聚合（Sponsor，起點 2021-06-30）
            if "broker_trade_agg" in active_steps:
                logger.info("\n[6/7] Fetching broker trade agg (Sponsor, from 2021-06-30)...")
                try:
                    broker_result = fetch_and_upsert_broker_trade_agg_sdk(
                        db, stock_ids, start_date, end_date, self.client
                    )
                    result["results"]["broker_trade_agg"] = broker_result
                    logger.info(f"✓ Broker trade agg: {broker_result['status']}")
                except Exception as e:
                    logger.error(f"✗ Broker trade agg ETL failed: {e}")
                    result["results"]["broker_trade_agg"] = {"status": "error", "error": str(e)}
            else:
                logger.info("\n[6/7] Broker trade agg: skipped")
                result["results"]["broker_trade_agg"] = {"status": "skipped"}

            # 判定整體狀態
            # 核心步驟失敗（price / inst_flow）→ error (exit 3)
            # 次要步驟失敗（valuation / revenue / financial / broker）→ partial (exit 1)
            result["status"] = determine_overall_status(result["results"])

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

    def save_etl_log(self, result: Dict[str, Any], log_dir: str = "logs") -> str:
        """保存 ETL 執行日誌"""
        log_dir_path = Path(log_dir)
        if not log_dir_path.is_absolute():
            log_dir_path = BACKEND_DIR / log_dir_path
        log_dir_path.mkdir(parents=True, exist_ok=True)

        # 轉換 datetime 為字串
        result_json = json.loads(json.dumps(result, default=str))

        start_date_str = result_json["start_date"]
        end_date_str = result_json["end_date"]
        log_file = log_dir_path / f"finmind_sdk_etl_{start_date_str}_to_{end_date_str}.json"

        with open(log_file, "w") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)

        logger.info(f"Log saved to {log_file}")
        return str(log_file)


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
        "--steps",
        type=str,
        default=None,
        help=(
            "只執行指定步驟，逗號分隔（預設全部）。"
            "可選：stocks_master,daily_price,inst_flow,daily_valuation,"
            "monthly_revenue,financial_statement,broker_trade_agg"
        )
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

    # 解析 --steps
    steps = [s.strip() for s in args.steps.split(",")] if args.steps else None

    if args.date:
        # 單日模式
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {args.date}")
            return 1

        logger.info(f"Running single-day ETL for {target_date}")
        result = orchestrator.run_etl_range(target_date, target_date, steps=steps)

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
        result = orchestrator.run_etl_range(start_date, end_date, steps=steps)

    else:
        # 預設：昨天
        target_date = date.today() - timedelta(days=1)
        logger.info(f"Running default ETL for {target_date}")
        result = orchestrator.run_etl_range(target_date, target_date, steps=steps)

    if not args.skip_log:
        orchestrator.save_etl_log(result)

    # 回傳狀態碼
    # 0 = ok, 1 = partial, 2 = insufficient_quota, 3 = error
    if result["status"] == "ok":
        return 0
    elif result["status"] == "partial":
        return 1
    elif result["status"] == "insufficient_quota":
        return 2
    else:
        return 3


if __name__ == "__main__":
    sys.exit(main())
