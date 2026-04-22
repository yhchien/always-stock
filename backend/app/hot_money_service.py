"""
M22 熱錢湧入個股排行共用 service。

給 L0 全市場 Top 20 與 L1 單一產業 Top 10 共用，演算法：
- 窗口 = 交易日（不是曆日），用 `inst_stock_flow.trade_date DESC LIMIT days` 決定
- 排序 key = 三大法人合計 `net_amount_est`（降冪）
- 漲跌 % = (窗口終點收盤 - 窗口起點前一交易日收盤) / 前一交易日收盤

詳見 docs/plans/hot_money_list_spec.md。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import DailyPrice, InstStockFlow, StockMaster

logger = logging.getLogger(__name__)

_INST_TYPES = ("foreign", "trust", "dealer")


@dataclass
class HotMoneyStockItem:
    rank: int
    stock_id: str
    stock_name: str
    industry_name: str
    sub_industry: Optional[str]
    start_close_price: Optional[float]
    end_close_price: Optional[float]
    price_change_pct: Optional[float]
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    total_net_amount: float


@dataclass
class HotMoneyResult:
    start_date: Optional[date]
    end_date: Optional[date]
    trade_dates: List[date]
    items: List[HotMoneyStockItem]


def get_recent_trade_dates(
    db: Session,
    end_date: date,
    days: int,
    stock_ids: Optional[Sequence[str]] = None,
) -> List[date]:
    """
    回傳 `<= end_date` 的最近 N 個交易日（由舊到新）。

    當 `stock_ids` 有值時，只看這些股票的交易日；避免某檔停牌時窗口天數變少。
    """
    if days <= 0:
        return []

    query = db.query(InstStockFlow.trade_date).filter(
        InstStockFlow.trade_date <= end_date,
    )
    if stock_ids:
        query = query.filter(InstStockFlow.stock_id.in_(list(stock_ids)))

    rows = (
        query.distinct()
        .order_by(InstStockFlow.trade_date.desc())
        .limit(days)
        .all()
    )
    dates = [row[0] for row in rows]
    dates.reverse()  # 由舊到新
    return dates


def _aggregate_net_amount(
    db: Session,
    trade_dates: Sequence[date],
    stock_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, float]]:
    """
    回傳 `{stock_id: {foreign, trust, dealer, total}}`。

    只聚合三大法人（foreign / trust / dealer），不含其他 inst_type。
    """
    if not trade_dates:
        return {}

    query = (
        db.query(
            InstStockFlow.stock_id,
            InstStockFlow.inst_type,
            func.sum(InstStockFlow.net_amount_est),
        )
        .filter(InstStockFlow.trade_date.in_(list(trade_dates)))
        .filter(InstStockFlow.inst_type.in_(_INST_TYPES))
        .group_by(InstStockFlow.stock_id, InstStockFlow.inst_type)
    )
    if stock_ids:
        query = query.filter(InstStockFlow.stock_id.in_(list(stock_ids)))

    totals: Dict[str, Dict[str, float]] = {}
    for stock_id, inst_type, amount in query.all():
        bucket = totals.setdefault(
            stock_id,
            {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "total": 0.0},
        )
        value = float(amount or 0.0)
        bucket[inst_type] += value
        bucket["total"] += value
    return totals


def _load_stock_master(
    db: Session,
    stock_ids: Sequence[str],
) -> Dict[str, StockMaster]:
    if not stock_ids:
        return {}
    rows = (
        db.query(StockMaster)
        .filter(StockMaster.stock_id.in_(list(stock_ids)))
        .all()
    )
    return {row.stock_id: row for row in rows}


def _load_close_prices_on(
    db: Session,
    trade_date: date,
    stock_ids: Sequence[str],
) -> Dict[str, float]:
    if not stock_ids:
        return {}
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.close_price)
        .filter(
            DailyPrice.trade_date == trade_date,
            DailyPrice.stock_id.in_(list(stock_ids)),
        )
        .all()
    )
    return {sid: price for sid, price in rows if price is not None}


def _load_prev_close_prices(
    db: Session,
    before_date: date,
    stock_ids: Sequence[str],
) -> Dict[str, float]:
    """
    撈每檔股票在 `< before_date` 的最近一筆 close_price。

    用每檔獨立 MAX(trade_date)，避免某檔停牌時撈不到。
    """
    if not stock_ids:
        return {}

    subq = (
        db.query(
            DailyPrice.stock_id.label("stock_id"),
            func.max(DailyPrice.trade_date).label("prev_date"),
        )
        .filter(
            DailyPrice.trade_date < before_date,
            DailyPrice.stock_id.in_(list(stock_ids)),
        )
        .group_by(DailyPrice.stock_id)
        .subquery()
    )
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.close_price)
        .join(
            subq,
            and_(
                DailyPrice.stock_id == subq.c.stock_id,
                DailyPrice.trade_date == subq.c.prev_date,
            ),
        )
        .all()
    )
    return {sid: price for sid, price in rows if price is not None}


def compute_hot_money(
    db: Session,
    end_date: date,
    days: int,
    limit: int,
    stock_ids: Optional[Sequence[str]] = None,
) -> HotMoneyResult:
    """
    計算窗口內三大法人累計淨買超 Top K。

    Parameters
    ----------
    end_date
        窗口終點（已被 caller resolve 到有資料的交易日）
    days
        窗口長度（交易日）
    limit
        回傳 Top K
    stock_ids
        L1 場景傳入「產業（或子產業）底下的股票清單」；L0 傳 None = 全市場
    """
    if stock_ids is not None and len(stock_ids) == 0:
        return HotMoneyResult(start_date=None, end_date=None, trade_dates=[], items=[])

    trade_dates = get_recent_trade_dates(db, end_date, days, stock_ids=stock_ids)
    if not trade_dates:
        return HotMoneyResult(start_date=None, end_date=None, trade_dates=[], items=[])

    window_start = trade_dates[0]
    window_end = trade_dates[-1]

    totals = _aggregate_net_amount(db, trade_dates, stock_ids=stock_ids)
    if not totals:
        return HotMoneyResult(
            start_date=window_start,
            end_date=window_end,
            trade_dates=list(trade_dates),
            items=[],
        )

    ranked = sorted(
        totals.items(),
        key=lambda kv: kv[1]["total"],
        reverse=True,
    )[:limit]
    top_stock_ids = [sid for sid, _ in ranked]

    masters = _load_stock_master(db, top_stock_ids)
    end_prices = _load_close_prices_on(db, window_end, top_stock_ids)
    prev_prices = _load_prev_close_prices(db, window_start, top_stock_ids)

    items: List[HotMoneyStockItem] = []
    for rank_idx, (stock_id, bucket) in enumerate(ranked, start=1):
        master = masters.get(stock_id)
        stock_name = master.stock_name if master else stock_id
        industry_name = master.industry_name if master else ""
        sub_industry = master.sub_industry if master else None

        end_price = end_prices.get(stock_id)
        prev_price = prev_prices.get(stock_id)
        change_pct: Optional[float] = None
        if (
            end_price is not None
            and prev_price is not None
            and prev_price != 0
        ):
            change_pct = (end_price - prev_price) / prev_price * 100.0

        items.append(
            HotMoneyStockItem(
                rank=rank_idx,
                stock_id=stock_id,
                stock_name=stock_name,
                industry_name=industry_name,
                sub_industry=sub_industry,
                start_close_price=prev_price,
                end_close_price=end_price,
                price_change_pct=change_pct,
                foreign_net_amount=bucket["foreign"],
                trust_net_amount=bucket["trust"],
                dealer_net_amount=bucket["dealer"],
                total_net_amount=bucket["total"],
            )
        )

    return HotMoneyResult(
        start_date=window_start,
        end_date=window_end,
        trade_dates=list(trade_dates),
        items=items,
    )
