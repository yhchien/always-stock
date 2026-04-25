"""M21 context pipeline 共用 helpers：peer 查詢 + query 下界推導。

這兩個 helper 是為了避免 `stock_id IN (peer_ids) AND trade_date <= buy_date`
這種 query 在沒有下界時掃過整段股價歷史（大產業 × 全歷史 × 多個 section
= 10 萬列等級）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

from sqlalchemy.orm import Session

from app.analysis.context_thresholds import (
    CHIP_SECONDARY_LOOKBACK_DAYS,
    INDUSTRY_PRICE_LOOKBACK_DAYS,
    INDUSTRY_VOLUME_BASELINE_DAYS,
    INDUSTRY_VOLUME_RECENT_DAYS,
    PRICE_BREAKOUT_BASELINE_DAYS,
)
from app.models import DailyPrice, StockMaster

# 所有 section lookback 的上界（交易日）。取 max，確保任何 section 拿到的
# query_start_date 都夠深，不會切到需要的歷史。
# +1 是因為 breakout baseline 是「前 20 日」不含當日，實際查詢需 21 天資料。
_MAX_LOOKBACK_TRADING_DAYS = max(
    PRICE_BREAKOUT_BASELINE_DAYS + 1,
    INDUSTRY_VOLUME_RECENT_DAYS + INDUSTRY_VOLUME_BASELINE_DAYS,
    INDUSTRY_PRICE_LOOKBACK_DAYS,
    CHIP_SECONDARY_LOOKBACK_DAYS,
)


def fetch_active_peer_ids(db: Session, industry_name: str) -> List[str]:
    """回傳同產業所有 active peer 的 stock_id list。

    空產業 / 未知產業會回空 list，呼叫端需自行 short-circuit。
    """
    rows = (
        db.query(StockMaster.stock_id)
        .filter(
            StockMaster.industry_name == industry_name,
            StockMaster.is_active.is_(True),
        )
        .all()
    )
    return [r[0] for r in rows]


def resolve_query_start_date(db: Session, buy_date: date) -> date:
    """從 daily_price 反推需要的 query 下界（以交易日計）。

    做法：`SELECT DISTINCT trade_date FROM daily_price WHERE trade_date <= buy_date
    ORDER BY trade_date DESC OFFSET (N-1) LIMIT 1`，N = `_MAX_LOOKBACK_TRADING_DAYS`。
    這會自動跳過週末 / 春節等長假，比單純 `buy_date - N days` 安全。

    若 DB 歷史深度不足（例如新股或 DB 剛 seed），fallback 為 `buy_date - 2N` 曆日，
    確保 query 仍有下界而不會全表掃描。
    """
    row = (
        db.query(DailyPrice.trade_date)
        .filter(DailyPrice.trade_date <= buy_date)
        .distinct()
        .order_by(DailyPrice.trade_date.desc())
        .offset(_MAX_LOOKBACK_TRADING_DAYS - 1)
        .limit(1)
        .first()
    )
    if row is None:
        return buy_date - timedelta(days=_MAX_LOOKBACK_TRADING_DAYS * 2)
    return row[0]
