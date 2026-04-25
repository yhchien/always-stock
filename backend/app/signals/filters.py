"""
M23 Step 6：deterministic filter 排除規則（hard / soft 兩層）。

骨架（slice 4）：簽章 + 規則註解，body raise NotImplementedError。
真實邏輯由 slice 5 填入。

對應 spec §9：
  §9.1 Hard Exclusions（直接剔除）
    1. ETF / 金融股（`exclusions.should_exclude` 已封裝）
    2. 三大法人合計近 5 日 net_amount < 0 且無 LAGGARD 條件
    3. price_change_3d > 15%（過熱避免追高）
    4. 流動性不足（5d 平均成交金額 < 5,000 萬 TWD）

  §9.2 Soft Filters（標 hint，最終由 LLM 在 Step 8 決定）
    - weakening：net_amount_3d > 0 且 net_amount_1d < -net_amount_3d × 0.5
    - retail_overheated：margin_change_3d > +5% 且 net_amount_3d ≤ 0
    - distribution：volume_1d/60d_avg > 2 且 price_change_1d ≤ 0
    - 高檔長上影：high - close > (close - open) × 2 且 close < high × 0.97
    - range_bound：10d 高低差 < 5%
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy.orm import Session

# Soft filter hint 字串（slice 5 / 6 共用）
HINT_WEAKENING = "weakening"
HINT_RETAIL_OVERHEATED = "retail_overheated"
HINT_DISTRIBUTION = "distribution"
HINT_RANGE_BOUND = "range_bound"


def apply_hard_exclusions(
    db: Session,
    target_date: date,
    classified: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Hard exclusions（spec §9.1）：直接剔除不符的股票。

    輸入：`classified`（已含 prelim_type 的候選池）
    輸出：剔除後的 list（保留 prelim_type 欄位）
    """
    raise NotImplementedError("Slice 5: hard exclusions not implemented yet")


def apply_soft_filters(
    db: Session,
    target_date: date,
    filtered: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Soft filters（spec §9.2）：標 hint 但不剔除，最終由 LLM Step 8 評估。

    輸入：`filtered`（hard exclusions 已過）
    輸出：每筆 dict 額外加上 `soft_hints: List[str]`（值為 HINT_* 之一或多個；無命中為空 list）
    """
    raise NotImplementedError("Slice 5: soft filters not implemented yet")
