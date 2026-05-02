"""
關注買進清單 API（M19）

- GET    /api/watchlist            → 取得目前使用者的持股列表（含最新收盤價 + 未實現損益）
- POST   /api/watchlist            → 加入一檔（上限 30 檔、同股不可重複）
- DELETE /api/watchlist/{entry_id} → 移除單筆
- DELETE /api/watchlist            → 清空整個清單

所有端點需要登入（Depends(require_user)）。

Cap enforcement 注意：
POST 的 30 檔上限由 COUNT(*) → INSERT 兩步完成，並未在 transaction 內上鎖。
同一使用者從兩個 tab 幾乎同時加入時，理論上有 race，可能插到第 21 筆；
實務觸發機率極低，且最壞結果僅是資料多 1 筆，不影響正確性。
若未來要徹底保證，可改用 SELECT ... FOR UPDATE 鎖定該使用者的子集，
或由上層改成「事務 + 重試」。目前設計為 best-effort。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import (
    DailyPrice,
    StockMaster,
    User,
    UserWatchlist,
    WatchlistTradeQualitySnapshot,
)
from app.trade_quality_cache import (
    load_latest_ok_snapshot,
    load_recent_ok_snapshots,
    load_snapshot,
    resolve_snapshot_trade_date,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

WATCHLIST_MAX_ENTRIES = 30

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _today_taipei() -> date:
    # Render server 跑 UTC；未來日期判斷必須以 Asia/Taipei 為準，
    # 否則台北時間 00:00~08:00 時使用者選「今天」會被 server 誤判為未來日期。
    return datetime.now(TAIPEI_TZ).date()


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


def _delete_trade_quality_snapshots_for_entry(db: Session, *, user_id: int, stock_id: str, buy_date: date) -> None:
    (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(
            WatchlistTradeQualitySnapshot.user_id == user_id,
            WatchlistTradeQualitySnapshot.stock_id == stock_id,
            WatchlistTradeQualitySnapshot.buy_date == buy_date,
        )
        .delete(synchronize_session=False)
    )


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

    if payload.buy_date > _today_taipei():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="買進日期不能是未來")

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
    _delete_trade_quality_snapshots_for_entry(
        db,
        user_id=user.id,
        stock_id=entry.stock_id,
        buy_date=entry.buy_date,
    )
    db.delete(entry)
    db.commit()
    logger.info("watchlist remove: user=%s entry=%s", user.id, entry_id)
    return None


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_watchlist(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(WatchlistTradeQualitySnapshot.user_id == user.id)
        .delete(synchronize_session=False)
    )
    deleted = (
        db.query(UserWatchlist)
        .filter(UserWatchlist.user_id == user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info("watchlist clear: user=%s deleted=%s", user.id, deleted)
    return None


# ── M25 trade quality snapshot endpoints ─────────────────────────────────────


class WatchlistKeyFactor(BaseModel):
    category: str
    level: str
    trend: str
    note: str


class WatchlistSnapshotPayload(BaseModel):
    """單一個 watchlist 個股的 trade quality 快照（給 GET /watchlist/trade-quality 用）。"""
    snapshot_trade_date: date
    rating: Optional[str]
    rating_label: Optional[str]
    classification: Optional[str]
    market_state: Optional[str]
    action: Optional[str]
    summary: Optional[str]
    report_markdown: Optional[str]
    key_factors: Optional[List[WatchlistKeyFactor]]
    status: str  # ok | failed
    is_stale: bool  # True 表示這筆不是今日 snapshot_trade_date（fallback 自舊快照）
    generated_at: datetime


class WatchlistFactorSnapshot(BaseModel):
    """精簡版快照：只給 KeyFactorsTimeline 多日趨勢使用，不含 markdown 等大欄位。"""
    snapshot_trade_date: date
    key_factors: Optional[List[WatchlistKeyFactor]]


class WatchlistTradeQualityItem(BaseModel):
    stock_id: str
    stock_name: str
    industry_name: Optional[str]
    buy_date: date
    avg_price: float
    latest_close: Optional[float]
    latest_trade_date: Optional[date]
    change_pct: Optional[float]                  # 相對前一交易日的漲跌幅 %
    unrealized_pct: Optional[float]              # 相對 avg_price
    latest: Optional[WatchlistSnapshotPayload]   # 今日 ok 快照（None = 還沒分析過 / 今日 failed）
    previous: Optional[WatchlistSnapshotPayload] # 上一筆 ok 快照（給 delta 比對）
    recent_factors: List[WatchlistFactorSnapshot] = Field(
        default_factory=list,
        description="最近 N 個 ok 快照（snapshot_trade_date 倒序，最新在前），給 KeyFactorsTimeline 多日趨勢用",
    )


class WatchlistTradeQualityResponse(BaseModel):
    items: List[WatchlistTradeQualityItem]
    total: int
    snapshot_trade_date: Optional[date]          # 今日預期 snapshot_trade_date（前端比對 latest 是否新鮮）


def _extract_key_factors(row: WatchlistTradeQualitySnapshot) -> List[WatchlistKeyFactor]:
    factors_raw = row.key_factors or []
    factors: List[WatchlistKeyFactor] = []
    if isinstance(factors_raw, list):
        for f in factors_raw:
            if isinstance(f, dict):
                try:
                    factors.append(WatchlistKeyFactor(**f))
                except Exception:
                    continue
    return factors


def _row_to_snapshot_payload(
    row: WatchlistTradeQualitySnapshot,
    *,
    expected_trade_date: Optional[date],
) -> WatchlistSnapshotPayload:
    factors = _extract_key_factors(row)
    is_stale = (
        expected_trade_date is not None
        and row.snapshot_trade_date != expected_trade_date
    )
    return WatchlistSnapshotPayload(
        snapshot_trade_date=row.snapshot_trade_date,
        rating=row.rating,
        rating_label=row.rating_label,
        classification=row.classification,
        market_state=row.market_state,
        action=row.action,
        summary=row.summary,
        report_markdown=row.report_markdown,
        key_factors=factors or None,
        status=row.status,
        is_stale=is_stale,
        generated_at=row.generated_at,
    )


def _compute_change_pct(
    db: Session, stock_id: str, latest_trade_date: date
) -> Optional[float]:
    """回傳該股 latest_trade_date 相對前一交易日的漲跌幅 %。"""
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= latest_trade_date,
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(2)
        .all()
    )
    if len(rows) < 2:
        return None
    today = rows[0].close_price
    prev = rows[1].close_price
    if today is None or prev is None or prev == 0:
        return None
    return (float(today) - float(prev)) / float(prev) * 100


@router.get("/trade-quality", response_model=WatchlistTradeQualityResponse)
def list_watchlist_trade_quality(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> WatchlistTradeQualityResponse:
    """M25 自選清單表現表格主資料來源。

    對使用者全 watchlist：
    - latest = 今日 snapshot_trade_date 對應的快照（找不到 / failed → None）
    - previous = 在 latest 之前最近一筆 ok 快照（給 delta 用）
    - is_stale = latest snapshot_trade_date != 今日預期 → 提示 UI「資料較舊」

    沒有任何 ok 快照的個股 latest=None；前端應對這種 row 觸發 POST refresh 補洞。
    """
    entries = (
        db.query(UserWatchlist)
        .filter(UserWatchlist.user_id == user.id)
        .order_by(UserWatchlist.created_at.asc())
        .all()
    )
    if not entries:
        return WatchlistTradeQualityResponse(
            items=[],
            total=0,
            snapshot_trade_date=resolve_snapshot_trade_date(db),
        )

    expected_snapshot_date = resolve_snapshot_trade_date(db)
    base_items = _build_items(db, entries)
    base_by_stock = {item.stock_id: item for item in base_items}

    items: List[WatchlistTradeQualityItem] = []
    for entry in entries:
        base = base_by_stock.get(entry.stock_id)
        latest_payload: Optional[WatchlistSnapshotPayload] = None
        previous_payload: Optional[WatchlistSnapshotPayload] = None

        # 今日 snapshot 命中（status 不限 ok/failed，給前端顯示「分析失敗」狀態）
        if expected_snapshot_date is not None:
            today_row = load_snapshot(
                db,
                user_id=user.id,
                stock_id=entry.stock_id,
                buy_date=entry.buy_date,
                snapshot_trade_date=expected_snapshot_date,
            )
            if today_row is not None and today_row.status == "ok":
                latest_payload = _row_to_snapshot_payload(today_row, expected_trade_date=expected_snapshot_date)

        # latest=None 時 fallback 找上一筆 ok（資料較舊的快照）
        # latest!=None 時也找前一筆 ok（給 delta layer 用）
        before_eq = (
            (latest_payload.snapshot_trade_date - timedelta(days=1))
            if latest_payload is not None
            else expected_snapshot_date
        )
        prev_row = load_latest_ok_snapshot(
            db,
            user_id=user.id,
            stock_id=entry.stock_id,
            buy_date=entry.buy_date,
            before_or_eq_trade_date=before_eq,
        )
        if prev_row is not None:
            payload = _row_to_snapshot_payload(prev_row, expected_trade_date=expected_snapshot_date)
            if latest_payload is None:
                # 沒有今日成功快照 → 把 prev 當成 latest 顯示，並標 is_stale=True 提示 UI
                latest_payload = payload
            else:
                previous_payload = payload

        change_pct: Optional[float] = None
        if base and base.latest_trade_date is not None:
            change_pct = _compute_change_pct(db, entry.stock_id, base.latest_trade_date)

        recent_rows = load_recent_ok_snapshots(
            db,
            user_id=user.id,
            stock_id=entry.stock_id,
            buy_date=entry.buy_date,
            limit=3,
        )
        recent_factors = [
            WatchlistFactorSnapshot(
                snapshot_trade_date=r.snapshot_trade_date,
                key_factors=_extract_key_factors(r) or None,
            )
            for r in recent_rows
        ]

        items.append(
            WatchlistTradeQualityItem(
                stock_id=entry.stock_id,
                stock_name=base.stock_name if base else entry.stock_id,
                industry_name=base.industry_name if base else None,
                buy_date=entry.buy_date,
                avg_price=float(entry.avg_price),
                latest_close=base.latest_close if base else None,
                latest_trade_date=base.latest_trade_date if base else None,
                change_pct=change_pct,
                unrealized_pct=base.unrealized_pct if base else None,
                latest=latest_payload,
                previous=previous_payload,
                recent_factors=recent_factors,
            )
        )

    return WatchlistTradeQualityResponse(
        items=items,
        total=len(items),
        snapshot_trade_date=expected_snapshot_date,
    )


class WatchlistRefreshRequest(BaseModel):
    stock_id: str = Field(min_length=1, max_length=20)


@router.post("/trade-quality/refresh", response_model=WatchlistTradeQualityItem)
def refresh_watchlist_trade_quality(
    payload: WatchlistRefreshRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> WatchlistTradeQualityItem:
    """對單一個 watchlist 個股即時跑 trade quality（on-demand 補洞）。

    若今日已有 ok 快照 → run_trade_quality_for_user 內部 cache hit 不打 OpenAI。
    若今日 failed 或無快照 → 重新打 OpenAI → 寫入快照表（source='on_demand'）→ 回傳更新後 row。
    Rate limit：與 /api/analysis/trade-quality 共用使用者層 30/day（slowapi 在 analysis router 套用，
    本 endpoint 直接呼叫 runner、不受相同 limit decorator 約束；以後若要嚴格約束可再加 decorator）。
    """
    stock_id = payload.stock_id.strip()
    entry = (
        db.query(UserWatchlist)
        .filter(
            UserWatchlist.user_id == user.id,
            UserWatchlist.stock_id == stock_id,
        )
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="此股票不在你的清單中")

    # 在這裡 import 才可避開「watchlist router import 時 analysis 還沒載入」的循環
    from app.routers.analysis import run_trade_quality_for_user

    try:
        run_trade_quality_for_user(
            db,
            user=user,
            stock_id=entry.stock_id,
            buy_date_input=entry.buy_date,
            persist_source="on_demand",
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Watchlist trade quality refresh failed: user=%s stock=%s",
            user.id,
            entry.stock_id,
        )
        # 寫一筆 failed 紀錄方便前端顯示
        from app.trade_quality_cache import save_snapshot_failed

        snapshot_trade_date = resolve_snapshot_trade_date(db)
        if snapshot_trade_date is not None:
            try:
                save_snapshot_failed(
                    db,
                    user_id=user.id,
                    stock_id=entry.stock_id,
                    buy_date=entry.buy_date,
                    snapshot_trade_date=snapshot_trade_date,
                    error_message="on-demand refresh failed",
                    source="on_demand",
                )
            except Exception:
                logger.exception("Failed to persist on-demand failure marker")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="分析服務暫時不可用，請稍後再試",
        )

    # 回傳該檔個股最新 row（重複用 list endpoint 的組裝邏輯）
    full = list_watchlist_trade_quality(user=user, db=db)
    for item in full.items:
        if item.stock_id == stock_id:
            return item
    # 異常邊界：剛跑完但 list 找不到 → 退回 404 而非沉默
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到剛重整的個股")
