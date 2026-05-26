"""
M23 Step 6：deterministic filter 排除規則（hard / soft 兩層）。

Slice 5：實作完成。

對應 spec §9：
  §9.1 Hard Exclusions（直接剔除）
    1. ETF / 金融股（`exclusions.should_exclude` 已封裝）
    2. 三大法人合計近 5 日 net_amount < 0 且**非** LAGGARD_CANDIDATE
       （LAGGARD 本身就是「落後股」，允許 5 日累計法人為負，等待法人轉強）
    3. price_change_3d > 15%（過熱避免追高）
    4. 流動性不足（5d 平均成交金額 < 5,000 萬 TWD）

    2026-05-26 新增（再偵測閘門 + 後段 FOLLOWER 防護）：
    5. failed_follow_through：candidate_pool 算的「首次抓到後 3 日內 max<+3% 且 max_neg<-6%」
       → 該股已被市場驗證為弱化訊號，不再進入新的候選池
    6. price_extended_inst_selling：price_change_10d > +25% 且 total_institution_flow_1d < 0
       → 短期已大漲且當日法人轉賣，典型派發前兆
    7. inst_3d_pos_1d_neg_price_dropping：flow_3d > 0 且 flow_1d < 0 且 price_change_1d < -1.5%
       → 三日累積買超但今日反轉大賣 + 股價大跌 = 主力出貨確認

  §9.2 Soft Filters（標 hint，最終由 LLM 在 Step 8 決定 WATCH / REMOVE）
    - weakening：total_institution_flow_3d > 0 且 total_institution_flow_1d
      < -total_institution_flow_3d × 0.5（前三日大買、昨日大賣）
    - retail_overheated：margin_change_3d > +5% 且 total_institution_flow_3d <= 0
    - distribution：兩條件命中其中之一即標：
        a) volume_1d/60d_avg > 2 且 price_change_1d <= 0（爆量不漲）
        b) 高檔長上影：high - close > (close - open) × 2 且 close < high × 0.97
    - range_bound：10d 高低差 < 5%（(high_10d - low_10d) / low_10d < 0.05）
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.signals.classification import PRELIM_TYPE_LAGGARD_CANDIDATE
from app.signals.exclusions import should_exclude

# Soft filter hint 字串（slice 5 / 6 共用）
HINT_WEAKENING = "weakening"
HINT_RETAIL_OVERHEATED = "retail_overheated"
HINT_DISTRIBUTION = "distribution"
HINT_RANGE_BOUND = "range_bound"

# Spec §9.1 Hard Exclusions thresholds
_HARD_PRICE_3D_OVERHEAT_PCT = 15.0
_HARD_LIQUIDITY_MIN_TWD = 5e7  # 5,000 萬

# 2026-05-26 新增（spec §再偵測閘門）
_HARD_PRICE_EXTENDED_10D_PCT = 25.0  # 短期已大漲門檻
_HARD_DIVERGENCE_PRICE_1D_DROP_PCT = -1.5  # 1d 大跌門檻（負值）

# Spec §9.2 Soft Filters thresholds
_SOFT_WEAKENING_RATIO = 0.5
_SOFT_RETAIL_MARGIN_PCT = 0.05  # 5%（margin_change_3d 是 ratio：0.05 = +5%）
_SOFT_DISTRIBUTION_VOL_RATIO = 2.0
_SOFT_DISTRIBUTION_UPPER_SHADOW_RATIO = 2.0  # upper_shadow > body × 2
_SOFT_DISTRIBUTION_PULLBACK_PCT = 0.97  # close < high × 0.97
_SOFT_RANGE_BOUND_PCT = 0.05  # (high - low) / low < 5%


def apply_hard_exclusions(
    db: Session,
    target_date: date,
    classified: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Hard exclusions（spec §9.1）：直接剔除不符的股票。

    輸入：`classified`（已含 prelim_type 的候選池）
    輸出：剔除後的 list（保留 prelim_type 欄位）

    db / target_date 暫時保留簽章；hard exclusions 全部用候選池欄位即可決策，
    不需另查 DB（exclusions.should_exclude 是純規則）。
    """
    if not classified:
        return []

    survivors: List[Dict[str, Any]] = []
    for c in classified:
        if _is_hard_excluded(c):
            continue
        survivors.append(c)
    return survivors


