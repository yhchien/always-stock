import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.industry_names import normalize_industry_name
from app.models import IndustryDailyFlow, InstStockFlow, StockMaster

logger = logging.getLogger(__name__)


@dataclass
class IndustryFlowSnapshot:
    trade_date: date
    industry_name: str
    total_net_amount: float
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    total_buy_amount: float
    total_sell_amount: float


def _serialize_agg_rows(rows: Sequence[IndustryDailyFlow]) -> List[IndustryFlowSnapshot]:
    return [
        IndustryFlowSnapshot(
            trade_date=row.trade_date,
            industry_name=normalize_industry_name(row.industry_name) or "",
            total_net_amount=row.total_net_amount or 0.0,
            foreign_net_amount=row.foreign_net_amount or 0.0,
            trust_net_amount=row.trust_net_amount or 0.0,
            dealer_net_amount=row.dealer_net_amount or 0.0,
            total_buy_amount=row.total_buy_amount or 0.0,
            total_sell_amount=row.total_sell_amount or 0.0,
        )
        for row in rows
    ]


def _merge_snapshots(rows: Sequence[IndustryFlowSnapshot]) -> List[IndustryFlowSnapshot]:
    merged: Dict[tuple[date, str], IndustryFlowSnapshot] = {}
    for row in rows:
        industry_name = normalize_industry_name(row.industry_name) or ""
        key = (row.trade_date, industry_name)
        current = merged.get(key)
        if current is None:
            merged[key] = IndustryFlowSnapshot(
                trade_date=row.trade_date,
                industry_name=industry_name,
                total_net_amount=row.total_net_amount,
                foreign_net_amount=row.foreign_net_amount,
                trust_net_amount=row.trust_net_amount,
                dealer_net_amount=row.dealer_net_amount,
                total_buy_amount=row.total_buy_amount,
                total_sell_amount=row.total_sell_amount,
            )
            continue

        current.total_net_amount += row.total_net_amount
        current.foreign_net_amount += row.foreign_net_amount
        current.trust_net_amount += row.trust_net_amount
        current.dealer_net_amount += row.dealer_net_amount
        current.total_buy_amount += row.total_buy_amount
        current.total_sell_amount += row.total_sell_amount

    return list(merged.values())


def _aggregate_from_inst_flow(
    db: Session,
    trade_dates: Sequence[date],
    industry_names: Optional[Sequence[str]] = None,
) -> List[IndustryFlowSnapshot]:
    if not trade_dates:
        return []

    query = (
        db.query(
            InstStockFlow.trade_date,
            StockMaster.industry_name,
            InstStockFlow.inst_type,
            func.sum(InstStockFlow.buy_amount_est),
            func.sum(InstStockFlow.sell_amount_est),
            func.sum(InstStockFlow.net_amount_est),
        )
        .join(StockMaster, StockMaster.stock_id == InstStockFlow.stock_id)
        .filter(InstStockFlow.trade_date.in_(trade_dates))
        .group_by(
            InstStockFlow.trade_date,
            StockMaster.industry_name,
            InstStockFlow.inst_type,
        )
    )

    if industry_names:
        query = query.filter(StockMaster.industry_name.in_(industry_names))

    rows = query.all()
    if not rows:
        return []

    merged: Dict[tuple, IndustryFlowSnapshot] = {}
    for trade_date, industry_name, inst_type, buy_amount, sell_amount, net_amount in rows:
        canonical_name = normalize_industry_name(industry_name) or ""
        key = (trade_date, canonical_name)
        snapshot = merged.get(key)
        if snapshot is None:
            snapshot = IndustryFlowSnapshot(
                trade_date=trade_date,
                industry_name=canonical_name,
                total_net_amount=0.0,
                foreign_net_amount=0.0,
                trust_net_amount=0.0,
                dealer_net_amount=0.0,
                total_buy_amount=0.0,
                total_sell_amount=0.0,
            )
            merged[key] = snapshot

        net_value = net_amount or 0.0
        snapshot.total_net_amount += net_value
        snapshot.total_buy_amount += buy_amount or 0.0
        snapshot.total_sell_amount += sell_amount or 0.0

        if inst_type == "foreign":
            snapshot.foreign_net_amount += net_value
        elif inst_type == "trust":
            snapshot.trust_net_amount += net_value
        elif inst_type == "dealer":
            snapshot.dealer_net_amount += net_value

    logger.info(
        "Industry flow fallback aggregated from inst_stock_flow for %d date(s): %s",
        len(set(trade_dates)),
        ", ".join(str(d) for d in sorted(set(trade_dates))),
    )
    return sorted(
        merged.values(),
        key=lambda row: (row.trade_date, row.total_net_amount),
        reverse=True,
    )


def load_industry_flow_rows(
    db: Session,
    trade_date: date,
    industry_names: Optional[Sequence[str]] = None,
) -> List[IndustryFlowSnapshot]:
    return load_industry_flow_rows_for_dates(db, [trade_date], industry_names)


def load_industry_flow_rows_for_dates(
    db: Session,
    trade_dates: Sequence[date],
    industry_names: Optional[Sequence[str]] = None,
) -> List[IndustryFlowSnapshot]:
    if not trade_dates:
        return []

    deduped_dates = list(dict.fromkeys(trade_dates))
    query = db.query(IndustryDailyFlow).filter(IndustryDailyFlow.trade_date.in_(deduped_dates))
    if industry_names:
        query = query.filter(IndustryDailyFlow.industry_name.in_(industry_names))
    agg_rows = query.all()

    rows = _serialize_agg_rows(agg_rows)
    covered_dates = {row.trade_date for row in rows}
    missing_dates = [d for d in deduped_dates if d not in covered_dates]
    if missing_dates:
        rows.extend(_aggregate_from_inst_flow(db, missing_dates, industry_names))

    merged_rows = _merge_snapshots(rows)
    return sorted(
        merged_rows,
        key=lambda row: (row.trade_date, row.total_net_amount),
        reverse=True,
    )


def get_latest_industry_trade_date(db: Session, ceiling: Optional[date] = None) -> Optional[date]:
    query = db.query(func.max(InstStockFlow.trade_date))
    if ceiling is not None:
        query = query.filter(InstStockFlow.trade_date <= ceiling)
    latest = query.scalar()
    if latest is not None:
        return latest

    fallback_query = db.query(func.max(IndustryDailyFlow.trade_date))
    if ceiling is not None:
        fallback_query = fallback_query.filter(IndustryDailyFlow.trade_date <= ceiling)
    return fallback_query.scalar()


def get_recent_industry_trade_dates(db: Session, end_date: date, limit: int) -> List[date]:
    rows = (
        db.query(InstStockFlow.trade_date)
        .filter(InstStockFlow.trade_date <= end_date)
        .distinct()
        .order_by(InstStockFlow.trade_date.desc())
        .limit(limit)
        .all()
    )
    dates = [row[0] for row in rows]
    if dates:
        return dates

    fallback_rows = (
        db.query(IndustryDailyFlow.trade_date)
        .filter(IndustryDailyFlow.trade_date <= end_date)
        .distinct()
        .order_by(IndustryDailyFlow.trade_date.desc())
        .limit(limit)
        .all()
    )
    return [row[0] for row in fallback_rows]
