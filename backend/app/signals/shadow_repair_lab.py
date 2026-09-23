"""Persistence helpers for the live strategy-repair experiment.

The helpers do not decide trades.  They persist a point-in-time comparison
produced by a baseline/candidate runner, so a later strategy change cannot
rewrite the evidence that led to it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import (
    ShadowPortfolioDailySnapshot,
    ShadowRepairCycleArchive,
    ShadowRepairDailyState,
    ShadowRepairDecisionDiff,
    ShadowRepairRevision,
    ShadowRepairRun,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPosition,
    ShadowPositionLot,
)


def create_repair_run(
    db: Session,
    *,
    run_key: str,
    title: str,
    baseline_strategy_version: str,
    candidate_strategy_version: str,
    anchor_trade_date: date,
    initial_capital: float,
    cycle_number: Optional[int] = None,
    notes: Optional[str] = None,
) -> ShadowRepairRun:
    """Create an immutable experiment anchor; never infer it from today."""
    existing = db.query(ShadowRepairRun).filter(ShadowRepairRun.run_key == run_key).first()
    if existing is not None:
        return existing
    row = ShadowRepairRun(
        run_key=run_key,
        title=title,
        baseline_strategy_version=baseline_strategy_version,
        candidate_strategy_version=candidate_strategy_version,
        anchor_trade_date=anchor_trade_date,
        cycle_number=cycle_number,
        initial_capital=initial_capital,
        notes=notes,
    )
    db.add(row)
    db.flush()
    return row


def add_repair_revision(
    db: Session,
    *,
    run_id: int,
    effective_trade_date: date,
    title: str,
    reason: str,
    before_strategy_label: str,
    after_strategy_label: str,
    effective_execution_date: Optional[date] = None,
    trigger_stock_id: Optional[str] = None,
    trigger_stock_name: Optional[str] = None,
    before_config: Optional[dict[str, Any]] = None,
    after_config: Optional[dict[str, Any]] = None,
    before_commit_sha: Optional[str] = None,
    after_commit_sha: Optional[str] = None,
    impact_scope: str = "WHOLE_STRATEGY",
) -> ShadowRepairRevision:
    """Append a revision and snapshot both complete strategy definitions."""
    revision_no = (
        db.query(ShadowRepairRevision)
        .filter(ShadowRepairRevision.run_id == run_id)
        .count()
        + 1
    )
    row = ShadowRepairRevision(
        run_id=run_id,
        revision_no=revision_no,
        effective_trade_date=effective_trade_date,
        effective_execution_date=effective_execution_date,
        title=title,
        trigger_stock_id=trigger_stock_id,
        trigger_stock_name=trigger_stock_name,
        reason=reason,
        before_strategy_label=before_strategy_label,
        after_strategy_label=after_strategy_label,
        before_config=before_config,
        after_config=after_config,
        before_commit_sha=before_commit_sha,
        after_commit_sha=after_commit_sha,
        impact_scope=impact_scope,
    )
    db.add(row)
    db.flush()
    return row


def record_decision_diff(
    db: Session,
    *,
    run_id: int,
    trade_date: date,
    stock_id: str,
    stock_name: str,
    revision_id: Optional[int],
    baseline_action: Optional[str],
    candidate_action: Optional[str],
    baseline_reason: Optional[str] = None,
    candidate_reason: Optional[str] = None,
    baseline_entry_pattern: Optional[str] = None,
    candidate_entry_pattern: Optional[str] = None,
    baseline_position_units: Optional[int] = None,
    candidate_position_units: Optional[int] = None,
    baseline_cash: Optional[float] = None,
    candidate_cash: Optional[float] = None,
    baseline_topup_required: Optional[float] = None,
    candidate_topup_required: Optional[float] = None,
    details: Optional[dict[str, Any]] = None,
) -> ShadowRepairDecisionDiff:
    """Upsert one stock/day diff so the daily runner stays idempotent."""
    action_changed = baseline_action != candidate_action
    position_changed = baseline_position_units != candidate_position_units
    cash_changed = baseline_cash is not None and candidate_cash is not None and abs(baseline_cash - candidate_cash) > 1e-6
    topup_changed = baseline_topup_required != candidate_topup_required
    difference_type = "ACTION" if action_changed else "POSITION" if position_changed else "TOPUP" if topup_changed else "CASH" if cash_changed else "NONE"
    row = (
        db.query(ShadowRepairDecisionDiff)
        .filter(
            ShadowRepairDecisionDiff.run_id == run_id,
            ShadowRepairDecisionDiff.trade_date == trade_date,
            ShadowRepairDecisionDiff.stock_id == stock_id,
        )
        .first()
    )
    if row is None:
        row = ShadowRepairDecisionDiff(
            run_id=run_id, trade_date=trade_date, stock_id=stock_id, stock_name=stock_name
        )
        db.add(row)
    row.revision_id = revision_id
    row.baseline_action = baseline_action
    row.candidate_action = candidate_action
    row.baseline_reason = baseline_reason
    row.candidate_reason = candidate_reason
    row.baseline_entry_pattern = baseline_entry_pattern
    row.candidate_entry_pattern = candidate_entry_pattern
    row.baseline_position_units = baseline_position_units
    row.candidate_position_units = candidate_position_units
    row.baseline_cash = baseline_cash
    row.candidate_cash = candidate_cash
    row.baseline_topup_required = baseline_topup_required
    row.candidate_topup_required = candidate_topup_required
    row.difference_type = difference_type
    row.details = details
    return row


def _positions_payload(db: Session, strategy_version: str) -> list[dict[str, Any]]:
    positions = (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .order_by(ShadowVirtualPosition.stock_id.asc())
        .all()
    )
    payload = []
    for position in positions:
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == position.id).all()
        payload.append({
            "stock_id": position.stock_id,
            "stock_name": position.stock_name,
            "units": len(lots),
            "lots": [
                {"entry_type": lot.entry_type, "entry_price": lot.entry_price, "allocation": lot.allocation}
                for lot in lots
            ],
        })
    return payload


def _orders_payload(db: Session, strategy_version: str, trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Snapshot orders relevant to this day's state, without mutating the source queue."""
    rows = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status.in_(("PENDING", "EXECUTED")),
        )
        .order_by(ShadowStrategyOrder.id.asc())
        .all()
    )
    executed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for order in rows:
        item = {
            "id": order.id,
            "stock_id": order.stock_id,
            "stock_name": order.stock_name,
            "action": order.action,
            "signal_date": order.signal_date.isoformat() if order.signal_date else None,
            "scheduled_execution_date": order.scheduled_execution_date.isoformat() if order.scheduled_execution_date else None,
            "status": order.status,
            "reason": order.reason,
            "entry_pattern": order.entry_pattern,
            "units": order.units,
            "planned_amount": order.planned_amount,
            "cash_topup_required": order.cash_topup_required or 0.0,
            "execution_price": order.execution_price,
            "executed_at": order.executed_at.isoformat() if order.executed_at else None,
        }
        # Historical replays may be executed days after the market date, so
        # scheduled_execution_date is the immutable date for this snapshot.
        if order.status == "EXECUTED" and order.scheduled_execution_date == trade_date:
            executed.append(item)
        elif order.signal_date <= trade_date < order.scheduled_execution_date:
            item["status"] = "PENDING_AT_SNAPSHOT"
            pending.append(item)
    return executed, pending


