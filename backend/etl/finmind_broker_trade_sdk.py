"""
FinMind ETL：券商分點買賣超抓取 (SDK 版本)
資料集：TaiwanStockTradingDailyReportSecIdAgg
需要 Sponsor 權限；歷史起點 2021-06-30
寫入表：broker_trade_agg
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000
BROKER_TRADE_START_DATE = "2021-06-30"


def fetch_and_upsert_broker_trade_agg_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取券商分點聚合資料並以 bulk upsert 寫入 DB。

    注意：資料起點為 2021-06-30，早於此日期的 start_date 會自動調整。
    """
    from datetime import date as date_type
    import datetime as dt

    broker_start = dt.date(2021, 6, 30)
    if start_date < broker_start:
        logger.info(f"Broker trade data only available from {BROKER_TRADE_START_DATE}, adjusting start_date")
        start_date = broker_start

    if start_date > end_date:
        logger.info("start_date > end_date after adjustment, skipping broker trade")
        return {"status": "skipped", "reason": "before_data_start", "upserted": 0}

    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "upserted": 0,
        "status": "ok",
    }

    logger.info(
        f"Fetching broker trade agg for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_broker_trade_agg(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No broker trade data returned from FinMind")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} broker trade records, writing to DB...")

        # FinMind SecIdAgg 欄位：date, stock_id, securities_trader_id, securities_trader, buy, sell
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["broker_id"] = df["securities_trader_id"].astype(str).str.strip()
        df["broker_name"] = df.get("securities_trader", df["broker_id"]).astype(str).str.strip()
        df["buy_shares"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
        df["sell_shares"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)
        df["net_shares"] = df["buy_shares"] - df["sell_shares"]

        records = df[["trade_date", "stock_id", "broker_id", "broker_name",
                       "buy_shares", "sell_shares", "net_shares"]].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO broker_trade_agg
                    (trade_date, stock_id, broker_id, broker_name,
                     buy_shares, sell_shares, net_shares, source, ingested_at)
                VALUES
                    (:trade_date, :stock_id, :broker_id, :broker_name,
                     :buy_shares, :sell_shares, :net_shares, 'finmind', NOW())
                ON CONFLICT ON CONSTRAINT uq_broker_agg_date_stock_broker DO UPDATE SET
                    broker_name  = EXCLUDED.broker_name,
                    buy_shares   = EXCLUDED.buy_shares,
                    sell_shares  = EXCLUDED.sell_shares,
                    net_shares   = EXCLUDED.net_shares,
                    source       = 'finmind',
                    ingested_at  = NOW()
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Broker trade agg ETL completed: upserted={result['upserted']}")
        return result

    except RuntimeError as e:
        if "quota" in str(e).lower():
            logger.error(f"Insufficient quota: {e}")
            result["status"] = "insufficient_quota"
        else:
            logger.error(f"Runtime error: {e}")
            result["status"] = "error"
        return result

    except Exception as e:
        logger.error(f"Broker trade agg ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
