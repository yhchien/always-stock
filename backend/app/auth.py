"""
使用者認證模組（M18）

- bcrypt 密碼雜湊 / 驗證
- Server-side session 建立 / 查詢 / 撤銷
- FastAPI dependencies：require_user / get_optional_user
- Admin seeder：app 啟動時若 admin 帳號不存在則建立

Session 機制採用 server-side：session_id（uuid4）寫入 DB 與 httpOnly cookie。
登出 / 撤銷時把 revoked_at 設為 now()，不刪 row。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserSession
from app.settings import (
    get_admin_email,
    get_admin_password,
    get_cookie_domain,
    get_session_cookie_name,
    get_session_ttl_days,
    is_cookie_secure,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 密碼雜湊
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password cannot be empty")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def create_session(
    db: Session,
    user: User,
    *,
    request: Optional[Request] = None,
    ttl_days: Optional[int] = None,
) -> UserSession:
    ttl = ttl_days if ttl_days is not None else get_session_ttl_days()
    now = datetime.utcnow()
    session = UserSession(
        session_id=_generate_session_id(),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(days=ttl),
        last_seen_at=now,
        user_agent=(request.headers.get("user-agent") if request else None),
        ip_address=(request.client.host if request and request.client else None),
    )
    db.add(session)
    user.last_login_at = now
    db.commit()
    db.refresh(session)
    return session


def revoke_session(db: Session, session_id: str) -> None:
    row = db.query(UserSession).filter(UserSession.session_id == session_id).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()


def _lookup_active_user(db: Session, session_id: Optional[str]) -> Optional[User]:
    if not session_id:
        return None
    session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
    if not session:
        return None
    if session.revoked_at is not None:
        return None
    if session.expires_at <= datetime.utcnow():
        return None

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user or not user.is_active:
        return None

    # best-effort last_seen_at 更新；失敗時不影響登入判斷
    try:
        session.last_seen_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()

    return user


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=get_session_cookie_name(),
        value=session_id,
        max_age=get_session_ttl_days() * 24 * 60 * 60,
        httponly=True,
        secure=is_cookie_secure(),
        samesite="lax",
        domain=get_cookie_domain(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=get_session_cookie_name(),
        domain=get_cookie_domain(),
        path="/",
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _read_session_cookie(request: Request) -> Optional[str]:
    return request.cookies.get(get_session_cookie_name())


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """讀取 cookie 查找使用者；無 / 過期 / revoked 時回 None（不拋例外）"""
    return _lookup_active_user(db, _read_session_cookie(request))


def require_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """要求已登入，否則 401"""
    user = _lookup_active_user(db, _read_session_cookie(request))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登入或登入階段已失效",
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理員權限",
        )
    return user


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

def ensure_admin_user(db: Session) -> User:
    """若 admin 帳號不存在則建立；密碼從 ADMIN_PASSWORD 讀取"""
    email = get_admin_email()
    password = get_admin_password()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if not existing.is_admin:
            existing.is_admin = True
            db.commit()
        return existing

    admin = User(
        email=email,
        password_hash=hash_password(password),
        name="Admin",
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("Seeded admin user: %s", email)
    return admin
