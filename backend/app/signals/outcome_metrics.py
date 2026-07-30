"""P6 post-decision outcome analytics.

This module is intentionally read-only with respect to production decisions.  It
may read signal snapshots, prices, and P4 lifecycle rows, and it may write only
P6 materialized analytics/review tables.  Candidate selection, global selection,
P4 state transitions, prompts, and replay code must never import this module.
"""

from __future__ import annotations

import bisect
import csv
import io
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Iterator, Optional, Sequence

from sqlalchemy import case, func
from sqlalchemy.orm import Query, Session

from app.models import (
    DailyPrice,
    SignalObservation,
    SignalObservationOutcomeMetric,
    SignalObservationReview,
    SignalOutcomeMetric,
    SignalOutcomeReviewQueue,
    SignalSnapshot,
)

OUTCOME_DEFINITION_VERSION = "day10_v1"
OUTCOME_HORIZON = "DAY10"
ENTRY_PRICE_DEFINITION = "signal_date_close"
EXIT_PRICE_DEFINITION = "tenth_subsequent_market_trade_date_close"
OBSERVATION_DEFINITION_VERSION = "p6_observation_outcome_v1"
PREMATURE_STOP_DEFINITION_VERSION = "stop_day10_plus10_v1"

LABEL_WINNER = "WINNER"
LABEL_NEUTRAL = "NEUTRAL"
LABEL_BIG_LOSER = "BIG_LOSER"
LABEL_IMMATURE = "IMMATURE"
LABEL_MISSING = "OUTCOME_DATA_MISSING"
MATURE_LABELS = (LABEL_WINNER, LABEL_NEUTRAL, LABEL_BIG_LOSER)

DECISION_RECOMMEND = "RECOMMEND"
DECISION_NOT_SELECTED = "NOT_SELECTED"

EXTERNAL_STOP_REASONS = {
    "BUSINESS_MISMATCH",
    "THEME_MISMATCH",
    "FALSE_SUPPLY_CHAIN_LINK",
    "MATERIAL_NEGATIVE_EVENT",
    "DATA_CONTRADICTION",
}


def classify_day10_return(value: float) -> str:
    """Apply the single P6 boundary contract, including exact ±10% edges."""

    if value >= 10.0:
        return LABEL_WINNER
    if value <= -10.0:
        return LABEL_BIG_LOSER
    return LABEL_NEUTRAL


def _safe_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stock_id(item: dict[str, Any]) -> str:
    return str(item.get("stock") or item.get("stock_id") or "").strip()


def _selection_complete(snapshot: SignalSnapshot) -> bool:
    summary = snapshot.summary or {}
    selection = summary.get("selection_summary") or {}
    processing = summary.get("processing_summary") or {}
    explicit = selection.get("selection_complete")
    if explicit is None:
        explicit = processing.get("selection_complete")
    # Historical pre-P3 rows can still provide RECOMMEND outcomes, but they are
    # not global-eligible comparison samples and therefore never enter recall.
    return explicit is True


def _extract_snapshot_items(snapshot: SignalSnapshot) -> list[dict[str, Any]]:
    summary = snapshot.summary or {}
    processing = summary.get("processing_summary") or {}
    selection_summary = summary.get("selection_summary") or {}
    complete = _selection_complete(snapshot)
    prompt_family = (
        processing.get("prompt_family_version")
        or ("legacy_split" if not complete else None)
        or "legacy_split"
    )
    common_versions = {
        "research_prompt_version": processing.get("research_prompt_version"),
        "assessment_prompt_version": processing.get("assessment_prompt_version"),
        "global_selector_version": processing.get("global_selector_version"),
        "reason_prompt_version": processing.get("reason_prompt_version"),
        "tracking_prompt_version": processing.get("tracking_prompt_version"),
        "tracking_state_machine_version": processing.get(
            "tracking_state_machine_version"
        ),
    }
    extracted: list[dict[str, Any]] = []
    buckets = (
        (DECISION_RECOMMEND, snapshot.watchlist or []),
        (DECISION_NOT_SELECTED, summary.get("not_selected") or []),
    )
    for decision, items in buckets:
        if decision == DECISION_NOT_SELECTED and not complete:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                decision == DECISION_RECOMMEND
                and not complete
                and str(
                    item.get("selection_status")
                    or item.get("decision")
                    or ""
                ).upper()
                != DECISION_RECOMMEND
            ):
                # A pre-P3 WATCH row was never a formal recommendation.
                continue
            sid = _stock_id(item)
            if not sid:
                continue
            metrics = item.get("signal_metrics") or {}
            theme = item.get("theme") or {}
            extracted.append(
                {
                    "signal_date": snapshot.snapshot_date,
                    "stock_id": sid,
                    "stock_name": str(item.get("name") or sid),
                    "asset_type": str(
                        item.get("asset_type") or "COMMON_STOCK"
                    ),
                    "p3_decision": decision,
                    "global_eligible": complete,
                    "recommendation_rank": _safe_int(
                        item.get("recommendation_rank")
                    ),
                    "backend_priority_rank": _safe_int(
                        item.get("backend_priority_rank")
                    ),
                    "rank_override": bool(item.get("rank_override")),
                    "rank_override_reason": item.get(
                        "rank_override_reason"
                    ),
                    "selection_reason_code": item.get("selection_reason_code"),
                    "selection_reason": item.get("selection_reason"),
                    "theme_cluster": (
                        item.get("theme_cluster")
                        or theme.get("main_theme")
                        or None
                    ),
                    "selection_version": (
                        item.get("selection_version")
                        or selection_summary.get("selection_version")
                        or processing.get("global_selector_version")
                    ),
                    "prompt_family_version": prompt_family,
                    **common_versions,
                    "momentum_score_version": metrics.get(
                        "momentum_score_version"
                    )
                    or processing.get("momentum_score_version"),
                    "metadata_json": {
                        "industry": item.get("industry"),
                        "sub_industry": item.get("sub_industry"),
                        "recommendation_thesis": item.get(
                            "recommendation_thesis"
                        ),
                        "relative_advantage": item.get("relative_advantage"),
                        "recommendation_basis": item.get(
                            "recommendation_basis"
                        )
                        or [],
                        "rank_override": bool(item.get("rank_override")),
                        "rank_override_reason": item.get(
                            "rank_override_reason"
                        ),
                        "overlap_with": item.get("overlap_with") or [],
                        "overlap_reason": item.get("overlap_reason"),
                        "reason_sections": {
                            key: item.get(key) or []
                            for key in (
                                "theme_reason",
                                "capital_reason",
                                "chip_reason",
                                "margin_reason",
                                "technical_reason",
                            )
                        },
                        "prompt_versions": common_versions,
                        "selection_complete": complete,
                    },
                }
            )
    return extracted


