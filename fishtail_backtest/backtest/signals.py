"""Entry/exit signal generation and candidate ranking.

Strict no-lookahead rule: every function here may only read fields on the
CURRENT day's CohortDayRow (day_index, p3_selected_today, hit_count_so_far,
momentum_score, p4_decision, mark_to_market_return_pct,
is_official_exit_signal_day). The two next_day_* price fields are read ONLY
by execution.py to resolve the fill price for a decision already made using
today's information -- they must never influence generate_entry_signal /
generate_exit_signal / rank_candidates themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .loader import CohortDayRow

ENTRY_TYPE_EARLY_HEALTHY_PULLBACK = "EARLY_HEALTHY_PULLBACK"
ENTRY_TYPE_DEEP_PULLBACK = "DEEP_PULLBACK"

EXIT_REASON_P4_STOP = "P4_STOP"
EXIT_REASON_OFFICIAL_EXIT = "OFFICIAL_EXIT"
EXIT_REASON_TAKE_PROFIT = "TAKE_PROFIT"
EXIT_REASON_REAL_STOP_LOSS = "REAL_POSITION_STOP_LOSS"
EXIT_REASON_END_OF_DATA_FORCED = "FORCED_LIQUIDATION_AT_END"


@dataclass(frozen=True)
class EntrySignal:
    row: CohortDayRow
    entry_type: str
    entry_score: float


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    row: CohortDayRow


def _in_range(value: Optional[float], lo: float, hi: float) -> bool:
    return value is not None and lo <= value <= hi


def _matches_setup_a(row: CohortDayRow, cfg: dict) -> bool:
    return (
        row.p4_decision == cfg["p4"]
        and cfg["day_index_min"] <= row.day_index <= cfg["day_index_max"]
        and row.hit_count_so_far == cfg["hit_count"]
        and _in_range(row.momentum_score, cfg["momentum_min"], cfg["momentum_max"])
        and _in_range(
            row.mark_to_market_return_pct, cfg["return_min"], cfg["return_max"]
        )
    )


def _matches_setup_b(row: CohortDayRow, cfg: dict) -> bool:
    return (
        row.p4_decision == cfg["p4"]
        and cfg["day_index_min"] <= row.day_index <= cfg["day_index_max"]
        and _in_range(row.momentum_score, cfg["momentum_min"], cfg["momentum_max"])
        and _in_range(
            row.mark_to_market_return_pct, cfg["return_min"], cfg["return_max"]
        )
    )


def _day_bonus(day_index: int) -> float:
    return {2: 3.0, 3: 2.0, 4: 1.0}.get(day_index, 0.0)


def _momentum_bonus(momentum_score: Optional[float]) -> float:
    if momentum_score is None:
        return 0.0
    return max(0.0, 2.0 - abs(momentum_score - 75.0) / 5.0)


def _pullback_bonus(entry_type: str, return_pct: Optional[float]) -> float:
    if return_pct is None:
        return 0.0
    if entry_type == ENTRY_TYPE_EARLY_HEALTHY_PULLBACK:
        # sweet spot -1.5% (midpoint of the -2.5..0 band's "meaningful pullback" zone)
        return max(0.0, 2.0 - abs(return_pct - (-1.5)) / 1.5)
    # DEEP_PULLBACK: sweet spot -9%
    return max(0.0, 2.0 - abs(return_pct - (-9.0)) / 1.5)


def compute_entry_score(row: CohortDayRow, entry_type: str) -> float:
    score = _day_bonus(row.day_index)
    if row.p3_selected_today:
        score += 1.0
    score += 2.0 if entry_type == ENTRY_TYPE_EARLY_HEALTHY_PULLBACK else 1.0
    score += _momentum_bonus(row.momentum_score)
    score += _pullback_bonus(entry_type, row.mark_to_market_return_pct)
    return score


def generate_entry_signal(row: CohortDayRow, params: dict) -> Optional[EntrySignal]:
    """Pure function: today's row -> at most one entry candidate.

    Never reads next_day_* prices. Never looks at any other day's row.
    """
    setup_a = params["setup_a"]
    setup_b = params["setup_b"]

    candidates = []
    if _matches_setup_a(row, setup_a):
        candidates.append(ENTRY_TYPE_EARLY_HEALTHY_PULLBACK)
    if _matches_setup_b(row, setup_b):
        candidates.append(ENTRY_TYPE_DEEP_PULLBACK)

    if not candidates:
        return None

    # A single row could in principle satisfy both setups' numeric ranges;
    # pick whichever scores higher for this specific row (deterministic).
    best_type = max(candidates, key=lambda t: compute_entry_score(row, t))
    return EntrySignal(row=row, entry_type=best_type, entry_score=compute_entry_score(row, best_type))


def generate_exit_signal(row: CohortDayRow, position, params: dict) -> Optional[ExitSignal]:
    """`position` is a portfolio.Position for the stock currently held (or
    None if not held -- caller should not invoke this without a position).
    Priority: P4 STOP > official exit day > take-profit threshold.
    """
    if row.p4_decision == "STOP_OBSERVING":
        return ExitSignal(reason=EXIT_REASON_P4_STOP, row=row)
    if row.is_official_exit_signal_day:
        return ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=row)
    take_profit = params.get("take_profit_signal_pct")
    if (
        take_profit is not None
        and row.mark_to_market_return_pct is not None
        and row.mark_to_market_return_pct >= take_profit
    ):
        return ExitSignal(reason=EXIT_REASON_TAKE_PROFIT, row=row)
    return None


def rank_candidates(candidates: List[EntrySignal]) -> List[EntrySignal]:
    """Deterministic ordering: entry_score desc, momentum_score desc, stock_id asc."""
    return sorted(
        candidates,
        key=lambda c: (
            -c.entry_score,
            -(c.row.momentum_score or 0.0),
            c.row.stock_id,
        ),
    )
