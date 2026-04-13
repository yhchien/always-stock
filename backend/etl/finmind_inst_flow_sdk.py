"""
FinMind ETL：三大法人買賣超抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockInstitutionalInvestorsBuySell
更新頻率：每日或批次
說明：取代既有的 TWSE T86 parser
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_and_upsert_inst_flow_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取三大法人買賣超

    特點：
    - 一次 batch 查詢所有股票 + 日期區間
    - SDK 內部自動並行（use_async=True）
    - FinMind 的法人分類：外資 (foreign)、投信 (trust)、自營商 (dealer)

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
    from app.models import InstStockFlow, DailyPrice
    from etl.finmind_utils import normalize_inst_type, calculate_est_amount

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
        f"Fetching institutional flows for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        # 一次 batch fetch（SDK 內部 async）
        df = client.fetch_institutional_investors(
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
        logger.info(f"Received {len(df)} institutional flow records")

        # 標準化 inst_type，過濾未知類型
        df["inst_type"] = df["name"].apply(lambda x: normalize_inst_type(str(x).strip()))
        unknown = df[df["inst_type"].isna()]["name"].unique().tolist()
        if unknown:
            logger.warning(f"Unknown inst types (skipped): {unknown}")
        df = df[df["inst_type"].notna()].copy()

        # 將 date 轉為 date 型態
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)

        # 關鍵：先 aggregate（同一 stock/date/inst_type 加總）
        # 因為 FinMind 回傳 5 種類型，我們映射成 3 種後需合併
        agg_df = (
            df.groupby(["trade_date", "stock_id", "inst_type"], as_index=False)
            .agg(buy_shares=("buy", "sum"), sell_shares=("sell", "sum"))
        )
        agg_df["net_shares"] = agg_df["buy_shares"] - agg_df["sell_shares"]

        # 預先載入所有收盤價（用於估算金額）
        prices_map = {}
        prices = db.query(DailyPrice).filter(
            DailyPrice.trade_date >= start_date,
            DailyPrice.trade_date <= end_date,
            DailyPrice.source == "finmind",
        ).all()
        for p in prices:
            key = (p.trade_date, p.stock_id)
            prices_map[key] = p.close_price

        for _, row in agg_df.iterrows():
            try:
                trade_date = row["trade_date"]
                stock_id = str(row["stock_id"]).strip()
                inst_type = row["inst_type"]

                buy_shares = float(row["buy_shares"])
                sell_shares = float(row["sell_shares"])
                net_shares = float(row["net_shares"])

                close_price = prices_map.get((trade_date, stock_id), 0)
                buy_amount_est = calculate_est_amount(buy_shares, close_price)
                sell_amount_est = calculate_est_amount(sell_shares, close_price)
                net_amount_est = buy_amount_est - sell_amount_est

                row_data = {
                    "trade_date": trade_date,
                    "stock_id": stock_id,
                    "inst_type": inst_type,
                    "buy_shares": buy_shares,
                    "sell_shares": sell_shares,
                    "net_shares": net_shares,
                    "buy_amount_est": buy_amount_est,
                    "sell_amount_est": sell_amount_est,
                    "net_amount_est": net_amount_est,
                    "source": "finmind",
                }

                # Upsert
                existing = db.query(InstStockFlow).filter(
                    InstStockFlow.trade_date == trade_date,
                    InstStockFlow.stock_id == stock_id,
                    InstStockFlow.inst_type == inst_type,
                ).first()

                if existing:
                    for key, value in row_data.items():
                        if key not in ("trade_date", "stock_id", "inst_type"):
                            setattr(existing, key, value)
                    result["updated"] += 1
                else:
                    new_row = InstStockFlow(**row_data)
                    db.add(new_row)
                    result["inserted"] += 1

            except Exception as e:
                logger.error(f"Error processing row: {e}")
                stock_id = str(row.get("stock_id", "UNKNOWN"))
                if stock_id not in result["failed_stocks"]:
                    result["failed_stocks"].append(stock_id)
                continue

            # 定期提交
            if (result["inserted"] + result["updated"]) % 1000 == 0:
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
            f"Institutional flow ETL completed: "
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
        logger.error(f"Institutional flow ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
