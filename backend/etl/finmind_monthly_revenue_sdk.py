"""
FinMind ETL：月營收抓取 (SDK 版本)
資料集：TaiwanStockMonthRevenue
需要 Sponsor 權限；歷史起點約 2013-01-01
寫入表：monthly_revenue
"""

import logging
from datetime import date, timedelta
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

BULK_BATCH_SIZE = 1000

# 2026-07-15 根因修正（兩層）：
# 1) FinMind TaiwanStockMonthRevenue 把「N 月營收」全部掛在「N+1 月 1 號」這一個
#    date key 上（例：6 月營收 2316 檔全是 date=2026-07-01），且公司是次月上旬陸續
#    公告、FinMind 陸續補進同一個 date key。
# 2) 該 dataset 的 v4 dataset-level fetch **只回 start_date 當日資料**（實測
#    2026-01-01~2026-04-01 只回 date=2026-01-01 的 2282 筆；與 margin_trade 同款）。
# 舊版 daily ETL 用 start=end=target_date 單日抓 → 只有「每月 1 號恰為交易日且
# ETL 成功」才抓得到，而且只抓到當天已公告的少數公司，之後永遠不回補
# （症狀：2026-04~06 全空、02/03 只有 ~837 檔）。
# 修法：從 start_date 回看 45 天起算，對範圍內**每個「月 1 號」date key 各打一次**
# （start=end=該 key），每日重抓 + upsert 冪等 → 公告陸續進來每天自動補齊。
# daily 模式 = 2 個 key = 2 quota/日，成本可忽略。
FETCH_LOOKBACK_DAYS = 45


def _month_first_days_between(start: date, end: date) -> list:
    """回傳 [start, end] 區間內所有「月 1 號」（升序）。"""
    keys = []
    y, m = start.year, start.month
    # 從 start 所在月的次月 1 號開始（若 start 本身是 1 號則含當月）
    if start.day != 1:
        m += 1
        if m > 12:
            y, m = y + 1, 1
    cur = date(y, m, 1)
    while cur <= end:
        keys.append(cur)
        m += 1
        if m > 12:
            y, m = y + 1, 1
        cur = date(y, m, 1)
    return keys


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
      date        - 營收次月 1 號（整月所有公司掛同一天；**不是**公告日）
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
        # 逐「月 1 號」date key 呼叫：見檔頭 FETCH_LOOKBACK_DAYS 註解
        # （dataset-level 只回 start_date 當日 + 公告陸續補進同一 key）
        fetch_start = start_date - timedelta(days=FETCH_LOOKBACK_DAYS)
        month_keys = _month_first_days_between(fetch_start, end_date)
        frames = []
        for key in month_keys:
            key_str = key.strftime("%Y-%m-%d")
            df_key = client.fetch_month_revenue_dataset(
                start_date=key_str,
                end_date=key_str,
            )
            if df_key is not None and not df_key.empty:
                frames.append(df_key)
        df = pd.concat(frames, ignore_index=True) if frames else None

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

        # NaN → None：新上市（無去年同期）回算出的 NaN 不可寫進 DB。
        # Postgres float8 收得下 NaN，但下游把值放進 JSON 欄位（signal_metrics）時
        # 會炸 invalid input syntax for type json（2026-07-16 daily_signals 踩到）。
        for col in ["revenue_val", "yoy_pct_val", "mom_pct_val"]:
            df[col] = df[col].astype("object").where(df[col].notna(), None)

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