def _market_trade_dates(db: Session) -> list[date]:
    return [
        row[0]
        for row in (
            db.query(DailyPrice.trade_date)
            .filter(DailyPrice.close_price.isnot(None))
            .distinct()
            .order_by(DailyPrice.trade_date.asc())
            .all()
        )
    ]


def _nth_subsequent_trade_date(
    trade_dates: Sequence[date],
    anchor: date,
    n: int = 10,
) -> Optional[date]:
    index = bisect.bisect_right(trade_dates, anchor) + n - 1
    return trade_dates[index] if index < len(trade_dates) else None


def _trading_days_between(
    trade_dates: Sequence[date],
    start: date,
    end: date,
) -> int:
    return max(
        0,
        bisect.bisect_right(trade_dates, end)
        - bisect.bisect_right(trade_dates, start),
    )


def refresh_outcome_cache(
    db: Session,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    commit: bool = True,
) -> dict[str, int]:
    """Idempotently materialize snapshot outcomes without mutating snapshots."""

    snapshot_query = db.query(SignalSnapshot)
    if start_date is not None:
        snapshot_query = snapshot_query.filter(
            SignalSnapshot.snapshot_date >= start_date
        )
    if end_date is not None:
        snapshot_query = snapshot_query.filter(
            SignalSnapshot.snapshot_date <= end_date
        )
    snapshots = snapshot_query.order_by(SignalSnapshot.snapshot_date.asc()).all()
    descriptors = [
        item for snapshot in snapshots for item in _extract_snapshot_items(snapshot)
    ]

    if snapshots:
        delete_query = db.query(SignalOutcomeMetric).filter(
            SignalOutcomeMetric.outcome_definition_version
            == OUTCOME_DEFINITION_VERSION
        )
        actual_start = start_date or snapshots[0].snapshot_date
        actual_end = end_date or snapshots[-1].snapshot_date
        delete_query.filter(
            SignalOutcomeMetric.signal_date >= actual_start,
            SignalOutcomeMetric.signal_date <= actual_end,
        ).delete(synchronize_session=False)

    trade_dates = _market_trade_dates(db)
    exit_by_signal_date = {
        value: _nth_subsequent_trade_date(trade_dates, value)
        for value in {item["signal_date"] for item in descriptors}
    }
    wanted_dates = {
        value
        for item in descriptors
        for value in (
            item["signal_date"],
            exit_by_signal_date[item["signal_date"]],
        )
        if value is not None
    }
    stock_ids = {item["stock_id"] for item in descriptors}
    price_map: dict[tuple[str, date], float] = {}
    if wanted_dates and stock_ids:
        for sid, trade_date, close in (
            db.query(
                DailyPrice.stock_id,
                DailyPrice.trade_date,
                DailyPrice.close_price,
            )
            .filter(
                DailyPrice.stock_id.in_(stock_ids),
                DailyPrice.trade_date.in_(wanted_dates),
                DailyPrice.close_price.isnot(None),
            )
            .all()
        ):
            price_map[(sid, trade_date)] = float(close)

    observations = (
        db.query(SignalObservation)
        .filter(SignalObservation.stock_id.in_(stock_ids))
        .order_by(
            SignalObservation.stock_id.asc(),
            SignalObservation.started_signal_date.asc(),
        )
        .all()
        if stock_ids
        else []
    )
    observation_by_stock: dict[str, list[SignalObservation]] = defaultdict(list)
    for observation in observations:
        observation_by_stock[observation.stock_id].append(observation)

    counts = {
        "calculated": 0,
        "immature": 0,
        "missing": 0,
        "failed": 0,
    }
    for item in descriptors:
        signal_date = item["signal_date"]
        exit_date = exit_by_signal_date[signal_date]
        entry_price = price_map.get((item["stock_id"], signal_date))
        exit_price = (
            price_map.get((item["stock_id"], exit_date))
            if exit_date is not None
            else None
        )
        return_pct: Optional[float] = None
        if exit_date is None:
            label = LABEL_IMMATURE
            counts["immature"] += 1
        elif entry_price in (None, 0) or exit_price is None:
            label = LABEL_MISSING
            counts["missing"] += 1
        else:
            return_pct = (exit_price - entry_price) / entry_price * 100.0
            label = classify_day10_return(return_pct)
            counts["calculated"] += 1

        matching_observation = next(
            (
                observation
                for observation in observation_by_stock.get(
                    item["stock_id"], []
                )
                if observation.started_signal_date == signal_date
            ),
            None,
        )
        if matching_observation is None:
            stock_episodes = observation_by_stock.get(item["stock_id"], [])
            matching_observation = stock_episodes[-1] if stock_episodes else None
        stop_date = None
        if matching_observation is not None:
            stop_date = (
                matching_observation.stopped_at.date()
                if matching_observation.stopped_at is not None
                else (
                    matching_observation.last_review_date
                    if matching_observation.status == "STOPPED"
                    else None
                )
            )
        db.add(
            SignalOutcomeMetric(
                **item,
                observation_status=(
                    matching_observation.status
                    if matching_observation is not None
                    else None
                ),
                stop_date=stop_date,
                stop_reason=(
                    matching_observation.stop_reason
                    if matching_observation is not None
                    else None
                ),
                entry_trade_date=signal_date if entry_price is not None else None,
                entry_price=entry_price,
                exit_trade_date=exit_date,
                exit_price=exit_price,
                outcome_return_pct=return_pct,
                outcome_label=label,
                matured_at=exit_date if label in MATURE_LABELS else None,
                outcome_horizon=OUTCOME_HORIZON,
                outcome_definition_version=OUTCOME_DEFINITION_VERSION,
                entry_price_definition=ENTRY_PRICE_DEFINITION,
                exit_price_definition=EXIT_PRICE_DEFINITION,
                calculated_at=datetime.utcnow(),
            )
        )
    db.flush()
    refresh_observation_outcome_cache(db, trade_dates=trade_dates, commit=False)
    _sync_review_queue(db)
    if commit:
        db.commit()
    return counts


