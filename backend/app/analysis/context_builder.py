"""
M21 主入口：`build_trade_quality_context(db, stock_id, buy_date)`

把 6 個 section 的結論層訊號組裝成 spec 定義的 JSON 結構。
deterministic、no hindsight、永遠不 raise（除了 stock 找不到）。

詳見 docs/plans/trade_quality_context_spec.md §REQUIRED OUTPUT SCHEMA
      docs/plans/m21_context_pipeline_implementation.md §3 §7
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List

from sqlalchemy.orm import Session

from app.analysis.chip_signals import compute_chip_signals
from app.analysis.fundamental_signals import compute_fundamental
from app.analysis.industry_signals import compute_industry_signals
from app.analysis.news_stub import build_news_stub
from app.analysis.peer_rank import compute_peer_rank
from app.analysis.price_structure import compute_price_structure
from app.models import StockMaster


def build_trade_quality_context(
    db: Session,
    stock_id: str,
    buy_date: date,
) -> Dict:
    """
    Pre-aggregate DB raw data into conclusion-level signals for trade-quality AI.

    Deterministic, no hindsight: uses only data on or before buy_date.

    Returns the schema documented in docs/plans/trade_quality_context_spec.md.
    On data gaps, the affected field is set to None and a reason is appended to
    `data_quality_notes`. If no gaps → `data_quality_notes` is [].

    Raises:
        ValueError: if stock_id not found in stocks_master.
    """
    stock = db.get(StockMaster, stock_id)
    if stock is None:
        raise ValueError(f"stock_id {stock_id!r} not found in stocks_master")

    industry_name = stock.industry_name or ""

    notes: List[str] = []

    industry_summary, industry_notes = compute_industry_signals(
        db, stock_id, buy_date, industry_name
    )
    chip_summary, chip_notes = compute_chip_signals(db, stock_id, buy_date, industry_name)
    peer_rank, peer_notes = compute_peer_rank(db, stock_id, buy_date, industry_name)
    fundamental, fundamental_notes = compute_fundamental(
        db, stock_id, buy_date, industry_name
    )
    price_structure, price_notes = compute_price_structure(
        db, stock_id, buy_date, industry_name
    )
    news_input_stub, news_notes = build_news_stub(
        stock_id=stock.stock_id,
        stock_name=stock.stock_name or "",
        industry_name=industry_name,
        buy_date=buy_date,
    )

    # 依 section 順序合併 notes，永遠 null 的欄位（industry_news_heat / guidance）不寫進來（決策 #4b）
    notes.extend(industry_notes)
    notes.extend(chip_notes)
    notes.extend(peer_notes)
    notes.extend(fundamental_notes)
    notes.extend(price_notes)
    notes.extend(news_notes)

    return {
        "stock_id": stock.stock_id,
        "buy_date": str(buy_date),
        "industry_summary": industry_summary,
        "chip_summary": chip_summary,
        "peer_rank": peer_rank,
        "fundamental": fundamental,
        "price_structure": price_structure,
        "news_input_stub": news_input_stub,
        "data_quality_notes": notes,
    }
