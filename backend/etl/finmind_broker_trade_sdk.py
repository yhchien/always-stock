"""
FinMind ETL：券商分點買賣超抓取
資料集：TaiwanStockTradingDailyReport（逐日抓取，本地 group by 分點聚合）
注意：TaiwanStockTradingDailyReportSecIdAgg 在 API/SDK 中不存在
歷史起點：2021-06-30（更早的日期 FinMind 無資料）
寫入表：broker_trade_agg
"""

import logging
from datetime import date, timedelta
from typing import Dict, Any, List

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000
BROKER_TRADE_START_DATE = "2021-06-30"


def _get_trading_dates(start_date: date, end_date: date) -> List[date]:
    """產生 start_date ~ end_date 之間的平日清單（不過濾台灣假日）。"""
    dates = []
    d = start_date
    while d <= end_date:
        if d.isoweekday() <= 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _upsert_batch(db: Session, records: list) -> int:
    """批量 upsert broker_trade_agg，回傳寫入筆數。"""
    for i in range(0, len(records), BULK_BATCH_SIZE):
        batch = records[i:i + BULK_BATCH_SIZE]
        db.execute(text("""
            INSERT INTO broker_trade_agg
                (trade_date, stock_id, broker_id, broker_name,
                 buy_shares, sell_shares, net_shares, source, ingested_at)
            VALUES
                (:trade_date, :stock_id, :broker_id, :broker_name,
                 :buy_shares, :sell_shares, :net_shares, 'finmind', CURRENT_TIMESTAMP)
            ON CONFLICT (trade_date, stock_id, broker_id) DO UPDATE SET
                broker_name  = EXCLUDED.broker_name,
                buy_shares   = EXCLUDED.buy_shares,
                sell_shares  = EXCLUDED.sell_shares,
                net_shares   = EXCLUDED.net_shares,
                source       = 'finmind',
                ingested_at  = CURRENT_TIMESTAMP
        """), batch)
        db.commit()
    return len(records)


def fetch_and_upsert_broker_trade_agg_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    逐日抓取分點買賣資料並聚合寫入 broker_trade_agg。

    每個交易日消耗 len(stock_ids) 個 quota（每支股票 1 request）。
    建議每次只跑短區間（daily ETL 每天 1 天，backfill 最多 30 天）。

    資料起點為 2021-06-30，早於此日期的 start_date 會自動調整。
    """
    import datetime as dt

    broker_start = dt.date(2021, 6, 30)
    if start_date < broker_start:
        logger.info(f"Broker trade data only available from {BROKER_TRADE_START_DATE}, adjusting start_date")
        start_date = broker_start

    if start_date > end_date:
        logger.info("start_date > end_date after adjustment, skipping broker trade")
        return {"status": "skipped", "reason": "before_data_start", "upserted": 0}

    trading_dates = _get_trading_dates(start_date, end_date)
    if not trading_dates:
        return {"status": "skipped", "reason": "no_trading_dates", "upserted": 0}

    result = {
        "start_date": start_date,
        "end_date": end_date,
        "total_stocks": len(stock_ids),
        "total_dates": len(trading_dates),
        "total_records": 0,
        "upserted": 0,
        "status": "ok",
    }

    estimated_requests = len(stock_ids) * len(trading_dates)
    logger.info(
        f"Broker trade agg: {len(stock_ids)} stocks × {len(trading_dates)} dates "
        f"= ~{estimated_requests} requests"
    )

    try:
        for i, trade_date in enumerate(trading_dates):
            date_str = trade_date.strftime("%Y-%m-%d")

            try:
                df = client.fetch_broker_trade_by_date(
                    stock_id_list=stock_ids,
                    trade_date=date_str,
                    use_async=True,
                )
            except RuntimeError as e:
                if "quota" in str(e).lower():
                    logger.error(f"Insufficient quota at {date_str}: {e}")
                    result["status"] = "insufficient_quota"
                    return result
                raise

            if df is None or df.empty:
                logger.info(f"{date_str}: no data (holiday or no trades), skipping")
                continue

            df["trade_date"] = pd.to_datetime(df["date"]).dt.date
            df["stock_id"] = df["stock_id"].astype(str).str.strip()
            df["broker_id"] = df["broker_id"].astype(str).str.strip()
            df["broker_name"] = df["broker_name"].astype(str).str.strip()
            df["buy_shares"] = pd.to_numeric(df["buy_shares"], errors="coerce").fillna(0).astype(int)
            df["sell_shares"] = pd.to_numeric(df["sell_shares"], errors="coerce").fillna(0).astype(int)
            df["net_shares"] = df["buy_shares"] - df["sell_shares"]

            records = df[["trade_date", "stock_id", "broker_id", "broker_name",
                           "buy_shares", "sell_shares", "net_shares"]].to_dict("records")

            upserted = _upsert_batch(db, records)
            result["upserted"] += upserted
            result["total_records"] += upserted

            if (i + 1) % 5 == 0 or (i + 1) == len(trading_dates):
                logger.info(
                    f"Progress: {i+1}/{len(trading_dates)} dates done, "
                    f"{result['upserted']} records upserted (latest: {date_str})"
                )

        result["status"] = "ok"
        logger.info(f"Broker trade agg ETL completed: {result['upserted']} records upserted")
        return result

    except Exception as e:
        logger.error(f"Broker trade agg ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
