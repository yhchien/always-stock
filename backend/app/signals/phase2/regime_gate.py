"""
Phase 2 §O/§P/§Q：Regime Gate（hit_count 從 hard eligibility gate 改為 conviction
enhancer）+ True Hard Exclusions。

解決統一案例：hit_count=2（< legacy 的 3）在舊版 RISK_OFF gate 直接被剔除，即使
它是強 LEADER、RS 91.5、法人在買。這是 incumbency bias——新股票不可能一開始就有
高 hit_count，用它當 hard gate 等於系統性偏袒「已經被抓過很多次」的股票。

`hit_count` / `independent_hit_count` 在 Phase 2 只能：
    - 提升 conviction（更有信心繼續 WATCH）
    - 作為 continuation evidence（tracking_state 的輔助資訊）
不能單獨造成 REMOVE。

Hard exclusion（§P）只保留真正該死的項目：
    - failed_follow_through（已由 candidate_pool 標記，這裡不重複判斷）
    - entry_state = STRUCTURE_DAMAGED
    - risk_gate_action = EXCLUDE（deterministic_signals 既有欄位，沿用）

**2026-07-21 修正（用 7/20 真實資料 replay 後使用者確認）**：`distribution`
soft hint **降級為 conviction 影響因子，不再是 hard exclusion**。原因：這個
hint 沿用 legacy 的「爆量不漲 / 高檔長上影」偵測，在大盤系統性下跌日（例如
RISK_OFF regime 那天）幾乎任何股票都會因為當日大盤跟跌而長出長上影——用它做
無條件硬剔除，效果等同於「大盤跌的那天，強勢股全部被自己的當日 K 線誤殺」，
這正是原始診斷想解決的同一種症狀（見台化/台虹/長榮/萬海/台塑化 7/20 replay
案例，5 檔全部因此被誤殺）。legacy 原本就只把它當 soft hint（餵給 LLM 參考，
不影響是否進候選名單），Phase 2 這裡恢復同樣的定位。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.exclusions import should_exclude
from app.signals.filters import (
    _HARD_DIVERGENCE_PRICE_1D_DROP_PCT,
    _HARD_LIQUIDITY_MIN_TWD,
    _HARD_PRICE_3D_OVERHEAT_PCT,
    _HARD_PRICE_EXTENDED_10D_PCT,
    _below_volume_deadline,
)
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import roles as roles_mod
from app.signals.phase2 import tracking_state as tracking_mod

REGIME_BULL_TREND = "BULL_TREND"
REGIME_VOLATILE_RANGE = "VOLATILE_RANGE"
REGIME_RISK_OFF = "RISK_OFF"

CONVICTION_HIGH = "high"
CONVICTION_MEDIUM = "medium"
CONVICTION_LOW = "low"

_CONVICTION_DOWNGRADE = {CONVICTION_HIGH: CONVICTION_MEDIUM, CONVICTION_MEDIUM: CONVICTION_LOW, CONVICTION_LOW: CONVICTION_LOW}

_TRUE_HARD_EXCLUSION_ENTRY_STATES = (entry_state_mod.ENTRY_STRUCTURE_DAMAGED,)
_RISK_OFF_SURVIVOR_ROLES = (
    roles_mod.ROLE_SECTOR_LEADER,
    roles_mod.ROLE_CO_LEADER,
    roles_mod.ROLE_INDEPENDENT_LEADER,
)
# 已追蹤股在 RISK_OFF 沒有 `role`（分類權讓給 tracking_state，見 §N），但體質
# 沒壞（ACTIVE_TREND/REACCELERATING/HEALTHY_PULLBACK）時，視為 formal leader
# 的替代存活條件——不然漢翔/星宇這類「已追蹤且回檔健康」的股票會因為欄位
# 對不上而被 RISK_OFF 誤刪（2026-07-21 用 7/20 replay 發現的落差，當輪修正）。
_RISK_OFF_SURVIVOR_TRACKING_STATES = (
    tracking_mod.TRACKING_ACTIVE_TREND,
    tracking_mod.TRACKING_REACCELERATING,
    tracking_mod.TRACKING_HEALTHY_PULLBACK,
)
_RISK_OFF_MIN_MARKET_RS = 90.0


def has_distribution_hint(candidate: Dict[str, Any]) -> bool:
    return "distribution" in (candidate.get("soft_hints") or [])


def is_true_hard_exclusion(candidate: Dict[str, Any]) -> Optional[str]:
    """§P：真正應該 hard fail 的項目，回傳排除原因；不排除回 None。

    這裡刻意 **不依賴 legacy `prelim_type`**（Phase 2 候選池直接來自
    `build_candidate_pool()`，不經過 legacy `classification.classify_stocks()`
    的三選一硬刪除）。除了 Phase 2 自有的判斷（failed_follow_through /
    structure_damaged / risk_gate_action），也納入 legacy
    `filters._is_hard_excluded` 裡與 prelim_type **無關**的「真正定義性」排除
    （§P 明訂：invalid security type / liquidity failure），沿用同一組門檻常數
    確保跟 legacy 對齊，不重新發明數字。legacy 條件 #2（法人 5 日流出且非
    ROTATION_LAGGARD）**刻意不搬進來**——那條依賴的正是 Phase 2 要拿掉的
    prelim_type 硬分類，Phase 2 改用 role + regime gate 表達同樣的風險意圖。

    `distribution` **不在這裡**（2026-07-21 起降級為 conviction 影響因子，
    見 `compute_conviction()`）。
    """
    sid = candidate.get("stock_id") or ""
    name = candidate.get("name")
    industry = candidate.get("industry")
    if should_exclude(sid, name, industry):
        return "invalid_security_type"

    if candidate.get("failed_follow_through"):
        return "failed_follow_through"

    if candidate.get("entry_state") in _TRUE_HARD_EXCLUSION_ENTRY_STATES:
        return "structure_damaged"

    if candidate.get("risk_gate_action") == "EXCLUDE":
        return "risk_gate_action_exclude"

    pct_3d = candidate.get("price_change_3d")
    if pct_3d is not None and pct_3d > _HARD_PRICE_3D_OVERHEAT_PCT:
        return "overheat_3d"

    turnover_5d = candidate.get("avg_turnover_5d")
    if turnover_5d is not None and turnover_5d < _HARD_LIQUIDITY_MIN_TWD:
        return "liquidity_failure"

    if _below_volume_deadline(candidate):
        return "volume_deadline"

    pct_10d = candidate.get("price_change_10d")
    flow_1d = candidate.get("total_institution_flow_1d")
    if (
        pct_10d is not None and pct_10d > _HARD_PRICE_EXTENDED_10D_PCT
        and flow_1d is not None and flow_1d < 0
    ):
        return "extended_with_institution_selling"

    flow_3d = candidate.get("total_institution_flow_3d")
    pct_1d = candidate.get("price_change_1d")
    if (
        flow_3d is not None and flow_3d > 0
        and flow_1d is not None and flow_1d < 0
        and pct_1d is not None and pct_1d < _HARD_DIVERGENCE_PRICE_1D_DROP_PCT
    ):
        return "institution_reversal_divergence"

    return None


def compute_conviction(candidate: Dict[str, Any], regime: str) -> str:
    """hit_count / independent_hit_count 只在這裡影響 conviction，不影響生死。

    `distribution` soft hint（2026-07-21 起）也只在這裡發生作用：命中時把算出來
    的 conviction 降一級（high→medium→low→low），不影響是否存活——大盤系統性
    下跌日這個訊號雜訊很大，降級而非硬殺，讓其他強證據（formal leader / 追蹤
    體質健康）仍有機會蓋過去。
    """
    hit_count = candidate.get("hit_count") or 0
    role = candidate.get("role")
    is_leader = role in _RISK_OFF_SURVIVOR_ROLES

    if regime == REGIME_RISK_OFF:
        if is_leader and hit_count >= 3:
            conviction = CONVICTION_HIGH
        elif is_leader:
            conviction = CONVICTION_MEDIUM
        else:
            conviction = CONVICTION_LOW
    elif regime == REGIME_VOLATILE_RANGE:
        if hit_count >= 3:
            conviction = CONVICTION_HIGH
        elif is_leader or hit_count == 2:
            conviction = CONVICTION_MEDIUM
        else:
            conviction = CONVICTION_LOW
    else:
        # BULL_TREND
        if is_leader and hit_count >= 2:
            conviction = CONVICTION_HIGH
        elif hit_count >= 2 or is_leader:
            conviction = CONVICTION_MEDIUM
        else:
            conviction = CONVICTION_LOW

    if has_distribution_hint(candidate):
        conviction = _CONVICTION_DOWNGRADE[conviction]

    return conviction


def apply_regime_gate_v2(
    candidates: List[Dict[str, Any]],
    regime: str,
) -> List[Dict[str, Any]]:
    """
    §Q：Phase 2 regime gate。輸入為已經跑過 `roles.classify_roles()` 的候選
    （每筆需含 `role` 欄位），回傳存活者（新增 `conviction` 欄位；不 mutate 原 dict）。

    與 legacy `filters.apply_regime_gate` 的關鍵差異：
        - hit_count 不再是任何 regime 的 hard 條件
        - RISK_OFF 存活條件改用「role 是否為 formal leader + market RS 夠高」，
          **或**「已追蹤且 tracking_state 顯示體質健康」（見
          `_RISK_OFF_SURVIVOR_TRACKING_STATES`）——不要求 hit_count >= 3
        - 真正的 hard exclusion 抽到 `is_true_hard_exclusion()`，任何 regime 都適用
        - `distribution` 不在 hard exclusion 裡，只透過 `compute_conviction()`
          降低信心度
    """
    out: List[Dict[str, Any]] = []
    for c in candidates:
        reason = is_true_hard_exclusion(c)
        if reason:
            continue

        if regime == REGIME_RISK_OFF:
            role = c.get("role")
            tracking = c.get("tracking_state")
            market_rs = c.get("rs_market_percentile_20d") or 0
            is_formal_leader = role in _RISK_OFF_SURVIVOR_ROLES
            is_healthy_tracked = tracking in _RISK_OFF_SURVIVOR_TRACKING_STATES
            if not (is_formal_leader or is_healthy_tracked) or market_rs < _RISK_OFF_MIN_MARKET_RS:
                continue

        conviction = compute_conviction(c, regime)
        out.append({**c, "conviction": conviction, "regime": regime})

    return out
