"""
FinMind ETL：財報資料抓取 (SDK 版本)
資料集：TaiwanStockFinancialStatements
需要 Sponsor 權限
寫入表：financial_statement
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_financial_statement_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取財報資料並以 bulk upsert 寫入 DB。

    FinMind 財報欄位：
      date        - 財報公告日期
      stock_id
      type        - 報表類型（綜合損益表 / 資產負債表 / 現金流量表）
      value       - 數值
      origin_name - 項目名稱（如 EPS、Revenue、NetIncome）
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
        f"Fetching financial statements for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_financial_statements(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No financial statement data returned from FinMind (expected for months without quarterly reports)")
            result["status"] = "ok"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} financial statement records, writing to DB...")

        df["report_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["item_name_val"] = df.get("origin_name", df.get("type", "unknown")).astype(str).str.strip()
        df["item_code_val"] = df.get("type", None)
        df["value_val"] = pd.to_numeric(df["value"], errors="coerce")

        records = df[["report_date", "stock_id", "item_name_val",
                       "item_code_val", "value_val"]].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO financial_statement
                    (report_date, stock_id, item_name, item_code,
                     value, period_type, source, ingested_at)
                VALUES
                    (:report_date, :stock_id, :item_name_val, :item_code_val,
                     :value_val, 'quarterly', 'finmind', NOW())
                ON CONFLICT ON CONSTRAINT uq_finstatement_date_stock_item DO UPDATE SET
                    item_code   = EXCLUDED.item_code,
                    value       = EXCLUDED.value,
                    source      = 'finmind',
                    ingested_at = NOW()
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Financial statement ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Financial statement ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
