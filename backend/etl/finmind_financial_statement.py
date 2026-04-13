"""
FinMind ETL：財報項目資料抓取
資料來源：FinMind TaiwanStockFinancialStatements
更新頻率：每季（通常隔月公布）
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# 常見財報項目映射（FinMind 可能的欄位名稱）
FINANCIAL_STATEMENT_ITEMS = [
    "revenue",              # 營業收入
    "operating_cost",       # 營業成本
    "operating_income",     # 營業淨利
    "net_income",          # 淨利
    "eps",                 # 每股盈餘
    "roe",                 # 股東權益報酬率
    "roa",                 # 資產報酬率
    "current_ratio",       # 流動比
    "debt_ratio",          # 負債比
]


def fetch_and_upsert_financial_statement_finmind(
    db: Session,
    report_date: date,
    client: Any,  # FinMindHTTPClient
    period_type: str = "quarterly",  # "quarterly" | "annual"
) -> Dict[str, Any]:
    """
    從 FinMind 抓取財報項目資料

    FinMind 的回傳格式（TaiwanStockFinancialStatements）：
    {
        "data": [
            {
                "date": "2026-03-31",
                "stock_id": "2330",
                "revenue": 123456789,
                "net_income": 23456789,
                "eps": 3.45,
                "roe": 15.2,
                ...
            }
        ]
    }

    Args:
        db: SQLAlchemy session
        report_date: 財報公布日期或報告期間結束日
        client: FinMind HTTP 客戶端
        period_type: "quarterly" 或 "annual"

    Returns:
        {
            "report_date": date,
            "period_type": str,
            "total_processed": int,
            "total_inserted": int,
            "total_updated": int,
            "failed_stocks": [stock_id, ...],
            "status": "ok" | "partial" | "error" | "sponsor_only",
        }
    """
    from app.models import FinancialStatement, StockMaster

    result = {
        "report_date": report_date,
        "period_type": period_type,
        "total_processed": 0,
        "total_inserted": 0,
        "total_updated": 0,
        "failed_stocks": [],
        "status": "ok",
    }

    logger.info(f"Fetching financial statements for {report_date} ({period_type})")

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
                # 注意：可能需要指定期間類型
                data = client.fetch(
                    "TaiwanStockFinancialStatements",
                    stock_id=stock.stock_id,
                    date=report_date.strftime("%Y-%m-%d"),
                )

                # 檢查 API 回應
                if data.get("status") == 403:
                    logger.warning("Access denied: Sponsor permission required for TaiwanStockFinancialStatements")
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
                    logger.debug(f"No financial data for {stock.stock_id}")
                    result["total_processed"] += 1
                    continue

                # 通常應該是 1 筆記錄，但內部可能包含多個項目
                record = records[0] if isinstance(records, list) else records

                # 遍歷已定義的財報項目
                for item_name in FINANCIAL_STATEMENT_ITEMS:
                    if item_name in record:
                        try:
                            value = record.get(item_name)
                            if value is None:
                                continue

                            row_data = {
                                "report_date": report_date,
                                "stock_id": stock.stock_id,
                                "item_name": item_name,
                                "item_code": item_name,  # 可能需要對應 FinMind 內部代碼
                                "value": float(value),
                                "period_type": period_type,
                                "source": "finmind",
                            }

                            # Upsert 邏輯
                            existing = db.query(FinancialStatement).filter(
                                FinancialStatement.report_date == report_date,
                                FinancialStatement.stock_id == stock.stock_id,
                                FinancialStatement.item_name == item_name,
                            ).first()

                            if existing:
                                # 更新既有記錄
                                for key, value in row_data.items():
                                    if key not in ("report_date", "stock_id", "item_name"):
                                        setattr(existing, key, value)
                                result["total_updated"] += 1
                            else:
                                # 新增記錄
                                new_row = FinancialStatement(**row_data)
                                db.add(new_row)
                                result["total_inserted"] += 1

                        except Exception as e:
                            logger.debug(f"Error processing item {item_name}: {e}")
                            continue

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
            f"Financial statement ETL completed: "
            f"inserted={result['total_inserted']}, "
            f"updated={result['total_updated']}, "
            f"failed={len(result['failed_stocks'])}"
        )

        return result

    except Exception as e:
        logger.error(f"Financial statement ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
