"""Live strategy-repair experiment API.

The repair lab is intentionally separate from the normal shadow-portfolio API:
the latter answers "what does the active strategy hold?" while this endpoint
answers "what changed when the strategy was repaired from that decision date?".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    ShadowRepairCycleArchive,
    ShadowRepairDailyState,
    ShadowRepairDecisionDiff,
    ShadowRepairRevision,
    ShadowRepairRun,
)

router = APIRouter(prefix="/signals/shadow-portfolio/repair-lab", tags=["signals"])


class RepairRunResponse(BaseModel):
    id: int
    run_key: str
    title: str
    baseline_strategy_version: str
    candidate_strategy_version: str
    anchor_trade_date: date
    cycle_number: Optional[int] = None
    status: str
    initial_capital: float
    notes: Optional[str] = None
    created_at: datetime
    archived_at: Optional[datetime] = None


class RepairRevisionResponse(BaseModel):
    id: int
    revision_no: int
    effective_trade_date: date
    effective_execution_date: Optional[date] = None
    title: str
    trigger_stock_id: Optional[str] = None
    trigger_stock_name: Optional[str] = None
    reason: str
    before_strategy_label: str
    after_strategy_label: str
    before_config: Optional[dict[str, Any]] = None
    after_config: Optional[dict[str, Any]] = None
    before_commit_sha: Optional[str] = None
    after_commit_sha: Optional[str] = None
    impact_scope: str
    created_at: datetime


class RepairDiffResponse(BaseModel):
    id: int
    revision_id: Optional[int] = None
    trade_date: date
    stock_id: str
    stock_name: str
    baseline_action: Optional[str] = None
    candidate_action: Optional[str] = None
    baseline_reason: Optional[str] = None
    candidate_reason: Optional[str] = None
    baseline_entry_pattern: Optional[str] = None
    candidate_entry_pattern: Optional[str] = None
    baseline_position_units: Optional[int] = None
    candidate_position_units: Optional[int] = None
    baseline_cash: Optional[float] = None
    candidate_cash: Optional[float] = None
    baseline_topup_required: Optional[float] = None
    candidate_topup_required: Optional[float] = None
    difference_type: str
    details: Optional[dict[str, Any]] = None


class RepairStateResponse(BaseModel):
    trade_date: date
    revision_id: Optional[int] = None
    track: str
    cash: float
    invested_cost: float
    market_value: Optional[float] = None
    total_equity: float
    total_return_pct: float
    cash_topup_required: float
    position_count: int
    total_units: int
    positions: Optional[list[dict[str, Any]]] = None
    executed_orders: Optional[list[dict[str, Any]]] = None
    pending_orders: Optional[list[dict[str, Any]]] = None


class RepairArchiveResponse(BaseModel):
    cycle_number: int
    start_trade_date: date
    end_trade_date: date
    baseline_final_equity: Optional[float] = None
    candidate_final_equity: Optional[float] = None
    baseline_return_pct: Optional[float] = None
    candidate_return_pct: Optional[float] = None
    return_delta_pct: Optional[float] = None
    revision_count: int
    summary: Optional[dict[str, Any]] = None


class RepairLabResponse(BaseModel):
    runs: List[RepairRunResponse]
    selected_run: Optional[RepairRunResponse] = None
    revisions: List[RepairRevisionResponse]
    diffs: List[RepairDiffResponse]
    daily_states: List[RepairStateResponse]
    archives: List[RepairArchiveResponse]


def _run_response(row: ShadowRepairRun) -> RepairRunResponse:
    return RepairRunResponse.model_validate(row, from_attributes=True)


@router.get("", response_model=RepairLabResponse)
def get_repair_lab(
    run_key: Optional[str] = Query(default=None),
    only_differences: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> RepairLabResponse:
    runs = db.query(ShadowRepairRun).order_by(ShadowRepairRun.created_at.desc()).all()
    run_rows = [_run_response(row) for row in runs]
    selected = next((row for row in runs if row.run_key == run_key), None) if run_key else (runs[0] if runs else None)
    if selected is None:
        return RepairLabResponse(
            runs=run_rows, selected_run=None, revisions=[], diffs=[], daily_states=[], archives=[]
        )

    revisions = (
        db.query(ShadowRepairRevision)
        .filter(ShadowRepairRevision.run_id == selected.id)
        .order_by(ShadowRepairRevision.revision_no.asc())
        .all()
    )
    diff_query = db.query(ShadowRepairDecisionDiff).filter(
        ShadowRepairDecisionDiff.run_id == selected.id
    )
    if only_differences:
        diff_query = diff_query.filter(ShadowRepairDecisionDiff.difference_type != "NONE")
    diffs = diff_query.order_by(
        ShadowRepairDecisionDiff.trade_date.desc(), ShadowRepairDecisionDiff.stock_id.asc()
    ).limit(limit).all()
    states = (
        db.query(ShadowRepairDailyState)
        .filter(ShadowRepairDailyState.run_id == selected.id)
        .order_by(ShadowRepairDailyState.trade_date.desc(), ShadowRepairDailyState.track.asc())
        .limit(limit)
        .all()
    )
    archives = (
        db.query(ShadowRepairCycleArchive)
        .filter(ShadowRepairCycleArchive.run_id == selected.id)
        .order_by(ShadowRepairCycleArchive.cycle_number.desc())
        .all()
    )
    return RepairLabResponse(
        runs=run_rows,
        selected_run=_run_response(selected),
        revisions=[RepairRevisionResponse.model_validate(row, from_attributes=True) for row in revisions],
        diffs=[RepairDiffResponse.model_validate(row, from_attributes=True) for row in diffs],
        daily_states=[RepairStateResponse.model_validate(row, from_attributes=True) for row in states],
        archives=[RepairArchiveResponse.model_validate(row, from_attributes=True) for row in archives],
    )
