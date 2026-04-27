"""
FinMind ETL：每日股價抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockPrice
更新頻率：每日或批次（市場收盤後或補歷史資料）
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_daily_price_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取股價並以 bulk upsert 寫入 DB。

    Args:
        db: SQLAlchemy session
        stock_ids: 股票代碼列表
        start_date: 開始日期
        end_date: 結束日期
        client: FinMind SDK 客戶端
    """
    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "upserted": 0,
        "status": "ok",
    }

    logger.info(
        f"Fetching daily prices for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_taiwan_stock_price_dataset(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )

        # dataset-level 回的是全市場 (含未追蹤的興櫃 / 零股 / 權證等)，先過濾到 stocks_master
        if df is not None and not df.empty and stock_ids:
            df = df[df["stock_id"].astype(str).str.strip().isin(set(stock_ids))].copy()

        if df is None or df.empty:
            # 區分「假日（非交易日）」vs「FinMind 尚未同步完成」：
            # - 配額健康 → 判定為 holiday，ETL 應短路並視為 pass
            # - 配額 critical → 退回 no_data，交由 orchestrator 的 CRITICAL retry 處理
            quota_info = getattr(client, "quota_info", None) or {}
            quota_status = quota_info.get("status")
            if quota_status in ("ok", "warning"):
                logger.info(
                    "No data returned from FinMind with healthy quota (%s) — "
                    "assuming non-trading day",
                    quota_status,
                )
                result["status"] = "holiday"
            else:
                logger.warning(
                    "No data returned from FinMind (quota: %s)",
                    quota_status,
                )
                result["status"] = "no_data"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} price records, writing to DB...")

        # 欄位映射
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["open_price"] = pd.to_numeric(df["open"], errors="coerce")
        df["high_price"] = pd.to_numeric(df["max"], errors="coerce")
        df["low_price"] = pd.to_numeric(df["min"], errors="coerce")
        df["close_price"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["Trading_Volume"], errors="coerce")
        df["turnover"] = pd.to_numeric(df["Trading_money"], errors="coerce")
        df["spread_val"] = pd.to_numeric(df.get("spread"), errors="coerce") if "spread" in df.columns else None

        records = df[["trade_date", "stock_id", "open_price", "high_price",
                       "low_price", "close_price", "volume", "turnover", "spread_val"]].to_dict("records")

        # Bulk upsert（分批）
        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO daily_price
                    (trade_date, stock_id, open_price, high_price, low_price,
                     close_price, volume, turnover, spread, source, ingested_at)
                VALUES
                    (:trade_date, :stock_id, :open_price, :high_price, :low_price,
                     :close_price, :volume, :turnover, :spread_val, 'finmind', CURRENT_TIMESTAMP)
                ON CONFLICT (trade_date, stock_id) DO UPDATE SET
                    open_price   = EXCLUDED.open_price,
                    high_price   = EXCLUDED.high_price,
                    low_price    = EXCLUDED.low_price,
                    close_price  = EXCLUDED.close_price,
                    volume       = EXCLUDED.volume,
                    turnover     = EXCLUDED.turnover,
                    spread       = EXCLUDED.spread,
                    source       = 'finmind',
                    ingested_at  = CURRENT_TIMESTAMP
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Daily price ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Daily price ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
