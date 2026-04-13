"""
FinMind ETL：日常估值資料抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockPER
更新頻率：每日或批次
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_and_upsert_daily_valuation_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取估值資料（P/E、P/B 等）

    Args:
        db: SQLAlchemy session
        stock_ids: 股票代碼列表
        start_date: 開始日期
        end_date: 結束日期
        client: FinMind SDK 客戶端

    Returns:
        {
            "start_date": date,
            "end_date": date,
            "total_records": int,
            "inserted": int,
            "updated": int,
            "failed": int,
            "status": "ok" | "partial" | "error" | "insufficient_quota" | "sponsor_only",
        }
    """
    from app.models import DailyValuation

    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "inserted": 0,
        "updated": 0,
        "failed": 0,
        "status": "ok",
    }

    logger.info(
        f"Fetching daily valuation for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        # 一次 batch fetch
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
        logger.info(f"Received {len(df)} valuation records")

        # 資料映射
        # FinMind 欄位（需要根據實際調整）：
        # date, stock_id, per, pbr, dividend_yield (等)
        
        for _, row in df.iterrows():
            try:
                trade_date = pd.to_datetime(row.get("date")).date()
                stock_id = str(row.get("stock_id")).strip()

                if not stock_id or trade_date is None:
                    logger.debug(f"Invalid row: {row}")
                    continue

                row_data = {
                    "trade_date": trade_date,
                    "stock_id": stock_id,
                    "per": float(row.get("per")) if row.get("per") else None,
                    "pbr": float(row.get("pbr")) if row.get("pbr") else None,
                    "dividend_yield": float(row.get("dividend_yield")) if row.get("dividend_yield") else None,
                    "source": "finmind",
                }

                # Upsert
                existing = db.query(DailyValuation).filter(
                    DailyValuation.trade_date == trade_date,
                    DailyValuation.stock_id == stock_id,
                ).first()

                if existing:
                    for key, value in row_data.items():
                        if key not in ("trade_date", "stock_id"):
                            setattr(existing, key, value)
                    result["updated"] += 1
                else:
                    new_row = DailyValuation(**row_data)
                    db.add(new_row)
                    result["inserted"] += 1

            except Exception as e:
                logger.error(f"Error processing row: {e}")
                result["failed"] += 1
                continue

            # 定期提交
            if (result["inserted"] + result["updated"]) % 500 == 0:
                db.commit()

        db.commit()

        if result["failed"] > 0:
            result["status"] = "partial"
        else:
            result["status"] = "ok"

        logger.info(
            f"Daily valuation ETL completed: "
            f"inserted={result['inserted']}, updated={result['updated']}"
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
        logger.error(f"Daily valuation ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
