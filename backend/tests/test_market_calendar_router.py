from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, MarketCalendarPrediction


def test_market_calendar_history_returns_latest_ten_and_accuracy():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.utcnow()
    for index in range(12):
        db.add(
            MarketCalendarPrediction(
                signal_date=date(2026, 1, index + 1),
                target_date=date(2026, 1, index + 2),
                predicted_binary="偏漲",
                predicted_return=1.0,
                actual_binary="偏漲" if index < 10 else "偏跌",
                actual_return=1.0 if index < 10 else -1.0,
                result_status="correct" if index < 10 else "incorrect",
                model_version="test",
                generated_at=now,
                updated_at=now,
            )
        )
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/market-calendar/history?limit=10")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["items"]) == 10
        assert payload["available"] == 12
        assert payload["evaluated"] == 12
        assert payload["correct"] == 10
        assert payload["accuracy"] == 10 / 12
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(bind=engine)
