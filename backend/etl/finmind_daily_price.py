"""
FinMind ETL：每日股價抓取
資料來源：FinMind TaiwanStockPrice
更新頻率：每日（市場收盤後）
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def fetch_and_upsert_daily_price_finmind(
    db: Session,
    trade_date: date,
    client: Any,  # FinMindHTTPClient
) -> Dict[str, Any]:
    """
    從 FinMind 抓取每日股價，自動重試 & 速率控制

    Args:
        db: SQLAlchemy session
        trade_date: 交易日期
        client: FinMind HTTP 客戶端

    Returns:
        {
            "date": date,
            "total_processed": int,
            "total_inserted": int,
            "total_updated": int,
            "failed_stocks": [stock_id, ...],
            "status": "ok" | "partial" | "error",
        }
    """
    from app.models import DailyPrice, StockMaster
    from etl.finmind_utils import is_trading_day, validate_price_data

    result = {
        "date": trade_date,
        "total_processed": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "failed_stocks": [],
        "status": "ok",
    }

    if not is_trading_day(trade_date):
        logger.warning(f"{trade_date} is not a trading day (weekend)")
        result["status"] = "skipped"
        return result

    logger.info(f"Fetching daily prices for {trade_date}")

    try:
        # 取得上市公司清單
        stocks = db.query(StockMaster).filter(
            StockMaster.market == "twse",
            StockMaster.is_active == True,
        ).all()

        logger.info(f"Total stocks to process: {len(stocks)}")

        for i, stock in enumerate(stocks):
            try:
                # 呼叫 FinMind API
                data = client.fetch(
                    "TaiwanStockPrice",
                    stock_id=stock.stock_id,
                    date=trade_date.strftime("%Y-%m-%d"),
                )

                # 檢查 API 回應
                if data.get("status") != 200:
                    logger.warning(f"API error for {stock.stock_id}: {data.get('message')}")
                    result["failed_stocks"].append(stock.stock_id)
                    result["total_processed"] += 1
                    continue

                # 確保 data 包含記錄
                records = data.get("data", [])
                if not records:
                    logger.debug(f"No data for {stock.stock_id} on {trade_date}")
                    result["total_processed"] += 1
                    continue

                # 通常應該是 1 筆記錄
                record = records[0] if isinstance(records, list) else records

                # 驗證資料
                if not validate_price_data(record):
                    logger.warning(f"Invalid price data for {stock.stock_id}")
                    result["failed_stocks"].append(stock.stock_id)
                    result["total_processed"] += 1
                    continue

                # 映射 FinMind 欄位 → DB 欄位
                row_data = {
                    "trade_date": trade_date,
                    "stock_id": stock.stock_id,
                    "open_price": float(record.get("open")),
                    "high_price": float(record.get("high")),  # FinMind 用 "high" 不是 "max"
                    "low_price": float(record.get("low")),
                    "close_price": float(record.get("close")),
                    "volume": float(record.get("volume")),
                    "turnover": float(record.get("money")),  # FinMind 用 "money"
                    "spread": float(record.get("spread", 0)) if "spread" in record else None,
                    "source": "finmind",
                }

                # Upsert 邏輯
                existing = db.query(DailyPrice).filter(
                    DailyPrice.trade_date == trade_date,
                    DailyPrice.stock_id == stock.stock_id,
                ).first()

                if existing:
                    # 更新既有記錄
                    for key, value in row_data.items():
                        if key not in ("trade_date", "stock_id"):
                            setattr(existing, key, value)
                    result["total_updated"] += 1
                else:
                    # 新增記錄
                    new_row = DailyPrice(**row_data)
                    db.add(new_row)
                    result["total_inserted"] += 1

                result["total_processed"] += 1

                # 定期提交（每 100 筆）
                if result["total_processed"] % 100 == 0:
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
            f"Daily price ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Daily price ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result


if __name__ == "__main__":
    # 測試用
    import os
    os.chdir("../")  # 改到 backend 目錄

    from app.database import SessionLocal
    from etl.http_client import FinMindHTTPClient
    from datetime import datetime, timedelta

    db = SessionLocal()
    token = os.getenv("FINMIND_TOKEN", "YOUR_TOKEN_HERE")
    client = FinMindHTTPClient(token)

    # 抓取昨天的資料
    yesterday = (datetime.now().date() - timedelta(days=1))

    result = fetch_and_upsert_daily_price_finmind(db, yesterday, client)
    print(result)

    db.close()