def refresh_incremental_outcomes(
    db: Session,
    *,
    commit: bool = True,
) -> dict[str, int]:
    """Refresh only unresolved/new signal dates after the latest price load."""

    pending_start = (
        db.query(func.min(SignalOutcomeMetric.signal_date))
        .filter(
            SignalOutcomeMetric.outcome_definition_version
            == OUTCOME_DEFINITION_VERSION,
            SignalOutcomeMetric.outcome_label.in_(
                (LABEL_IMMATURE, LABEL_MISSING)
            ),
        )
        .scalar()
    )
    cache_max = (
        db.query(func.max(SignalOutcomeMetric.signal_date))
        .filter(
            SignalOutcomeMetric.outcome_definition_version
            == OUTCOME_DEFINITION_VERSION
        )
        .scalar()
    )
    new_start_query = db.query(func.min(SignalSnapshot.snapshot_date))
    if cache_max is not None:
        new_start_query = new_start_query.filter(
            SignalSnapshot.snapshot_date > cache_max
        )
    new_start = new_start_query.scalar()
    starts = [value for value in (pending_start, new_start) if value is not None]
    if not starts:
        refresh_observation_outcome_cache(db, commit=False)
        if commit:
            db.commit()
        return {"calculated": 0, "immature": 0, "missing": 0, "failed": 0}
    return refresh_outcome_cache(
        db,
        start_date=min(starts),
        commit=commit,
    )


def _stop_category(reason_code: Optional[str]) -> Optional[str]:
    if not reason_code:
        return None
    if reason_code in EXTERNAL_STOP_REASONS:
        return "EXTERNAL_THESIS_STOP"
    if "SUSTAINED" in reason_code:
        return "SUSTAINED_STOP"
    return "IMMEDIATE_STOP"


def refresh_observation_outcome_cache(
    db: Session,
    *,
    trade_dates: Optional[Sequence[date]] = None,
    commit: bool = True,
) -> int:
    observations = (
        db.query(SignalObservation)
        .order_by(
            SignalObservation.stock_id.asc(),
            SignalObservation.started_signal_date.asc(),
        )
        .all()
    )
    db.query(SignalObservationOutcomeMetric).filter(
        SignalObservationOutcomeMetric.definition_version
        == OBSERVATION_DEFINITION_VERSION
    ).delete(synchronize_session=False)
    if not observations:
        if commit:
            db.commit()
        return 0
    calendar = list(trade_dates or _market_trade_dates(db))
    stock_ids = {row.stock_id for row in observations}
    price_rows = (
        db.query(
            DailyPrice.stock_id,
            DailyPrice.trade_date,
            DailyPrice.close_price,
        )
        .filter(
            DailyPrice.stock_id.in_(stock_ids),
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.stock_id.asc(), DailyPrice.trade_date.asc())
        .all()
    )
    prices: dict[str, list[tuple[date, float]]] = defaultdict(list)
    price_lookup: dict[tuple[str, date], float] = {}
    for sid, trade_date, close in price_rows:
        value = float(close)
        prices[sid].append((trade_date, value))
        price_lookup[(sid, trade_date)] = value

    by_stock: dict[str, list[SignalObservation]] = defaultdict(list)
    for observation in observations:
        by_stock[observation.stock_id].append(observation)

    for observation in observations:
        stop_date = (
            observation.stopped_at.date()
            if observation.stopped_at is not None
            else (
                observation.last_review_date
                if observation.status == "STOPPED"
                else None
            )
        )
        post_stop_return = None
        premature = False
        if stop_date is not None:
            exit_date = _nth_subsequent_trade_date(calendar, stop_date)
            stop_price = price_lookup.get((observation.stock_id, stop_date))
            exit_price = (
                price_lookup.get((observation.stock_id, exit_date))
                if exit_date is not None
                else None
            )
            if stop_price not in (None, 0) and exit_price is not None:
                post_stop_return = (
                    (exit_price - stop_price) / stop_price * 100.0
                )
                premature = post_stop_return >= 10.0

        entry_price = price_lookup.get(
            (observation.stock_id, observation.started_signal_date)
        )
        hit_minus10_date = None
        if entry_price not in (None, 0):
            hit_minus10_date = next(
                (
                    trade_date
                    for trade_date, close in prices[observation.stock_id]
                    if trade_date > observation.started_signal_date
                    and (close - entry_price) / entry_price * 100.0 <= -10.0
                ),
                None,
            )
        stopped_before = (
            stop_date < hit_minus10_date
            if stop_date is not None and hit_minus10_date is not None
            else (False if hit_minus10_date is not None else None)
        )
        next_episode = next(
            (
                candidate
                for candidate in by_stock[observation.stock_id]
                if candidate.started_signal_date
                > observation.started_signal_date
            ),
            None,
        )
        db.add(
            SignalObservationOutcomeMetric(
                observation_id=observation.id,
                episode_id=observation.episode_id,
                stock_id=observation.stock_id,
                started_signal_date=observation.started_signal_date,
                stop_date=stop_date,
                stop_reason_code=observation.stop_reason_code,
                stop_category=_stop_category(observation.stop_reason_code),
                trading_days_to_stop=(
                    _trading_days_between(
                        calendar,
                        observation.started_signal_date,
                        stop_date,
                    )
                    if stop_date is not None
                    else None
                ),
                post_stop_day10_return_pct=post_stop_return,
                premature_stop_candidate=premature,
                hit_minus10_date=hit_minus10_date,
                stopped_before_minus10=stopped_before,
                trading_days_before_minus10=(
                    _trading_days_between(calendar, stop_date, hit_minus10_date)
                    if stopped_before
                    and stop_date is not None
                    and hit_minus10_date is not None
                    else None
                ),
                next_episode_id=(
                    next_episode.episode_id if next_episode is not None else None
                ),
                trading_days_to_rerecommend=(
                    _trading_days_between(
                        calendar,
                        stop_date or observation.started_signal_date,
                        next_episode.started_signal_date,
                    )
                    if next_episode is not None
                    else None
                ),
                definition_version=OBSERVATION_DEFINITION_VERSION,
                premature_stop_definition_version=(
                    PREMATURE_STOP_DEFINITION_VERSION
                ),
                calculated_at=datetime.utcnow(),
            )
        )
    db.flush()
    if commit:
        db.commit()
    return len(observations)


