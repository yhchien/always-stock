"""
FinMind ETL：三大法人買賣超抓取
資料來源：FinMind TaiwanStockInstitutionalInvestorsBuySell
更新頻率：每日（市場收盤後）
說明：取代既有的 TWSE T86 parser
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def fetch_and_upsert_inst_flow_finmind(
    db: Session,
    trade_date: date,
    client: Any,  # FinMindHTTPClient
) -> Dict[str, Any]:
    """
    從 FinMind 抓取三大法人買賣超，自動重試 & 速率控制

    FinMind 的回傳格式：
    {
        "data": [
            {
                "date": "2026-04-13",
                "stock_id": "2330",
                "name": "外資",  # 或 "投信", "自營商"
                "buy": 1000000,
                "sell": 900000,
            },
            ...
        ]
    }

    Args:
        db: SQLAlchemy session
        trade_date: 交易日期
        client: FinMind HTTP 客戶端

    Returns:
        {
            "date": date,
            "total_stocks": int,
            "total_inserted": int,
            "total_updated": int,
            "failed_stocks": [stock_id, ...],
            "missing_prices": [stock_id, ...],  # 缺少收盤價無法估算金額
            "status": "ok" | "partial" | "error",
        }
    """
    from app.models import InstStockFlow, StockMaster, DailyPrice
    from etl.finmind_utils import (
        is_trading_day,
        normalize_inst_type,
        calculate_est_amount,
    )

    result = {
        "date": trade_date,
        "total_stocks": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "failed_stocks": [],
        "missing_prices": [],
        "status": "ok",
    }

    if not is_trading_day(trade_date):
        logger.warning(f"{trade_date} is not a trading day (weekend)")
        result["status"] = "skipped"
        return result

    logger.info(f"Fetching institutional flows for {trade_date}")

    try:
        # 取得上市公司清單
        stocks = db.query(StockMaster).filter(
            StockMaster.market == "twse",
            StockMaster.is_active == True,
        ).all()

        logger.info(f"Total stocks to process: {len(stocks)}")

        # 預先載入該日的所有收盤價（用於估算金額）
        prices_map = {}
        prices = db.query(DailyPrice).filter(
            DailyPrice.trade_date == trade_date
        ).all()
        for p in prices:
            prices_map[p.stock_id] = p.close_price

        for i, stock in enumerate(stocks):
            try:
                # 呼叫 FinMind API
                data = client.fetch(
                    "TaiwanStockInstitutionalInvestorsBuySell",
                    stock_id=stock.stock_id,
                    date=trade_date.strftime("%Y-%m-%d"),
                )

                # 檢查 API 回應
                if data.get("status") != 200:
                    logger.debug(f"API error for {stock.stock_id}: {data.get('message')}")
                    result["failed_stocks"].append(stock.stock_id)
                    result["total_stocks"] += 1
                    continue

                # 確保 data 包含記錄
                records = data.get("data", [])
                if not records:
                    logger.debug(f"No institutional flow data for {stock.stock_id}")
                    result["total_stocks"] += 1
                    continue

                # FinMind 通常回傳 3 筆（外資、投信、自營商）
                close_price = prices_map.get(stock.stock_id, 0)

                for record in records:
                    try:
                        # 取出法人名稱並標準化
                        finmind_name = record.get("name", "").strip()
                        inst_type = normalize_inst_type(finmind_name)

                        if not inst_type:
                            logger.warning(f"Unknown inst type: {finmind_name} for {stock.stock_id}")
                            continue

                        # 取出買賣資料
                        buy_shares = float(record.get("buy", 0))
                        sell_shares = float(record.get("sell", 0))
                        net_shares = buy_shares - sell_shares

                        # 估算金額（使用該日收盤價）
                        buy_amount_est = calculate_est_amount(buy_shares, close_price)
                        sell_amount_est = calculate_est_amount(sell_shares, close_price)
                        net_amount_est = buy_amount_est - sell_amount_est

                        row_data = {
                            "trade_date": trade_date,
                            "stock_id": stock.stock_id,
                            "inst_type": inst_type,
                            "buy_shares": buy_shares,
                            "sell_shares": sell_shares,
                            "net_shares": net_shares,
                            "buy_amount_est": buy_amount_est,
                            "sell_amount_est": sell_amount_est,
                            "net_amount_est": net_amount_est,
                            "source": "finmind",
                        }

                        # Upsert 邏輯
                        existing = db.query(InstStockFlow).filter(
                            InstStockFlow.trade_date == trade_date,
                            InstStockFlow.stock_id == stock.stock_id,
                            InstStockFlow.inst_type == inst_type,
                        ).first()

                        if existing:
                            # 更新既有記錄
                            for key, value in row_data.items():
                                if key not in ("trade_date", "stock_id", "inst_type"):
                                    setattr(existing, key, value)
                            result["total_updated"] += 1
                        else:
                            # 新增記錄
                            new_row = InstStockFlow(**row_data)
                            db.add(new_row)
                            result["total_inserted"] += 1

                    except Exception as e:
                        logger.error(f"Error processing record for {stock.stock_id}: {e}")
                        continue

                result["total_stocks"] += 1

                # 定期提交（每 100 筆股票）
                if result["total_stocks"] % 100 == 0:
                    db.commit()
                    logger.info(f"Progress: {result['total_stocks']}/{len(stocks)}")

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
            f"Institutional flow ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Institutional flow ETL failed: {e}")
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

    result = fetch_and_upsert_inst_flow_finmind(db, yesterday, client)
    print(result)

    db.close()
