"""
Rate limiting 工具（M18）

目前主要用途：
- `POST /api/analysis/trade-quality`（未登入：3/day by IP，已登入：30/day by user.id）

其他 OpenAI / 計算重的 endpoint（backtest 系列）直接用 require_user 擋未登入，
不再額外 rate limit；日後若使用者量大再考慮加 per-user 的 quota。

限流後端預設為 in-memory（slowapi 預設）。單進程部署足矣；
若未來拆成多 worker / 多 instance 需要準確計數，再換成 Redis backend。
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

TRADE_QUALITY_AUTHENTICATED_LIMIT = "30/day"
TRADE_QUALITY_ANONYMOUS_LIMIT = "3/day"


def _auth_user_id(request: Request) -> Optional[int]:
    """讀取 get_optional_user dependency 塞進 request.state 的 auth_user_id。"""
    return getattr(request.state, "auth_user_id", None)


def rate_limit_key(request: Request) -> str:
    """優先以 user.id 為 key，未登入 fallback 到 IP。"""
    uid = _auth_user_id(request)
    if uid is not None:
        return f"user:{uid}"
    return get_remote_address(request)


def trade_quality_limit_value(key: str) -> str:
    """依 slowapi 呼叫慣例：收到 rate_limit_key 產生的字串，回傳 limit 敘述。

    key 前綴為 `user:` 代表已登入（放寬至 30/day），否則為 anonymous IP（3/day）。
    """
    if key.startswith("user:"):
        return TRADE_QUALITY_AUTHENTICATED_LIMIT
    return TRADE_QUALITY_ANONYMOUS_LIMIT


limiter = Limiter(key_func=rate_limit_key)
