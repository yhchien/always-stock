"""Resolve the actual fill price/date for a decision made on day T.

All prices used here already live on the CohortDayRow for day T
(next_day_buy_price_if_entering_today / next_day_sell_price_if_exiting_today
were pre-computed by the data export as "the adjacent trading day's
high/low"). This module's only job is to (a) refuse to fill when the price
is missing, and (b) compute the calendar date the fill actually happens on,
for reporting.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from .loader import CohortDayRow


def next_trading_day(calendar: List[date], d: date) -> Optional[date]:
    try:
        i = calendar.index(d)
    except ValueError:
        return None
    if i + 1 >= len(calendar):
        return None
    return calendar[i + 1]


def resolve_buy_fill(row: CohortDayRow, calendar: List[date]):
    """Returns (execution_date, execution_price) or (None, None) if no next
    trading day's price is available (data ends, or gap)."""
    price = row.next_day_buy_price_if_entering_today
    if price is None:
        return None, None
    exec_date = next_trading_day(calendar, row.trade_date)
    if exec_date is None:
        return None, None
    return exec_date, price


def resolve_sell_fill(row: CohortDayRow, calendar: List[date]):
    price = row.next_day_sell_price_if_exiting_today
    if price is None:
        return None, None
    exec_date = next_trading_day(calendar, row.trade_date)
    if exec_date is None:
        return None, None
    return exec_date, price
