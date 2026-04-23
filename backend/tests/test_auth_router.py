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


def test_me_requires_session(api):
    client, _ = api
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_me_returns_current_user(api):
    client, _ = api
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json()["email"] == "alice@example.com"


def test_logout_revokes_session(api):
    client, db = api
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    # TestClient 會保留 cookie jar，但 session 已 revoked，/me 應回 401
    assert client.get("/api/auth/me").status_code == 401

    # DB 層面 revoked_at 已設定
    session_row = db.query(UserSession).first()
    assert session_row is not None
    assert session_row.revoked_at is not None


def test_expired_session_is_rejected(api):
    client, db = api
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "passw0rd!"},
    )
    # 手動把 expires_at 設到過去
    session_row = db.query(UserSession).first()
    session_row.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    assert client.get("/api/auth/me").status_code == 401


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


def test_admin_seeder_default_email_passes_pydantic_emailstr(api):
    """
    Regression: 預設 ADMIN_EMAIL 必須含 TLD，否則 /api/auth/login 的 Pydantic EmailStr 會直接 422 拒絕，
    即使 seeder 已經把 admin 寫進 DB，也永遠登不進來。
    """
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
