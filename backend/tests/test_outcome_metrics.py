from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    DailyPrice,
    SignalGenerationJob,
    SignalObservation,
    SignalObservationReview,
    SignalOutcomeMetric,
    SignalOutcomeReviewQueue,
    SignalSnapshot,
)
from app.signals import outcome_metrics
from app.signals import global_selector, observation_lifecycle


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session
    Base.metadata.drop_all(bind=engine)


def _weekdays(start: date, count: int) -> list[date]:
    values = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def _snapshot(
    db,
    signal_date: date,
    *,
    watchlist: list[dict],
    not_selected: list[dict] | None = None,
    selection_complete: bool = True,
):
    job_id = f"job-{signal_date.isoformat()}"
    db.add(
        SignalGenerationJob(
            job_id=job_id,
            snapshot_date=signal_date,
            triggered_by="cron",
            status="done",
            progress_pct=100,
        )
    )
    db.add(
        SignalSnapshot(
            snapshot_date=signal_date,
            market_context={},
            watchlist=watchlist,
            removed=[],
            summary={
                "not_selected": not_selected or [],
                "selection_summary": {
                    "selection_complete": selection_complete,
                    "selection_version": "v7_global_selector",
                },
                "processing_summary": {
                    "selection_complete": selection_complete,
                    "prompt_family_version": "v7",
                    "momentum_score_version": "v3_applicability_aware",
                },
            },
            job_id=job_id,
        )
    )
    db.commit()


def _price(db, stock: str, trade_date: date, close: float):
    db.add(
        DailyPrice(
            stock_id=stock,
            trade_date=trade_date,
            open_price=close,
            high_price=close,
            low_price=close,
            close_price=close,
        )
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10.0, "WINNER"),
        (9.99, "NEUTRAL"),
        (-9.99, "NEUTRAL"),
        (-10.0, "BIG_LOSER"),
    ],
)
def test_outcome_label_boundaries(value, expected):
    assert outcome_metrics.classify_day10_return(value) == expected


def test_day10_is_tenth_subsequent_market_trading_day(db):
    dates = _weekdays(date(2026, 7, 1), 11)
    signal_date = dates[0]
    _snapshot(
        db,
        signal_date,
        watchlist=[
            {
                "stock": "2330",
                "name": "台積電",
                "decision": "RECOMMEND",
                "recommendation_rank": 1,
            }
        ],
    )
    for index, trade_date in enumerate(dates):
        _price(db, "MARKET", trade_date, 100 + index)
    _price(db, "2330", signal_date, 100)
    _price(db, "2330", dates[10], 110)
    db.commit()

    result = outcome_metrics.refresh_outcome_cache(db)
    row = db.query(SignalOutcomeMetric).one()

    assert result["calculated"] == 1
    assert row.exit_trade_date == dates[10]
    assert row.outcome_return_pct == pytest.approx(10.0)
    assert row.outcome_label == "WINNER"


def test_summary_core_goals_and_winner_recall(db):
    dates = _weekdays(date(2026, 7, 1), 11)
    signal_date = dates[0]
    recommended = []
    not_selected = []
    for index in range(10):
        recommended.append(
            {
                "stock": f"R{index}",
                "name": f"推薦{index}",
                "recommendation_rank": index + 1,
                "backend_priority_rank": index + 1,
            }
        )
    for index in range(3):
        not_selected.append(
            {
                "stock": f"N{index}",
                "name": f"未選{index}",
                "backend_priority_rank": 11 + index,
                "selection_reason_code": "LOWER_RELATIVE_PRIORITY",
            }
        )
    _snapshot(
        db,
        signal_date,
        watchlist=recommended,
        not_selected=not_selected,
    )
    for trade_date in dates:
        _price(db, "MARKET", trade_date, 100)
    exits = [110] * 5 + [100] * 3 + [90] * 2
    for item, exit_price in zip(recommended, exits):
        _price(db, item["stock"], signal_date, 100)
        _price(db, item["stock"], dates[10], exit_price)
    for index, item in enumerate(not_selected):
        _price(db, item["stock"], signal_date, 100)
        _price(db, item["stock"], dates[10], 110 if index < 2 else 100)
    db.commit()

    outcome_metrics.refresh_outcome_cache(db)
    summary = outcome_metrics.get_outcome_summary(db)

    assert summary["sample"]["matured"] == 10
    assert summary["recommendation"]["winner_count"] == 5
    assert summary["recommendation"]["neutral_count"] == 3
    assert summary["recommendation"]["big_loser_count"] == 2
    assert summary["recommendation"]["acceptable_rate"] == pytest.approx(0.8)
    assert summary["recommendation"]["acceptable_target_met"] is True
    assert summary["recommendation"]["winner_greater_than_neutral"] is True
    assert summary["selection"]["winner_recall"] == pytest.approx(5 / 7)
    assert summary["selection"]["not_selected_winner_count"] == 2


