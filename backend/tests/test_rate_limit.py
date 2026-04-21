"""
Tests for the tiered rate limit on POST /api/analysis/trade-quality (M18).

匿名使用者：3 次 / day（by IP）
已登入使用者：30 次 / day（by user.id）
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import Base, DailyPrice, InstStockFlow, StockMaster, User
from app.rate_limit import limiter


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
    limiter.reset()
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _seed_minimal(db):
    """trade-quality endpoint 需要至少一檔股票 + 一天行情才能走完整流程。"""
    db.add(StockMaster(stock_id="2330", stock_name="台積電", industry_name="半導體"))
    db.add(
        DailyPrice(
            trade_date=date(2026, 4, 15),
            stock_id="2330",
            open_price=580,
            high_price=585,
            low_price=575,
            close_price=580,
            volume=1000,
            turnover=580000,
            spread=0,
        )
    )
    for inst in ("foreign", "trust", "dealer"):
        db.add(
            InstStockFlow(
                trade_date=date(2026, 4, 15),
                stock_id="2330",
                inst_type=inst,
                net_shares=0,
                net_amount_est=0,
            )
        )
    db.commit()


def _mock_openai_payload():
    return {
        "rating": "NEUTRAL",
        "summary": "測試用。",
        "target_price_low": 600,
        "target_price_high": 650,
        "classification": "B",
        "action": "觀望",
        "report_markdown": "# 測試\n內容。",
    }


@pytest.fixture
def mock_trade_quality_openai():
    with patch("app.routers.analysis._call_openai", return_value=_mock_openai_payload()), \
         patch("app.routers.analysis._load_system_prompt", return_value="test prompt"):
        yield


def _post(client):
    return client.post(
        "/api/analysis/trade-quality",
        json={"stock_id": "2330", "buy_date": "2026-04-15"},
    )


def test_anonymous_limited_to_three_per_day(api, mock_trade_quality_openai):
    client, db = api
    _seed_minimal(db)

    for i in range(3):
        res = _post(client)
        assert res.status_code == 200, f"request {i+1}: {res.text}"

    res = _post(client)
    assert res.status_code == 429


def test_authenticated_user_has_higher_limit(api, mock_trade_quality_openai):
    client, db = api
    _seed_minimal(db)

    # 註冊 + 自動登入（cookie 帶在 TestClient 內）
    reg = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    assert reg.status_code == 200

    # 登入使用者 4 次都不會被擋（已超過匿名上限）
    for i in range(4):
        res = _post(client)
        assert res.status_code == 200, f"request {i+1}: {res.text}"


def test_authenticated_and_anonymous_limits_are_separate_buckets(api, mock_trade_quality_openai):
    """驗證 user_id bucket 與 IP bucket 不互相干擾。"""
    client, db = api
    _seed_minimal(db)

    # 匿名用掉 3 次
    for _ in range(3):
        _post(client)
    assert _post(client).status_code == 429

    # 登入後就應該可以再用（切到 user bucket）
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    res = _post(client)
    assert res.status_code == 200
