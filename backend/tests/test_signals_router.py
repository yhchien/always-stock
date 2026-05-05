"""M23 slice 7 — `/api/signals/*` endpoints 測試。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    DailyPrice,
    InstStockFlow,
    SignalGenerationJob,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
    User,
)
from app.routers import signals as signals_router


TAIPEI_TZ = ZoneInfo("Asia/Taipei")


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    # 攔截背景 pipeline，避免真的去呼叫 OpenAI / SessionLocal（測試環境的 SessionLocal
    # 並未連到此 in-memory engine）
    pipeline_calls: list = []

    def fake_pipeline(job_id, target_date):
        pipeline_calls.append((job_id, target_date))

    monkeypatch.setattr(signals_router, "_run_pipeline_safely", fake_pipeline)

    client = TestClient(app)
    yield client, session, pipeline_calls
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _register_login(client: TestClient, email: str = "alice@example.com") -> None:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": "passw0rd!"},
    )
    assert res.status_code == 200, res.text


def _seed_snapshot(
    db,
    snapshot_date: date,
    *,
    market_state: str = "STRONG_BULL",
    job_id: Optional[str] = None,
) -> SignalSnapshot:
    snap = SignalSnapshot(
        snapshot_date=snapshot_date,
        market_context={"market_state": market_state},
        watchlist=[{"stock_id": "2330", "decision": "WATCH"}],
        removed=[],
        summary={"watch_count": 1},
        candidate_pool_size=80,
        final_watchlist_size=1,
        llm_model="gpt-4o-search-preview",
        llm_total_tokens=1234,
        generated_at=datetime(2026, 4, 25, 3, 12, 45),
        job_id=job_id,
    )
    db.add(snap)
    db.commit()
    return snap


def _seed_job(
    db,
    job_id: str,
    snapshot_date: date,
    *,
    triggered_by: str = "cron",
    status: str = "running",
    started_at: Optional[datetime] = None,
) -> SignalGenerationJob:
    job = SignalGenerationJob(
        job_id=job_id,
        snapshot_date=snapshot_date,
        triggered_by=triggered_by,
        status=status,
        current_stage="llm_explain",
        progress_pct=65,
        progress_label="正在分析第 28 / 45 檔",
        started_at=started_at or datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    return job


def _seed_trade_date(
    db,
    trade_date: date,
    *,
    stock_id: str = "2330",
) -> InstStockFlow:
    row = InstStockFlow(
        trade_date=trade_date,
        stock_id=stock_id,
        inst_type="foreign",
        buy_shares=1000,
        sell_shares=0,
        net_shares=1000,
        buy_amount_est=1000000,
        sell_amount_est=0,
        net_amount_est=1000000,
    )
    db.add(row)
    db.commit()
    return row


def _seed_daily_price(
    db,
    stock_id: str,
    trade_date: date,
    *,
    open_price: Optional[float] = None,
    close_price: Optional[float] = None,
) -> DailyPrice:
    row = DailyPrice(
        stock_id=stock_id,
        trade_date=trade_date,
        open_price=open_price,
        close_price=close_price,
        high_price=close_price,
        low_price=close_price,
        volume=1000,
        turnover=1000000,
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# /latest
# ---------------------------------------------------------------------------


def test_latest_returns_404_when_empty(api):
    client, _, _ = api
    res = client.get("/api/signals/latest")
    assert res.status_code == 404
    assert res.json()["detail"] == "No snapshot yet"


def test_latest_returns_most_recent_snapshot(api):
    client, db, _ = api
    _seed_snapshot(db, date(2026, 4, 24), market_state="RANGE")
    _seed_snapshot(db, date(2026, 4, 25), market_state="STRONG_BULL")

    res = client.get("/api/signals/latest")
    assert res.status_code == 200
    body = res.json()
    assert body["snapshot_date"] == "2026-04-25"
    assert body["llm_model"] == "gpt-4o-search-preview"
    assert body["data"]["market_context"]["market_state"] == "STRONG_BULL"
    assert body["data"]["watchlist"][0]["stock_id"] == "2330"
    assert body["data"]["candidate_pool_size"] == 80
    assert body["data"]["final_watchlist_size"] == 1


# ---------------------------------------------------------------------------
# /snapshot/{date}
# ---------------------------------------------------------------------------


def test_snapshot_by_date_404_when_unknown(api):
    client, _, _ = api
    res = client.get("/api/signals/snapshot/2026-04-25")
    assert res.status_code == 404


def test_snapshot_by_date_returns_match(api):
    client, db, _ = api
    _seed_snapshot(db, date(2026, 4, 25))
    res = client.get("/api/signals/snapshot/2026-04-25")
    assert res.status_code == 200
    body = res.json()
    assert body["snapshot_date"] == "2026-04-25"


def test_snapshot_invalid_date_format_returns_422(api):
    client, _, _ = api
    res = client.get("/api/signals/snapshot/not-a-date")
    # FastAPI 對 path param 型別解析失敗會回 422
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# /jobs/latest
# ---------------------------------------------------------------------------


def test_jobs_latest_returns_null_when_empty(api):
    client, _, _ = api
    res = client.get("/api/signals/jobs/latest")
    assert res.status_code == 200
    assert res.json() is None


def test_jobs_latest_returns_most_recent(api):
    client, db, _ = api
    _seed_job(
        db,
        "job-old",
        date(2026, 4, 24),
        started_at=datetime(2026, 4, 24, 3, 0, 0),
    )
    _seed_job(
        db,
        "job-new",
        date(2026, 4, 25),
        started_at=datetime(2026, 4, 25, 3, 0, 0),
    )
    res = client.get("/api/signals/jobs/latest")
    assert res.status_code == 200
    body = res.json()
    assert body["job_id"] == "job-new"
    assert body["status"] == "running"
    assert body["progress_pct"] == 65
    assert body["current_stage"] == "llm_explain"


# ---------------------------------------------------------------------------
# /regenerate
# ---------------------------------------------------------------------------


def test_regenerate_requires_login(api):
    client, _, _ = api
    res = client.post("/api/signals/regenerate")
    assert res.status_code == 401


def test_regenerate_happy_path_202_and_kicks_off_background(api):
    client, db, calls = api
    _seed_trade_date(db, date(2026, 4, 25))
    _register_login(client)

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["snapshot_date"] == "2026-04-25"
    assert "job_id" in body and body["job_id"]

    # 確認 DB 有 pending job 且 background task 已被排程
    job = db.query(SignalGenerationJob).filter_by(job_id=body["job_id"]).first()
    assert job is not None
    assert job.status == "pending"
    assert job.triggered_by.startswith("user:")
    assert job.snapshot_date == date(2026, 4, 25)
    assert calls == [(body["job_id"], date(2026, 4, 25))]


def test_run_pipeline_safely_refreshes_archive_returns(monkeypatch):
    calls = []

    class DummySession:
        def close(self):
            calls.append(("close",))

    dummy_session = DummySession()

    def fake_pipeline(job_id, target_date, session_factory):
        calls.append(("pipeline", job_id, target_date, session_factory))

    def fake_update(db, *, as_of_trade_date=None):
        calls.append(("returns", db, as_of_trade_date))
        return 1

    fake_session_factory = lambda: dummy_session

    monkeypatch.setattr(signals_router, "run_signal_pipeline_sync", fake_pipeline)
    monkeypatch.setattr(signals_router, "SessionLocal", fake_session_factory)
    monkeypatch.setattr(
        signals_router.signal_archive,
        "update_signal_watch_returns",
        fake_update,
    )

    signals_router._run_pipeline_safely("job-1", date(2026, 4, 25))

    assert calls == [
        ("pipeline", "job-1", date(2026, 4, 25), fake_session_factory),
        ("returns", dummy_session, date(2026, 4, 25)),
        ("close",),
    ]


def test_regenerate_returns_409_when_running_job_exists_same_date(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))
    _seed_job(db, "running-job", date(2026, 4, 25), status="running")
    _register_login(client)

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 409
    assert "產生中" in res.json()["detail"]


def test_regenerate_429_when_user_already_triggered_today(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))
    _register_login(client)

    limit = signals_router.USER_DAILY_REGENERATE_LIMIT

    # 跑完 user 額度上限：每次成功 202 後手動把 job 標 done，繞過 409 concurrency guard
    for _ in range(limit):
        res = client.post("/api/signals/regenerate")
        assert res.status_code == 202
        job_id = res.json()["job_id"]
        job = db.query(SignalGenerationJob).filter_by(job_id=job_id).first()
        job.status = "done"
        job.finished_at = datetime.utcnow()
        db.commit()

    # 額度用滿後下一次 → 429
    over = client.post("/api/signals/regenerate")
    assert over.status_code == 429
    assert "今日" in over.json()["detail"]


def test_regenerate_failed_job_does_not_count_toward_user_limit(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))
    _register_login(client)

    _seed_job(
        db,
        "failed-user-job",
        date(2026, 4, 25),
        triggered_by="user:1",
        status="failed",
    )

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 202


def test_regenerate_429_when_global_count_exhausted(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))

    limit = signals_router.GLOBAL_DAILY_REGENERATE_LIMIT
    # 預埋 N 筆 cron job 撐滿全站額度（已完成才不卡 concurrency guard）
    for i in range(limit):
        _seed_job(
            db,
            f"cron-{i}",
            date(2026, 4, 25),
            triggered_by="cron",
            status="done",
        )

    _register_login(client, email="bob@example.com")
    res = client.post("/api/signals/regenerate")
    assert res.status_code == 429
    assert "全站" in res.json()["detail"]


def test_regenerate_uses_today_when_no_trade_data_or_snapshot(api, monkeypatch):
    """DB 完全空 → fallback target_date = 今天。"""
    client, db, calls = api
    _register_login(client)

    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 4, 29, 20, 0, 0, tzinfo=TAIPEI_TZ),
    )

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 202
    body = res.json()
    assert body["snapshot_date"] == "2026-04-29"
    job_id = body["job_id"]
    assert calls == [(job_id, date(2026, 4, 29))]


def test_regenerate_quota_requires_login(api):
    client, _, _ = api
    res = client.get("/api/signals/quota")
    assert res.status_code == 401


def test_regenerate_quota_returns_remaining_count_and_excludes_failed(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))
    _register_login(client)

    _seed_job(db, "user-done", date(2026, 4, 25), triggered_by="user:1", status="done")
    _seed_job(db, "user-running", date(2026, 4, 25), triggered_by="user:1", status="running")
    _seed_job(db, "user-failed", date(2026, 4, 25), triggered_by="user:1", status="failed")

    res = client.get("/api/signals/quota")
    assert res.status_code == 200
    body = res.json()
    assert body["snapshot_date"] == "2026-04-25"
    assert body["daily_limit"] == 3
    assert body["used_count"] == 2
    assert body["remaining_count"] == 1
    assert body["disabled"] is False


def test_regenerate_quota_disables_when_limit_reached(api):
    client, db, _ = api
    _seed_trade_date(db, date(2026, 4, 25))
    _register_login(client)

    for i in range(signals_router.USER_DAILY_REGENERATE_LIMIT):
        _seed_job(
            db,
            f"user-counted-{i}",
            date(2026, 4, 25),
            triggered_by="user:1",
            status="done",
        )

    res = client.get("/api/signals/quota")
    assert res.status_code == 200
    body = res.json()
    assert body["used_count"] == 3
    assert body["remaining_count"] == 0
    assert body["disabled"] is True


def test_archive_summary_returns_aggregated_rows_and_return_pct(api, monkeypatch):
    client, db, _ = api
    db.add_all(
        [
            SignalWatchHit(
                snapshot_date=date(2026, 4, 24),
                stock_id="2330",
                stock_name="台積電",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="晶圓代工",
                business_summary="晶圓代工龍頭",
                reason="第一次",
                theme={},
                group_info={},
                leader_check={},
                signals={},
            ),
            SignalWatchHit(
                snapshot_date=date(2026, 4, 28),
                stock_id="2330",
                stock_name="台積電",
                signal_type="FOLLOWER",
                industry_name="半導體業",
                sub_industry="晶圓代工",
                business_summary="晶圓代工龍頭",
                reason="第二次",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 25),
                baseline_price=105.0,
                latest_eval_trade_date=date(2026, 4, 29),
                latest_eval_price=121.0,
                return_pct=(121.0 - 105.0) / 105.0 * 100.0,
            ),
        ]
    )
    db.commit()

    monkeypatch.setattr(
        signals_router.signal_archive,
        "resolve_archive_as_of_trade_date",
        lambda db, now=None: date(2026, 4, 29),
    )

    res = client.get("/api/signals/archive")
    assert res.status_code == 200
    body = res.json()
    assert body["as_of_trade_date"] == "2026-04-29"
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["stock_id"] == "2330"
    assert item["tracking_day_index"] >= 1
    assert item["hit_count"] == 2
    assert item["latest_signal_type"] == "FOLLOWER"
    assert item["baseline_trade_date"] == "2026-04-25"
    assert item["baseline_price"] == 105.0
    assert item["latest_eval_trade_date"] == "2026-04-29"
    assert item["latest_eval_price"] == 121.0
    assert round(item["return_pct"], 2) == round((121.0 - 105.0) / 105.0 * 100.0, 2)


def test_archive_detail_returns_reports_in_desc_date_order(api, monkeypatch):
    client, db, _ = api
    db.add_all(
        [
            SignalWatchHit(
                snapshot_date=date(2026, 4, 24),
                stock_id="2454",
                stock_name="聯發科",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="IC 設計",
                business_summary="第一次摘要",
                reason="第一次報告",
                theme={},
                group_info={},
                leader_check={},
                signals={},
            ),
            SignalWatchHit(
                snapshot_date=date(2026, 4, 28),
                stock_id="2454",
                stock_name="聯發科",
                signal_type="FOLLOWER",
                industry_name="半導體業",
                sub_industry="IC 設計",
                business_summary="第二次摘要",
                reason="第二次報告",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 25),
                baseline_price=205.0,
                latest_eval_trade_date=date(2026, 4, 29),
                latest_eval_price=215.0,
                return_pct=(215.0 - 205.0) / 205.0 * 100.0,
            ),
        ]
    )
    db.commit()

    monkeypatch.setattr(
        signals_router.signal_archive,
        "resolve_archive_as_of_trade_date",
        lambda db, now=None: date(2026, 4, 29),
    )

    res = client.get("/api/signals/archive/2454")
    assert res.status_code == 200
    body = res.json()
    assert body["stock_id"] == "2454"
    assert body["hit_count"] == 2
    assert [report["snapshot_date"] for report in body["reports"]] == ["2026-04-28", "2026-04-24"]
    assert body["reports"][0]["reason"] == "第二次報告"


def test_archive_detail_returns_404_when_missing(api):
    client, _, _ = api
    res = client.get("/api/signals/archive/9999")
    assert res.status_code == 404


def test_completed_archive_summary_returns_rows(api):
    client, db, _ = api
    db.add(
        SignalWatchCompletedArchive(
            stock_id="2454",
            stock_name="聯發科",
            industry_name="半導體業",
            sub_industry="IC 設計",
            first_seen_date=date(2026, 3, 3),
            latest_hit_date=date(2026, 3, 21),
            hit_count=3,
            latest_signal_type="FOLLOWER",
            baseline_trade_date=date(2026, 3, 4),
            baseline_price=205.0,
            return_day_10_pct=2.5,
            return_day_20_pct=4.5,
            return_day_30_pct=6.5,
            return_day_40_pct=8.5,
            completed_trade_date=date(2026, 4, 29),
        )
    )
    db.commit()

    res = client.get("/api/signals/archive/completed")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["stock_id"] == "2454"
    assert item["stock_name"] == "聯發科"
    assert item["first_seen_date"] == "2026-03-03"
    assert item["hit_count"] == 3
    assert item["return_day_40_pct"] == 8.5
    assert item["completed_trade_date"] == "2026-04-29"


# ---------------------------------------------------------------------------
# helper unit tests
# ---------------------------------------------------------------------------


def test_resolve_target_date_uses_same_day_trade_date_after_1900(api, monkeypatch):
    _, db, _ = api
    _seed_trade_date(db, date(2026, 4, 28))
    _seed_trade_date(db, date(2026, 4, 29), stock_id="2317")
    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 4, 29, 20, 0, 0, tzinfo=TAIPEI_TZ),
    )
    assert signals_router._resolve_target_date(db) == date(2026, 4, 29)


def test_resolve_target_date_uses_previous_trade_date_before_1900(api, monkeypatch):
    _, db, _ = api
    _seed_trade_date(db, date(2026, 4, 28))
    _seed_trade_date(db, date(2026, 4, 29), stock_id="2317")
    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 4, 29, 18, 59, 0, tzinfo=TAIPEI_TZ),
    )
    assert signals_router._resolve_target_date(db) == date(2026, 4, 28)


def test_resolve_target_date_falls_back_to_previous_available_trade_date_on_holiday(api, monkeypatch):
    _, db, _ = api
    _seed_trade_date(db, date(2026, 4, 28))
    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 5, 2, 20, 0, 0, tzinfo=TAIPEI_TZ),
    )
    assert signals_router._resolve_target_date(db) == date(2026, 4, 28)


def test_resolve_target_date_falls_back_to_latest_snapshot_when_trade_data_empty(api, monkeypatch):
    _, db, _ = api
    _seed_snapshot(db, date(2026, 4, 25))
    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 4, 29, 20, 0, 0, tzinfo=TAIPEI_TZ),
    )
    assert signals_router._resolve_target_date(db) == date(2026, 4, 25)


def test_resolve_target_date_falls_back_to_today_when_empty(api, monkeypatch):
    _, db, _ = api
    monkeypatch.setattr(
        signals_router,
        "_get_taipei_now",
        lambda: datetime(2026, 4, 29, 20, 0, 0, tzinfo=TAIPEI_TZ),
    )
    assert signals_router._resolve_target_date(db) == date(2026, 4, 29)