def apply_soft_filters(
    db: Session,
    target_date: date,
    filtered: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Soft filters（spec §9.2）：標 hint 但不剔除，最終由 LLM Step 8 評估。

    輸入：`filtered`（hard exclusions 已過）
    輸出：每筆 dict 額外加上 `soft_hints: List[str]`（值為 HINT_* 之一或多個；無命中為空 list）
    """
    if not filtered:
        return []

    out: List[Dict[str, Any]] = []
    for c in filtered:
        hints = _detect_soft_hints(c)
        # 不修改原 dict（pipeline 可能對候選池有其他引用）；shallow copy + 加欄位
        out.append({**c, "soft_hints": hints})
    return out


# ---------- helpers ----------


def _is_hard_excluded(candidate: Dict[str, Any]) -> bool:
    """spec §9.1 任一條件命中即剔除。"""
    sid = candidate.get("stock_id") or ""
    name = candidate.get("name")
    industry = candidate.get("industry")

    # 1. ETF / 金融 / 黑名單（idempotent；候選池應已過濾，但保險再過一次）
    if should_exclude(sid, name, industry):
        return True

    # 2. 三大法人合計近 5 日 net < 0 且非 LAGGARD
    flow_5d = candidate.get("total_institution_flow_5d")
    prelim = candidate.get("prelim_type")
    if (
        flow_5d is not None
        and flow_5d < 0
        and prelim != PRELIM_TYPE_LAGGARD_CANDIDATE
    ):
        return True

    # 3. price_change_3d > 15%（過熱）
    pct_3d = candidate.get("price_change_3d")
    if pct_3d is not None and pct_3d > _HARD_PRICE_3D_OVERHEAT_PCT:
        return True

    # 4. 流動性不足（5d avg turnover < 5,000 萬 TWD）
    turnover_5d = candidate.get("avg_turnover_5d")
    if turnover_5d is not None and turnover_5d < _HARD_LIQUIDITY_MIN_TWD:
        return True

    # 5. failed_follow_through：首次抓到後 3 日驗證失敗（再偵測閘門）
    if candidate.get("failed_follow_through"):
        return True

    # 6. 短期已大漲且當日法人轉賣（派發前兆）
    pct_10d = candidate.get("price_change_10d")
    flow_1d = candidate.get("total_institution_flow_1d")
    if (
        pct_10d is not None
        and pct_10d > _HARD_PRICE_EXTENDED_10D_PCT
        and flow_1d is not None
        and flow_1d < 0
    ):
        return True

    # 7. 3d 累積買超但 1d 反轉大賣 + 股價大跌（主力出貨確認）
    flow_3d = candidate.get("total_institution_flow_3d")
    pct_1d = candidate.get("price_change_1d")
    if (
        flow_3d is not None
        and flow_3d > 0
        and flow_1d is not None
        and flow_1d < 0
        and pct_1d is not None
        and pct_1d < _HARD_DIVERGENCE_PRICE_1D_DROP_PCT
    ):
        return True

    return False


def _detect_soft_hints(candidate: Dict[str, Any]) -> List[str]:
    """spec §9.2：所有命中的 hint 都加進 list（可多個）。"""
    hints: List[str] = []

    # weakening：3d 正、1d 反向大賣（賣超超過 3d 累計的一半）
    flow_3d = candidate.get("total_institution_flow_3d")
    flow_1d = candidate.get("total_institution_flow_1d")
    if (
        flow_3d is not None
        and flow_1d is not None
        and flow_3d > 0
        and flow_1d < -flow_3d * _SOFT_WEAKENING_RATIO
    ):
        hints.append(HINT_WEAKENING)

    # retail_overheated：融資暴增 + 法人未買
    margin_3d = candidate.get("margin_change_3d")
    if (
        margin_3d is not None
        and margin_3d > _SOFT_RETAIL_MARGIN_PCT
        and (flow_3d is None or flow_3d <= 0)
    ):
        hints.append(HINT_RETAIL_OVERHEATED)

    # distribution：兩條件命中之一即可（去重後只會出現一次）
    if _is_distribution(candidate):
        hints.append(HINT_DISTRIBUTION)

    # range_bound：10d 高低差 < 5%
    high_10d = candidate.get("high_10d")
    low_10d = candidate.get("low_10d")
    if (
        high_10d is not None
        and low_10d is not None
        and low_10d > 0
        and (high_10d - low_10d) / low_10d < _SOFT_RANGE_BOUND_PCT
    ):
        hints.append(HINT_RANGE_BOUND)

    return hints


def _is_distribution(candidate: Dict[str, Any]) -> bool:
    """爆量不漲 OR 高檔長上影。"""
    # a) 爆量不漲
    vol_1d_60d = candidate.get("volume_1d_to_60d_ratio")
    pct_1d = candidate.get("price_change_1d")
    if (
        vol_1d_60d is not None
        and vol_1d_60d > _SOFT_DISTRIBUTION_VOL_RATIO
        and pct_1d is not None
        and pct_1d <= 0
    ):
        return True

    # b) 高檔長上影
    high = candidate.get("high_1d")
    open_ = candidate.get("open_1d")
    close = candidate.get("close_1d")
    if (
        high is not None
        and open_ is not None
        and close is not None
        and high > 0
    ):
        upper_shadow = high - close
        body = close - open_
        # 上影線 > 實體 × 2（red 蠟燭 body < 0 時 inequality 自然成立 → 反轉訊號）
        if (
            upper_shadow > body * _SOFT_DISTRIBUTION_UPPER_SHADOW_RATIO
            and close < high * _SOFT_DISTRIBUTION_PULLBACK_PCT
        ):
            return True

    return False
