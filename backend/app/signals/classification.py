"""
M23 Step 6：LEADER / FOLLOWER / LAGGARD candidate 預分類（deterministic）。

骨架（slice 4）：簽章 + 規則註解，body raise NotImplementedError。
真實邏輯由 slice 5 填入。

對應 spec §7 三類條件：
  - LEADER：產業 5d 漲幅前 30% + 法人 net_3d 產業內前 20% + 三大法人連 2/3 天買
              + 5d 量 / 60d 均量 ≥ 1.5
  - FOLLOWER：與 LEADER 同產業 / sub_industry，price_change_5d > 0 但 < LEADER × 0.7，
              三大法人 3d net 為正，且非 LEADER 本身
  - LAGGARD_CANDIDATE：滿足 §7.3 五條件中至少 2 條（其中「業務題材相關」由 LLM
              在 Step 7 驗證，deterministic 階段先標 candidate）
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy.orm import Session

# 預分類 enum（用 string literal 而非 Enum，方便 JSON 序列化）
PRELIM_TYPE_LEADER = "LEADER"
PRELIM_TYPE_FOLLOWER = "FOLLOWER"
PRELIM_TYPE_LAGGARD_CANDIDATE = "LAGGARD_CANDIDATE"

ALL_PRELIM_TYPES = (
    PRELIM_TYPE_LEADER,
    PRELIM_TYPE_FOLLOWER,
    PRELIM_TYPE_LAGGARD_CANDIDATE,
)


def classify_stocks(
    db: Session,
    target_date: date,
    candidate_pool: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """對候選池每檔股票標記 prelim_type（spec §7）。

    回傳：候選池每筆 dict 額外加上 `prelim_type` 欄位（值為三個 PRELIM_TYPE_ 之一），
    不符合任何分類的股票會被剔除（不會原地保留）。

    規則順序（避免 LEADER 同時被分到 FOLLOWER）：
      1. 先標 LEADER（最嚴格）
      2. 再標 FOLLOWER（需參照 LEADER 的同產業 price_change_5d）
      3. 最後標 LAGGARD_CANDIDATE
      4. 三類都不符 → 剔除
    """
    raise NotImplementedError("Slice 5: classification not implemented yet")
