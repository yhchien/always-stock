"""台股行事曆預測 API。

The prediction job writes one row per signal date.  The UI deliberately asks
for only ten rows, while this endpoint allows up to fifty for history and
auditing.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MarketCalendarPrediction

router = APIRouter(prefix="/market-calendar", tags=["market-calendar"])


class MarketCalendarPredictionItem(BaseModel):
    signal_date: date
    target_date: Optional[date] = None
    predicted_binary: str
    predicted_direction: Optional[str] = None
    predicted_return: Optional[float] = None
    predicted_return_low: Optional[float] = None
    predicted_return_high: Optional[float] = None
    confidence: Optional[str] = None
    data_completeness: Optional[float] = None
    factor_snapshot: Optional[Dict[str, Any]] = None
    explanation: Optional[Dict[str, Any]] = None
    actual_binary: Optional[str] = None
    actual_direction: Optional[str] = None
    actual_broad: Optional[str] = None
    actual_return: Optional[float] = None
    result_status: str
    model_version: str
    generated_at: datetime
    updated_at: datetime


class MarketCalendarHistoryResponse(BaseModel):
    items: List[MarketCalendarPredictionItem]
    limit: int
    available: int
    evaluated: int
    correct: int
    accuracy: Optional[float] = None


def _serialize(row: MarketCalendarPrediction) -> MarketCalendarPredictionItem:
    return MarketCalendarPredictionItem(
        signal_date=row.signal_date,
        target_date=row.target_date,
        predicted_binary=row.predicted_binary,
        predicted_direction=row.predicted_direction,
        predicted_return=row.predicted_return,
        predicted_return_low=row.predicted_return_low,
        predicted_return_high=row.predicted_return_high,
        confidence=row.confidence,
        data_completeness=row.data_completeness,
        factor_snapshot=row.factor_snapshot,
        explanation=row.explanation,
        actual_binary=row.actual_binary,
        actual_direction=row.actual_direction,
        actual_broad=row.actual_broad,
        actual_return=row.actual_return,
        result_status=row.result_status,
        model_version=row.model_version,
        generated_at=row.generated_at,
        updated_at=row.updated_at,
    )


@router.get("/history", response_model=MarketCalendarHistoryResponse)
def get_market_calendar_history(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> MarketCalendarHistoryResponse:
    """Return recent predictions; at most fifty rows can be requested."""
    rows = (
        db.query(MarketCalendarPrediction)
        .order_by(MarketCalendarPrediction.signal_date.desc())
        .limit(50)
        .all()
    )
    evaluated = [row for row in rows if row.actual_binary is not None]
    correct = [row for row in evaluated if row.actual_binary == row.predicted_binary]
    selected = rows[:limit]
    return MarketCalendarHistoryResponse(
        items=[_serialize(row) for row in selected],
        limit=limit,
        available=len(rows),
        evaluated=len(evaluated),
        correct=len(correct),
        accuracy=(len(correct) / len(evaluated)) if evaluated else None,
    )


@router.get("/latest", response_model=MarketCalendarPredictionItem)
def get_latest_market_calendar_prediction(
    db: Session = Depends(get_db),
) -> MarketCalendarPredictionItem:
    """Return the latest stored forecast."""
    row = (
        db.query(MarketCalendarPrediction)
        .order_by(MarketCalendarPrediction.signal_date.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="目前尚無台股行事曆預測")
    return _serialize(row)
