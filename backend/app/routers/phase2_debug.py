"""
Phase 2 Comparison Debug View API（2026-07-21）。

顯示層 / debug 專用：讀 `signal_shadow_snapshots`（由 `run_phase2_replay.py` 或
`SIGNALS_PIPELINE_MODE=phase2_shadow` 的 cron 寫入），**不影響**任何選股決策。
給 frontend 的 Phase 2 Comparison Debug View 頁面使用，比較 legacy vs Phase 2
在同一天的候選池差異、每檔的完整 explain trace、每日 funnel metrics。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SignalShadowSnapshot

router = APIRouter(prefix="/signals/phase2", tags=["phase2-debug"])


class ShadowSnapshotDateItem(BaseModel):
    snapshot_date: date
    pipeline_version: str
    candidate_pool_size: Optional[int]
    role_survivor_count: Optional[int]
    regime_survivor_count: Optional[int]
    generated_at: str


class ShadowSnapshotDetail(BaseModel):
    snapshot_date: date
    pipeline_version: str
    candidate_pool_size: Optional[int]
    role_survivor_count: Optional[int]
    regime_survivor_count: Optional[int]
    funnel_metrics: Dict[str, Any]
    explain_traces: Dict[str, Any]
    comparison_summary: Optional[Dict[str, Any]]
    generated_at: str


@router.get("/shadow-dates", response_model=List[ShadowSnapshotDateItem])
def list_shadow_dates(db: Session = Depends(get_db)):
    """列出所有已跑過 replay 的日期（給前端日期選單），最新在前。"""
    rows = (
        db.query(SignalShadowSnapshot)
        .order_by(desc(SignalShadowSnapshot.snapshot_date))
        .all()
    )
    return [
        ShadowSnapshotDateItem(
            snapshot_date=r.snapshot_date,
            pipeline_version=r.pipeline_version,
            candidate_pool_size=r.candidate_pool_size,
            role_survivor_count=r.role_survivor_count,
            regime_survivor_count=r.regime_survivor_count,
            generated_at=r.generated_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/shadow/{snapshot_date}", response_model=ShadowSnapshotDetail)
def get_shadow_snapshot(snapshot_date: date, db: Session = Depends(get_db)):
    """單一日期的完整 shadow 結果：funnel metrics + 每檔 explain trace +
    legacy vs phase2 比較摘要。"""
    row = (
        db.query(SignalShadowSnapshot)
        .filter(SignalShadowSnapshot.snapshot_date == snapshot_date)
        .order_by(desc(SignalShadowSnapshot.generated_at))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No shadow snapshot for {snapshot_date.isoformat()}")

    return ShadowSnapshotDetail(
        snapshot_date=row.snapshot_date,
        pipeline_version=row.pipeline_version,
        candidate_pool_size=row.candidate_pool_size,
        role_survivor_count=row.role_survivor_count,
        regime_survivor_count=row.regime_survivor_count,
        funnel_metrics=row.funnel_metrics or {},
        explain_traces=row.explain_traces or {},
        comparison_summary=row.comparison_summary,
        generated_at=row.generated_at.isoformat(),
    )
