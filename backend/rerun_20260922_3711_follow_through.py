"""Correct the 2026-09-22 false P4 stop for stock 3711.

The normal P4 decision ran before the post-pipeline archive refresh, so the
same-day close was missing from the follow-through extrema.  This bounded
repair is intentionally stock/date/strategy scoped and is dry-run by default.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from app.database import SessionLocal
from app.models import (
    DailyPrice,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    SignalObservation,
    SignalObservationArchive,
    SignalObservationReview,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
    SignalWatchStoppedObservation,
)
from app.signals import observation_lifecycle as lifecycle
from app.signals import shadow_portfolio
from app.signals.phase2 import tracking_state


STOCK_ID = "3711"
REVIEW_DATE = date(2026, 9, 22)
FIRST_SEEN_DATE = date(2026, 9, 9)
HIT_DATES = (date(2026, 9, 9), date(2026, 9, 18))
OBSERVATION_ID = 397
STRATEGY_VERSION = "v1_frozen"
SELL_ORDER_ID = 979
SHADOW_DECISION_ID = 31726


def _close(db, trade_date: date) -> float:
    row = (
        db.query(DailyPrice.close_price)
        .filter(DailyPrice.stock_id == STOCK_ID, DailyPrice.trade_date == trade_date)
        .one()
    )
    return float(row[0])


def _trade_day_count(db) -> int:
    return int(
        db.query(DailyPrice.trade_date)
        .filter(
            DailyPrice.stock_id == STOCK_ID,
            DailyPrice.trade_date > FIRST_SEEN_DATE,
            DailyPrice.trade_date <= REVIEW_DATE,
        )
        .distinct()
        .count()
    )


def _snapshot_item(db, snapshot_date: date) -> dict[str, Any]:
    snapshot = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == snapshot_date)
        .one()
    )
    item = next(
        item
        for item in (snapshot.watchlist or [])
        if str(item.get("stock") or item.get("stock_id") or "") == STOCK_ID
    )
    item["_job_id"] = snapshot.job_id
    item["_generated_at"] = snapshot.generated_at
    return item


def _restore_hits(db) -> list[SignalWatchHit]:
    baseline_date = date(2026, 9, 10)
    baseline_price = 645.5
    max_positive = (_close(db, REVIEW_DATE) - baseline_price) / baseline_price * 100.0
    max_negative = -8.288148721920992
    restored: list[SignalWatchHit] = []
    for hit_date in HIT_DATES:
        if (
            db.query(SignalWatchHit)
            .filter(
                SignalWatchHit.stock_id == STOCK_ID,
                SignalWatchHit.snapshot_date == hit_date,
            )
            .one_or_none()
            is not None
        ):
            continue
        item = _snapshot_item(db, hit_date)
        row = SignalWatchHit(
            snapshot_date=hit_date,
            stock_id=STOCK_ID,
            stock_name=str(item.get("name") or "日月光投控"),
            signal_type=str(item.get("type") or "FOLLOWER"),
            industry_name=item.get("industry"),
            sub_industry=item.get("sub_industry"),
            business_summary=item.get("business_summary"),
            reason=str(item.get("reason") or ""),
            recommendation_thesis=item.get("recommendation_thesis"),
            relative_advantage=item.get("relative_advantage"),
            margin_analysis=item.get("margin_analysis"),
            market_resilience=item.get("market_resilience"),
            market_context_reason=item.get("market_context_reason"),
            theme=item.get("theme") or {},
            group_info=item.get("group_info") or {},
            leader_check=item.get("leader_check") or {},
            signals=item.get("signals") or {},
            signal_metrics=item.get("signal_metrics"),
            prompt_version=item.get("prompt_version") or "v7_research",
            baseline_trade_date=baseline_date,
            baseline_price=baseline_price,
            latest_eval_trade_date=REVIEW_DATE,
            latest_eval_price=_close(db, REVIEW_DATE),
            return_pct=max_positive,
            max_positive_return_pct=max_positive,
            max_positive_return_trade_date=REVIEW_DATE,
            max_negative_return_pct=max_negative,
            max_negative_return_trade_date=date(2026, 9, 15),
            snapshot_generated_at=item.get("_generated_at"),
            job_id=item.get("_job_id"),
        )
        db.add(row)
        restored.append(row)
    db.flush()
    return restored


def _corrected_evidence(review: SignalObservationReview) -> dict[str, Any]:
    evidence = dict(review.backend_evidence_json or {})
    max_positive = 7.358636715724244
    max_negative = -8.288148721920992
    evidence["tracking_state"] = tracking_state.TRACKING_ACTIVE_TREND
    evidence["hard_exclusion"] = {
        "excluded": False,
        "reason": None,
        "matched_hard_rules": [],
        "risk_warnings": evidence.get("risk_warnings") or [],
        "liquidity_state": "NORMAL",
        "evidence_families": [],
    }
    evidence["failed_follow_through"] = False
    evidence["backend_max_decision"] = "WATCH"
    evidence["tracking_performance"] = {
        "first_seen_date": FIRST_SEEN_DATE.isoformat(),
        "days_since_first_seen": 9,
        "max_positive_return_pct": max_positive,
        "max_positive_return_trade_date": REVIEW_DATE.isoformat(),
        "max_negative_return_pct": max_negative,
        "max_negative_return_trade_date": date(2026, 9, 15).isoformat(),
        "failed_follow_through": False,
    }
    evidence["repair_metadata"] = {
        "repair_code": "same_day_follow_through_refresh_v1",
        "repaired_review_date": REVIEW_DATE.isoformat(),
        "reason": "當日收盤未納入原 P4 判斷，重算 current-cycle extrema",
    }
    return evidence


def _prior_reviews(db) -> list[dict[str, Any]]:
    rows = (
        db.query(SignalObservationReview)
        .filter(
            SignalObservationReview.observation_id == OBSERVATION_ID,
            SignalObservationReview.review_date < REVIEW_DATE,
            SignalObservationReview.decision != lifecycle.DECISION_FAILED,
        )
        .order_by(SignalObservationReview.review_date.asc())
        .all()
    )
    return [lifecycle._review_to_state_dict(row) for row in rows]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="write the bounded production repair")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        observation = db.get(SignalObservation, OBSERVATION_ID)
        review = (
            db.query(SignalObservationReview)
            .filter(
                SignalObservationReview.observation_id == OBSERVATION_ID,
                SignalObservationReview.review_date == REVIEW_DATE,
            )
            .one()
        )
        sell_order = db.get(ShadowStrategyOrder, SELL_ORDER_ID)
        daily_decision = db.get(ShadowStrategyDailyDecision, SHADOW_DECISION_ID)
        if observation is None or observation.stock_id != STOCK_ID:
            raise RuntimeError("unexpected observation target")
        if review.decision != lifecycle.DECISION_STOP:
            raise RuntimeError(f"expected original STOP review, got {review.decision}")
        if sell_order is None or sell_order.status != "PENDING":
            raise RuntimeError("expected pending 3711 SELL order")
        if daily_decision is None or daily_decision.action != "SELL":
            raise RuntimeError("expected original 3711 SELL daily decision")

        evidence = _corrected_evidence(review)
        external = None
        prior_external = (
            db.query(SignalObservationReview)
            .filter(
                SignalObservationReview.observation_id == OBSERVATION_ID,
                SignalObservationReview.review_date < REVIEW_DATE,
                SignalObservationReview.external_assessment_json.isnot(None),
            )
            .order_by(SignalObservationReview.review_date.desc())
            .first()
        )
        if prior_external is not None:
            external = prior_external.external_assessment_json

        decision = lifecycle.decide_observation_action(
            current_backend_evidence=evidence,
            external_thesis_assessment=external,
            latest_valid_reviews=_prior_reviews(db),
            current_observation={
                "status": observation.status,
                "baseline_quality": observation.baseline_quality,
                "pending_stop_status": None,
                "pending_stop_reason": None,
                "pending_stop_review_count": 0,
                "pending_stop_trigger_snapshot": None,
            },
        )
        print(
            f"3711 {REVIEW_DATE}: {review.decision} -> {decision.decision}; "
            f"reason={decision.reason_codes}; "
            f"max_pos={evidence['tracking_performance']['max_positive_return_pct']:.4f}%; "
            f"max_neg={evidence['tracking_performance']['max_negative_return_pct']:.4f}%"
        )
        if not args.execute:
            print("dry-run: no production rows changed")
            return 0

        restored_hits = _restore_hits(db)
        print(f"restored active fishtail hits: {len(restored_hits)}")

        db.query(SignalObservationArchive).filter(
            SignalObservationArchive.observation_id == OBSERVATION_ID,
            SignalObservationArchive.archived_date == REVIEW_DATE,
        ).delete(synchronize_session=False)
        for model in (SignalWatchStoppedObservation, SignalWatchCompletedArchive):
            db.query(model).filter(
                model.stock_id == STOCK_ID,
                model.first_seen_date == FIRST_SEEN_DATE,
                model.completed_trade_date == REVIEW_DATE,
                model.closure_reason == "p4_stopped",
            ).delete(synchronize_session=False)

        observation.status = (
            lifecycle.STATUS_CAUTION
            if decision.decision == lifecycle.DECISION_CAUTION
            else lifecycle.STATUS_OBSERVING
        )
        observation.latest_decision = decision.decision
        observation.last_review_date = REVIEW_DATE
        observation.stopped_at = None
        observation.stop_reason_code = None
        observation.stop_reason = None
        observation.stop_confirm_count = 0
        observation.consecutive_caution_count = (
            lifecycle._consecutive_caution_count(_prior_reviews(db))
            if decision.decision == lifecycle.DECISION_CAUTION
            else 0
        )
        observation.pending_stop_status = None
        observation.pending_stop_reason = None
        observation.pending_stop_since = None
        observation.pending_stop_trigger_snapshot = None
        observation.pending_stop_review_count = 0
        observation.latest_snapshot_json = {
            "review_date": REVIEW_DATE.isoformat(),
            "decision": decision.decision,
            "backend_evidence": evidence,
            "external_assessment": external,
        }
        lifecycle._upsert_review(
            db,
            observation=observation,
            review_date=REVIEW_DATE,
            decision=decision,
            backend_evidence=evidence,
            external_assessment=external,
            market_context=review.market_context_json or {},
            existing=review,
        )

        sell_order.status = "CANCELLED"
        sell_order.reason = (
            sell_order.reason or "P4_STOP"
        ) + "；已修正同日收盤未納入 follow-through 的誤判"
        daily_decision.action = "HOLD"
        daily_decision.action_reason = "修正：當日收盤已納入追蹤極值，P4 未停止觀察"
        daily_decision.p4_decision = decision.decision
        daily_decision.scheduled_execution_date = None
        shadow_portfolio.create_portfolio_daily_snapshot(
            db, target_date=REVIEW_DATE, strategy_version=STRATEGY_VERSION
        )
        db.commit()
        print("production repair committed")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
