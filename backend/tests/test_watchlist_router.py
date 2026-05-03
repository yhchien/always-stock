"""Tests for /api/watchlist endpoints (M19; 2026-05-03 簡化後)."""

from __future__ import annotations

from datetime import date, datetime

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


def _seed_price(
    db,
    stock_id: str,
    trade_date: date,
    *,
    open_price: float = 100.0,
    close_price: float = 120.0,
) -> None:
    db.add(
        DailyPrice(
            trade_date=trade_date,
            stock_id=stock_id,
            open_price=open_price,
            close_price=close_price,
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
    assert client.post("/api/watchlist", json={"stock_id": "2330"}).status_code == 401
    assert client.delete("/api/watchlist").status_code == 401
    assert client.delete("/api/watchlist/1").status_code == 401


def test_add_entry_returns_joined_data_without_legacy_fields(api):
    """POST 只帶 stock_id；response 不含 buy_date/avg_price/unrealized_pct。"""
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _register_and_login(client)

    res = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert res.status_code == 201, res.text
    body = res.json()
    # 對外回應仍含基本資訊與「最新收盤」（純股價資訊，跟買入無關）
    assert body["stock_id"] == "2330"
    assert body["stock_name"] == "台積電"
    assert body["industry_name"] == "半導體業"
    assert body["latest_close"] == 985.0
    assert body["latest_trade_date"] == "2026-04-22"
    # 三個 legacy 欄位永遠不在 response 裡
    assert "buy_date" not in body
    assert "avg_price" not in body
    assert "unrealized_pct" not in body


def test_add_auto_fills_buy_date_and_avg_price(api, monkeypatch):
    """後端自動填：buy_date=台北今天、avg_price=(open+close)/2。"""
    from app.routers import watchlist as watchlist_module

    client, db = api
    _seed_stock(db, "2330", "台積電")
    # 兩天的價格，POST handler 應取最新一筆（4/22）的 (open+close)/2 = (980+1000)/2 = 990
    _seed_price(db, "2330", date(2026, 4, 21), open_price=950.0, close_price=970.0)
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=1000.0)
    _register_and_login(client)

    monkeypatch.setattr(watchlist_module, "_today_taipei", lambda: date(2026, 4, 23))
    res = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert res.status_code == 201, res.text

    # 雖然 response 不暴露 buy_date / avg_price，DB 內仍有自動填好的值
    row = db.query(UserWatchlist).filter(UserWatchlist.stock_id == "2330").one()
    assert row.buy_date == date(2026, 4, 23)
    assert row.avg_price == pytest.approx(990.0)


def test_add_rejects_when_no_price_data(api):
    """daily_price 完全沒資料時 → 無法算均價 → 400 reject。"""
    client, db = api
    _seed_stock(db, "2330", "台積電")
    # 不 seed daily_price
    _register_and_login(client)

    res = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert res.status_code == 400
    assert "價格資料" in res.json()["detail"]


def test_add_rejects_unknown_stock(api):
    client, _ = api
    _register_and_login(client)
    res = client.post("/api/watchlist", json={"stock_id": "9999"})
    assert res.status_code == 404


def test_add_rejects_duplicate_stock(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _register_and_login(client)

    first = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert first.status_code == 201
    second = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert second.status_code == 409


def test_add_rejects_when_over_capacity(api):
    client, db = api
    _register_and_login(client)

    for i in range(WATCHLIST_MAX_ENTRIES):
        stock_id = f"10{i:02d}"
        _seed_stock(db, stock_id, f"Stock{i}")
        _seed_price(db, stock_id, date(2026, 4, 22), open_price=100.0, close_price=120.0)
        res = client.post("/api/watchlist", json={"stock_id": stock_id})
        assert res.status_code == 201, res.text

    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    res = client.post("/api/watchlist", json={"stock_id": "2330"})
    assert res.status_code == 409
    assert "上限" in res.json()["detail"]


def test_list_returns_entries_ordered_by_creation(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _seed_price(db, "2303", date(2026, 4, 22), open_price=48.0, close_price=52.0)
    _register_and_login(client)

    client.post("/api/watchlist", json={"stock_id": "2330"})
    client.post("/api/watchlist", json={"stock_id": "2303"})

    res = client.get("/api/watchlist")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert [i["stock_id"] for i in body["items"]] == ["2330", "2303"]


def test_delete_single_entry(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _register_and_login(client)

    created = client.post("/api/watchlist", json={"stock_id": "2330"})
    entry_id = created.json()["id"]

    res = client.delete(f"/api/watchlist/{entry_id}")
    assert res.status_code == 204
    assert client.get("/api/watchlist").json()["total"] == 0


def test_delete_single_entry_also_deletes_trade_quality_snapshots(api, monkeypatch):
    """刪 entry 時對應的 trade quality snapshot（同 user/stock/buy_date）也要清除。"""
    from app.routers import watchlist as watchlist_module

    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _register_and_login(client)
    monkeypatch.setattr(watchlist_module, "_today_taipei", lambda: date(2026, 4, 23))

    created = client.post("/api/watchlist", json={"stock_id": "2330"})
    entry = created.json()
    # 用 entry 真實的 buy_date（自動填的台北今天）建立 snapshot 才會被刪掉
    db.add(
        WatchlistTradeQualitySnapshot(
            user_id=1,
            stock_id="2330",
            buy_date=date(2026, 4, 23),
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

    # 直接建立另一個使用者的 entry（DB 仍有 buy_date / avg_price 兩個 NOT NULL column）
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
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _seed_price(db, "2303", date(2026, 4, 22), open_price=48.0, close_price=52.0)
    _register_and_login(client)

    client.post("/api/watchlist", json={"stock_id": "2330"})
    client.post("/api/watchlist", json={"stock_id": "2303"})

    res = client.delete("/api/watchlist")
    assert res.status_code == 204
    assert client.get("/api/watchlist").json()["total"] == 0


def test_clear_watchlist_also_deletes_trade_quality_snapshots(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2303", "聯電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _seed_price(db, "2303", date(2026, 4, 22), open_price=48.0, close_price=52.0)
    _register_and_login(client)

    client.post("/api/watchlist", json={"stock_id": "2330"})
    client.post("/api/watchlist", json={"stock_id": "2303"})

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
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)

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
    client.post("/api/watchlist", json={"stock_id": "2330"})

    res = client.delete("/api/watchlist")
    assert res.status_code == 204
    # 別人的 entry 還在
    assert db.query(UserWatchlist).filter(UserWatchlist.user_id == other.id).count() == 1


def test_list_response_does_not_expose_legacy_fields(api):
    """GET /api/watchlist response 永遠不含 buy_date/avg_price/unrealized_pct。"""
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_price(db, "2330", date(2026, 4, 22), open_price=980.0, close_price=985.0)
    _register_and_login(client)
    client.post("/api/watchlist", json={"stock_id": "2330"})

    res = client.get("/api/watchlist")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    for legacy in ("buy_date", "avg_price", "unrealized_pct"):
        assert legacy not in item
