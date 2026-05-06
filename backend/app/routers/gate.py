"""Site gate：全站單一密碼閘門。

訪問控制流程（前端 SiteGate）：
1. 未解鎖 → 顯示密碼輸入畫面
2. POST /api/gate/verify { password } → 200 ok / 403 wrong / 423 locked / 503 unconfigured
3. 連續失敗達上限 → 前端 localStorage 寫鎖定到期時間，鎖定期內顯示鎖定畫面

後端只做 password 比對 + 503 防呆；attempt 計數與鎖定狀態存在前端 localStorage
（個人專案訪問控制，不用對抗清 storage 的使用者）。
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.settings import (
    get_site_gate_lockout_seconds,
    get_site_gate_max_attempts,
    get_site_gate_password,
)

router = APIRouter(prefix="/gate", tags=["gate"])


class GateVerifyRequest(BaseModel):
    password: str


class GateVerifyResponse(BaseModel):
    ok: bool


class GateConfigResponse(BaseModel):
    max_attempts: int
    lockout_seconds: int


@router.get("/config", response_model=GateConfigResponse)
def gate_config() -> GateConfigResponse:
    return GateConfigResponse(
        max_attempts=get_site_gate_max_attempts(),
        lockout_seconds=get_site_gate_lockout_seconds(),
    )


@router.post("/verify", response_model=GateVerifyResponse)
def gate_verify(payload: GateVerifyRequest) -> GateVerifyResponse:
    expected = get_site_gate_password()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SITE_GATE_PASSWORD 尚未設定，請聯絡管理員",
        )

    if not hmac.compare_digest(payload.password, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="密碼錯誤",
        )

    return GateVerifyResponse(ok=True)
