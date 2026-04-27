"""M23 slice 7 — `/api/signals/*` 4 endpoints 測試。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, SignalGenerationJob, SignalSnapshot
from app.routers import signals as signals_router


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
        removed=[{"stock_id": "9999", "reason": "ETF"}],
        summary={"watch_count": 1, "remove_count": 1},
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
    _seed_snapshot(db, date(2026, 4, 25))
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


def test_regenerate_returns_409_when_running_job_exists_same_date(api):
    client, db, _ = api
    _seed_snapshot(db, date(2026, 4, 25))
    _seed_job(db, "running-job", date(2026, 4, 25), status="running")
    _register_login(client)

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 409
    assert "產生中" in res.json()["detail"]


def test_regenerate_429_when_user_already_triggered_today(api):
    client, db, _ = api
    _seed_snapshot(db, date(2026, 4, 25))
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


def test_regenerate_429_when_global_count_exhausted(api):
    client, db, _ = api
    _seed_snapshot(db, date(2026, 4, 25))

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


def test_regenerate_uses_today_when_db_has_no_snapshot(api, monkeypatch):
    """DB 完全空 → fallback target_date = today。"""
    client, db, calls = api
    _register_login(client)

    res = client.post("/api/signals/regenerate")
    assert res.status_code == 202
    body = res.json()
    assert body["snapshot_date"] == date.today().isoformat()
    job_id = body["job_id"]
    assert calls == [(job_id, date.today())]


# ---------------------------------------------------------------------------
# helper unit tests
# ---------------------------------------------------------------------------


def test_resolve_target_date_picks_latest_snapshot(api):
    _, db, _ = api
    _seed_snapshot(db, date(2026, 4, 24))
    _seed_snapshot(db, date(2026, 4, 25))
    assert signals_router._resolve_target_date(db) == date(2026, 4, 25)


def test_resolve_target_date_falls_back_to_today_when_empty(api):
    _, db, _ = api
    assert signals_router._resolve_target_date(db) == date.today()
