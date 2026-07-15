"""
M23 Step 6：LEADER / FOLLOWER / ROTATION_LAGGARD 預分類（deterministic）。

v2.1（fishtail momentum upgrade spec §6.3，2026-07-15 改版）：
分類從「產業排行 + 法人連買」升級為 momentum-score / 相對強度驅動。
輸入候選池（candidate_pool 已 merge momentum frame 特徵 + momentum_score），
輸出每筆額外帶 `prelim_type` 的 list；三類都不符的股票會被剔除（不會原地保留）。

  LEADER（**全部滿足**）：
    - 產業 20 日相對強度位於全市場前 30%（industry_rs_percentile_20d >= 70）
    - 個股產業內相對強度位於前 20%（rs_industry_percentile_20d >= 80）
    - momentum_score >= 70
    - 近 3 日法人至少 2 日買超，或 institution_buy_to_turnover_2d 位於全市場前 20%
    - volume_5d_to_60d_ratio >= 1.3
    - 距離 20 日高點不超過 3%（distance_to_20d_high >= -3）

  FOLLOWER（**全部滿足**）：
    - 同產業存在 LEADER
    - momentum_score 介於 55~69
    - 近 5 日漲幅低於 LEADER
    - rs_rank_improvement_5d > 0
    - 近 3 日法人買超為正
    - 無爆量長上影

  ROTATION_LAGGARD（**全部滿足**；原 LAGGARD_CANDIDATE 改名）：
    - 同產業存在 LEADER
    - 產業仍為強勢產業（industry_rs_percentile_20d >= 70 或 in_top_industries_3d）
    - 個股 20 日報酬落後產業平均至少 5 個百分點
    - 近 5 日相對強度改善（rs_rank_improvement_5d > 0）
    - 法人由賣轉買（1d 轉正且 5d 累計仍 <= 0）或量能轉強（vol_1d / 5d avg > 1.2）
    - 站回 10 日線或突破整理（收盤 > MA10 或收盤創 20 日新高）
    - momentum_score >= 50
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

# 預分類 enum（用 string literal 而非 Enum，方便 JSON 序列化）
PRELIM_TYPE_LEADER = "LEADER"
PRELIM_TYPE_FOLLOWER = "FOLLOWER"
PRELIM_TYPE_ROTATION_LAGGARD = "ROTATION_LAGGARD"
# 向後相容 alias（舊測試 / 舊 snapshot 讀取用；值已指向新命名）
PRELIM_TYPE_LAGGARD_CANDIDATE = PRELIM_TYPE_ROTATION_LAGGARD

ALL_PRELIM_TYPES = (
    PRELIM_TYPE_LEADER,
    PRELIM_TYPE_FOLLOWER,
    PRELIM_TYPE_ROTATION_LAGGARD,
)

# v2.1 LEADER thresholds（spec §6.3）
_LEADER_INDUSTRY_RS_PERCENTILE_MIN = 70.0   # 產業 RS 全市場前 30%
_LEADER_STOCK_INDUSTRY_RS_MIN = 80.0        # 個股產業內 RS 前 20%
_LEADER_MOMENTUM_SCORE_MIN = 70.0
_LEADER_BUY_DAYS_MIN = 2
_LEADER_INST_TURNOVER_PERCENTILE_MIN = 80.0  # institution_buy_to_turnover_2d 前 20%
_LEADER_VOLUME_RATIO_MIN = 1.3
_LEADER_DISTANCE_TO_20D_HIGH_MIN = -3.0     # 距 20 日高點不超過 3%

# v2.1 FOLLOWER thresholds
_FOLLOWER_MOMENTUM_SCORE_MIN = 55.0
_FOLLOWER_MOMENTUM_SCORE_MAX = 70.0         # 55 <= score < 70（spec 55~69）

# v2.1 ROTATION_LAGGARD thresholds
_LAGGARD_INDUSTRY_RS_PERCENTILE_MIN = 70.0
_LAGGARD_INDUSTRY_GAP_MIN_PCT = 5.0         # 20 日報酬落後產業平均至少 5 個百分點
_LAGGARD_VOLUME_TURN_RATIO_MIN = 1.2
_LAGGARD_MOMENTUM_SCORE_MIN = 50.0

# 爆量長上影（與 momentum._has_blowoff_upper_shadow 同門檻；此處獨立實作
# 避免依賴內部函式，且 filters 依賴本模組不可反向 import）
_BLOWOFF_VOL_RATIO = 2.0
_BLOWOFF_SHADOW_BODY_RATIO = 2.0
_BLOWOFF_PULLBACK_PCT = 0.97


def classify_stocks(
    db: Session,
    target_date: date,
    candidate_pool: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """對候選池每檔股票標記 prelim_type（v2.1 spec §6.3）。

    回傳：候選池每筆 dict 額外加上 `prelim_type` 欄位，
    不符合任何分類的股票會被剔除（不會原地保留）。

    規則順序（避免 LEADER 同時被分到 FOLLOWER）：
      1. 先標 LEADER（最嚴格）
      2. 再標 FOLLOWER（需參照同產業 LEADER 的 price_change_5d）
      3. 最後標 ROTATION_LAGGARD
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

    # 有 LEADER 但其 price_change_5d 缺值的產業：FOLLOWER / LAGGARD 仍需知道「產業有 LEADER」
    industries_with_leader = {
        (c.get("industry") or "") for c in candidate_pool if c["stock_id"] in leader_ids
    }

    # Pass 2：依序判定 LEADER → FOLLOWER → ROTATION_LAGGARD
    classified: List[Dict[str, Any]] = []
    for c in candidate_pool:
        sid = c["stock_id"]
        if sid in leader_ids:
            classified.append({**c, "prelim_type": PRELIM_TYPE_LEADER})
            continue

        ind = c.get("industry") or ""
        has_leader = ind in industries_with_leader
        leader_gain = industry_top_leader_gain.get(ind)

        if _is_follower(c, has_leader, leader_gain):
            classified.append({**c, "prelim_type": PRELIM_TYPE_FOLLOWER})
            continue

        if _is_rotation_laggard(c, has_leader):
            classified.append({**c, "prelim_type": PRELIM_TYPE_ROTATION_LAGGARD})
            continue

        # 三類都不符 → 剔除
    return classified


