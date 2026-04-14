"""
FinMind ETL：月營收抓取 (SDK 版本)
資料集：TaiwanStockMonthRevenue
需要 Sponsor 權限；歷史起點約 2013-01-01
寫入表：monthly_revenue
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_monthly_revenue_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取月營收並以 bulk upsert 寫入 DB。

    FinMind 月營收欄位：
      date        - 公告日期
      stock_id
      country
      revenue     - 月營收（千元）
      revenue_month - 營收月份（YYYY-MM）
      revenue_year  - 營收年份
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
        f"Fetching monthly revenue for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_month_revenue(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No monthly revenue data returned from FinMind")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} monthly revenue records, writing to DB...")

        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["revenue_val"] = pd.to_numeric(df["revenue"], errors="coerce")

        # revenue_month 欄位格式為 "YYYY-MM"，轉成月末日期
        def to_month_end(row):
            try:
                yr = int(row.get("revenue_year") or str(row.get("revenue_month", ""))[:4])
                mo_str = str(row.get("revenue_month", ""))
                mo = int(mo_str[-2:]) if len(mo_str) >= 2 else 1
                import calendar
                last_day = calendar.monthrange(yr, mo)[1]
                return date(yr, mo, last_day)
            except Exception:
                return pd.to_datetime(row.get("date")).date()

        df["rev_month_date"] = df.apply(to_month_end, axis=1)

        # 計算 YoY / MoM（若 FinMind 有提供則直接用，否則留 None）
        df["yoy_pct_val"] = pd.to_numeric(df.get("revenue_year_difference_per"), errors="coerce") if "revenue_year_difference_per" in df.columns else None
        df["mom_pct_val"] = pd.to_numeric(df.get("revenue_month_difference_per"), errors="coerce") if "revenue_month_difference_per" in df.columns else None

        records = df[["rev_month_date", "stock_id", "revenue_val",
                       "yoy_pct_val", "mom_pct_val"]].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO monthly_revenue
                    (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source, ingested_at)
                VALUES
                    (:rev_month_date, :stock_id, :revenue_val, :yoy_pct_val, :mom_pct_val, 'finmind', CURRENT_TIMESTAMP)
                ON CONFLICT (revenue_month, stock_id) DO UPDATE SET
                    revenue     = EXCLUDED.revenue,
                    yoy_pct     = EXCLUDED.yoy_pct,
                    mom_pct     = EXCLUDED.mom_pct,
                    source      = 'finmind',
                    ingested_at = CURRENT_TIMESTAMP
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Monthly revenue ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Monthly revenue ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
