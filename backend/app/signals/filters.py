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

from app.signals.classification import (
    PRELIM_TYPE_LAGGARD_CANDIDATE,
    PRELIM_TYPE_LEADER,
)
from app.signals.exclusions import should_exclude
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)

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

# 2026-06-26 新增：當日成交量死線（依股價級距要求最低張數，不足直接剔除）
# 1 張 = 1000 股；DB volume 為股數，故門檻乘以 _SHARES_PER_LOT 比對。
_SHARES_PER_LOT = 1000
_HARD_MIN_LOTS_PRICE_UNDER_1000 = 1500  # 股價 < 1000 元 → 日量需 > 1500 張
_HARD_MIN_LOTS_PRICE_1000_TO_5000 = 800  # 1000 <= 股價 < 5000 元 → 日量需 > 800 張
_HARD_MIN_LOTS_PRICE_OVER_5000 = 500  # 股價 >= 5000 元 → 日量需 > 500 張

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


# ---------- M27 Market Regime Gate（regime-aware deterministic 降級）----------

# 震盪 / 退潮盤的「急拉突破」判定（避免追剛噴的）
_REGIME_SPIKE_VOL_RATIO = 2.0       # 當日量 / 5 日均量
_REGIME_SPIKE_PRICE_1D_PCT = 5.0    # 當日漲幅


def apply_regime_gate(
    candidates: List[Dict[str, Any]],
    market_regime: str,
) -> List[Dict[str, Any]]:
    """M27：依大盤 regime 對候選池做 deterministic 降級 / 剔除（spec 外，2026-06-26）。

    背景：6/5 後魚尾命中勝率大跌，主因是震盪盤仍用「多頭追強」邏輯把
    Follower / Laggard / 單次命中 / 急拉突破股送進 LLM。regime gate 在 LLM 之前
    先依大盤狀態收斂候選範圍，並對存活者標 `regime_conviction`（high/medium/low）。

    - BULL_TREND：不額外剔除（維持原行為），只標 conviction
    - VOLATILE_RANGE：剔除 distribution / 單次命中的 Follower-Laggard / 急拉突破
    - RISK_OFF：只保留「LEADER + hit_count>=3 + 近 5 日法人為正 + 非 distribution」，其餘剔除

    每筆存活者新增欄位（不 mutate 原 dict）：
      `market_regime` / `regime_conviction` / `regime_gate_note`
    """
    out: List[Dict[str, Any]] = []
    for c in candidates:
        if _regime_should_remove(c, market_regime):
            continue
        conviction = _regime_conviction(c, market_regime)
        out.append(
            {
                **c,
                "market_regime": market_regime,
                "regime_conviction": conviction,
                "regime_gate_note": _regime_gate_note(market_regime, conviction),
            }
        )
    return out


def _hit_count(candidate: Dict[str, Any]) -> int:
    return int(candidate.get("hit_count") or 0)


def _is_leader(candidate: Dict[str, Any]) -> bool:
    return candidate.get("prelim_type") == PRELIM_TYPE_LEADER


def _has_distribution(candidate: Dict[str, Any]) -> bool:
    return HINT_DISTRIBUTION in (candidate.get("soft_hints") or [])


def _is_spike_breakout(candidate: Dict[str, Any]) -> bool:
    """急拉突破：當日量 > 5 日均量 ×2 且當日漲幅 > 5%（震盪盤不該追）。"""
    vol = candidate.get("volume_1d_to_5d_ratio")
    pct = candidate.get("price_change_1d")
    return (
        vol is not None
        and vol > _REGIME_SPIKE_VOL_RATIO
        and pct is not None
        and pct > _REGIME_SPIKE_PRICE_1D_PCT
    )


def _regime_should_remove(candidate: Dict[str, Any], market_regime: str) -> bool:
    if market_regime == REGIME_BULL_TREND:
        return False

    if market_regime == REGIME_VOLATILE_RANGE:
        if _has_distribution(candidate):
            return True
        # 單次命中（含未追蹤）的 Follower / Laggard：震盪盤勝率最低，剔除
        if _hit_count(candidate) <= 1 and not _is_leader(candidate):
            return True
        if _is_spike_breakout(candidate):
            return True
        return False

    if market_regime == REGIME_RISK_OFF:
        # 退潮盤只留最強的：LEADER + 重複命中 + 法人續買 + 非派發
        flow_5d = candidate.get("total_institution_flow_5d")
        keep = (
            _is_leader(candidate)
            and _hit_count(candidate) >= 3
            and flow_5d is not None
            and flow_5d > 0
            and not _has_distribution(candidate)
        )
        return not keep

    return False


def _regime_conviction(candidate: Dict[str, Any], market_regime: str) -> str:
    hit = _hit_count(candidate)
    leader = _is_leader(candidate)

    if market_regime == REGIME_BULL_TREND:
        if leader and hit >= 2:
            return "high"
        if hit >= 2 or leader:
            return "medium"
        return "low"

    if market_regime == REGIME_VOLATILE_RANGE:
        if leader and hit >= 3:
            return "high"
        if hit >= 3 or (leader and hit >= 2):
            return "medium"
        return "low"

    # RISK_OFF：能存活的已是 leader + hit>=3，最高給 medium（退潮盤不追高）
    return "medium"


def _regime_gate_note(market_regime: str, conviction: str) -> str:
    label = {
        REGIME_BULL_TREND: "大多頭",
        REGIME_VOLATILE_RANGE: "震盪盤",
        REGIME_RISK_OFF: "風險退潮",
    }.get(market_regime, market_regime)
    conv = {"high": "高", "medium": "中", "low": "低"}.get(conviction, conviction)
    return f"{label}：信心度 {conv}"


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

    # 4b. 當日成交量死線（依股價級距要求最低張數）
    if _below_volume_deadline(candidate):
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


def _below_volume_deadline(candidate: Dict[str, Any]) -> bool:
    """當日成交量未達股價級距對應的最低張數 → True（應剔除）。

    級距（股價以 close_1d 判斷）：
      - < 1000 元：日量需 > 1500 張
      - 1000 ~ 5000 元（含 1000、不含 5000）：日量需 > 800 張
      - >= 5000 元：日量需 > 500 張

    price 或 volume 任一缺值（None）→ 不剔除（沿用流動性 filter 慣例，
    避免資料缺漏日把整池清空）。
    """
    price = candidate.get("close_1d")
    volume = candidate.get("volume_1d")
    if price is None or volume is None:
        return False

    if price < 1000:
        min_lots = _HARD_MIN_LOTS_PRICE_UNDER_1000
    elif price < 5000:
        min_lots = _HARD_MIN_LOTS_PRICE_1000_TO_5000
    else:
        min_lots = _HARD_MIN_LOTS_PRICE_OVER_5000

    lots = volume / _SHARES_PER_LOT
    return lots <= min_lots


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
