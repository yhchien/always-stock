"""
FinMind ETL：發行股數 / 外資持股比每日快照（fishtail momentum upgrade 2026-07-15）
資料來源：FinMind TaiwanStockShareholding
更新頻率：每日（盤後同步）
寫入表：stock_shares_outstanding

欄位映射：
    NumberOfSharesIssued          → shares_issued
    ForeignInvestmentSharesRatio  → foreign_shares_ratio

用途：市值 = shares_issued × close_price；`institution_buy_to_market_cap` 分母
（spec §6.1 A 延後項，資料到位後 momentum frame 直接可算）。

Gotcha（與 margin_trade 同款）：v4 dataset-level fetch **只回 start_date 當日資料**，
忽略 end_date；多日 backfill 必須逐交易日呼叫，每日 1 quota。
"""

import logging
from datetime import date
from typing import Any, Dict

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def fetch_and_upsert_shareholding_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind 抓全市場發行股數 / 外資持股比並 bulk upsert 至 stock_shares_outstanding。

    狀態語義（同 margin_trade）：
        ok / no_data / insufficient_quota / error
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
        f"Fetching shareholding for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        # dataset-level 只回 start_date 當日 → 多日必須逐交易日呼叫（每日 1 quota）
        if start_date == end_date:
            df = client.fetch_shareholding_dataset(
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=start_date.strftime("%Y-%m-%d"),
            )
        else:
            trade_dates = [
                row[0]
                for row in db.execute(
                    text(
                        "SELECT DISTINCT trade_date FROM daily_price "
                        "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
                    ),
                    {"s": start_date, "e": end_date},
                ).all()
            ]
            if not trade_dates:
                logger.warning(
                    "No trading days in daily_price between %s and %s", start_date, end_date
                )
                result["status"] = "no_data"
                return result

            frames = []
            for d in trade_dates:
                # PostgreSQL 回 date、SQLite（測試）回 str，都要吃
                d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                sub_df = client.fetch_shareholding_dataset(start_date=d_str, end_date=d_str)
                if sub_df is None or sub_df.empty:
                    logger.info("No shareholding data on %s (skipped)", d_str)
                    continue
                frames.append(sub_df)

            if not frames:
                logger.warning("No shareholding data returned across all trading days")
                result["status"] = "no_data"
                return result

            df = pd.concat(frames, ignore_index=True)

        if df is None or df.empty:
            logger.warning("No shareholding data returned from FinMind")
            result["status"] = "no_data"
            return result

        # dataset-level 回的是全市場（含 ETF / 興櫃），先過濾到 stocks_master
        if stock_ids:
            df = df[df["stock_id"].astype(str).str.strip().isin(set(stock_ids))].copy()

        if df.empty:
            logger.warning("No shareholding data after filtering to stocks_master")
            result["status"] = "no_data"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} shareholding records, writing to DB...")

        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["shares_issued"] = pd.to_numeric(
            df.get("NumberOfSharesIssued"), errors="coerce"
        )
        df["foreign_shares_ratio"] = pd.to_numeric(
            df.get("ForeignInvestmentSharesRatio"), errors="coerce"
        )

        # 同 (trade_date, stock_id) 若 FinMind 重複回傳取最後一筆
        df = df.drop_duplicates(subset=["trade_date", "stock_id"], keep="last")

        # NaN → None 讓 SQL nullable 正確處理
        for col in ["shares_issued", "foreign_shares_ratio"]:
            df[col] = df[col].astype("object").where(df[col].notna(), None)

        records = df[
            ["trade_date", "stock_id", "shares_issued", "foreign_shares_ratio"]
        ].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i : i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO stock_shares_outstanding
                    (trade_date, stock_id, shares_issued, foreign_shares_ratio, source, ingested_at)
                VALUES
                    (:trade_date, :stock_id, :shares_issued, :foreign_shares_ratio, 'finmind', CURRENT_TIMESTAMP)
                ON CONFLICT (trade_date, stock_id) DO UPDATE SET
                    shares_issued        = EXCLUDED.shares_issued,
                    foreign_shares_ratio = EXCLUDED.foreign_shares_ratio,
                    source               = 'finmind',
                    ingested_at          = CURRENT_TIMESTAMP
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Shareholding ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Shareholding ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