# ---------- helpers ----------


def _score(candidate: Dict[str, Any]) -> Optional[float]:
    value = candidate.get("momentum_score")
    return float(value) if value is not None else None


def _is_leader(candidate: Dict[str, Any]) -> bool:
    """v2.1 LEADER：六條件全部滿足（任一缺值視為不滿足）。"""
    ind_rs = candidate.get("industry_rs_percentile_20d")
    if ind_rs is None or ind_rs < _LEADER_INDUSTRY_RS_PERCENTILE_MIN:
        return False

    stock_rs = candidate.get("rs_industry_percentile_20d")
    if stock_rs is None or stock_rs < _LEADER_STOCK_INDUSTRY_RS_MIN:
        return False

    score = _score(candidate)
    if score is None or score < _LEADER_MOMENTUM_SCORE_MIN:
        return False

    buy_days = candidate.get("consecutive_buy_days_3d") or 0
    inst_pct = candidate.get("inst_buy_to_turnover_percentile_2d")
    if buy_days < _LEADER_BUY_DAYS_MIN and (
        inst_pct is None or inst_pct < _LEADER_INST_TURNOVER_PERCENTILE_MIN
    ):
        return False

    vol_ratio = candidate.get("volume_5d_to_60d_ratio")
    if vol_ratio is None or vol_ratio < _LEADER_VOLUME_RATIO_MIN:
        return False

    dist_high = candidate.get("distance_to_20d_high")
    if dist_high is None or dist_high < _LEADER_DISTANCE_TO_20D_HIGH_MIN:
        return False

    return True


