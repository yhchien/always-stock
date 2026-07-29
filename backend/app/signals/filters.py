"""
M23 Step 6：deterministic filter 排除規則（hard / soft 兩層）。

Slice 5：實作完成。

對應 spec §9：
  §9.1 Hard Exclusions（直接剔除）
    1. 人工黑名單（資產類型不是 exclusion）
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
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.signals.classification import (
    PRELIM_TYPE_LEADER,
    PRELIM_TYPE_ROTATION_LAGGARD,
)
from app.signals.exclusions import is_blacklisted
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

# v2.1（2026-07-15，fishtail momentum upgrade spec §6.4）：弱相對強度淘汰
# RS 全市場 percentile < 40 且近 5 日排名沒有改善 → 動能死水，直接剔除
_HARD_RS_MARKET_PERCENTILE_MIN = 40.0

# v2.1 regime-specific score gate（spec §6.4）
_REGIME_VOLATILE_MOMENTUM_SCORE_MIN = 60.0   # 震盪盤 momentum_score < 60 剔除
_REGIME_RISK_OFF_RS_PERCENTILE_MIN = 90.0    # 退潮盤 rs_market_percentile_20d < 90 剔除

# v2.2 breadth-aware gate（spec §7.3；2026-07-15 第三輪）
_REGIME_BROAD_BULL_SCORE_MIN = 50.0          # BROAD_BULL：momentum_score < 50 剔除
_REGIME_NARROW_BULL_LEADER_SCORE_MIN = 65.0  # NARROW_BULL：LEADER 需 score >= 65
_REGIME_NARROW_BULL_SCORE_MIN = 70.0         # NARROW_BULL：非 LEADER 需 score >= 70 且無 distribution
_REGIME_BULL_HIGH_CONVICTION_SCORE = 75.0    # BROAD_BULL 高信心：score >= 75 且 independent_hit >= 2
_REGIME_BULL_MEDIUM_CONVICTION_SCORE = 60.0
_VOLATILE_RS_DETERIORATION = -50             # 震盪盤：RS 排名 5 日掉超過 50 名 → 剔除（spec「相對強度近 5 日惡化」）

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
    不需另查 DB（exclusions.is_blacklisted 是純規則）。
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
    *,
    regime_detail: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """M27：依大盤 regime 對候選池做 deterministic 降級 / 剔除（spec 外，2026-06-26）。

    背景：6/5 後魚尾命中勝率大跌，主因是震盪盤仍用「多頭追強」邏輯把
    Follower / Laggard / 單次命中 / 急拉突破股送進 LLM。regime gate 在 LLM 之前
    先依大盤狀態收斂候選範圍，並對存活者標 `regime_conviction`（high/medium/low）。

    - BULL_TREND（v2.2 疊 breadth，`regime_detail` 由 market_breadth.resolve_regime_detail 算）：
      - BROAD_BULL（或 detail 缺值，保守不加嚴）：momentum_score < 50 剔除（spec §7.3）
      - NARROW_BULL（指數強但廣度弱）：只留 (LEADER 且 score>=65) 或 (score>=70 且無 distribution)
    - VOLATILE_RANGE：剔除 distribution / 急拉突破 / momentum_score<60 /
      RS 排名 5 日惡化 / conviction=low（單次命中非 LEADER），
      但「已追蹤且表現中（max_pos>=3% 且 max_neg>-6%）」的 low 給留校 watch
    - RISK_OFF：只保留「LEADER + hit_count>=3 + 近 5 日法人為正 + 非 distribution +
      rs_market_percentile>=90」，其餘剔除

    conviction 採資料導向（2026-06 CSV：hit_count>=3 勝率 77%、單次命中僅 24%）：
    - VOLATILE_RANGE：hit>=3 → high；LEADER 或 hit==2 → medium；其餘 → low
    - RISK_OFF：存活者皆為強 LEADER → high
    - BULL_TREND：leader+hit>=2 → high；hit>=2 或 leader → medium；其餘 → low

    每筆存活者新增欄位（不 mutate 原 dict）：
      `market_regime` / `regime_conviction` / `regime_gate_note`
    """
    out: List[Dict[str, Any]] = []
    for c in candidates:
        if _regime_should_remove(c, market_regime, regime_detail):
            continue
        conviction = _regime_conviction(c, market_regime)
        out.append(
            {
                **c,
                "market_regime": market_regime,
                "market_regime_detail": regime_detail or market_regime,
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


def _is_tracked_and_holding(candidate: Dict[str, Any]) -> bool:
    """已追蹤且表現中：max_pos >= +3% 且 max_neg > -6%（給 low conviction 的留校例外）。"""
    if not candidate.get("is_tracked"):
        return False
    max_pos = candidate.get("max_positive_return_pct")
    max_neg = candidate.get("max_negative_return_pct")
    return (
        max_pos is not None
        and max_pos >= 3.0
        and max_neg is not None
        and max_neg > -6.0
    )


def _regime_should_remove(
    candidate: Dict[str, Any],
    market_regime: str,
    regime_detail: Optional[str] = None,
) -> bool:
    if market_regime == REGIME_BULL_TREND:
        score = candidate.get("momentum_score")
        # v2.2（spec §7.3）NARROW_BULL：指數強但廣度弱 → 只留最強的
        if regime_detail == "NARROW_BULL":
            if score is None:
                return False  # 缺值不觸發（向後相容）
            if _is_leader(candidate) and score >= _REGIME_NARROW_BULL_LEADER_SCORE_MIN:
                return False
            if score >= _REGIME_NARROW_BULL_SCORE_MIN and not _has_distribution(candidate):
                return False
            return True
        # BROAD_BULL（或 detail 缺值）：保留 score >= 50（spec §7.3）
        if score is not None and score < _REGIME_BROAD_BULL_SCORE_MIN:
            return True
        return False

    if market_regime == REGIME_VOLATILE_RANGE:
        if _has_distribution(candidate):
            return True
        if _is_spike_breakout(candidate):
            return True
        # v2.1（spec §6.4）：震盪盤 momentum_score < 60 直接剔除。
        # momentum_score 缺值（舊 snapshot / 測試造資料）→ 不觸發此條（向後相容）；
        # 正常 pipeline 每檔都有 score（資料缺漏股 score 會偏低 → 自然被擋）。
        score = candidate.get("momentum_score")
        if score is not None and score < _REGIME_VOLATILE_MOMENTUM_SCORE_MIN:
            return True
        # v2.2（spec §7.3）：相對強度近 5 日惡化（排名掉超過 50 名）→ 剔除
        improvement = candidate.get("rs_rank_improvement_5d")
        if improvement is not None and improvement <= _VOLATILE_RS_DETERIORATION:
            return True
        # conviction=low（單次命中且非 LEADER）→ 剔除，除非已追蹤且表現中
        if _regime_conviction(candidate, market_regime) == "low" and not _is_tracked_and_holding(
            candidate
        ):
            return True
        return False

    if market_regime == REGIME_RISK_OFF:
        # v2.1（spec §6.4）：退潮盤相對強度不在全市場前 10% 直接剔除（缺值不觸發）
        rs_mkt = candidate.get("rs_market_percentile_20d")
        if rs_mkt is not None and rs_mkt < _REGIME_RISK_OFF_RS_PERCENTILE_MIN:
            return True
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
        score = candidate.get("momentum_score")
        indep = int(candidate.get("independent_hit_count") or 0)
        # v2.2（spec §7.3 BROAD_BULL 高信心）：score >= 75 且獨立 episode >= 2
        if (leader and hit >= 2) or (
            score is not None and score >= _REGIME_BULL_HIGH_CONVICTION_SCORE and indep >= 2
        ):
            return "high"
        if hit >= 2 or leader or (
            score is not None and score >= _REGIME_BULL_MEDIUM_CONVICTION_SCORE
        ):
            return "medium"
        return "low"

    if market_regime == REGIME_VOLATILE_RANGE:
        # 資料導向：重複命中（hit>=3）是震盪盤最可靠訊號 → high
        if hit >= 3:
            return "high"
        if leader or hit >= 2:
            return "medium"
        return "low"

    # RISK_OFF：能存活的已是 LEADER + hit>=3，是當下最強的一批 → high
    return "high"


def regime_watch_intensity(market_regime: str, conviction: Optional[str]) -> str:
    """(regime, conviction) → watch_intensity（前端一眼判斷今日清單要不要積極看）。

    aggressive：值得明天積極盯；normal：正常觀察；cautious：保留 / 嚴守紀律。
    """
    conv = conviction or "low"
    table = {
        (REGIME_BULL_TREND, "high"): "aggressive",
        (REGIME_BULL_TREND, "medium"): "normal",
        (REGIME_BULL_TREND, "low"): "cautious",
        (REGIME_VOLATILE_RANGE, "high"): "normal",
        (REGIME_VOLATILE_RANGE, "medium"): "cautious",
        (REGIME_VOLATILE_RANGE, "low"): "cautious",
    }
    if market_regime == REGIME_RISK_OFF:
        return "cautious"
    return table.get((market_regime, conv), "cautious")


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
    # 1. 人工黑名單（idempotent；候選池應已過濾，但保險再過一次）。
    # P2：ETF / 金融股與一般股共用後續 hard/base/regime gate。
    if is_blacklisted(sid):
        return True

    # 2. 三大法人合計近 5 日 net < 0 且非 ROTATION_LAGGARD
    flow_5d = candidate.get("total_institution_flow_5d")
    prelim = candidate.get("prelim_type")
    if (
        flow_5d is not None
        and flow_5d < 0
        and prelim != PRELIM_TYPE_ROTATION_LAGGARD
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

    # 8. v2.1：弱相對強度且未改善（spec §6.4）
    #    rs_market_percentile_20d < 40 且 rs_rank_improvement_5d <= 0 → 剔除。
    #    任一缺值（新上市 / universe 樣本不足）→ 不剔除（沿用資料缺漏不清池慣例；
    #    這類股在震盪盤仍會被 momentum_score gate 擋掉）。
    rs_mkt = candidate.get("rs_market_percentile_20d")
    rs_improvement = candidate.get("rs_rank_improvement_5d")
    if (
        rs_mkt is not None
        and rs_mkt < _HARD_RS_MARKET_PERCENTILE_MIN
        and rs_improvement is not None
        and rs_improvement <= 0
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
