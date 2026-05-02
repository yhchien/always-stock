"""Schema sanity tests for M23 SignalSnapshot / SignalGenerationJob models."""
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import SignalGenerationJob, SignalSnapshot, SignalWatchHit


def _make_job(snapshot_date=date(2026, 4, 25), status="pending", triggered_by="cron"):
    return SignalGenerationJob(
        job_id=str(uuid.uuid4()),
        snapshot_date=snapshot_date,
        triggered_by=triggered_by,
        status=status,
        progress_pct=0,
    )


def test_job_round_trip_with_progress_fields(db):
    job = _make_job()
    job.current_stage = "llm_research"
    job.progress_pct = 60
    job.progress_label = "正在分析第 28 / 45 檔"
    db.add(job)
    db.commit()

    rec = db.query(SignalGenerationJob).one()
    assert rec.status == "pending"
    assert rec.progress_pct == 60
    assert rec.progress_label == "正在分析第 28 / 45 檔"
    assert rec.error_message is None
    assert rec.finished_at is None
    assert rec.started_at is not None  # default=datetime.utcnow


def test_job_can_persist_failure_with_traceback(db):
    job = _make_job(status="failed")
    job.error_message = "Traceback (most recent call last):\n  File ..."
    job.finished_at = datetime(2026, 4, 25, 3, 8, 0)
    db.add(job)
    db.commit()

    rec = db.query(SignalGenerationJob).one()
    assert rec.status == "failed"
    assert "Traceback" in rec.error_message
    assert rec.finished_at == datetime(2026, 4, 25, 3, 8, 0)


def test_snapshot_persists_json_blobs(db):
    job = _make_job(status="done")
    db.add(job)
    db.commit()

    snap = SignalSnapshot(
        snapshot_date=date(2026, 4, 25),
        market_context={
            "market_state": "STRUCTURAL_BULL",
            "market_state_reason": "VIX 偏低、台指期站上月線",
            "vix": 14.2,
        },
        watchlist=[
            {
                "stock_id": "2330",
                "stock_name": "台積電",
                "category": "LEADER",
                "decision": "WATCH",
                "reason": "AI 伺服器主軸延續，外資連 5 買 ...",
            },
        ],
        removed=[],
        summary={"leader": 1, "follower": 0, "laggard": 0},
        candidate_pool_size=68,
        final_watchlist_size=1,
        llm_model="gpt-4o-search-preview",
        llm_total_tokens=42000,
        job_id=job.job_id,
    )
    db.add(snap)
    db.commit()

    rec = db.query(SignalSnapshot).one()
    assert rec.snapshot_date == date(2026, 4, 25)
    assert rec.market_context["market_state"] == "STRUCTURAL_BULL"
    assert rec.watchlist[0]["category"] == "LEADER"
    assert rec.removed == []
    assert rec.summary["leader"] == 1
    assert rec.candidate_pool_size == 68
    assert rec.llm_model == "gpt-4o-search-preview"
    assert rec.job_id == job.job_id
    assert rec.generated_at is not None


def test_snapshot_unique_per_date(db):
    snap1 = SignalSnapshot(
        snapshot_date=date(2026, 4, 25),
        market_context={"market_state": "RANGE"},
        watchlist=[],
        removed=[],
        summary={"leader": 0, "follower": 0, "laggard": 0},
    )
    db.add(snap1)
    db.commit()

    snap2 = SignalSnapshot(
        snapshot_date=date(2026, 4, 25),
        market_context={"market_state": "WEAK"},
        watchlist=[],
        removed=[],
        summary={"leader": 0, "follower": 0, "laggard": 0},
    )
    db.add(snap2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_snapshot_job_id_nullable_for_legacy_or_orphan(db):
    """job_id 可為 NULL — snapshot 可能比 job table 早出現（手動 seed）或 job 被清掉。"""
    snap = SignalSnapshot(
        snapshot_date=date(2026, 4, 24),
        market_context={"market_state": "RANGE"},
        watchlist=[],
        removed=[],
        summary={"leader": 0, "follower": 0, "laggard": 0},
        job_id=None,
    )
    db.add(snap)
    db.commit()

    rec = db.query(SignalSnapshot).one()
    assert rec.job_id is None


def test_signal_watch_hit_persists_reason_and_json_fields(db):
    rec = SignalWatchHit(
        snapshot_date=date(2026, 4, 25),
        stock_id="2330",
        stock_name="台積電",
        signal_type="LEADER",
        industry_name="半導體業",
        sub_industry="晶圓代工",
        business_summary="晶圓代工龍頭",
        reason="AI 主線延續，外資續買。",
        theme={"main_theme": "AI"},
        group_info={"is_group_stock": False},
        leader_check={"industry_leader": "2330"},
        signals={"capital_flow": "strong"},
        baseline_trade_date=date(2026, 4, 28),
        baseline_price=105.0,
        latest_eval_trade_date=date(2026, 4, 29),
        latest_eval_price=121.0,
        return_pct=15.2381,
    )
    db.add(rec)
    db.commit()

    got = db.query(SignalWatchHit).one()
    assert got.stock_id == "2330"
    assert got.signal_type == "LEADER"
    assert got.reason == "AI 主線延續，外資續買。"
    assert got.theme["main_theme"] == "AI"
    assert got.baseline_trade_date == date(2026, 4, 28)
    assert got.baseline_price == 105.0
    assert got.latest_eval_trade_date == date(2026, 4, 29)
    assert got.latest_eval_price == 121.0
    assert got.return_pct == pytest.approx(15.2381)
    assert got.created_at is not None
