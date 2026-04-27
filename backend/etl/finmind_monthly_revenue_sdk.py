"""
FinMind ETL：月營收抓取 (SDK 版本)
資料集：TaiwanStockMonthRevenue
需要 Sponsor 權限；歷史起點約 2013-01-01
寫入表：monthly_revenue
"""

import logging
from datetime import date
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000


def _to_month_end(value: Any) -> date:
    """Convert FinMind month/date fields to month-end date."""
    import calendar

    if value is None:
        raise ValueError("month value is None")

    text = str(value).strip()
    if not text:
        raise ValueError("month value is empty")

    # "YYYY-MM" or "YYYY-MM-DD"
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"unsupported month format: {text}")
    y, m = int(parsed.year), int(parsed.month)
    return date(y, m, calendar.monthrange(y, m)[1])


def _pick_numeric_series(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    """Pick the first matching numeric column from candidates; otherwise all-NaN."""
    for name in candidates:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")


def _resolve_revenue_month_series(df: pd.DataFrame) -> pd.Series:
    """
    Prefer the payload's revenue month field over announcement date.
    FinMind may expose revenue_month as either numeric month or YYYY-MM string.
    """
    if {"revenue_year", "revenue_month"}.issubset(df.columns):
        year_series = pd.to_numeric(df["revenue_year"], errors="coerce")
        month_series = pd.to_numeric(df["revenue_month"], errors="coerce")
        if year_series.notna().all() and month_series.notna().all():
            import calendar

            years = year_series.astype(int)
            months = month_series.astype(int)
            if ((months >= 1) & (months <= 12)).all():
                return pd.Series(
                    [
                        date(y, m, calendar.monthrange(y, m)[1])
                        for y, m in zip(years, months)
                    ],
                    index=df.index,
                    dtype="object",
                )

    if "revenue_month" in df.columns:
        return df["revenue_month"].apply(_to_month_end)

    month_source = df.get("date")
    if month_source is None:
        raise RuntimeError("FinMind monthly revenue payload has no revenue_month/date field")
    return month_source.apply(_to_month_end)


def fetch_and_upsert_monthly_revenue_sdk(
    db: Session,
    stock_ids: list,
    start_date: date,
    end_date: date,
    client: Any,  # FinMindSDKClient
) -> Dict[str, Any]:
    """
    從 FinMind SDK 批量抓取月營收並以 bulk upsert 寫入 DB。

    FinMind 月營收欄位：
      date        - 公告日期
      stock_id
      country
      revenue     - 月營收（千元）
      revenue_month - 營收月份（YYYY-MM）
      revenue_year  - 營收年份
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
        f"Fetching monthly revenue for {len(stock_ids)} stocks "
        f"from {start_date} to {end_date}"
    )

    try:
        df = client.fetch_month_revenue_dataset(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
        )

        # dataset-level 回的是全市場，先過濾到 stocks_master
        if df is not None and not df.empty and stock_ids:
            df = df[df["stock_id"].astype(str).str.strip().isin(set(stock_ids))].copy()

        if df is None or df.empty:
            logger.warning("No monthly revenue data returned from FinMind")
            result["status"] = "error"
            return result

        result["total_records"] = len(df)
        logger.info(f"Received {len(df)} monthly revenue records, writing to DB...")

        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        df["revenue_val"] = pd.to_numeric(df["revenue"], errors="coerce")

        df["rev_month_date"] = _resolve_revenue_month_series(df)

        # 先吃資料源原生欄位（不同 SDK/版本欄位名不一致）
        df["yoy_pct_val"] = _pick_numeric_series(df, [
            "revenue_year_difference_per",
            "revenue_year_difference_percent",
            "revenue_year_difference_ratio",
            "yoy",
            "YoY",
        ])
        df["mom_pct_val"] = _pick_numeric_series(df, [
            "revenue_month_difference_per",
            "revenue_month_difference_percent",
            "revenue_month_difference_ratio",
            "mom",
            "MoM",
        ])

        # 若資料源沒有 YoY/MoM，依營收序列回算（百分比）
        if df["yoy_pct_val"].isna().all() or df["mom_pct_val"].isna().all():
            ordered = df.sort_values(["stock_id", "rev_month_date"]).copy()
            revenue = ordered["revenue_val"]
            if ordered["yoy_pct_val"].isna().all():
                prev_year = ordered.groupby("stock_id")["revenue_val"].shift(12)
                ordered["yoy_pct_val"] = ((revenue / prev_year) - 1.0) * 100.0
            if ordered["mom_pct_val"].isna().all():
                prev_month = ordered.groupby("stock_id")["revenue_val"].shift(1)
                ordered["mom_pct_val"] = ((revenue / prev_month) - 1.0) * 100.0
            df = ordered

        records = df[["rev_month_date", "stock_id", "revenue_val",
                       "yoy_pct_val", "mom_pct_val"]].to_dict("records")

        for i in range(0, len(records), BULK_BATCH_SIZE):
            batch = records[i:i + BULK_BATCH_SIZE]
            db.execute(text("""
                INSERT INTO monthly_revenue
                    (revenue_month, stock_id, revenue, yoy_pct, mom_pct, source, ingested_at)
                VALUES
                    (:rev_month_date, :stock_id, :revenue_val, :yoy_pct_val, :mom_pct_val, 'finmind', CURRENT_TIMESTAMP)
                ON CONFLICT (revenue_month, stock_id) DO UPDATE SET
                    revenue     = EXCLUDED.revenue,
                    yoy_pct     = EXCLUDED.yoy_pct,
                    mom_pct     = EXCLUDED.mom_pct,
                    source      = 'finmind',
                    ingested_at = CURRENT_TIMESTAMP
            """), batch)
            db.commit()
            result["upserted"] += len(batch)
            logger.info(f"Progress: {result['upserted']}/{len(records)} upserted")

        result["status"] = "ok"
        logger.info(f"Monthly revenue ETL completed: upserted={result['upserted']}")
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
        logger.error(f"Monthly revenue ETL failed: {e}")
        db.rollback()
        result["status"] = "error"
        return result
