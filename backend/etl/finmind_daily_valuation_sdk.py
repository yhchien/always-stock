"""
FinMind ETL：日常估值資料抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockPER
更新頻率：每日或批次
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_daily_valuation_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取估值資料（P/E、P/B、殖利率）並以 bulk upsert 寫入 DB。
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
        f"Fetching daily valuation for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_per(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No valuation data returned from FinMind")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} valuation records, writing to DB...")

        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["per_val"] = pd.to_numeric(df.get("PER"), errors="coerce") if "PER" in df.columns else None
        df["pbr_val"] = pd.to_numeric(df.get("PBR"), errors="coerce") if "PBR" in df.columns else None
        df["dy_val"] = pd.to_numeric(df.get("dividend_yield"), errors="coerce") if "dividend_yield" in df.columns else None

        records = df[["trade_date", "stock_id", "per_val", "pbr_val", "dy_val"]].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO daily_valuation
                    (trade_date, stock_id, per, pbr, dividend_yield, source, ingested_at)
                VALUES
                    (:trade_date, :stock_id, :per_val, :pbr_val, :dy_val, 'finmind', NOW())
                ON CONFLICT (trade_date, stock_id) DO UPDATE SET
                    per          = EXCLUDED.per,
                    pbr          = EXCLUDED.pbr,
                    dividend_yield = EXCLUDED.dividend_yield,
                    source       = 'finmind',
                    ingested_at  = NOW()
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Daily valuation ETL completed: upserted={result['upserted']}")
        return result

    except RuntimeError as e:
        if "quota" in str(e).lower():
            result["status"] = "insufficient_quota"
        else:
            result["status"] = "error"
        logger.error(f"Error: {e}")
        return result

    except Exception as e:
        logger.error(f"Daily valuation ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
