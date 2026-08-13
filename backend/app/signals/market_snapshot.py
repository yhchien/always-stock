"""Build DB-backed market context for M23 Step 0."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import DailyPrice

_TAIEX_SYMBOLS: Sequence[str] = ("TAIEX",)
# 2026-08-13：確認過的正確 FinMind stock_id（見 CLAUDE.md「今日市場狀態」章節）——
# 舊版猜測的 OTCI/OTC/TWO/TPEX 在 FinMind TaiwanStockInfo 裡都不存在，正確代碼是
# 混合大小寫的 "TPEx"（`fetch_stock_master.py` 現在會把這個指數佔位列放進
# stocks_master，讓每日 ETL 自動帶出它的收盤價）。
_OTC_SYMBOLS: Sequence[str] = ("TPEx",)


def _load_recent_closes(
    db: Session,
    symbols: Sequence[str],
    target_date: date,
    limit: int = 5,
) -> list[DailyPrice]:
    rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id.in_(list(symbols)),
            DailyPrice.trade_date <= target_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return rows


def _build_index_snapshot(rows: Sequence[DailyPrice]) -> Optional[dict[str, Any]]:
    if not rows:
        return None

    closes = [float(row.close_price) for row in rows if row.close_price is not None]
    if not closes:
        return None

    latest = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    base_5d = closes[0] if len(closes) >= 2 else None

    change_1d_pct = None
    if prev not in (None, 0):
        change_1d_pct = (latest - prev) / prev * 100.0

    change_5d_pct = None
    if base_5d not in (None, 0):
        change_5d_pct = (latest - base_5d) / base_5d * 100.0

    return {
        "trade_date": str(rows[-1].trade_date),
        "close": latest,
        "change_pct_1d": change_1d_pct,
        "change_pct_5d": change_5d_pct,
        "closes_5d": closes,
    }


def build_db_market_snapshot(db: Session, target_date: date) -> dict[str, Any]:
    taiex_rows = _load_recent_closes(db, _TAIEX_SYMBOLS, target_date, limit=5)
    otc_rows = _load_recent_closes(db, _OTC_SYMBOLS, target_date, limit=5)

    snapshot = {
        "target_date": str(target_date),
        "taiex": _build_index_snapshot(taiex_rows),
        "otc": _build_index_snapshot(otc_rows),
    }

    return snapshot
