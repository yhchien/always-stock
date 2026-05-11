"""list register <password> — 驗證並寫入 telegram_chats。

密碼來源：`SITE_GATE_PASSWORD` env（與站台閘門共用，避免兩套密碼管理）。
比對採 `hmac.compare_digest` 防 timing attack。
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import TelegramChat
from app.settings import get_site_gate_password

logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    success: bool
    message: str
    already_registered: bool = False


def register_chat(
    db: Session,
    *,
    chat_id: int,
    password: str,
    chat_label: Optional[str] = None,
) -> RegistrationResult:
    """驗證密碼後寫入 telegram_chats。

    回傳：
    - success=True / already_registered=False：新註冊成功
    - success=True / already_registered=True：已註冊（順手更新 last_seen_at）
    - success=False：密碼錯誤 / 密碼未設定
    """
    expected = get_site_gate_password()
    if not expected:
        logger.error("SITE_GATE_PASSWORD not configured; registration unavailable")
        return RegistrationResult(
            success=False,
            message="⚠️ 系統尚未設定註冊密碼，請聯絡管理員。",
        )

    if not hmac.compare_digest(password.strip(), expected):
        logger.info("Telegram registration rejected: bad password chat_id=%s", chat_id)
        return RegistrationResult(success=False, message="❌ 密碼錯誤。")

    existing = db.get(TelegramChat, chat_id)
    now = datetime.utcnow()
    if existing is not None:
        existing.last_seen_at = now
        if chat_label and not existing.chat_label:
            existing.chat_label = chat_label
        db.commit()
        return RegistrationResult(
            success=True,
            message="✅ 此 chat 已註冊，可直接使用 `list help` 查看支援指令。",
            already_registered=True,
        )

    chat = TelegramChat(
        chat_id=chat_id,
        password_verified_at=now,
        registered_at=now,
        last_seen_at=now,
        chat_label=chat_label,
    )
    db.add(chat)
    db.commit()
    logger.info("Telegram chat registered: chat_id=%s label=%s", chat_id, chat_label)
    return RegistrationResult(
        success=True,
        message=(
            "🎉 註冊成功！\n\n"
            "輸入 `list help` 查看支援指令。\n"
            "你的清單上限為 20 檔，每日 21:30 會自動推送清單報告。"
        ),
    )


def is_registered(db: Session, chat_id: int) -> bool:
    return db.get(TelegramChat, chat_id) is not None


def touch_last_seen(db: Session, chat_id: int) -> None:
    """記錄 chat 最後活動時間，供未來清理 inactive chat 用。失敗不擋主流程。"""
    try:
        chat = db.get(TelegramChat, chat_id)
        if chat is not None:
            chat.last_seen_at = datetime.utcnow()
            db.commit()
    except Exception:
        logger.exception("Failed to touch last_seen_at for chat_id=%s", chat_id)
        db.rollback()
