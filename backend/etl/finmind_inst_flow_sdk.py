"""
FinMind ETL：三大法人買賣超抓取 (SDK 版本，支援 batch + async)
資料來源：FinMind TaiwanStockInstitutionalInvestorsBuySell
更新頻率：每日或批次
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

from etl.finmind_utils import normalize_inst_type

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_inst_flow_finmind_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取三大法人買賣超並以 bulk upsert 寫入 DB。

    FinMind 回傳 5 種法人類型，映射並加總成 3 種（foreign/trust/dealer）。
    """
    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_records": 0,
        "upserted": 0,
        "status": "ok",
    }

    logger.info(
        f"Fetching institutional flows for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_institutional_investors(
            stock_id_list=stock_ids,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            use_async=True,
        )

        if df is None or df.empty:
            logger.warning("No data returned from FinMind")
            result["status"] = "no_data"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} institutional flow records, writing to DB...")

        # 標準化 inst_type，過濾未知類型
        df["inst_type"] = df["name"].apply(lambda x: normalize_inst_type(str(x).strip()))
        df = df[df["inst_type"].notna()].copy()

        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0)
        df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0)

        # 合併同一 stock/date/inst_type 的多筆（Foreign_Investor + Foreign_Dealer_Self → foreign）
        agg = (
            df.groupby(["trade_date", "stock_id", "inst_type"], as_index=False)
            .agg(buy_shares=("buy", "sum"), sell_shares=("sell", "sum"))
        )
        agg["net_shares"] = agg["buy_shares"] - agg["sell_shares"]
        
        price_rows = db.execute(
            text(
                """
                SELECT trade_date, stock_id, close_price
                FROM daily_price
                WHERE trade_date BETWEEN :start_date AND :end_date
                """
            ),
            {"start_date": start_date, "end_date": end_date},
        ).fetchall()

        if price_rows:
            price_df = pd.DataFrame(
                [(row[0], row[1], row[2]) for row in price_rows],
                columns=["trade_date", "stock_id", "close_price"],
            )
            price_df["trade_date"] = pd.to_datetime(price_df["trade_date"]).dt.date
            price_df["stock_id"] = price_df["stock_id"].astype(str).str.strip()
            price_df["close_price"] = pd.to_numeric(
                price_df["close_price"], errors="coerce"
            ).fillna(0.0)
            agg = agg.merge(price_df, on=["trade_date", "stock_id"], how="left")
        else:
            agg["close_price"] = 0.0

        agg["close_price"] = agg["close_price"].fillna(0.0).clip(lower=0.0)
        agg["buy_amount_est"] = agg["buy_shares"] * agg["close_price"]
        agg["sell_amount_est"] = agg["sell_shares"] * agg["close_price"]
        agg["net_amount_est"] = agg["buy_amount_est"] - agg["sell_amount_est"]

        records = agg[
            [
                "trade_date",
                "stock_id",
                "inst_type",
                "buy_shares",
                "sell_shares",
                "net_shares",
                "buy_amount_est",
                "sell_amount_est",
                "net_amount_est",
            ]
        ].to_dict("records")

        # Bulk upsert（分批）
        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO inst_stock_flow
                    (trade_date, stock_id, inst_type,
                     buy_shares, sell_shares, net_shares,
                     buy_amount_est, sell_amount_est, net_amount_est,
                     source, ingested_at)
                VALUES
                    (:trade_date, :stock_id, :inst_type,
                     :buy_shares, :sell_shares, :net_shares,
                     :buy_amount_est, :sell_amount_est, :net_amount_est,
                     'finmind', CURRENT_TIMESTAMP)
                ON CONFLICT (trade_date, stock_id, inst_type) DO UPDATE SET
                    buy_shares      = EXCLUDED.buy_shares,
                    sell_shares     = EXCLUDED.sell_shares,
                    net_shares      = EXCLUDED.net_shares,
                    buy_amount_est  = EXCLUDED.buy_amount_est,
                    sell_amount_est = EXCLUDED.sell_amount_est,
                    net_amount_est  = EXCLUDED.net_amount_est,
                    source          = 'finmind',
                    ingested_at     = CURRENT_TIMESTAMP
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Institutional flow ETL completed: upserted={result['upserted']}")
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