def _sync_review_queue(db: Session) -> None:
    existing = set(
        db.query(
            SignalOutcomeReviewQueue.source_type,
            SignalOutcomeReviewQueue.source_key,
            SignalOutcomeReviewQueue.category,
        ).all()
    )
    desired: list[dict[str, Any]] = []
    for metric in db.query(SignalOutcomeMetric).yield_per(1000):
        categories: list[str] = []
        if (
            metric.p3_decision == DECISION_NOT_SELECTED
            and metric.outcome_label == LABEL_WINNER
        ):
            categories.append("NOT_SELECTED_WINNER")
            if (
                metric.backend_priority_rank is not None
                and metric.backend_priority_rank <= 10
            ):
                categories.append("HIGH_RANK_NOT_SELECTED_WINNER")
        if (
            metric.p3_decision == DECISION_RECOMMEND
            and metric.outcome_label == LABEL_BIG_LOSER
        ):
            categories.append("RECOMMEND_BIG_LOSER")
            if bool((metric.metadata_json or {}).get("rank_override")):
                categories.append("RANK_OVERRIDE_BIG_LOSER")
        if metric.outcome_label == LABEL_MISSING:
            categories.append("OUTCOME_DATA_MISSING")
        for category in categories:
            desired.append(
                {
                    "source_type": "SIGNAL_OUTCOME",
                    "source_key": (
                        f"{metric.signal_date.isoformat()}:{metric.stock_id}"
                    ),
                    "category": category,
                    "stock_id": metric.stock_id,
                    "signal_date": metric.signal_date,
                }
            )
    for metric in (
        db.query(SignalObservationOutcomeMetric)
        .filter(
            SignalObservationOutcomeMetric.premature_stop_candidate.is_(True)
        )
        .all()
    ):
        desired.append(
            {
                "source_type": "OBSERVATION_OUTCOME",
                "source_key": str(metric.observation_id),
                "category": "PREMATURE_STOP_CANDIDATE",
                "stock_id": metric.stock_id,
                "observation_id": metric.observation_id,
            }
        )
    for snapshot in db.query(SignalSnapshot).yield_per(250):
        for failure in (snapshot.summary or {}).get(
            "technical_failures", []
        ) or []:
            if not isinstance(failure, dict):
                continue
            status = str(
                failure.get("processing_status")
                or failure.get("status")
                or ""
            ).upper()
            if status != "TRACKING_SELECTION_CONFLICT":
                continue
            sid = _stock_id(failure)
            if not sid:
                continue
            desired.append(
                {
                    "source_type": "TRACKING_CONFLICT",
                    "source_key": (
                        f"{snapshot.snapshot_date.isoformat()}:{sid}"
                    ),
                    "category": "GLOBAL_SELECTION_CONFLICT",
                    "stock_id": sid,
                    "signal_date": snapshot.snapshot_date,
                    "observation_id": _safe_int(
                        failure.get("observation_id")
                    ),
                }
            )
    for item in desired:
        key = (item["source_type"], item["source_key"], item["category"])
        if key not in existing:
            db.add(SignalOutcomeReviewQueue(**item))


def _apply_metric_filters(
    query: Query,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    prompt_family: Optional[str] = None,
    selection_version: Optional[str] = None,
    asset_type: Optional[str] = None,
    theme_cluster: Optional[str] = None,
    outcome_label: Optional[str] = None,
    p3_decision: Optional[str] = None,
    selection_reason_code: Optional[str] = None,
    observation_status: Optional[str] = None,
    momentum_score_version: Optional[str] = None,
    research_prompt_version: Optional[str] = None,
    assessment_prompt_version: Optional[str] = None,
    global_selector_version: Optional[str] = None,
    reason_prompt_version: Optional[str] = None,
    tracking_prompt_version: Optional[str] = None,
    tracking_state_machine_version: Optional[str] = None,
    backend_rank_min: Optional[int] = None,
    backend_rank_max: Optional[int] = None,
    recommendation_rank_min: Optional[int] = None,
    recommendation_rank_max: Optional[int] = None,
) -> Query:
    if start_date is not None:
        query = query.filter(SignalOutcomeMetric.signal_date >= start_date)
    if end_date is not None:
        query = query.filter(SignalOutcomeMetric.signal_date <= end_date)
    if prompt_family:
        query = query.filter(
            SignalOutcomeMetric.prompt_family_version == prompt_family
        )
    if selection_version:
        query = query.filter(
            SignalOutcomeMetric.selection_version == selection_version
        )
    if asset_type:
        query = query.filter(SignalOutcomeMetric.asset_type == asset_type)
    if theme_cluster:
        query = query.filter(
            SignalOutcomeMetric.theme_cluster == theme_cluster
        )
    if outcome_label:
        query = query.filter(
            SignalOutcomeMetric.outcome_label == outcome_label
        )
    if p3_decision:
        query = query.filter(SignalOutcomeMetric.p3_decision == p3_decision)
    if selection_reason_code:
        query = query.filter(
            SignalOutcomeMetric.selection_reason_code
            == selection_reason_code
        )
    if observation_status:
        query = query.filter(
            SignalOutcomeMetric.observation_status == observation_status
        )
    if momentum_score_version:
        query = query.filter(
            SignalOutcomeMetric.momentum_score_version
            == momentum_score_version
        )
    for value, column in (
        (research_prompt_version, SignalOutcomeMetric.research_prompt_version),
        (
            assessment_prompt_version,
            SignalOutcomeMetric.assessment_prompt_version,
        ),
        (global_selector_version, SignalOutcomeMetric.global_selector_version),
        (reason_prompt_version, SignalOutcomeMetric.reason_prompt_version),
        (tracking_prompt_version, SignalOutcomeMetric.tracking_prompt_version),
        (
            tracking_state_machine_version,
            SignalOutcomeMetric.tracking_state_machine_version,
        ),
    ):
        if value:
            query = query.filter(column == value)
    if backend_rank_min is not None:
        query = query.filter(
            SignalOutcomeMetric.backend_priority_rank >= backend_rank_min
        )
    if backend_rank_max is not None:
        query = query.filter(
            SignalOutcomeMetric.backend_priority_rank <= backend_rank_max
        )
    if recommendation_rank_min is not None:
        query = query.filter(
            SignalOutcomeMetric.recommendation_rank
            >= recommendation_rank_min
        )
    if recommendation_rank_max is not None:
        query = query.filter(
            SignalOutcomeMetric.recommendation_rank
            <= recommendation_rank_max
        )
    return query


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _phase2_counts_by_date(
    db: Session,
    dates: Iterable[date],
) -> dict[date, int]:
    wanted = set(dates)
    if not wanted:
        return {}
    rows = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date.in_(wanted))
        .all()
    )
    result: dict[date, int] = {}
    for snapshot in rows:
        summary = snapshot.summary or {}
        processing = summary.get("processing_summary") or {}
        selection = summary.get("selection_summary") or {}
        value = (
            processing.get("llm_eligible_count")
            or processing.get("phase2_pool_count")
            or selection.get("phase2_eligible_count")
            or 0
        )
        result[snapshot.snapshot_date] = int(value)
    return result


