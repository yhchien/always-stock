"""
M21 PART 4：fundamental

取 `monthly_revenue` 中 buy_date 當月以前最新一筆 revenue 的 YoY / MoM。
`guidance` 永遠 None（DB 無法說會 / 展望來源）。

若 buy_date 前沒有任何 monthly_revenue row → 整段 None + data_quality note。
"""
from __future__ import annotations

from datetime import date
from typing import List, Tuple

from sqlalchemy.orm import Session

from app.models import MonthlyRevenue


def compute_fundamental(
    db: Session,
    stock_id: str,
    buy_date: date,
    industry_name: str,  # noqa: ARG001
) -> Tuple[dict, List[str]]:
    row = (
        db.query(MonthlyRevenue)
        .filter(
            MonthlyRevenue.stock_id == stock_id,
            MonthlyRevenue.revenue_month <= buy_date,
        )
        .order_by(MonthlyRevenue.revenue_month.desc())
        .first()
    )

    if row is None:
        notes = [
            f"fundamental is null because no monthly_revenue row on/before {buy_date} for {stock_id}"
        ]
        return ({"revenue_yoy": None, "revenue_mom": None, "guidance": None}, notes)

    return (
        {
            "revenue_yoy": _to_float(row.yoy_pct),
            "revenue_mom": _to_float(row.mom_pct),
            "guidance": None,
        },
        [],
    )


def _to_float(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
