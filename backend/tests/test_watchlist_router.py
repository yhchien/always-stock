"""Tests for /api/watchlist endpoints (M19)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import get_db
from app.main import app
from app.models import Base, DailyPrice, StockMaster, User, UserWatchlist, WatchlistTradeQualitySnapshot
from app.routers.watchlist import WATCHLIST_MAX_ENTRIES


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
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _register_and_login(client: TestClient, email: str = "alice@example.com") -> None:
    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": "passw0rd!"},
    )
    assert res.status_code == 200, res.text


def _seed_stock(db, stock_id: str, stock_name: str = "測試股", industry: str = "半導體業") -> None:
    db.add(
        StockMaster(
            stock_id=stock_id,
            stock_name=stock_name,
            industry_name=industry,
            is_active=True,
        )
    )
    db.commit()


def _seed_price(db, stock_id: str, trade_date: date, close: float) -> None:
    db.add(
        DailyPrice(
            trade_date=trade_date,
            stock_id=stock_id,
            close_price=close,
        )
    )
    db.commit()


def test_list_empty_when_no_entries(api):
    client, _ = api
    _register_and_login(client)
    res = client.get("/api/watchlist")
    assert res.status_code == 200
    body = res.json()
    assert body == {"items": [], "total": 0, "capacity": WATCHLIST_MAX_ENTRIES}


def test_requires_login(api):
    client, _ = api
    assert client.get("/api/watchlist").status_code == 401
    assert client.post("/api/watchlist", json={"stock_id": "2330", "buy_date": "2026-04-20", "avg_price": 800}).status_code == 401
    assert client.delete("/api/watchlist").status_code == 401
    assert client.delete("/api/watchlist/1").status_code == 401


def test_add_entry_returns_joined_data(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), 985.0)
    _register_and_login(client)

    res = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["stock_id"] == "2330"
    assert body["stock_name"] == "台積電"
    assert body["industry_name"] == "半導體業"
    assert body["avg_price"] == 900.0
    assert body["latest_close"] == 985.0
    assert body["latest_trade_date"] == "2026-04-22"
    # (985 - 900) / 900 * 100 ≈ 9.4444
    assert body["unrealized_pct"] == pytest.approx(9.4444, rel=1e-3)


def test_add_rejects_unknown_stock(api):
    client, _ = api
    _register_and_login(client)
    res = client.post(
        "/api/watchlist",
        json={"stock_id": "9999", "buy_date": "2026-04-01", "avg_price": 100.0},
    )
    assert res.status_code == 404


def test_add_rejects_duplicate_stock(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _register_and_login(client)

    first = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-02", "avg_price": 910.0},
    )
    assert second.status_code == 409


def test_add_rejects_when_over_capacity(api):
    client, db = api
    _register_and_login(client)

    for i in range(WATCHLIST_MAX_ENTRIES):
        stock_id = f"10{i:02d}"
        _seed_stock(db, stock_id, f"Stock{i}")
        res = client.post(
            "/api/watchlist",
            json={"stock_id": stock_id, "buy_date": "2026-04-01", "avg_price": 100.0},
        )
        assert res.status_code == 201, res.text

    _seed_stock(db, "2330", "台積電")
    res = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    assert res.status_code == 409
    assert "上限" in res.json()["detail"]


def test_list_returns_entries_ordered_by_creation(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _register_and_login(client)

    client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    client.post(
        "/api/watchlist",
        json={"stock_id": "2303", "buy_date": "2026-04-02", "avg_price": 50.0},
    )

    res = client.get("/api/watchlist")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert [i["stock_id"] for i in body["items"]] == ["2330", "2303"]


def test_delete_single_entry(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _register_and_login(client)

    created = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    entry_id = created.json()["id"]

    res = client.delete(f"/api/watchlist/{entry_id}")
    assert res.status_code == 204
    assert client.get("/api/watchlist").json()["total"] == 0


def test_delete_single_entry_also_deletes_trade_quality_snapshots(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _register_and_login(client)

    created = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    entry = created.json()
    db.add(
        WatchlistTradeQualitySnapshot(
            user_id=1,
            stock_id="2330",
            buy_date=date(2026, 4, 1),
            snapshot_trade_date=date(2026, 4, 30),
            rating="BUY",
            rating_label="推薦",
            source="cron",
            status="ok",
            generated_at=datetime.utcnow(),
        )
    )
    db.commit()

    res = client.delete(f"/api/watchlist/{entry['id']}")
    assert res.status_code == 204
    assert (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(WatchlistTradeQualitySnapshot.user_id == 1)
        .count()
    ) == 0


def test_delete_unknown_entry(api):
    client, _ = api
    _register_and_login(client)
    res = client.delete("/api/watchlist/99999")
    assert res.status_code == 404


def test_delete_another_users_entry_is_forbidden(api):
    """不能刪別人清單裡的股票（404 而非 500）"""
    client, db = api
    _seed_stock(db, "2330", "台積電")

    # 直接建立另一個使用者的 entry
    other = User(email="bob@example.com", password_hash=hash_password("passw0rd!"), is_active=True)
    db.add(other)
    db.commit()
    foreign_entry = UserWatchlist(
        user_id=other.id,
        stock_id="2330",
        buy_date=date(2026, 4, 1),
        avg_price=900.0,
        created_at=datetime.utcnow(),
    )
    db.add(foreign_entry)
    db.commit()

    _register_and_login(client, "alice@example.com")
    res = client.delete(f"/api/watchlist/{foreign_entry.id}")
    assert res.status_code == 404

    # 別人的 entry 沒被刪
    assert db.query(UserWatchlist).filter(UserWatchlist.id == foreign_entry.id).first() is not None


def test_clear_watchlist(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _register_and_login(client)

    client.post("/api/watchlist", json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0})
    client.post("/api/watchlist", json={"stock_id": "2303", "buy_date": "2026-04-02", "avg_price": 50.0})

    res = client.delete("/api/watchlist")
    assert res.status_code == 204
    assert client.get("/api/watchlist").json()["total"] == 0


def test_clear_watchlist_also_deletes_trade_quality_snapshots(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _register_and_login(client)

    client.post("/api/watchlist", json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0})
    client.post("/api/watchlist", json={"stock_id": "2303", "buy_date": "2026-04-02", "avg_price": 50.0})

    db.add_all(
        [
            WatchlistTradeQualitySnapshot(
                user_id=1,
                stock_id="2330",
                buy_date=date(2026, 4, 1),
                snapshot_trade_date=date(2026, 4, 30),
                rating="BUY",
                rating_label="推薦",
                source="cron",
                status="ok",
                generated_at=datetime.utcnow(),
            ),
            WatchlistTradeQualitySnapshot(
                user_id=1,
                stock_id="2303",
                buy_date=date(2026, 4, 2),
                snapshot_trade_date=date(2026, 4, 30),
                rating="WATCH",
                rating_label="再看看",
                source="cron",
                status="ok",
                generated_at=datetime.utcnow(),
            ),
        ]
    )
    db.commit()

    res = client.delete("/api/watchlist")
    assert res.status_code == 204
    assert (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(WatchlistTradeQualitySnapshot.user_id == 1)
        .count()
    ) == 0


def test_clear_only_affects_current_user(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")

    # 另一個使用者有一筆
    other = User(email="bob@example.com", password_hash=hash_password("passw0rd!"), is_active=True)
    db.add(other)
    db.commit()
    db.add(
        UserWatchlist(
            user_id=other.id,
            stock_id="2330",
            buy_date=date(2026, 4, 1),
            avg_price=900.0,
            created_at=datetime.utcnow(),
        )
    )
    db.commit()

    _register_and_login(client, "alice@example.com")
    client.post("/api/watchlist", json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 910.0})

    res = client.delete("/api/watchlist")
    assert res.status_code == 204
    # 別人的 entry 還在
    assert db.query(UserWatchlist).filter(UserWatchlist.user_id == other.id).count() == 1


def test_unrealized_pct_is_null_when_no_price(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    # 沒 seed daily_price
    _register_and_login(client)

    res = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 900.0},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["latest_close"] is None
    assert body["latest_trade_date"] is None
    assert body["unrealized_pct"] is None


def test_add_validates_avg_price_positive(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _register_and_login(client)

    res = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-01", "avg_price": 0.0},
    )
    assert res.status_code == 422


def test_add_rejects_future_buy_date(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _register_and_login(client)

    future = (date.today() + timedelta(days=1)).isoformat()
    res = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": future, "avg_price": 900.0},
    )
    assert res.status_code == 400
    assert "未來" in res.json()["detail"]


def test_add_buy_date_uses_taipei_timezone(api, monkeypatch):
    """Render server 跑 UTC；未來日期判斷必須以 Asia/Taipei 為準。
    模擬 UTC 深夜（台北已是新的一天）時，使用者選「台北今天」應 201 而不是 400。"""
    from app.routers import watchlist as watchlist_module

    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _register_and_login(client)

    # Freeze 台北今天為固定日期，切斷本機時區依賴
    monkeypatch.setattr(watchlist_module, "_today_taipei", lambda: date(2026, 4, 23))

    # 台北今天應可加入
    res_today = client.post(
        "/api/watchlist",
        json={"stock_id": "2330", "buy_date": "2026-04-23", "avg_price": 900.0},
    )
    assert res_today.status_code == 201, res_today.text

    # 台北明天仍應拒絕
    res_future = client.post(
        "/api/watchlist",
        json={"stock_id": "2303", "buy_date": "2026-04-24", "avg_price": 50.0},
    )
    assert res_future.status_code == 400
    assert "未來" in res_future.json()["detail"]