def _noneligible_counts_by_date(
    db: Session,
    dates: Iterable[date],
) -> dict[date, dict[str, int]]:
    wanted = set(dates)
    if not wanted:
        return {}
    result: dict[date, dict[str, int]] = {}
    for snapshot in (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date.in_(wanted))
        .all()
    ):
        summary = snapshot.summary or {}
        result[snapshot.snapshot_date] = {
            "removed": len(snapshot.removed or []),
            "technical_failure": len(
                summary.get("technical_failures") or []
            ),
        }
    return result


def _date_range_payload(
    db: Session,
    filtered_query: Query,
    requested_start: Optional[date],
    requested_end: Optional[date],
) -> dict[str, Any]:
    bounds = filtered_query.with_entities(
        func.min(SignalOutcomeMetric.signal_date),
        func.max(SignalOutcomeMetric.signal_date),
    ).one()
    return {
        "requested_start": requested_start,
        "requested_end": requested_end,
        "actual_start": bounds[0],
        "actual_end": bounds[1],
    }


def get_outcome_summary(
    db: Session,
    **filters: Any,
) -> dict[str, Any]:
    base = _apply_metric_filters(db.query(SignalOutcomeMetric), **filters)
    recommendation = base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_RECOMMEND
    )
    total = recommendation.count()
    grouped = dict(
        recommendation.with_entities(
            SignalOutcomeMetric.outcome_label,
            func.count(SignalOutcomeMetric.id),
        )
        .group_by(SignalOutcomeMetric.outcome_label)
        .all()
    )
    winner = int(grouped.get(LABEL_WINNER, 0))
    neutral = int(grouped.get(LABEL_NEUTRAL, 0))
    big_loser = int(grouped.get(LABEL_BIG_LOSER, 0))
    immature = int(grouped.get(LABEL_IMMATURE, 0))
    missing = int(grouped.get(LABEL_MISSING, 0))
    matured = winner + neutral + big_loser

    selection_base = base.filter(
        SignalOutcomeMetric.global_eligible.is_(True)
    )
    by_date = (
        selection_base.with_entities(
            SignalOutcomeMetric.signal_date,
            func.sum(
                case(
                    (
                        SignalOutcomeMetric.p3_decision
                        == DECISION_RECOMMEND,
                        1,
                    ),
                    else_=0,
                )
            ),
            func.count(SignalOutcomeMetric.id),
        )
        .group_by(SignalOutcomeMetric.signal_date)
        .all()
    )
    compression_rates = [
        1.0 - int(rec_count or 0) / int(eligible_count)
        for _, rec_count, eligible_count in by_date
        if eligible_count
    ]
    average_recommend_count = (
        sum(int(row[1] or 0) for row in by_date) / len(by_date)
        if by_date
        else 0.0
    )
    phase2_by_date = _phase2_counts_by_date(
        db, (row[0] for row in by_date)
    )
    average_phase2_count = (
        sum(phase2_by_date.values()) / len(phase2_by_date)
        if phase2_by_date
        else 0.0
    )
    average_global_count = (
        sum(int(row[2]) for row in by_date) / len(by_date)
        if by_date
        else 0.0
    )

    global_winners = selection_base.filter(
        SignalOutcomeMetric.outcome_label == LABEL_WINNER,
    ).count()
    recommended_global_winners = selection_base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_RECOMMEND,
        SignalOutcomeMetric.outcome_label == LABEL_WINNER,
    ).count()
    not_selected_matured = selection_base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_NOT_SELECTED,
        SignalOutcomeMetric.outcome_label.in_(MATURE_LABELS),
    ).count()
    not_selected_winners = selection_base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_NOT_SELECTED,
        SignalOutcomeMetric.outcome_label == LABEL_WINNER,
    ).count()
    not_selected_winner_by_reason = {
        (reason or "UNKNOWN"): int(count)
        for reason, count in (
            selection_base.filter(
                SignalOutcomeMetric.p3_decision
                == DECISION_NOT_SELECTED,
                SignalOutcomeMetric.outcome_label == LABEL_WINNER,
            )
            .with_entities(
                SignalOutcomeMetric.selection_reason_code,
                func.count(SignalOutcomeMetric.id),
            )
            .group_by(SignalOutcomeMetric.selection_reason_code)
            .all()
        )
    }
    rank_override_count = selection_base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_RECOMMEND,
        SignalOutcomeMetric.rank_override.is_(True),
    ).count()
    rank_override_big_loser_count = selection_base.filter(
        SignalOutcomeMetric.p3_decision == DECISION_RECOMMEND,
        SignalOutcomeMetric.rank_override.is_(True),
        SignalOutcomeMetric.outcome_label == LABEL_BIG_LOSER,
    ).count()
    rank_bucket = case(
        (SignalOutcomeMetric.backend_priority_rank <= 10, "1-10"),
        (SignalOutcomeMetric.backend_priority_rank <= 25, "11-25"),
        (SignalOutcomeMetric.backend_priority_rank <= 50, "26-50"),
        else_="51+",
    )
    rank_rows = selection_base.with_entities(
        SignalOutcomeMetric.p3_decision,
        SignalOutcomeMetric.outcome_label,
        rank_bucket.label("rank_bucket"),
        func.count(SignalOutcomeMetric.id),
    ).group_by(
        SignalOutcomeMetric.p3_decision,
        SignalOutcomeMetric.outcome_label,
        rank_bucket,
    ).all()
    rank_distribution = {
        "recommend": defaultdict(int),
        "not_selected": defaultdict(int),
        "winner": defaultdict(int),
    }
    for decision, label, bucket, count in rank_rows:
        target = (
            "recommend"
            if decision == DECISION_RECOMMEND
            else "not_selected"
        )
        rank_distribution[target][bucket] += int(count)
        if label == LABEL_WINNER:
            rank_distribution["winner"][bucket] += int(count)
    observation = get_observation_analytics(db)

    version_rows = base.with_entities(
        SignalOutcomeMetric.prompt_family_version,
        SignalOutcomeMetric.selection_version,
        SignalOutcomeMetric.momentum_score_version,
        SignalOutcomeMetric.research_prompt_version,
        SignalOutcomeMetric.assessment_prompt_version,
        SignalOutcomeMetric.global_selector_version,
        SignalOutcomeMetric.reason_prompt_version,
        SignalOutcomeMetric.tracking_prompt_version,
        SignalOutcomeMetric.tracking_state_machine_version,
    ).distinct().all()
    versions = {
        "prompt_family": sorted(
            {row[0] for row in version_rows if row[0]}
        ),
        "selection_version": sorted(
            {row[1] for row in version_rows if row[1]}
        ),
        "momentum_score_version": sorted(
            {row[2] for row in version_rows if row[2]}
        ),
        "research_prompt_version": sorted(
            {row[3] for row in version_rows if row[3]}
        ),
        "assessment_prompt_version": sorted(
            {row[4] for row in version_rows if row[4]}
        ),
        "global_selector_version": sorted(
            {row[5] for row in version_rows if row[5]}
        ),
        "reason_prompt_version": sorted(
            {row[6] for row in version_rows if row[6]}
        ),
        "tracking_prompt_version": sorted(
            {row[7] for row in version_rows if row[7]}
        ),
        "tracking_state_machine_version": sorted(
            {row[8] for row in version_rows if row[8]}
        ),
        "outcome_definition_version": [OUTCOME_DEFINITION_VERSION],
    }
    requested_start = filters.get("start_date")
    requested_end = filters.get("end_date")
    return {
        "date_range": _date_range_payload(
            db, base, requested_start, requested_end
        ),
        "sample": {
            "total": total,
            "matured": matured,
            "immature": immature,
            "missing": missing,
        },
        "recommendation": {
            "winner_count": winner,
            "neutral_count": neutral,
            "big_loser_count": big_loser,
            "winner_rate": _ratio(winner, matured),
            "neutral_rate": _ratio(neutral, matured),
            "big_loser_rate": _ratio(big_loser, matured),
            "acceptable_rate": _ratio(winner + neutral, matured),
            "acceptable_target_met": (
                matured > 0 and _ratio(winner + neutral, matured) >= 0.8
            ),
            "winner_greater_than_neutral": winner > neutral,
            "average_recommend_count": average_recommend_count,
        },
        "selection": {
            "winner_recall": _ratio(
                recommended_global_winners, global_winners
            ),
            "not_selected_winner_count": not_selected_winners,
            "not_selected_winner_rate": _ratio(
                not_selected_winners, not_selected_matured
            ),
            "not_selected_winner_by_reason": (
                not_selected_winner_by_reason
            ),
            "average_compression_rate": (
                sum(compression_rates) / len(compression_rates)
                if compression_rates
                else 0.0
            ),
            "average_phase2_eligible_count": average_phase2_count,
            "average_global_eligible_count": average_global_count,
            "average_recommended_count": average_recommend_count,
            "rank_override_count": rank_override_count,
            "rank_override_big_loser_count": (
                rank_override_big_loser_count
            ),
            "backend_rank_distribution": {
                key: dict(value)
                for key, value in rank_distribution.items()
            },
        },
        "observation": observation["summary"],
        "versions": versions,
        "definitions": {
            "outcome_definition_version": OUTCOME_DEFINITION_VERSION,
            "outcome_horizon": OUTCOME_HORIZON,
            "entry_price_definition": ENTRY_PRICE_DEFINITION,
            "exit_price_definition": EXIT_PRICE_DEFINITION,
            "premature_stop_definition_version": (
                PREMATURE_STOP_DEFINITION_VERSION
            ),
        },
    }


