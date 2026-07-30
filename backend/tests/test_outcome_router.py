from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, SignalOutcomeMetric


@pytest.fixture
def api():
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
    yield TestClient(app), session
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _metric(
    db,
    stock: str,
    decision: str,
    label: str,
    rank: int | None,
):
    db.add(
        SignalOutcomeMetric(
            signal_date=date(2026, 7, 1),
            stock_id=stock,
            stock_name=stock,
            asset_type="COMMON_STOCK",
            p3_decision=decision,
            global_eligible=True,
            recommendation_rank=rank,
            backend_priority_rank=rank or 2,
            entry_price=100,
            exit_price=110 if label == "WINNER" else 100,
            outcome_return_pct=10 if label == "WINNER" else 0,
            outcome_label=label,
            matured_at=date(2026, 7, 15),
            outcome_horizon="DAY10",
            outcome_definition_version="day10_v1",
            entry_price_definition="signal_date_close",
            exit_price_definition="tenth_subsequent_market_trade_date_close",
            prompt_family_version="v7",
            selection_version="v7_global_selector",
            metadata_json={},
        )
    )
    db.commit()


def test_outcome_api_summary_timeseries_items_and_csv(api):
    client, db = api
    _metric(db, "2330", "RECOMMEND", "WINNER", 1)
    _metric(db, "2454", "NOT_SELECTED", "NEUTRAL", None)

    summary = client.get("/api/signals/outcomes/summary")
    assert summary.status_code == 200
    assert summary.json()["sample"]["matured"] == 1
    assert summary.json()["selection"]["winner_recall"] == 1.0

    timeseries = client.get("/api/signals/outcomes/timeseries")
    assert timeseries.status_code == 200
    assert timeseries.json()["items"][0]["eligible"] == 2

    items = client.get(
        "/api/signals/outcomes/items?page=1&page_size=1&p3_decision=RECOMMEND"
    )
    assert items.status_code == 200
    assert items.json()["total"] == 1
    assert items.json()["items"][0]["stock"] == "2330"

    exported = client.get("/api/signals/outcomes/items?export=csv")
    assert exported.status_code == 200
    assert "signal_date,stock,name" in exported.text
    assert "2330" in exported.text


def test_outcome_api_empty_and_invalid_interval(api):
    client, _ = api
    empty = client.get("/api/signals/outcomes/summary")
    assert empty.status_code == 200
    assert empty.json()["sample"]["total"] == 0
    invalid = client.get(
        "/api/signals/outcomes/summary"
        "?start_date=2026-07-02&end_date=2026-07-01"
    )
    assert invalid.status_code == 422