def _decision_topup_for_stock(db: Session, strategy_version: str, stock_id: str, trade_date: date) -> float:
    """Return only this stock's planned top-up for its decision date."""
    return sum(
        max(float(order.cash_topup_required or 0.0), 0.0)
        for order in db.query(ShadowStrategyOrder).filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.stock_id == stock_id,
            ShadowStrategyOrder.signal_date == trade_date,
            ShadowStrategyOrder.status.in_(("PENDING", "EXECUTED")),
        ).all()
    )


def record_daily_state_from_strategy(
    db: Session,
    *,
    run_id: int,
    trade_date: date,
    track: str,
    strategy_version: str,
    revision_id: Optional[int] = None,
) -> Optional[ShadowRepairDailyState]:
    """Copy a completed strategy snapshot into the immutable repair timeline."""
    snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
            ShadowPortfolioDailySnapshot.trade_date == trade_date,
        )
        .first()
    )
    if snapshot is None:
        return None
    row = (
        db.query(ShadowRepairDailyState)
        .filter(
            ShadowRepairDailyState.run_id == run_id,
            ShadowRepairDailyState.trade_date == trade_date,
            ShadowRepairDailyState.track == track,
        )
        .first()
    )
    if row is None:
        row = ShadowRepairDailyState(
            run_id=run_id, trade_date=trade_date, track=track, cash=snapshot.cash,
            invested_cost=snapshot.invested_cost, total_equity=snapshot.total_equity,
            total_return_pct=snapshot.total_return_pct,
        )
        db.add(row)
    row.revision_id = revision_id
    row.cash = snapshot.cash
    row.invested_cost = snapshot.invested_cost
    row.market_value = snapshot.market_value
    row.total_equity = snapshot.total_equity
    row.total_return_pct = snapshot.total_return_pct
    row.cash_topup_required = snapshot.cash_topup_required or 0.0
    row.position_count = snapshot.position_count
    row.total_units = snapshot.total_units
    row.positions = _positions_payload(db, strategy_version)
    row.executed_orders, row.pending_orders = _orders_payload(db, strategy_version, trade_date)
    return row


