"""
FinMind ETL：券商交易聚合資料抓取
資料來源：FinMind TaiwanStockTradingDailyReportSecIdAgg
更新頻率：每日（市場收盤後）
說明：取代既有的 TWSE BSR parser，直接使用 FinMind 彙總資料
限制：需要 Sponsor 權限，歷史起點 2021-06-30
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def fetch_and_upsert_broker_trade_finmind(
    db: Session,
    trade_date: date,
    client: Any,  # FinMindHTTPClient
    use_agg_table: bool = True,  # 是否同時寫入 broker_trade_agg
) -> Dict[str, Any]:
    """
    從 FinMind 抓取券商聚合交易資料

    FinMind 的回傳格式（TaiwanStockTradingDailyReportSecIdAgg）：
    {
        "data": [
            {
                "date": "2026-04-13",
                "stock_id": "2330",
                "securities_trader_id": "0961",  # 券商代碼
                "buy": 1000000,  # 買進股數
                "sell": 900000,  # 賣出股數
                # 可能還有其他欄位如券商名稱等
            },
            ...
        ]
    }

    Args:
        db: SQLAlchemy session
        trade_date: 交易日期
        client: FinMind HTTP 客戶端
        use_agg_table: 是否同時寫入新的 broker_trade_agg 表

    Returns:
        {
            "date": date,
            "total_stocks": int,
            "total_brokers": int,  # 總券商筆數
            "total_inserted": int,
            "total_updated": int,
            "failed_stocks": [stock_id, ...],
            "status": "ok" | "partial" | "error" | "sponsor_only",
        }
    """
    from app.models import StockMaster, BrokerTradeAgg
    from etl.finmind_utils import is_trading_day

    result = {
        "date": trade_date,
        "total_stocks": 0,
        "total_brokers": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "failed_stocks": [],
        "status": "ok",
    }

    if not is_trading_day(trade_date):
        logger.warning(f"{trade_date} is not a trading day (weekend)")
        result["status"] = "skipped"
        return result

    logger.info(f"Fetching broker trade data for {trade_date} (FinMind Sponsor)")

    try:
        # 檢查是否有資料庫表
        if not hasattr(db, "query"):
            logger.error("Invalid database session")
            result["status"] = "error"
            return result

        # 取得上市公司清單
        stocks = db.query(StockMaster).filter(
            StockMaster.market == "twse",
            StockMaster.is_active == True,
        ).all()

        logger.info(f"Total stocks to process: {len(stocks)}")

        for i, stock in enumerate(stocks):
            try:
                # 呼叫 FinMind API
                # 注意：這個 API 需要 Sponsor 權限
                data = client.fetch(
                    "TaiwanStockTradingDailyReportSecIdAgg",
                    stock_id=stock.stock_id,
                    date=trade_date.strftime("%Y-%m-%d"),
                )

                # 檢查 API 回應
                if data.get("status") == 403:
                    logger.warning("Access denied: Sponsor permission required for TaiwanStockTradingDailyReportSecIdAgg")
                    result["status"] = "sponsor_only"
                    return result

                if data.get("status") != 200:
                    logger.debug(f"API error for {stock.stock_id}: {data.get('message')}")
                    result["failed_stocks"].append(stock.stock_id)
                    result["total_stocks"] += 1
                    continue

                # 確保 data 包含記錄
                records = data.get("data", [])
                if not records:
                    logger.debug(f"No broker trade data for {stock.stock_id}")
                    result["total_stocks"] += 1
                    continue

                # 如果使用 broker_trade_agg 表
                if use_agg_table:
                    for record in records:
                        try:
                            broker_id = record.get("securities_trader_id", "").strip()
                            broker_name = record.get("securities_trader_name", "").strip()

                            if not broker_id:
                                logger.warning(f"Missing broker_id for {stock.stock_id}")
                                continue

                            buy_shares = float(record.get("buy", 0))
                            sell_shares = float(record.get("sell", 0))
                            net_shares = buy_shares - sell_shares

                            row_data = {
                                "trade_date": trade_date,
                                "stock_id": stock.stock_id,
                                "broker_id": broker_id,
                                "broker_name": broker_name,
                                "buy_shares": buy_shares,
                                "sell_shares": sell_shares,
                                "net_shares": net_shares,
                                "source": "finmind",
                            }

                            # Upsert 邏輯
                            existing = db.query(BrokerTradeAgg).filter(
                                BrokerTradeAgg.trade_date == trade_date,
                                BrokerTradeAgg.stock_id == stock.stock_id,
                                BrokerTradeAgg.broker_id == broker_id,
                            ).first()

                            if existing:
                                # 更新既有記錄
                                for key, value in row_data.items():
                                    if key not in ("trade_date", "stock_id", "broker_id"):
                                        setattr(existing, key, value)
                                result["total_updated"] += 1
                            else:
                                # 新增記錄
                                new_row = BrokerTradeAgg(**row_data)
                                db.add(new_row)
                                result["total_inserted"] += 1

                            result["total_brokers"] += 1

                        except Exception as e:
                            logger.error(f"Error processing broker record for {stock.stock_id}: {e}")
                            continue

                result["total_stocks"] += 1

                # 定期提交（每 50 筆股票）
                if result["total_stocks"] % 50 == 0:
                    db.commit()
                    logger.info(f"Progress: {result['total_stocks']}/{len(stocks)}, brokers: {result['total_brokers']}")

            except Exception as e:
                logger.error(f"Error processing {stock.stock_id}: {e}")
                result["failed_stocks"].append(stock.stock_id)
                result["total_stocks"] += 1
                continue

        # 最後提交
        db.commit()

        # 判定最終狀態
        if result["failed_stocks"]:
            result["status"] = "partial" if result["total_inserted"] + result["total_updated"] > 0 else "error"
        else:
            result["status"] = "ok"

        logger.info(
            f"Broker trade ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"brokers={result['total_brokers']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Broker trade ETL failed: {e}")
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

    # 抓取昨天的資料（若需要測試，可能需要用近期有資料的日期）
    yesterday = (datetime.now().date() - timedelta(days=1))

    result = fetch_and_upsert_broker_trade_finmind(db, yesterday, client)
    print(result)

    db.close()
