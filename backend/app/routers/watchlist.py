"""
關注買進清單 API（M19）

- GET    /api/watchlist            → 取得目前使用者的持股列表（含最新收盤價 + 未實現損益）
- POST   /api/watchlist            → 加入一檔（上限 20 檔、同股不可重複）
- DELETE /api/watchlist/{entry_id} → 移除單筆
- DELETE /api/watchlist            → 清空整個清單

所有端點需要登入（Depends(require_user)）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import DailyPrice, StockMaster, User, UserWatchlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

WATCHLIST_MAX_ENTRIES = 20


class WatchlistCreateRequest(BaseModel):
    stock_id: str = Field(min_length=1, max_length=20)
    buy_date: date
    avg_price: float = Field(gt=0)


class WatchlistItem(BaseModel):
    id: int
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    buy_date: date
    avg_price: float
    latest_close: Optional[float]
    latest_trade_date: Optional[date]
    unrealized_pct: Optional[float]


class WatchlistResponse(BaseModel):
    items: List[WatchlistItem]
    total: int
    capacity: int


def _compute_pct(avg_price: float, latest_close: Optional[float]) -> Optional[float]:
    if latest_close is None or avg_price <= 0:
        return None
    return (latest_close - avg_price) / avg_price * 100


def _build_items(
    db: Session,
    entries: List[UserWatchlist],
) -> List[WatchlistItem]:
    if not entries:
        return []

    stock_ids = sorted({e.stock_id for e in entries})
    master_rows = (
        db.query(StockMaster.stock_id, StockMaster.stock_name, StockMaster.industry_name)
        .filter(StockMaster.stock_id.in_(stock_ids))
        .all()
    )
    master_map = {row.stock_id: row for row in master_rows}

    # 取每檔股票的最新收盤價（可能多檔，每檔都取 MAX(trade_date) 的 close_price）
    latest_subq = (
        db.query(
            DailyPrice.stock_id.label("stock_id"),
            func.max(DailyPrice.trade_date).label("latest_date"),
        )
        .filter(DailyPrice.stock_id.in_(stock_ids))
        .group_by(DailyPrice.stock_id)
        .subquery()
    )
    latest_rows = (
        db.query(
            DailyPrice.stock_id,
            DailyPrice.trade_date,
            DailyPrice.close_price,
        )
        .join(
            latest_subq,
            and_(
                DailyPrice.stock_id == latest_subq.c.stock_id,
                DailyPrice.trade_date == latest_subq.c.latest_date,
            ),
        )
        .all()
    )
    latest_map = {row.stock_id: (row.trade_date, row.close_price) for row in latest_rows}

    items: List[WatchlistItem] = []
    for e in entries:
        master = master_map.get(e.stock_id)
        latest = latest_map.get(e.stock_id)
        latest_close = latest[1] if latest else None
        latest_trade_date = latest[0] if latest else None
        items.append(
            WatchlistItem(
                id=e.id,
                stock_id=e.stock_id,
                stock_name=master.stock_name if master else e.stock_id,
                industry_name=master.industry_name if master else None,
                buy_date=e.buy_date,
                avg_price=float(e.avg_price),
                latest_close=float(latest_close) if latest_close is not None else None,
                latest_trade_date=latest_trade_date,
                unrealized_pct=_compute_pct(float(e.avg_price), float(latest_close) if latest_close is not None else None),
            )
        )
    return items


@router.get("", response_model=WatchlistResponse)
def list_watchlist(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> WatchlistResponse:
    entries = (
        db.query(UserWatchlist)
        .filter(UserWatchlist.user_id == user.id)
        .order_by(UserWatchlist.created_at.asc())
        .all()
    )
    items = _build_items(db, entries)
    return WatchlistResponse(
        items=items,
        total=len(items),
        capacity=WATCHLIST_MAX_ENTRIES,
    )


@router.post("", response_model=WatchlistItem, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    payload: WatchlistCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> WatchlistItem:
    stock_id = payload.stock_id.strip()
    if not stock_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="stock_id 不能為空")

    master = db.query(StockMaster).filter(StockMaster.stock_id == stock_id).first()
    if master is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此股票代號")

    existing = (
        db.query(UserWatchlist)
        .filter(
            UserWatchlist.user_id == user.id,
            UserWatchlist.stock_id == stock_id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="這檔股票已在清單中")

    count = (
        db.query(func.count(UserWatchlist.id))
        .filter(UserWatchlist.user_id == user.id)
        .scalar()
    )
    if count >= WATCHLIST_MAX_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"清單已達上限 {WATCHLIST_MAX_ENTRIES} 檔，請先移除部分股票再加入",
        )

    entry = UserWatchlist(
        user_id=user.id,
        stock_id=stock_id,
        buy_date=payload.buy_date,
        avg_price=payload.avg_price,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info("watchlist add: user=%s stock=%s", user.id, stock_id)

    items = _build_items(db, [entry])
    return items[0]


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(
    entry_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    entry = (
        db.query(UserWatchlist)
        .filter(
            UserWatchlist.id == entry_id,
            UserWatchlist.user_id == user.id,
        )
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此項目")
    db.delete(entry)
    db.commit()
    logger.info("watchlist remove: user=%s entry=%s", user.id, entry_id)
    return None


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_watchlist(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    deleted = (
        db.query(UserWatchlist)
        .filter(UserWatchlist.user_id == user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("watchlist clear: user=%s deleted=%s", user.id, deleted)
    return None