def record_daily_diffs_for_run(
    db: Session,
    *,
    run: ShadowRepairRun,
    trade_date: date,
) -> int:
    """Compare every stock evaluated by the two strategy tracks on one day."""
    def _load(version: str) -> dict[str, ShadowStrategyDailyDecision]:
        return {
            row.stock_id: row
            for row in db.query(ShadowStrategyDailyDecision).filter(
                ShadowStrategyDailyDecision.strategy_version == version,
                ShadowStrategyDailyDecision.trade_date == trade_date,
            ).all()
        }

    baseline = _load(run.baseline_strategy_version)
    candidate = _load(run.candidate_strategy_version)
    if not baseline and not candidate:
        return 0
    revision = (
        db.query(ShadowRepairRevision)
        .filter(
            ShadowRepairRevision.run_id == run.id,
            ShadowRepairRevision.effective_trade_date <= trade_date,
        )
        .order_by(ShadowRepairRevision.revision_no.desc())
        .first()
    )
    baseline_snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == run.baseline_strategy_version,
            ShadowPortfolioDailySnapshot.trade_date == trade_date,
        ).first()
    )
    candidate_snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == run.candidate_strategy_version,
            ShadowPortfolioDailySnapshot.trade_date == trade_date,
        ).first()
    )
    count = 0
    for stock_id in sorted(set(baseline) | set(candidate)):
        old = baseline.get(stock_id)
        new = candidate.get(stock_id)
        stock_name = (new or old).stock_name
        baseline_topup = _decision_topup_for_stock(db, run.baseline_strategy_version, stock_id, trade_date)
        candidate_topup = _decision_topup_for_stock(db, run.candidate_strategy_version, stock_id, trade_date)
        action_changed = (old.action if old else None) != (new.action if new else None)
        position_changed = (old.position_units if old else None) != (new.position_units if new else None)
        cash_relevant = action_changed or position_changed or abs(baseline_topup - candidate_topup) > 1e-6
        record_decision_diff(
            db,
            run_id=run.id,
            revision_id=revision.id if revision else None,
            trade_date=trade_date,
            stock_id=stock_id,
            stock_name=stock_name,
            baseline_action=old.action if old else None,
            candidate_action=new.action if new else None,
            baseline_reason=old.action_reason if old else None,
            candidate_reason=new.action_reason if new else None,
            baseline_entry_pattern=old.entry_pattern if old else None,
            candidate_entry_pattern=new.entry_pattern if new else None,
            baseline_position_units=old.position_units if old else None,
            candidate_position_units=new.position_units if new else None,
            baseline_cash=baseline_snapshot.cash if baseline_snapshot and cash_relevant else None,
            candidate_cash=candidate_snapshot.cash if candidate_snapshot and cash_relevant else None,
            baseline_topup_required=baseline_topup if cash_relevant else None,
            candidate_topup_required=candidate_topup if cash_relevant else None,
        )
        count += 1
    return count


def archive_repair_cycle(
    db: Session,
    *,
    run_id: int,
    cycle_number: int,
    start_trade_date: date,
    end_trade_date: date,
    baseline_final_equity: Optional[float],
    candidate_final_equity: Optional[float],
    baseline_return_pct: Optional[float],
    candidate_return_pct: Optional[float],
    revision_count: int,
    summary: Optional[dict[str, Any]] = None,
) -> ShadowRepairCycleArchive:
    row = (
        db.query(ShadowRepairCycleArchive)
        .filter(
            ShadowRepairCycleArchive.run_id == run_id,
            ShadowRepairCycleArchive.cycle_number == cycle_number,
        )
        .first()
    )
    if row is None:
        row = ShadowRepairCycleArchive(run_id=run_id, cycle_number=cycle_number)
        db.add(row)
    row.start_trade_date = start_trade_date
    row.end_trade_date = end_trade_date
    row.baseline_final_equity = baseline_final_equity
    row.candidate_final_equity = candidate_final_equity
    row.baseline_return_pct = baseline_return_pct
    row.candidate_return_pct = candidate_return_pct
    row.return_delta_pct = (
        candidate_return_pct - baseline_return_pct
        if candidate_return_pct is not None and baseline_return_pct is not None
        else None
    )
    row.revision_count = revision_count
    row.summary = summary
    return row
