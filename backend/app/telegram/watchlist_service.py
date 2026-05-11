"""Telegram 清單 CRUD（list add / list delete / list show 共用底層）。

設計：
- 純 service 層；不處理 Telegram 訊息格式（formatters.py 負責）
- comma 分隔解析：「2330」「2330,2317」「2330, 2317」「2330 , 2317」皆支援
- 上限 20 檔（service 強制；超過拒絕新增剩餘檔位）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyPrice, StockMaster, TelegramWatchlistEntry

logger = logging.getLogger(__name__)

WATCHLIST_LIMIT = 20


@dataclass
class StockSnapshot:
    """list add 成功 / list show 用的單檔快照（純文字組裝用）。"""
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    sub_industry: Optional[str]
    close_price: Optional[float]
    spread_pct: Optional[float]
    trade_date: Optional[date]


@dataclass
class AddResult:
    added: List[StockSnapshot] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)       # 已在清單
    not_found: List[str] = field(default_factory=list)        # stocks_master 找不到
    over_limit: List[str] = field(default_factory=list)       # 超過 20 檔上限
    current_count: int = 0


@dataclass
class DeleteResult:
    removed: List[str] = field(default_factory=list)
    not_in_list: List[str] = field(default_factory=list)
    remaining: List[StockSnapshot] = field(default_factory=list)
    current_count: int = 0


def parse_stock_ids(raw: str) -> List[str]:
    """解析 comma-separated 股票代號；strip whitespace + dedupe（保留輸入順序）。"""
    if not raw:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for token in raw.split(","):
        sid = token.strip()
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _latest_price_row(db: Session, stock_id: str) -> Optional[DailyPrice]:
    """撈該股最新一筆 daily_price（不依今日；走 daily_price.trade_date DESC LIMIT 1）。"""
    return (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )


def _build_snapshot(stock: StockMaster, price: Optional[DailyPrice]) -> StockSnapshot:
    return StockSnapshot(
        stock_id=stock.stock_id,
        stock_name=stock.stock_name,
        industry_name=stock.industry_name,
        sub_industry=stock.sub_industry,
        close_price=price.close_price if price else None,
        spread_pct=price.spread if price else None,
        trade_date=price.trade_date if price else None,
    )


def get_current_count(db: Session, chat_id: int) -> int:
    return (
        db.query(func.count(TelegramWatchlistEntry.id))
        .filter(TelegramWatchlistEntry.chat_id == chat_id)
        .scalar()
        or 0
    )


def list_watchlist(db: Session, chat_id: int) -> List[StockSnapshot]:
    """回傳 chat 的觀察清單 + 每檔最新股價。順序：依 added_at 升序。"""
    entries = (
        db.query(TelegramWatchlistEntry)
        .filter(TelegramWatchlistEntry.chat_id == chat_id)
        .order_by(TelegramWatchlistEntry.added_at.asc())
        .all()
    )
    if not entries:
        return []

    stock_ids = [e.stock_id for e in entries]
    stocks_by_id = {
        s.stock_id: s
        for s in db.query(StockMaster).filter(StockMaster.stock_id.in_(stock_ids)).all()
    }

    # 每檔個別撈最新 daily_price；20 檔規模可接受
    out: List[StockSnapshot] = []
    for entry in entries:
        stock = stocks_by_id.get(entry.stock_id)
        if stock is None:
            # 邊界：stock 被 ETL 移除但 watchlist 還有 — 顯示為 fallback
            out.append(
                StockSnapshot(
                    stock_id=entry.stock_id,
                    stock_name=entry.stock_id,
                    industry_name=None,
                    sub_industry=None,
                    close_price=None,
                    spread_pct=None,
                    trade_date=None,
                )
            )
            continue
        price = _latest_price_row(db, entry.stock_id)
        out.append(_build_snapshot(stock, price))
    return out


def add_stocks(db: Session, *, chat_id: int, stock_ids: List[str]) -> AddResult:
    """新增多檔到 chat 觀察清單。

    優先序：
    1. 先把所有 stock_ids 分類 (added / duplicates / not_found / over_limit)
    2. 對 added 批次寫 DB；DB 寫入失敗 → 回滾、移入 not_found
    """
    result = AddResult()

    if not stock_ids:
        result.current_count = get_current_count(db, chat_id)
        return result

    # 1. 查 stocks_master
    stocks_by_id = {
        s.stock_id: s
        for s in db.query(StockMaster).filter(StockMaster.stock_id.in_(stock_ids)).all()
    }

    # 2. 查既有 watchlist
    existing_ids = set(
        row[0]
        for row in db.query(TelegramWatchlistEntry.stock_id)
        .filter(
            TelegramWatchlistEntry.chat_id == chat_id,
            TelegramWatchlistEntry.stock_id.in_(stock_ids),
        )
        .all()
    )

    current = get_current_count(db, chat_id)
    remaining_capacity = max(0, WATCHLIST_LIMIT - current)

    to_add_stocks: List[StockMaster] = []
    for sid in stock_ids:
        if sid not in stocks_by_id:
            result.not_found.append(sid)
            continue
        if sid in existing_ids:
            result.duplicates.append(sid)
            continue
        if len(to_add_stocks) >= remaining_capacity:
            result.over_limit.append(sid)
            continue
        to_add_stocks.append(stocks_by_id[sid])

    # 3. 批次寫入
    if to_add_stocks:
        try:
            for stock in to_add_stocks:
                db.add(TelegramWatchlistEntry(chat_id=chat_id, stock_id=stock.stock_id))
            db.commit()
            for stock in to_add_stocks:
                price = _latest_price_row(db, stock.stock_id)
                result.added.append(_build_snapshot(stock, price))
        except Exception:
            logger.exception("Failed to add watchlist entries chat_id=%s", chat_id)
            db.rollback()
            # 寫失敗 → 全部移到 not_found（無法區分哪幾檔成功；保守處理）
            result.not_found.extend(s.stock_id for s in to_add_stocks)
            result.added = []

    result.current_count = get_current_count(db, chat_id)
    return result


def remove_stocks(db: Session, *, chat_id: int, stock_ids: List[str]) -> DeleteResult:
    """從 chat 觀察清單刪除多檔。回傳剩餘清單以便 caller 印出。"""
    result = DeleteResult()
    if not stock_ids:
        result.remaining = list_watchlist(db, chat_id)
        result.current_count = len(result.remaining)
        return result

    existing_ids = set(
        row[0]
        for row in db.query(TelegramWatchlistEntry.stock_id)
        .filter(
            TelegramWatchlistEntry.chat_id == chat_id,
            TelegramWatchlistEntry.stock_id.in_(stock_ids),
        )
        .all()
    )

    to_remove = [sid for sid in stock_ids if sid in existing_ids]
    not_in = [sid for sid in stock_ids if sid not in existing_ids]
    result.not_in_list = not_in

    if to_remove:
        try:
            db.query(TelegramWatchlistEntry).filter(
                TelegramWatchlistEntry.chat_id == chat_id,
                TelegramWatchlistEntry.stock_id.in_(to_remove),
            ).delete(synchronize_session=False)
            db.commit()
            result.removed = to_remove
        except Exception:
            logger.exception("Failed to remove watchlist entries chat_id=%s", chat_id)
            db.rollback()
            result.removed = []
            result.not_in_list.extend(to_remove)

    result.remaining = list_watchlist(db, chat_id)
    result.current_count = len(result.remaining)
    return result
