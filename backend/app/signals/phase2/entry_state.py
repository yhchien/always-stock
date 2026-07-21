"""
Phase 2 §J：Entry State（與 Role 分離）。

舊版 `distance_to_20d_high >= -3` 直接是 LEADER 判定的一部分，造成 -3.01% 與
-2.99% 之間有一條 cliff（差 0.02 個百分點，一個過關一個死當）。

Entry state 回答「目前處於什麼價格位置」，用 ATR 正規化距離（`pullback_atr_multiple`）
取代固定 3% 門檻，並且**不決定角色資格**——它是描述性欄位，由 risk/regime 決定要不要
因此降級或剔除。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

ENTRY_NEAR_HIGH = "NEAR_HIGH"
ENTRY_NORMAL_PULLBACK = "NORMAL_PULLBACK"
ENTRY_DEEP_PULLBACK = "DEEP_PULLBACK"
ENTRY_REACCELERATING = "REACCELERATING"
ENTRY_STRUCTURE_DAMAGED = "STRUCTURE_DAMAGED"

# pullback_atr_multiple 門檻（待 replay 校準；先用「距高點回落幅度相當於幾倍
# 14 日 ATR」這個尺度，取代固定 3% cliff）
_NEAR_HIGH_ATR_MULTIPLE = 0.5     # 回落 <= 0.5 倍 ATR 視為貼近高點
_NORMAL_PULLBACK_ATR_MULTIPLE = 2.0
_DEEP_PULLBACK_ATR_MULTIPLE = 4.0
_REACCELERATE_RS_IMPROVEMENT_MIN = 30  # RS 排名 5 日進步門檻


def compute_entry_state(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    輸入候選池 dict（需要 `distance_to_20d_high`、`atr_pct_14d`、
    `rs_rank_improvement_5d`，缺值時保守回傳 entry_state=None，不臆測）。

    回傳 {"entry_state": ..., "pullback_atr_multiple": float | None}
    """
    distance_pct = candidate.get("distance_to_20d_high")
    atr_pct = candidate.get("atr_pct_14d")
    rs_improvement = candidate.get("rs_rank_improvement_5d")

    if distance_pct is None or atr_pct is None or atr_pct <= 0:
        return {"entry_state": None, "pullback_atr_multiple": None}

    pullback_atr_multiple = round(abs(distance_pct) / atr_pct, 2)

    # 結構性損壞優先判斷：距高點很遠（>= 4 倍 ATR）且 RS 排名還在惡化
    if pullback_atr_multiple >= _DEEP_PULLBACK_ATR_MULTIPLE and (
        rs_improvement is not None and rs_improvement < 0
    ):
        state = ENTRY_STRUCTURE_DAMAGED
    elif rs_improvement is not None and rs_improvement >= _REACCELERATE_RS_IMPROVEMENT_MIN and pullback_atr_multiple > _NEAR_HIGH_ATR_MULTIPLE:
        # 曾經拉回但 RS 排名正在快速改善 → 重新加速中
        state = ENTRY_REACCELERATING
    elif pullback_atr_multiple <= _NEAR_HIGH_ATR_MULTIPLE:
        state = ENTRY_NEAR_HIGH
    elif pullback_atr_multiple <= _NORMAL_PULLBACK_ATR_MULTIPLE:
        state = ENTRY_NORMAL_PULLBACK
    else:
        state = ENTRY_DEEP_PULLBACK

    return {"entry_state": state, "pullback_atr_multiple": pullback_atr_multiple}
