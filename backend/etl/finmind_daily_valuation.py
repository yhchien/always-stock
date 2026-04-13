"""
FinMind ETL：日常估值資料抓取
資料來源：FinMind TaiwanStockPER
更新頻率：每日
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def fetch_and_upsert_daily_valuation_finmind(
    db: Session,
    trade_date: date,
    client: Any,  # FinMindHTTPClient
) -> Dict[str, Any]:
    """
    從 FinMind 抓取日常估值資料（P/E、P/B、股息殖利率等）

    FinMind 的回傳格式（TaiwanStockPER）：
    {
        "data": [
            {
                "date": "2026-04-13",
                "stock_id": "2330",
                "per": 26.5,
                "pbr": 3.2,
                "dividend_yield": 2.3,
                ...
            }
        ]
    }

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
            "status": "ok" | "partial" | "error" | "sponsor_only",
        }
    """
    from app.models import DailyValuation, StockMaster
    from etl.finmind_utils import is_trading_day

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

    logger.info(f"Fetching daily valuation for {trade_date}")

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
                    "TaiwanStockPER",
                    stock_id=stock.stock_id,
                    date=trade_date.strftime("%Y-%m-%d"),
                )

                # 檢查 API 回應
                if data.get("status") == 403:
                    logger.warning("Access denied: Sponsor permission required for TaiwanStockPER")
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
                    logger.debug(f"No valuation data for {stock.stock_id}")
                    result["total_processed"] += 1
                    continue

                # 通常應該是 1 筆記錄
                record = records[0] if isinstance(records, list) else records

                row_data = {
                    "trade_date": trade_date,
                    "stock_id": stock.stock_id,
                    "per": float(record.get("per")) if record.get("per") else None,
                    "pbr": float(record.get("pbr")) if record.get("pbr") else None,
                    "dividend_yield": float(record.get("dividend_yield")) if record.get("dividend_yield") else None,
                    "source": "finmind",
                }

                # Upsert 邏輯
                existing = db.query(DailyValuation).filter(
                    DailyValuation.trade_date == trade_date,
                    DailyValuation.stock_id == stock.stock_id,
                ).first()

                if existing:
                    # 更新既有記錄
                    for key, value in row_data.items():
                        if key not in ("trade_date", "stock_id"):
                            setattr(existing, key, value)
                    result["total_updated"] += 1
                else:
                    # 新增記錄
                    new_row = DailyValuation(**row_data)
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
            f"Daily valuation ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Daily valuation ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
