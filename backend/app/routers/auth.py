"""
認證 API（M18）

- POST /api/auth/register  → 註冊 + 自動登入
- POST /api/auth/login     → email/password 登入
- POST /api/auth/logout    → 撤銷 cookie 對應的 session
- GET  /api/auth/me        → 回傳目前登入使用者資訊
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth import (
    clear_session_cookie,
    create_session,
    get_optional_user,
    hash_password,
    revoke_session,
    set_session_cookie,
    verify_password,
)
from app.database import get_db
from app.models import User
from app.settings import get_session_cookie_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_MIN_LENGTH = 8


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)
    name: Optional[str] = Field(default=None, max_length=50)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str]
    is_admin: bool

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            is_admin=bool(user.is_admin),
        )


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", response_model=UserResponse)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    email = _normalize_email(payload.email)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此 email 已被註冊",
        )

    name = (payload.name or "").strip() or None
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        name=name,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    session = create_session(db, user, request=request)
    set_session_cookie(response, session.session_id)
    logger.info("User registered: %s (id=%s)", user.email, user.id)
    return UserResponse.from_user(user)


@router.post("/login", response_model=UserResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    email = _normalize_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        # 統一錯誤訊息避免洩漏 email 是否存在
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email 或密碼錯誤",
        )

    session = create_session(db, user, request=request)
    set_session_cookie(response, session.session_id)
    logger.info("User logged in: %s (id=%s)", user.email, user.id)
    return UserResponse.from_user(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    session_id = request.cookies.get(get_session_cookie_name())
    if session_id:
        revoke_session(db, session_id)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
def me(user: Optional[User] = Depends(get_optional_user)) -> UserResponse:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登入",
        )
    return UserResponse.from_user(user)
