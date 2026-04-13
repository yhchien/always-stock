"""
FinMind ETL：每日股價抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockPrice
更新頻率：每日或批次（市場收盤後或補歷史資料）
"""

import logging
from datetime import date
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_and_upsert_daily_price_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取股價（支援 async）

    特點：
    - 一次 batch 查詢所有股票 + 日期區間
    - SDK 內部自動並行（use_async=True）
    - 大幅減少 API 呼叫次數

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
            "total_stocks": int,
            "total_records": int,
            "inserted": int,
            "updated": int,
            "failed_stocks": [stock_id, ...],
            "status": "ok" | "partial" | "error" | "insufficient_quota",
        }
    """
    from app.models import DailyPrice, StockMaster

    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "inserted": 0,
        "updated": 0,
        "failed_stocks": [],
        "status": "ok",
    }

    logger.info(
        f"Fetching daily prices for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        # 一次 batch fetch（SDK 內部 async）
        df = client.fetch_taiwan_stock_price(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,  # 自動並行
        )

        if df is None or df.empty:
            logger.warning("No data returned from FinMind")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} price records")

        # 資料映射與驗證
        # FinMind 返回的欄位名稱（需要根據實際調整）：
        # date, stock_id, open, high, low, close, volume, money (turnover)
        
        for _, row in df.iterrows():
            try:
                trade_date = pd.to_datetime(row.get("date")).date()
                stock_id = str(row.get("stock_id")).strip()

                # 驗證
                if not stock_id or trade_date is None:
                    logger.warning(f"Invalid row: {row}")
                    continue

                open_price = float(row.get("open")) if row.get("open") else None
                high_price = float(row.get("max")) if row.get("max") else None
                low_price = float(row.get("min")) if row.get("min") else None
                close_price = float(row.get("close")) if row.get("close") else None
                volume = float(row.get("Trading_Volume")) if row.get("Trading_Volume") else None
                turnover = float(row.get("Trading_money")) if row.get("Trading_money") else None

                row_data = {
                    "trade_date": trade_date,
                    "stock_id": stock_id,
                    "open_price": open_price,
                    "high_price": high_price,
                    "low_price": low_price,
                    "close_price": close_price,
                    "volume": volume,
                    "turnover": turnover,
                    "spread": float(row.get("spread")) if row.get("spread") else None,
                    "source": "finmind",
                }

                # Upsert
                existing = db.query(DailyPrice).filter(
                    DailyPrice.trade_date == trade_date,
                    DailyPrice.stock_id == stock_id,
                ).first()

                if existing:
                    for key, value in row_data.items():
                        if key not in ("trade_date", "stock_id"):
                            setattr(existing, key, value)
                    result["updated"] += 1
                else:
                    new_row = DailyPrice(**row_data)
                    db.add(new_row)
                    result["inserted"] += 1

            except Exception as e:
                logger.error(f"Error processing row {row.to_dict()}: {e}")
                stock_id = str(row.get("stock_id", "UNKNOWN"))
                if stock_id not in result["failed_stocks"]:
                    result["failed_stocks"].append(stock_id)
                continue

            # 定期提交
            if (result["inserted"] + result["updated"]) % 500 == 0:
                db.commit()
                logger.info(
                    f"Progress: inserted={result['inserted']}, "
                    f"updated={result['updated']}"
                )

        # 最後提交
        db.commit()

        # 判定狀態
        if result["failed_stocks"]:
            result["status"] = "partial" if result["inserted"] + result["updated"] > 0 else "error"
        else:
            result["status"] = "ok"

        logger.info(
            f"Daily price ETL completed: "
            f"inserted={result['inserted']}, updated={result['updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

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
