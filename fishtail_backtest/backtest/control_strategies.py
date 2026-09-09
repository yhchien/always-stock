"""Control groups for comparison against Strategy v1 (§25).

Control 1: buy on P3's *first* selection only, hold until P4 confirms STOP
           (no take-profit override, no other exit rule).
Control 2: buy/add-on every time P3 reselects the stock (not just the
           first time), same minimal exit rule as Control 1.
Control 3: purely P4-lifecycle driven -- CONTINUE/CAUTION always hold,
           STOP_OBSERVING always sell; entry timing mirrors Control 1
           (first P3 pick) since the spec does not separately define an
           entry rule for Control 3. NOTE: given this dataset's official
           `is_official_exit_signal_day` always coincides with the day the
           STOP_OBSERVING decision that closed the cohort was made, Control
           1 and Control 3 are numerically identical here -- documented
           explicitly in the final report rather than silently duplicated.
"""
from __future__ import annotations

from typing import List, Optional

from .loader import CohortDayRow
from .signals import EntrySignal, ExitSignal, EXIT_REASON_OFFICIAL_EXIT, EXIT_REASON_P4_STOP


def control1_entry(row: CohortDayRow, params: dict) -> Optional[EntrySignal]:
    if row.day_index == 1 and row.p3_selected_today and row.hit_count_so_far == 1:
        return EntrySignal(row=row, entry_type="CONTROL1_FIRST_PICK", entry_score=1.0)
    return None


def control2_entry(row: CohortDayRow, params: dict) -> Optional[EntrySignal]:
    if row.p3_selected_today:
        return EntrySignal(row=row, entry_type="CONTROL2_ANY_RESELECT", entry_score=1.0)
    return None


def control_exit_p4_only(row: CohortDayRow, position, params: dict) -> Optional[ExitSignal]:
    if row.p4_decision == "STOP_OBSERVING":
        return ExitSignal(reason=EXIT_REASON_P4_STOP, row=row)
    if row.is_official_exit_signal_day:
        return ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=row)
    return None


def control_rank(candidates: List[EntrySignal]) -> List[EntrySignal]:
    return sorted(candidates, key=lambda c: (-(c.row.momentum_score or 0.0), c.row.stock_id))