def test_immature_and_missing_are_not_neutral(db):
    dates = _weekdays(date(2026, 7, 1), 11)
    _snapshot(
        db,
        dates[0],
        watchlist=[
            {"stock": "MISS", "name": "缺價"},
            {"stock": "OK", "name": "完整"},
        ],
    )
    for trade_date in dates:
        _price(db, "MARKET", trade_date, 100)
    _price(db, "MISS", dates[0], 100)
    _price(db, "OK", dates[0], 100)
    _price(db, "OK", dates[10], 100)
    db.commit()

    outcome_metrics.refresh_outcome_cache(db)
    labels = {
        row.stock_id: row.outcome_label
        for row in db.query(SignalOutcomeMetric).all()
    }
    summary = outcome_metrics.get_outcome_summary(db)

    assert labels == {"MISS": "OUTCOME_DATA_MISSING", "OK": "NEUTRAL"}
    assert summary["sample"] == {
        "total": 2,
        "matured": 1,
        "immature": 0,
        "missing": 1,
    }


def test_global_selection_failure_does_not_create_recall_denominator(db):
    signal_date = date(2026, 7, 1)
    _snapshot(
        db,
        signal_date,
        watchlist=[],
        not_selected=[{"stock": "SHOULD_NOT_EXIST"}],
        selection_complete=False,
    )
    outcome_metrics.refresh_outcome_cache(db)
    assert db.query(SignalOutcomeMetric).count() == 0


def test_pre_p3_watch_is_not_reclassified_as_formal_recommendation(db):
    _snapshot(
        db,
        date(2026, 6, 1),
        watchlist=[
            {
                "stock": "2330",
                "name": "台積電",
                "decision": "WATCH",
            }
        ],
        selection_complete=False,
    )
    outcome_metrics.refresh_outcome_cache(db)
    assert db.query(SignalOutcomeMetric).count() == 0


def test_observation_analytics_and_premature_stop(db):
    dates = _weekdays(date(2026, 7, 1), 16)
    observation = SignalObservation(
        stock_id="2330",
        stock_name="台積電",
        asset_type="COMMON_STOCK",
        episode_id="episode-1",
        status="STOPPED",
        started_signal_date=dates[0],
        stopped_at=datetime.combine(dates[5], datetime.min.time()),
        stop_reason_code="STRUCTURE_DAMAGED",
        initial_snapshot_json={},
    )
    db.add(observation)
    db.flush()
    db.add_all(
        [
            SignalObservationReview(
                observation_id=observation.id,
                review_date=dates[2],
                decision="CAUTION",
                reason_codes=[],
                reason="警戒",
                caution_dimensions=[],
                failed_dimensions=[],
                prompt_version="p4_tracking_v7",
                state_machine_version="p4_state_v1",
            ),
            SignalObservationReview(
                observation_id=observation.id,
                review_date=dates[3],
                decision="CONTINUE",
                reason_codes=[],
                reason="恢復",
                caution_dimensions=[],
                failed_dimensions=[],
                prompt_version="p4_tracking_v7",
                state_machine_version="p4_state_v1",
            ),
        ]
    )
    for trade_date in dates:
        _price(db, "MARKET", trade_date, 100)
    _price(db, "2330", dates[0], 100)
    _price(db, "2330", dates[5], 100)
    _price(db, "2330", dates[15], 111)
    db.commit()

    outcome_metrics.refresh_observation_outcome_cache(db)
    analytics = outcome_metrics.get_observation_analytics(db)

    assert analytics["summary"]["caution_recovery_rate"] == 1.0
    assert analytics["summary"]["premature_stop_candidate_count"] == 1
    assert (
        analytics["definitions"]["premature_stop_definition_version"]
        == "stop_day10_plus10_v1"
    )


