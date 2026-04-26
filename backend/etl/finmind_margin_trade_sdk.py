"""
FinMind ETL：融資融券每日餘額抓取（M23 訊號管線使用）
資料來源：FinMind TaiwanStockMarginPurchaseShortSale
更新頻率：每日（盤後同步）

欄位映射：
    MarginPurchaseTodayBalance        → margin_balance
    MarginPurchaseTodayBalance
        - MarginPurchaseYesterdayBalance → margin_change
    ShortSaleTodayBalance             → short_balance
    ShortSaleTodayBalance
        - ShortSaleYesterdayBalance      → short_change
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_margin_trade_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取融資融券餘額並以 bulk upsert 寫入 margin_trade。

    狀態語義：
        ok               寫入成功
        no_data          API 回空（假日或 FinMind 尚未同步）— 由 orchestrator 視為 non-CRITICAL
        insufficient_quota
        error
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
        f"Fetching margin/short balance for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_margin_purchase_short_sale(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No margin/short data returned from FinMind")
            result["status"] = "no_data"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} margin/short records, writing to DB...")

        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()

        margin_today = pd.to_numeric(df.get("MarginPurchaseTodayBalance"), errors="coerce")
        margin_yest = pd.to_numeric(df.get("MarginPurchaseYesterdayBalance"), errors="coerce")
        short_today = pd.to_numeric(df.get("ShortSaleTodayBalance"), errors="coerce")
        short_yest = pd.to_numeric(df.get("ShortSaleYesterdayBalance"), errors="coerce")

        df["margin_balance"] = margin_today
        df["margin_change"] = (margin_today - margin_yest)
        df["short_balance"] = short_today
        df["short_change"] = (short_today - short_yest)

        # 同 (trade_date, stock_id) 若 FinMind 重複回傳取最後一筆
        df = df.drop_duplicates(subset=["trade_date", "stock_id"], keep="last")

        # NaN → None 讓 SQL nullable 正確處理
        for col in ["margin_balance", "margin_change", "short_balance", "short_change"]:
            df[col] = df[col].astype("object").where(df[col].notna(), None)

        records = df[
            [
                "trade_date",
                "stock_id",
                "margin_balance",
                "margin_change",
                "short_balance",
                "short_change",
            ]
        ].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(
                text(
                    """
                    INSERT INTO margin_trade
                        (trade_date, stock_id,
                         margin_balance, margin_change,
                         short_balance, short_change,
                         source, ingested_at)
                    VALUES
                        (:trade_date, :stock_id,
                         :margin_balance, :margin_change,
                         :short_balance, :short_change,
                         'finmind', CURRENT_TIMESTAMP)
                    ON CONFLICT (trade_date, stock_id) DO UPDATE SET
                        margin_balance = EXCLUDED.margin_balance,
                        margin_change  = EXCLUDED.margin_change,
                        short_balance  = EXCLUDED.short_balance,
                        short_change   = EXCLUDED.short_change,
                        source         = 'finmind',
                        ingested_at    = CURRENT_TIMESTAMP
                    """
                ),
                batch,
            )
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Margin trade ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Margin trade ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
