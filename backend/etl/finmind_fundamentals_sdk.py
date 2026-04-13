"""
FinMind ETL：月營收與財報資料抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockMonthRevenue、TaiwanStockFinancialStatements
更新頻率：月營收（每月中）、財報（每季末）
"""

import logging
from datetime import date
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_and_upsert_monthly_revenue_finmind_sdk(
    db: Session,
    stock_ids: list,
    target_month_start: date,
    target_month_end: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取月營收資料

    注意：FinMind 的月營收通常在月中公布前月資料
    
    Args:
        db: SQLAlchemy session
        stock_ids: 股票代碼列表
        target_month_start: 目標月份開始日期
        target_month_end: 目標月份結束日期
        client: FinMind SDK 客戶端

    Returns:
        {
            "month": str (YYYY-MM),
            "total_records": int,
            "inserted": int,
            "updated": int,
            "status": "ok" | "partial" | "error" | "insufficient_quota" | "sponsor_only",
        }
    """
    from app.models import MonthlyRevenue

    month_str = target_month_start.strftime("%Y-%m")

    result = {
        "month": month_str,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "inserted": 0,
        "updated": 0,
        "status": "ok",
    }

    logger.info(f"Fetching monthly revenue for {month_str}")

    try:
        # 一次 batch fetch
        df = client.fetch_month_revenue(
            stock_id_list=stock_ids,
            start_date=target_month_start.strftime("%Y-%m-%d"),
            end_date=target_month_end.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning(f"No monthly revenue data for {month_str}")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} monthly revenue records")

        # 資料映射
        # FinMind 欄位：date (YYYY-MM 格式), stock_id, revenue, yoy, mom
        
        for _, row in df.iterrows():
            try:
                # FinMind 月營收的 date 通常是 "YYYY-MM" 格式，需要轉換
                date_str = str(row.get("date", "")).strip()
                if not date_str or len(date_str) < 7:
                    continue

                # 轉換為月末日期
                year_month = date_str[:7]  # YYYY-MM
                revenue_month = pd.to_datetime(year_month + "-01").to_period("M").end_time.date()

                stock_id = str(row.get("stock_id")).strip()

                if not stock_id or revenue_month is None:
                    continue

                row_data = {
                    "revenue_month": revenue_month,
                    "stock_id": stock_id,
                    "revenue": float(row.get("revenue")) if row.get("revenue") else None,
                    "yoy_pct": float(row.get("yoy")) if row.get("yoy") else None,
                    "mom_pct": float(row.get("mom")) if row.get("mom") else None,
                    "source": "finmind",
                }

                # Upsert
                existing = db.query(MonthlyRevenue).filter(
                    MonthlyRevenue.revenue_month == revenue_month,
                    MonthlyRevenue.stock_id == stock_id,
                ).first()

                if existing:
                    for key, value in row_data.items():
                        if key not in ("revenue_month", "stock_id"):
                            setattr(existing, key, value)
                    result["updated"] += 1
                else:
                    new_row = MonthlyRevenue(**row_data)
                    db.add(new_row)
                    result["inserted"] += 1

            except Exception as e:
                logger.error(f"Error processing revenue row: {e}")
                continue

            # 定期提交
            if (result["inserted"] + result["updated"]) % 500 == 0:
                db.commit()

        db.commit()

        if result["inserted"] + result["updated"] == 0:
            result["status"] = "error"
        else:
            result["status"] = "ok"

        logger.info(
            f"Monthly revenue ETL completed: {result['inserted']} inserted, {result['updated']} updated"
        )

        return result

    except RuntimeError as e:
        if "quota" in str(e).lower():
            result["status"] = "insufficient_quota"
        else:
            result["status"] = "error"
        logger.error(f"Error: {e}")
        return result

    except Exception as e:
        logger.error(f"Monthly revenue ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result


def fetch_and_upsert_financial_statements_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
    period_type: str = "quarterly",
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取財報資料（需要 Sponsor 權限）

    Note: SDK 可能不直接支援 financial_statements，此函式為佔位符
    實際上可能需要用 REST API 或客製 SDK 擴充

    Args:
        db: SQLAlchemy session
        stock_ids: 股票代碼列表
        start_date: 開始日期
        end_date: 結束日期
        client: FinMind SDK 客戶端
        period_type: "quarterly" 或 "annual"

    Returns:
        {
            "period_type": str,
            "total_records": int,
            "status": "ok" | "error" | "not_supported" | "sponsor_only",
        }
    """
    result = {
        "period_type": period_type,
        "total_records": 0,
        "status": "not_supported",  # SDK 可能不支援
    }

    logger.warning(
        "Financial statements ETL via SDK not yet supported. "
        "Please implement via REST API or custom SDK extension."
    )

    return result