def get_outcome_timeseries(db: Session, **filters: Any) -> dict[str, Any]:
    recommend_case = (
        SignalOutcomeMetric.p3_decision == DECISION_RECOMMEND
    )
    rows = (
        _apply_metric_filters(db.query(SignalOutcomeMetric), **filters)
        .with_entities(
            SignalOutcomeMetric.signal_date,
            func.count(SignalOutcomeMetric.id),
            func.sum(case((recommend_case, 1), else_=0)),
            func.sum(
                case(
                    (
                        recommend_case
                        & (
                            SignalOutcomeMetric.outcome_label
                            == LABEL_WINNER
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        recommend_case
                        & (
                            SignalOutcomeMetric.outcome_label
                            == LABEL_NEUTRAL
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        recommend_case
                        & (
                            SignalOutcomeMetric.outcome_label
                            == LABEL_BIG_LOSER
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        SignalOutcomeMetric.global_eligible.is_(True)
                        & (
                            SignalOutcomeMetric.outcome_label
                            == LABEL_WINNER
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .group_by(SignalOutcomeMetric.signal_date)
        .order_by(SignalOutcomeMetric.signal_date.asc())
        .all()
    )
    items = []
    phase2_by_date = _phase2_counts_by_date(
        db, (row[0] for row in rows)
    )
    noneligible_by_date = _noneligible_counts_by_date(
        db, (row[0] for row in rows)
    )
    for (
        signal_date,
        eligible,
        recommended,
        winner,
        neutral,
        big_loser,
        all_winners,
    ) in rows:
        eligible = int(eligible or 0)
        recommended = int(recommended or 0)
        winner = int(winner or 0)
        neutral = int(neutral or 0)
        big_loser = int(big_loser or 0)
        all_winners = int(all_winners or 0)
        matured = winner + neutral + big_loser
        items.append(
            {
                "date": signal_date,
                "eligible": eligible,
                "phase2_eligible": phase2_by_date.get(signal_date, 0),
                "recommended": recommended,
                "not_selected": eligible - recommended,
                "removed": noneligible_by_date.get(signal_date, {}).get(
                    "removed", 0
                ),
                "technical_failure": noneligible_by_date.get(
                    signal_date, {}
                ).get("technical_failure", 0),
                "winner": winner,
                "neutral": neutral,
                "big_loser": big_loser,
                "matured_sample": matured,
                "acceptable_rate": _ratio(winner + neutral, matured),
                "winner_recall": _ratio(winner, all_winners),
            }
        )
    return {
        "outcome_definition_version": OUTCOME_DEFINITION_VERSION,
        "items": items,
    }


SORT_COLUMNS = {
    "signal_date": SignalOutcomeMetric.signal_date,
    "stock": SignalOutcomeMetric.stock_id,
    "backend_rank": SignalOutcomeMetric.backend_priority_rank,
    "recommendation_rank": SignalOutcomeMetric.recommendation_rank,
    "day10_return": SignalOutcomeMetric.outcome_return_pct,
    "outcome_label": SignalOutcomeMetric.outcome_label,
}


def _serialize_metric(row: SignalOutcomeMetric) -> dict[str, Any]:
    metadata = row.metadata_json or {}
    return {
        "id": row.id,
        "signal_date": row.signal_date,
        "stock": row.stock_id,
        "name": row.stock_name,
        "asset_type": row.asset_type,
        "backend_priority_rank": row.backend_priority_rank,
        "recommendation_rank": row.recommendation_rank,
        "p3_decision": row.p3_decision,
        "selection_reason_code": row.selection_reason_code,
        "selection_reason": row.selection_reason,
        "theme_cluster": row.theme_cluster,
        "observation_status": row.observation_status,
        "stop_date": row.stop_date,
        "stop_reason": row.stop_reason,
        "day10_return": row.outcome_return_pct,
        "outcome_label": row.outcome_label,
        "entry_trade_date": row.entry_trade_date,
        "entry_price": row.entry_price,
        "exit_trade_date": row.exit_trade_date,
        "exit_price": row.exit_price,
        "selection_version": row.selection_version,
        "prompt_family_version": row.prompt_family_version,
        "momentum_score_version": row.momentum_score_version,
        "research_prompt_version": row.research_prompt_version,
        "assessment_prompt_version": row.assessment_prompt_version,
        "global_selector_version": row.global_selector_version,
        "reason_prompt_version": row.reason_prompt_version,
        "tracking_prompt_version": row.tracking_prompt_version,
        "tracking_state_machine_version": (
            row.tracking_state_machine_version
        ),
        "outcome_definition_version": row.outcome_definition_version,
        "rank_override": row.rank_override,
        "rank_override_reason": row.rank_override_reason,
        "metadata": metadata,
    }


def get_outcome_items(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    sort: str = "signal_date",
    direction: str = "desc",
    **filters: Any,
) -> dict[str, Any]:
    query = _apply_metric_filters(db.query(SignalOutcomeMetric), **filters)
    total = query.count()
    column = SORT_COLUMNS.get(sort, SignalOutcomeMetric.signal_date)
    ordered = column.asc() if direction.lower() == "asc" else column.desc()
    rows = (
        query.order_by(ordered, SignalOutcomeMetric.stock_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": [_serialize_metric(row) for row in rows],
    }


def iter_outcome_items_csv(
    db: Session,
    *,
    chunk_size: int = 500,
    **filters: Any,
) -> Iterator[str]:
    rows = (
        _apply_metric_filters(db.query(SignalOutcomeMetric), **filters)
        .order_by(
            SignalOutcomeMetric.signal_date.desc(),
            SignalOutcomeMetric.stock_id.asc(),
        )
        .yield_per(1000)
    )
    stream = io.StringIO()
    fields = [
        "signal_date",
        "stock",
        "name",
        "asset_type",
        "backend_priority_rank",
        "recommendation_rank",
        "p3_decision",
        "selection_reason_code",
        "selection_reason",
        "theme_cluster",
        "observation_status",
        "stop_date",
        "stop_reason",
        "day10_return",
        "outcome_label",
        "selection_version",
        "prompt_family_version",
        "momentum_score_version",
        "outcome_definition_version",
    ]
    writer = csv.writer(stream)
    writer.writerow(fields)
    yield stream.getvalue()
    stream.seek(0)
    stream.truncate(0)
    buffered_rows = 0
    for row in rows:
        writer.writerow(
            (
                row.signal_date,
                row.stock_id,
                row.stock_name,
                row.asset_type,
                row.backend_priority_rank,
                row.recommendation_rank,
                row.p3_decision,
                row.selection_reason_code,
                row.selection_reason,
                row.theme_cluster,
                row.observation_status,
                row.stop_date,
                row.stop_reason,
                row.outcome_return_pct,
                row.outcome_label,
                row.selection_version,
                row.prompt_family_version,
                row.momentum_score_version,
                row.outcome_definition_version,
            )
        )
        buffered_rows += 1
        if buffered_rows >= chunk_size:
            yield stream.getvalue()
            stream.seek(0)
            stream.truncate(0)
            buffered_rows = 0
    if buffered_rows:
        yield stream.getvalue()


def get_observation_analytics(db: Session) -> dict[str, Any]:
    observations = db.query(SignalObservation).all()
    reviews = (
        db.query(SignalObservationReview)
        .order_by(
            SignalObservationReview.observation_id.asc(),
            SignalObservationReview.review_date.asc(),
        )
        .all()
    )
    review_groups: dict[int, list[SignalObservationReview]] = defaultdict(list)
    for review in reviews:
        review_groups[review.observation_id].append(review)
    caution_events = 0
    recovered_events = 0
    caution_episodes = 0
    recovered_episodes = 0
    for observation in observations:
        values = review_groups[observation.id]
        episode_had_caution = False
        episode_recovered = False
        for index, review in enumerate(values):
            if review.decision != "CAUTION":
                continue
            caution_events += 1
            episode_had_caution = True
            future = values[index + 1 :]
            recovered = any(
                item.decision == "CONTINUE"
                for item in future[
                    : next(
                        (
                            idx
                            for idx, item in enumerate(future)
                            if item.decision == "STOP_OBSERVING"
                        ),
                        len(future),
                    )
                ]
            )
            if recovered:
                recovered_events += 1
                episode_recovered = True
        if episode_had_caution:
            caution_episodes += 1
        if episode_recovered:
            recovered_episodes += 1

    metrics = db.query(SignalObservationOutcomeMetric).all()
    stopped = [row for row in metrics if row.stop_date is not None]
    hit_big_loss = [row for row in metrics if row.hit_minus10_date is not None]
    stopped_before = [
        row for row in hit_big_loss if row.stopped_before_minus10 is True
    ]
    stop_days = [
        row.trading_days_to_stop
        for row in stopped
        if row.trading_days_to_stop is not None
    ]
    premature = [
        row for row in stopped if row.premature_stop_candidate
    ]
    rerecommended = [
        row for row in metrics if row.next_episode_id is not None
    ]
    by_category: dict[str, list[int]] = defaultdict(list)
    for row in stopped:
        if row.stop_category and row.trading_days_to_stop is not None:
            by_category[row.stop_category].append(row.trading_days_to_stop)
    return {
        "summary": {
            "caution_recovery_rate": _ratio(
                recovered_events, caution_events
            ),
            "caution_event_recovery_rate": _ratio(
                recovered_events, caution_events
            ),
            "caution_episode_recovery_rate": _ratio(
                recovered_episodes, caution_episodes
            ),
            "premature_stop_candidate_count": len(premature),
            "stop_before_big_loss_rate": _ratio(
                len(stopped_before), len(hit_big_loss)
            ),
            "average_trading_days_to_stop": (
                sum(stop_days) / len(stop_days) if stop_days else 0.0
            ),
            "rerecommended_episode_count": len(rerecommended),
        },
        "definitions": {
            "observation_definition_version": (
                OBSERVATION_DEFINITION_VERSION
            ),
            "premature_stop_definition_version": (
                PREMATURE_STOP_DEFINITION_VERSION
            ),
            "caution_recovery": (
                "CAUTION followed by CONTINUE before STOP_OBSERVING"
            ),
            "stop_before_big_loss": (
                "STOP_OBSERVING before first close at or below -10% "
                "from initial recommendation close"
            ),
        },
        "premature_stop_candidates": [
            {
                "observation_id": row.observation_id,
                "episode_id": row.episode_id,
                "stock": row.stock_id,
                "stop_date": row.stop_date,
                "stop_reason_code": row.stop_reason_code,
                "post_stop_day10_return": row.post_stop_day10_return_pct,
            }
            for row in premature
        ],
        "stopped_before_big_loss": {
            "big_loser_episode_count": len(hit_big_loss),
            "stopped_before_count": len(stopped_before),
            "rate": _ratio(len(stopped_before), len(hit_big_loss)),
            "average_lead_trading_days": (
                sum(
                    row.trading_days_before_minus10 or 0
                    for row in stopped_before
                )
                / len(stopped_before)
                if stopped_before
                else 0.0
            ),
        },
        "average_days_to_stop": {
            "all": (
                sum(stop_days) / len(stop_days) if stop_days else 0.0
            ),
            **{
                category.lower(): sum(values) / len(values)
                for category, values in by_category.items()
                if values
            },
        },
        "rerecommended_episodes": [
            {
                "observation_id": row.observation_id,
                "episode_id": row.episode_id,
                "stock": row.stock_id,
                "stop_reason_code": row.stop_reason_code,
                "next_episode_id": row.next_episode_id,
                "trading_days_to_rerecommend": (
                    row.trading_days_to_rerecommend
                ),
            }
            for row in rerecommended
        ],
    }


def get_review_queue(
    db: Session,
    *,
    review_status: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    query = db.query(SignalOutcomeReviewQueue)
    if review_status:
        query = query.filter(
            SignalOutcomeReviewQueue.review_status == review_status
        )
    if category:
        query = query.filter(
            SignalOutcomeReviewQueue.category == category
        )
    total = query.count()
    rows = (
        query.order_by(
            SignalOutcomeReviewQueue.review_status.asc(),
            SignalOutcomeReviewQueue.signal_date.desc(),
            SignalOutcomeReviewQueue.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                "id": row.id,
                "source_type": row.source_type,
                "category": row.category,
                "stock": row.stock_id,
                "signal_date": row.signal_date,
                "observation_id": row.observation_id,
                "review_status": row.review_status,
                "review_note": row.review_note,
                "reviewed_at": row.reviewed_at,
                "reviewed_by": row.reviewed_by,
            }
            for row in rows
        ],
    }


def update_review_queue_item(
    db: Session,
    queue_id: int,
    *,
    review_status: str,
    review_note: Optional[str],
    reviewed_by: Optional[str],
) -> Optional[dict[str, Any]]:
    row = db.get(SignalOutcomeReviewQueue, queue_id)
    if row is None:
        return None
    normalized_status = review_status.upper()
    if normalized_status not in {"UNREVIEWED", "REVIEWED"}:
        raise ValueError("Invalid review status")
    note = (review_note or "").strip()
    if len(note) > 2000:
        raise ValueError("Review note must be at most 2000 characters")
    row.review_status = normalized_status
    row.review_note = note or None
    row.reviewed_at = (
        datetime.utcnow() if normalized_status == "REVIEWED" else None
    )
    row.reviewed_by = (
        reviewed_by if normalized_status == "REVIEWED" else None
    )
    row.updated_at = datetime.utcnow()
    db.commit()
    return {
        "id": row.id,
        "source_type": row.source_type,
        "category": row.category,
        "stock": row.stock_id,
        "signal_date": row.signal_date,
        "observation_id": row.observation_id,
        "review_status": row.review_status,
        "review_note": row.review_note,
        "reviewed_at": row.reviewed_at,
        "reviewed_by": row.reviewed_by,
    }
