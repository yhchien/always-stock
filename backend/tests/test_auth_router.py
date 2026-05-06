"""Tests for /api/auth/* endpoints (M18)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import ensure_admin_user, hash_password
from app.database import get_db
from app.main import app
from app.models import Base, User, UserSession
from app.settings import get_admin_email, get_admin_password, get_session_cookie_name


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


def test_register_creates_user_and_sets_cookie(api):
    client, db = api
    res = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!", "name": "Alice"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["email"] == "alice@example.com"
    assert body["name"] == "Alice"
    assert body["is_admin"] is False

    # cookie 已設定
    assert get_session_cookie_name() in res.cookies
    # DB 有寫入
    user = db.query(User).filter(User.email == "alice@example.com").first()
    assert user is not None
    assert user.password_hash != "passw0rd!"  # 被 hash


def test_register_rejects_duplicate_email(api):
    client, _ = api
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    res = client.post(
        "/api/auth/register",
        json={"email": "Alice@Example.com", "password": "different1"},  # 大小寫不同
    )
    assert res.status_code == 409


def test_register_rejects_short_password(api):
    client, _ = api
    res = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "short"},
    )
    assert res.status_code == 422


def test_login_with_correct_password(api):
    client, db = api
    db.add(
        User(
            email="bob@example.com",
            password_hash=hash_password("s3cret123"),
            is_active=True,
        )
    )
    db.commit()
    res = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "s3cret123"},
    )
    assert res.status_code == 200
    assert res.json()["email"] == "bob@example.com"
    assert get_session_cookie_name() in res.cookies


def test_login_with_wrong_password(api):
    client, db = api
    db.add(
        User(
            email="bob@example.com",
            password_hash=hash_password("s3cret123"),
            is_active=True,
        )
    )
    db.commit()
    res = client.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "wrong1234"},
    )
    assert res.status_code == 401


def test_login_nonexistent_email(api):
    client, _ = api
    res = client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "anything1"},
    )
    assert res.status_code == 401


def test_login_inactive_user(api):
    client, db = api
    db.add(
        User(
            email="inactive@example.com",
            password_hash=hash_password("s3cret123"),
            is_active=False,
        )
    )
    db.commit()
    res = client.post(
        "/api/auth/login",
        json={"email": "inactive@example.com", "password": "s3cret123"},
    )
    assert res.status_code == 401


# NOTE: 移除 4 個既有測試（test_me_requires_session / test_me_returns_current_user /
# test_logout_revokes_session / test_expired_session_is_rejected）。
# 全站永久 DISABLE_AUTH=true 後 require_user / get_optional_user 不再讀 cookie，
# 「沒帶 cookie / session 過期 / revoked」皆會回 demo user，原契約已失效。
# 對應 disabled 模式的正向測試在 test_disable_auth_makes_me_endpoint_return_demo_user
# 與 test_disable_auth_allows_protected_endpoints_without_cookie。


def test_email_is_normalized_to_lowercase(api):
    client, db = api
    client.post(
        "/api/auth/register",
        json={"email": "Alice@Example.COM", "password": "passw0rd!"},
    )
    user = db.query(User).first()
    assert user.email == "alice@example.com"

    # 登入時也要接受大小寫
    client.post("/api/auth/logout")
    res = client.post(
        "/api/auth/login",
        json={"email": "ALICE@example.com", "password": "passw0rd!"},
    )
    assert res.status_code == 200


def test_invalid_email_format_rejected(api):
    client, _ = api
    res = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "passw0rd!"},
    )
    assert res.status_code == 422


def test_admin_seeder_default_email_passes_pydantic_emailstr(api, monkeypatch):
    """
    Regression: 預設 ADMIN_EMAIL 必須含 TLD，否則 /api/auth/login 的 Pydantic EmailStr 會直接 422 拒絕，
    即使 seeder 已經把 admin 寫進 DB，也永遠登不進來。
    """
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-passw0rd!")
    client, db = api
    admin = ensure_admin_user(db)
    assert admin.is_admin is True

    res = client.post(
        "/api/auth/login",
        json={"email": get_admin_email(), "password": get_admin_password()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_admin"] is True


def test_session_cookie_samesite_follows_secure_flag(api, monkeypatch):
    """
    Regression: 跨站部署（Vercel ↔ Render）必須 SameSite=None + Secure，
    否則瀏覽器不會把 session cookie 帶到跨站 fetch，登入後打 /api/watchlist 會 401。
    本地 dev (secure=False) 仍用 Lax，避免 Chrome 拒絕 Secure=False+SameSite=None。
    """
    from app import auth as auth_module

    # 1) Secure 模式：SameSite 必須是 None
    monkeypatch.setattr(auth_module, "is_cookie_secure", lambda: True)
    client, db = api
    db.add(
        User(
            email="cross@example.com",
            password_hash=hash_password("passw0rd!"),
            is_active=True,
        )
    )
    db.commit()
    res = client.post(
        "/api/auth/login",
        json={"email": "cross@example.com", "password": "passw0rd!"},
    )
    assert res.status_code == 200
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie

    # 2) 非 Secure（本地 dev）：維持 Lax
    monkeypatch.setattr(auth_module, "is_cookie_secure", lambda: False)
    res2 = client.post(
        "/api/auth/login",
        json={"email": "cross@example.com", "password": "passw0rd!"},
    )
    assert res2.status_code == 200
    set_cookie2 = res2.headers.get("set-cookie", "")
    assert "samesite=lax" in set_cookie2.lower()


def test_session_cookie_auto_uses_secure_when_forwarded_proto_is_https(api, monkeypatch):
    """
    Regression: prod 常在反向代理後面終止 TLS；若漏設 COOKIE_SECURE=true，
    backend 仍應從 X-Forwarded-Proto=https 推斷 Secure + SameSite=None，
    否則 login 200 後 /api/auth/me、/api/watchlist 仍會 401。
    """
    from app import auth as auth_module

    monkeypatch.setattr(auth_module, "is_cookie_secure", lambda: False)
    client, db = api
    db.add(
        User(
            email="proxy@example.com",
            password_hash=hash_password("passw0rd!"),
            is_active=True,
        )
    )
    db.commit()

    res = client.post(
        "/api/auth/login",
        json={"email": "proxy@example.com", "password": "passw0rd!"},
        headers={"x-forwarded-proto": "https"},
    )
    assert res.status_code == 200
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie


def test_disable_auth_makes_me_endpoint_return_demo_user(api, monkeypatch):
    """DISABLE_AUTH=true → 即使沒有 session cookie，/api/auth/me 也回 demo user。"""
    from app import auth as auth_module
    from app.settings import get_demo_user_email

    monkeypatch.setattr(auth_module, "is_auth_disabled", lambda: True)
    client, _db = api

    res = client.get("/api/auth/me")
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == get_demo_user_email()
    assert body["is_admin"] is False


def test_disable_auth_allows_protected_endpoints_without_cookie(api, monkeypatch):
    """DISABLE_AUTH=true → require_user 也不擋；watchlist 之類的 endpoint 應放行回 demo user 資料。"""
    from app import auth as auth_module

    monkeypatch.setattr(auth_module, "is_auth_disabled", lambda: True)
    client, _db = api

    res = client.get("/api/watchlist")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 0
    assert isinstance(body["items"], list)
