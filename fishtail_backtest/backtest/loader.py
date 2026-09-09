"""Load the per-cohort daily panel CSV into a clean, typed, sorted structure.

One input row = one (stock_id, first_seen_date) cohort's state on one
trade_date. A single stock_id can have multiple concurrent/overlapping
cohorts (re-caught after being dropped, or a fresh cohort while an older
one is still open). The loader keeps cohorts distinct; deduplication across
cohorts of the same stock_id on the same trade_date is the portfolio
engine's job (see portfolio.py / signals.py), not the loader's.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_bool(s: str) -> bool:
    return str(s).strip().lower() in {"true", "1", "yes"}


def _parse_float(s: str) -> Optional[float]:
    s = (s or "").strip()
    if s == "":
        return None
    return float(s)


def _parse_str_or_none(s: str) -> Optional[str]:
    s = (s or "").strip()
    return s if s else None


@dataclass(frozen=True)
class CohortDayRow:
    stock_id: str
    stock_name: str
    first_seen_date: date
    day_index: int
    trade_date: date
    p3_selected_today: bool
    hit_count_so_far: int
    momentum_score: Optional[float]
    p4_decision: Optional[str]  # CONTINUE / CAUTION / STOP_OBSERVING / None
    mark_to_market_return_pct: Optional[float]
    next_day_buy_price_if_entering_today: Optional[float]
    next_day_sell_price_if_exiting_today: Optional[float]
    is_official_exit_signal_day: bool

    @property
    def cohort_key(self):
        return (self.stock_id, self.first_seen_date)


def load_daily_panel(csv_path: str) -> List[CohortDayRow]:
    rows: List[CohortDayRow] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                CohortDayRow(
                    stock_id=r["stock_id"],
                    stock_name=r["stock_name"],
                    first_seen_date=_parse_date(r["first_seen_date"]),
                    day_index=int(r["day_index"]),
                    trade_date=_parse_date(r["trade_date"]),
                    p3_selected_today=_parse_bool(r["p3_selected_today"]),
                    hit_count_so_far=int(r["hit_count_so_far"]),
                    momentum_score=_parse_float(r["momentum_score"]),
                    p4_decision=_parse_str_or_none(r["p4_decision"]),
                    mark_to_market_return_pct=_parse_float(r["mark_to_market_return_pct"]),
                    next_day_buy_price_if_entering_today=_parse_float(
                        r["next_day_buy_price_if_entering_today"]
                    ),
                    next_day_sell_price_if_exiting_today=_parse_float(
                        r["next_day_sell_price_if_exiting_today"]
                    ),
                    is_official_exit_signal_day=_parse_bool(r["is_official_exit_signal_day"]),
                )
            )
    rows.sort(key=lambda r: (r.trade_date, r.stock_id, r.first_seen_date, r.day_index))
    return rows


def load_etf_ids(json_path: str) -> set:
    if not os.path.exists(json_path):
        return set()
    with open(json_path) as f:
        return set(json.load(f))


def group_by_trade_date(rows: List[CohortDayRow]) -> Dict[date, List[CohortDayRow]]:
    """Every row seen on a given trade_date, across all cohorts/stocks."""
    out: Dict[date, List[CohortDayRow]] = {}
    for r in rows:
        out.setdefault(r.trade_date, []).append(r)
    return out


def trading_calendar(rows: List[CohortDayRow]) -> List[date]:
    return sorted({r.trade_date for r in rows})