def test_production_selection_modules_do_not_import_outcomes():
    root = Path(__file__).resolve().parents[1] / "app" / "signals"
    protected = [
        "candidate_pool.py",
        "pipeline.py",
        "global_selector.py",
        "observation_lifecycle.py",
        "prompt_family.py",
        "llm_caller.py",
    ]
    for filename in protected:
        source = (root / filename).read_text(encoding="utf-8")
        assert "outcome_metrics" not in source
        assert "SignalOutcomeMetric" not in source


def test_selector_and_tracking_payloads_contain_no_future_outcomes():
    cards = global_selector.build_compact_selection_cards(
        [
            {
                "stock": "2330",
                "name": "台積電",
                "momentum_score": 90,
                # Even if an upstream caller accidentally carries these fields,
                # the compact projection must discard them.
                "outcome_label": "WINNER",
                "day10_return": 12.0,
            }
        ],
        selection_date=date(2026, 7, 1),
    )
    assert "outcome_label" not in cards[0]
    assert "day10_return" not in cards[0]

    observation = SignalObservation(
        stock_id="2330",
        stock_name="台積電",
        asset_type="COMMON_STOCK",
        episode_id="payload-guard",
        status="OBSERVING",
        started_signal_date=date(2026, 7, 1),
        initial_snapshot_json={
            "recommendation_thesis": "原始論點",
            "day10_return": 99.0,
        },
    )
    payload = observation_lifecycle._tracking_prompt_input(
        observation,
        review_date=date(2026, 7, 2),
        evidence={"tracking_state": "TRACKING_HEALTHY"},
        latest_review=None,
    )
    serialized = str(payload).lower()
    assert "day10" not in serialized
    assert "outcome" not in serialized


def test_manual_review_cannot_modify_outcome_or_original_decision(db):
    _snapshot(
        db,
        date(2026, 7, 1),
        watchlist=[{"stock": "2330", "name": "台積電"}],
    )
    metric = SignalOutcomeMetric(
        signal_date=date(2026, 7, 1),
        stock_id="2330",
        stock_name="台積電",
        asset_type="COMMON_STOCK",
        p3_decision="RECOMMEND",
        global_eligible=True,
        outcome_label="BIG_LOSER",
        outcome_horizon="DAY10",
        outcome_definition_version="day10_v1",
        entry_price_definition="signal_date_close",
        exit_price_definition="tenth_subsequent_market_trade_date_close",
        metadata_json={},
    )
    queue = SignalOutcomeReviewQueue(
        source_type="SIGNAL_OUTCOME",
        source_key="2026-07-01:2330",
        category="RECOMMEND_BIG_LOSER",
        stock_id="2330",
        signal_date=date(2026, 7, 1),
    )
    db.add_all([metric, queue])
    db.commit()

    outcome_metrics.update_review_queue_item(
        db,
        queue.id,
        review_status="REVIEWED",
        review_note="<script>not rendered as HTML</script>",
        reviewed_by="tester@example.com",
    )
    db.refresh(metric)
    snapshot = db.query(SignalSnapshot).one()
    assert metric.outcome_label == "BIG_LOSER"
    assert metric.p3_decision == "RECOMMEND"
    assert snapshot.watchlist[0]["stock"] == "2330"
