"""In-memory per-chat 鎖（給 `list run <id>` / `list run all` 用）。

設計：
- module-level dict[int, datetime] 記錄每個 chat_id 的 acquired_at
- timeout 10 分鐘：超時自動視為釋放（防 worker crash 卡死永久）
- server 重啟會清空 → 接受（個人專案、Render web service 偶爾重啟 OK）

API：
- try_acquire(chat_id) -> bool：成功拿鎖回 True；已被鎖且未超時回 False
- release(chat_id)：handler / background task 完成時 finally 呼叫
- is_locked(chat_id) -> bool：純查詢，不修改狀態
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Dict

LOCK_TIMEOUT_SECONDS = 600  # 10 分鐘

_locks: Dict[int, datetime] = {}
_lock_guard = threading.Lock()


def _now() -> datetime:
    return datetime.utcnow()


def _is_expired(acquired_at: datetime) -> bool:
    return _now() - acquired_at > timedelta(seconds=LOCK_TIMEOUT_SECONDS)


def try_acquire(chat_id: int) -> bool:
    """嘗試拿鎖。已被鎖且未超時 → False；成功拿到（或前一個已過期）→ True。"""
    with _lock_guard:
        existing = _locks.get(chat_id)
        if existing is not None and not _is_expired(existing):
            return False
        _locks[chat_id] = _now()
        return True


def release(chat_id: int) -> None:
    """釋放鎖。重複呼叫安全（key 不存在時 pop 預設值 None）。"""
    with _lock_guard:
        _locks.pop(chat_id, None)


def is_locked(chat_id: int) -> bool:
    """純查詢；超時的鎖視為未鎖（但不主動清理 — 由 try_acquire 順手清）。"""
    with _lock_guard:
        existing = _locks.get(chat_id)
        if existing is None:
            return False
        return not _is_expired(existing)


def _reset_all_for_tests() -> None:
    """測試用：清空所有鎖。production 程式碼不應呼叫。"""
    with _lock_guard:
        _locks.clear()
