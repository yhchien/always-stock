"""
M23 Step 6：LEADER / FOLLOWER / LAGGARD candidate 預分類（deterministic）。

Slice 5：實作完成。輸入候選池（已含 candidate_pool 計算好的 industry_rank_5d /
industry_rank_net_3d / industry_count / 法人 1d/3d/5d 等欄位），輸出每筆額外
帶 `prelim_type` 的 list；三類都不符的股票會被剔除（不會原地保留）。

對應 spec §7：
  §7.1 LEADER（**全部滿足**）
    - 該產業中漲幅 5d 排名前 30%（industry_rank_5d <= ceil(industry_count * 0.3)）
    - net_amount_3d 在該產業內排名前 20%
    - 三大法人合計近 3 日連買（至少 2 / 3 天 net_amount > 0）
    - 5d volume / 60d_avg_volume >= 1.5

  §7.2 FOLLOWER（**全部滿足**）
    - 與 LEADER 同產業（OR 同 sub_industry，這裡以 industry 為主，同 industry 已涵蓋同 sub）
    - 0 < price_change_5d < LEADER price_change_5d × 0.7
    - 三大法人合計近 3 日 net_amount > 0
    - 排除 LEADER 本身

  §7.3 LAGGARD candidate（**1-4 條中滿足 >= 2 條**；第 5 條業務題材相關由 LLM 驗證）
    1. 同產業有 LEADER 已漲（leader.price_change_5d >= 5%）— guard 條件
    2. 該股 price_change_5d 落後 LEADER 至少 5 個百分點
    3. 近 3 日法人或成交量開始轉強（net_amount_1d > 0 OR volume_1d / 5d_avg > 1.2）
    4. 技術面 early_turn（突破 5MA OR 站回 10MA）
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional

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

# Spec §7.1 LEADER thresholds
_LEADER_PRICE_PERCENTILE = 0.30
_LEADER_FLOW_PERCENTILE = 0.20
_LEADER_BUY_DAYS_MIN = 2
_LEADER_VOLUME_RATIO_MIN = 1.5

# Spec §7.2 FOLLOWER thresholds
_FOLLOWER_LEADER_RATIO = 0.7

# Spec §7.3 LAGGARD CANDIDATE thresholds
_LAGGARD_LEADER_GAIN_MIN_PCT = 5.0
_LAGGARD_GAP_MIN_PCT = 5.0
_LAGGARD_VOLUME_RATIO_MIN = 1.2
_LAGGARD_MIN_HITS = 2


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
    if not candidate_pool:
        return []

    # Pass 1：找出所有 LEADER
    leader_ids = {c["stock_id"] for c in candidate_pool if _is_leader(c)}

    # 為每個 industry 取「最強 LEADER 的 price_change_5d」當對照基準
    industry_top_leader_gain: Dict[str, float] = {}
    for c in candidate_pool:
        if c["stock_id"] not in leader_ids:
            continue
        ind = c.get("industry") or ""
        gain = c.get("price_change_5d")
        if gain is None:
            continue
        if ind not in industry_top_leader_gain or gain > industry_top_leader_gain[ind]:
            industry_top_leader_gain[ind] = gain

    # Pass 2：依序判定 LEADER → FOLLOWER → LAGGARD CANDIDATE
    classified: List[Dict[str, Any]] = []
    for c in candidate_pool:
        sid = c["stock_id"]
        if sid in leader_ids:
            classified.append({**c, "prelim_type": PRELIM_TYPE_LEADER})
            continue

        ind = c.get("industry") or ""
        leader_gain = industry_top_leader_gain.get(ind)

        if _is_follower(c, leader_gain):
            classified.append({**c, "prelim_type": PRELIM_TYPE_FOLLOWER})
            continue

        if _is_laggard_candidate(c, leader_gain):
            classified.append({**c, "prelim_type": PRELIM_TYPE_LAGGARD_CANDIDATE})
            continue

        # 三類都不符 → 剔除
    return classified


# ---------- helpers ----------


def _is_top_pct(rank: Optional[int], count: Optional[int], pct: float) -> bool:
    """判斷 rank 是否在前 pct%（用 ceil 取整；count<=0 視為 False）。"""
    if rank is None or count is None or count <= 0:
        return False
    threshold = max(1, math.ceil(count * pct))
    return rank <= threshold


def _is_leader(candidate: Dict[str, Any]) -> bool:
    """spec §7.1 四條件全部滿足。"""
    if not _is_top_pct(
        candidate.get("industry_rank_5d"),
        candidate.get("industry_count"),
        _LEADER_PRICE_PERCENTILE,
    ):
        return False
    if not _is_top_pct(
        candidate.get("industry_rank_net_3d"),
        candidate.get("industry_count"),
        _LEADER_FLOW_PERCENTILE,
    ):
        return False
    if (candidate.get("consecutive_buy_days_3d") or 0) < _LEADER_BUY_DAYS_MIN:
        return False
    vol_ratio = candidate.get("volume_5d_to_60d_ratio")
    if vol_ratio is None or vol_ratio < _LEADER_VOLUME_RATIO_MIN:
        return False
    return True


def _is_follower(candidate: Dict[str, Any], leader_gain: Optional[float]) -> bool:
    """spec §7.2：同產業已有 LEADER 且四條件全滿足。"""
    if leader_gain is None or leader_gain <= 0:
        return False
    price_5d = candidate.get("price_change_5d")
    if price_5d is None or price_5d <= 0:
        return False
    if price_5d >= leader_gain * _FOLLOWER_LEADER_RATIO:
        return False
    net_3d = candidate.get("total_institution_flow_3d") or 0.0
    if net_3d <= 0:
        return False
    return True


def _is_laggard_candidate(
    candidate: Dict[str, Any], leader_gain: Optional[float]
) -> bool:
    """spec §7.3：guard 條件成立後算 hits >= 2。"""
    if leader_gain is None or leader_gain < _LAGGARD_LEADER_GAIN_MIN_PCT:
        return False

    price_5d = candidate.get("price_change_5d") or 0.0
    gap = leader_gain - price_5d

    # 條件 1（guard 已成立）：同產業 LEADER 已漲超過 5%
    hits = 1

    # 條件 2：落後 LEADER 至少 5 個百分點
    if gap >= _LAGGARD_GAP_MIN_PCT:
        hits += 1

    # 條件 3：近 1 日法人或量能轉強
    net_1d = candidate.get("total_institution_flow_1d") or 0.0
    vol_1d_to_5d = candidate.get("volume_1d_to_5d_ratio")
    if net_1d > 0 or (
        vol_1d_to_5d is not None and vol_1d_to_5d > _LAGGARD_VOLUME_RATIO_MIN
    ):
        hits += 1

    # 條件 4：技術面 early_turn（站上 5MA 或 10MA）
    close = candidate.get("close_1d")
    ma5 = candidate.get("ma_5d")
    ma10 = candidate.get("ma_10d")
    if close is not None and (
        (ma5 is not None and close > ma5) or (ma10 is not None and close > ma10)
    ):
        hits += 1

    return hits >= _LAGGARD_MIN_HITS
