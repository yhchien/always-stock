"""
FinMind ETL：月營收資料抓取
資料來源：FinMind TaiwanStockMonthRevenue
更新頻率：每月（通常月中公布上月資料）
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Any, List
import calendar

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_end_of_month(target_date: date) -> date:
    """取得指定月份的最後一天"""
    last_day = calendar.monthrange(target_date.year, target_date.month)[1]
    return date(target_date.year, target_date.month, last_day)


def fetch_and_upsert_monthly_revenue_finmind(
    db: Session,
    revenue_month: date,
    client: Any,  # FinMindHTTPClient
) -> Dict[str, Any]:
    """
    從 FinMind 抓取月營收資料

    FinMind 的回傳格式（TaiwanStockMonthRevenue）：
    {
        "data": [
            {
                "date": "2026-03",  # 報告月份
                "stock_id": "2330",
                "revenue": 123456789,  # 月營收
                "yoy": 10.5,  # 年增率 (%)
                "mom": 5.2,  # 月增率 (%)
            }
        ]
    }

    Args:
        db: SQLAlchemy session
        revenue_month: 營收月份（會轉換為 "YYYY-MM" 格式）
        client: FinMind HTTP 客戶端

    Returns:
        {
            "month": date,
            "total_processed": int,
            "total_inserted": int,
            "total_updated": int,
            "failed_stocks": [stock_id, ...],
            "status": "ok" | "partial" | "error" | "sponsor_only",
        }
    """
    from app.models import MonthlyRevenue, StockMaster

    result = {
        "month": revenue_month,
        "total_processed": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "failed_stocks": [],
        "status": "ok",
    }

    logger.info(f"Fetching monthly revenue for {revenue_month.strftime('%Y-%m')}")

    try:
        # 取得上市公司清單
        stocks = db.query(StockMaster).filter(
            StockMaster.market == "twse",
            StockMaster.is_active == True,
        ).all()

        logger.info(f"Total stocks to process: {len(stocks)}")

        # FinMind 通常使用 "YYYY-MM" 格式
        month_str = revenue_month.strftime("%Y-%m")

        for i, stock in enumerate(stocks):
            try:
                # 呼叫 FinMind API
                data = client.fetch(
                    "TaiwanStockMonthRevenue",
                    stock_id=stock.stock_id,
                    date=month_str,
                )

                # 檢查 API 回應
                if data.get("status") == 403:
                    logger.warning("Access denied: Sponsor permission required for TaiwanStockMonthRevenue")
                    result["status"] = "sponsor_only"
                    return result

                if data.get("status") != 200:
                    logger.debug(f"API error for {stock.stock_id}: {data.get('message')}")
                    result["failed_stocks"].append(stock.stock_id)
                    result["total_processed"] += 1
                    continue

                # 確保 data 包含記錄
                records = data.get("data", [])
                if not records:
                    logger.debug(f"No revenue data for {stock.stock_id} in {month_str}")
                    result["total_processed"] += 1
                    continue

                # 通常應該是 1 筆記錄
                record = records[0] if isinstance(records, list) else records

                # 將營收月份轉換為月末日期
                month_end_date = get_end_of_month(revenue_month)

                row_data = {
                    "revenue_month": month_end_date,
                    "stock_id": stock.stock_id,
                    "revenue": float(record.get("revenue")) if record.get("revenue") else None,
                    "yoy_pct": float(record.get("yoy")) if record.get("yoy") else None,
                    "mom_pct": float(record.get("mom")) if record.get("mom") else None,
                    "source": "finmind",
                }

                # Upsert 邏輯
                existing = db.query(MonthlyRevenue).filter(
                    MonthlyRevenue.revenue_month == month_end_date,
                    MonthlyRevenue.stock_id == stock.stock_id,
                ).first()

                if existing:
                    # 更新既有記錄
                    for key, value in row_data.items():
                        if key not in ("revenue_month", "stock_id"):
                            setattr(existing, key, value)
                    result["total_updated"] += 1
                else:
                    # 新增記錄
                    new_row = MonthlyRevenue(**row_data)
                    db.add(new_row)
                    result["total_inserted"] += 1

                result["total_processed"] += 1

                # 定期提交（每 200 筆）
                if result["total_processed"] % 200 == 0:
                    db.commit()
                    logger.info(f"Progress: {result['total_processed']}/{len(stocks)}")

            except Exception as e:
                logger.error(f"Error processing {stock.stock_id}: {e}")
                result["failed_stocks"].append(stock.stock_id)
                result["total_processed"] += 1
                continue

        # 最後提交
        db.commit()

        # 判定最終狀態
        if result["failed_stocks"]:
            result["status"] = "partial" if result["total_inserted"] + result["total_updated"] > 0 else "error"
        else:
            result["status"] = "ok"

        logger.info(
            f"Monthly revenue ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Monthly revenue ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