def _is_follower(
    candidate: Dict[str, Any],
    has_leader: bool,
    leader_gain: Optional[float],
) -> bool:
    """v2.1 FOLLOWER：同產業有 LEADER + 五條件全滿足。"""
    if not has_leader:
        return False

    score = _score(candidate)
    if score is None or not (
        _FOLLOWER_MOMENTUM_SCORE_MIN <= score < _FOLLOWER_MOMENTUM_SCORE_MAX
    ):
        return False

    # 近 5 日漲幅低於 LEADER（leader_gain 缺值時無從比較 → 不通過，保守）
    price_5d = candidate.get("price_change_5d")
    if leader_gain is None or price_5d is None or price_5d >= leader_gain:
        return False

    improvement = candidate.get("rs_rank_improvement_5d")
    if improvement is None or improvement <= 0:
        return False

    net_3d = candidate.get("total_institution_flow_3d") or 0.0
    if net_3d <= 0:
        return False

    if _has_blowoff_upper_shadow(candidate):
        return False

    return True


def _is_rotation_laggard(candidate: Dict[str, Any], has_leader: bool) -> bool:
    """v2.1 ROTATION_LAGGARD：七條件全部滿足（補漲輪動，非弱勢撿便宜）。"""
    if not has_leader:
        return False

    # 產業仍為強勢產業
    ind_rs = candidate.get("industry_rs_percentile_20d")
    if not (
        (ind_rs is not None and ind_rs >= _LAGGARD_INDUSTRY_RS_PERCENTILE_MIN)
        or candidate.get("in_top_industries_3d")
    ):
        return False

    # 個股 20 日報酬落後產業平均至少 5 個百分點
    ind_ret = candidate.get("industry_return_20d")
    stock_ret = candidate.get("return_20d")
    if ind_ret is None or stock_ret is None or (ind_ret - stock_ret) < _LAGGARD_INDUSTRY_GAP_MIN_PCT:
        return False

    # 近 5 日相對強度改善
    improvement = candidate.get("rs_rank_improvement_5d")
    if improvement is None or improvement <= 0:
        return False

    # 法人由賣轉買（1d 轉正、5d 累計仍未轉正）或量能轉強
    flow_1d = candidate.get("total_institution_flow_1d") or 0.0
    flow_5d = candidate.get("total_institution_flow_5d") or 0.0
    inst_turn = flow_1d > 0 and flow_5d <= 0
    vol_1d_to_5d = candidate.get("volume_1d_to_5d_ratio")
    volume_turn = vol_1d_to_5d is not None and vol_1d_to_5d > _LAGGARD_VOLUME_TURN_RATIO_MIN
    if not (inst_turn or volume_turn):
        return False

    # 站回 10 日線或突破整理（收盤創 20 日新高）
    close = candidate.get("close_1d")
    ma10 = candidate.get("ma_10d")
    dist_high = candidate.get("distance_to_20d_high")
    back_above_ma10 = close is not None and ma10 is not None and close > ma10
    breakout = dist_high is not None and dist_high >= 0.0
    if not (back_above_ma10 or breakout):
        return False

    score = _score(candidate)
    if score is None or score < _LAGGARD_MOMENTUM_SCORE_MIN:
        return False

    return True


def _has_blowoff_upper_shadow(candidate: Dict[str, Any]) -> bool:
    """爆量長上影：當日量 > 60 日均量 ×2 且上影線 > 實體 ×2 且收盤 < 高點 ×0.97。"""
    vol_ratio = candidate.get("volume_1d_to_60d_ratio")
    if vol_ratio is None or vol_ratio <= _BLOWOFF_VOL_RATIO:
        return False
    high = candidate.get("high_1d")
    open_ = candidate.get("open_1d")
    close = candidate.get("close_1d")
    if high is None or open_ is None or close is None or high <= 0:
        return False
    upper_shadow = high - close
    body = close - open_
    return (
        upper_shadow > body * _BLOWOFF_SHADOW_BODY_RATIO
        and close < high * _BLOWOFF_PULLBACK_PCT
    )
