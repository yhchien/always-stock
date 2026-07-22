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

--------------------------------------------------------------------------
2026-07-22 Hard Exclusion 重構（§十四 TRUE HARD EXCLUSION 最終定義）
--------------------------------------------------------------------------
Hard Exclusion 只能代表「即使後續題材/公司業務/角色/LLM 驗證多好，也不應再進入
WATCH 評估」的真正失效情況——「漲很多」「短線過熱」「法人單日小幅轉賣」「單日跌幅
較大」都不是失敗的證明，只是 entry risk 高。重構前這些單一 % 門檻會直接刪除候選，
本輪一律降級為 risk_warning，不再 Hard Exclude：

    - ETF / 金融股（資產類型不再是排除理由，只有人工黑名單才是）
    - 近 3 日漲幅 > 15%          → risk_warning: EXTENDED_3D
    - 股價級距 × 日張數門檻         → risk_warning: LOW_RAW_VOLUME
    - 近 10 日漲幅 >25% + 法人轉賣 → risk_warning: EXTENDED_PROFIT_TAKING_WARNING
    - 3D 法人正+今日反轉賣+跌>1.5% → risk_warning: INSTITUTION_REVERSAL_WARNING

`HARD_EXCLUSION_VERSION = "phase2_new_hard_gate"` 標記本輪方法論版本（寫進
explain_trace / funnel_metrics），供 historical snapshot 區分新舊規則。

重構後 TRUE HARD EXCLUSION 只剩 6 種（`REASON_*` 常數）：
    1. MANUAL_BLACKLIST：人工黑名單
    2. FAILED_FOLLOW_THROUGH_CURRENT_EPISODE：`candidate_pool._load_tracking_status`
       算好的「當前 cycle 主升段驗證失敗」——天生 cycle-scoped（cycle 結束後
       `signal_watch_hits` 對應列會被 archive.py 刪除，新一輪命中會是全新
       first_seen_date），不會永久封殺股票
    3. STRUCTURE_DAMAGED：`entry_state.py` 已要求「距高點 >= 4 倍 ATR」（結構性、
       非固定 % 門檻）+「RS 排名同時惡化」雙重確認，不是單一日跌幅
    4. COMPOSITE_RISK_EXCLUDE（原 `risk_gate_action == EXCLUDE`）：
       `deterministic_signals.py` 既有兩條路徑（distribution+institution_flow_
       reversal，或 failed_rotation+momentum_phase=weakening）本來就已橫跨兩個
       獨立 evidence family，不重寫判斷邏輯，只新增 `evidence_families` 標記
    5. LIQUIDITY_FAILURE：5 日均成交金額 < 5,000 萬 TWD（沿用既有門檻，集中為
       `liquidity_eligible()` helper，為未來 NEWLY_ACTIVATED 分級留接口）
    6. REVERSAL_FAILURE（新）：取代原本粗糙的「10D>25%+法人賣」「3D法人正+反轉+
       跌>1.5%」兩條規則——法人反轉需有實質性（`institution_reversal_ratio`）
       + 相對大盤明顯轉弱（`excess_return_vs_market`，非絕對報酬）+ 至少一項
       獨立 deterioration confirmation，三者同時成立才 Hard Exclude

`distribution` soft hint 定位不變（2026-07-21 起只影響 conviction，見
`compute_conviction()`），本輪未重新升級為 hard 條件。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.exclusions import is_blacklisted
from app.signals.filters import (
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
# 的替代存活條件。
_RISK_OFF_SURVIVOR_TRACKING_STATES = (
    tracking_mod.TRACKING_ACTIVE_TREND,
    tracking_mod.TRACKING_REACCELERATING,
    tracking_mod.TRACKING_HEALTHY_PULLBACK,
)
_RISK_OFF_MIN_MARKET_RS = 90.0

# Hard exclusion 方法論版本（2026-07-22 重構）
HARD_EXCLUSION_VERSION = "phase2_new_hard_gate"
HARD_EXCLUSION_VERSION_LEGACY = "phase2_old_hard_gate"

# ---------------- Hard exclusion reasons（§十四，僅這 6 種可 Hard Exclude）----------------
REASON_MANUAL_BLACKLIST = "MANUAL_BLACKLIST"
REASON_FAILED_FOLLOW_THROUGH = "FAILED_FOLLOW_THROUGH_CURRENT_EPISODE"
REASON_STRUCTURE_DAMAGED = "STRUCTURE_DAMAGED"
REASON_COMPOSITE_RISK_EXCLUDE = "COMPOSITE_RISK_EXCLUDE"
REASON_LIQUIDITY_FAILURE = "LIQUIDITY_FAILURE"
REASON_REVERSAL_FAILURE = "REVERSAL_FAILURE"

ALL_HARD_EXCLUSION_REASONS = (
    REASON_MANUAL_BLACKLIST,
    REASON_FAILED_FOLLOW_THROUGH,
    REASON_STRUCTURE_DAMAGED,
    REASON_COMPOSITE_RISK_EXCLUDE,
    REASON_LIQUIDITY_FAILURE,
    REASON_REVERSAL_FAILURE,
)

# ---------------- Risk warning flags（不 hard exclude，只降級 conviction/entry risk）----------------
WARNING_EXTENDED_3D = "EXTENDED_3D"
WARNING_EXTENDED_PROFIT_TAKING = "EXTENDED_PROFIT_TAKING_WARNING"
WARNING_INSTITUTION_REVERSAL = "INSTITUTION_REVERSAL_WARNING"
WARNING_LOW_RAW_VOLUME = "LOW_RAW_VOLUME"

# ---------------- Evidence families ----------------
# COMPOSITE_RISK_EXCLUDE / REVERSAL_FAILURE 都要求「至少兩個獨立 family」，避免
# 同一個底層事實被拆成兩個 flag 灌水成「多重確認」。
FAMILY_PRICE_STRUCTURE = "PRICE_STRUCTURE"
FAMILY_RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
FAMILY_INSTITUTION_FLOW = "INSTITUTION_FLOW"
FAMILY_VOLUME_PRICE = "VOLUME_PRICE"
FAMILY_SECTOR_ROTATION = "SECTOR_ROTATION"
FAMILY_RETAIL_POSITIONING = "RETAIL_POSITIONING"

# deterministic_signals.py 既有 risk_flags → evidence family 對照（純標記，不改
# risk_gate_action 的判斷邏輯）。
_RISK_FLAG_TO_FAMILY = {
    "institution_flow_reversal": FAMILY_INSTITUTION_FLOW,
    "distribution": FAMILY_VOLUME_PRICE,
    "failed_rotation": FAMILY_SECTOR_ROTATION,
    "retail_overheated": FAMILY_RETAIL_POSITIONING,
    "extended_chase": FAMILY_PRICE_STRUCTURE,
    "rs_deterioration": FAMILY_RELATIVE_STRENGTH,
}

# REVERSAL_FAILURE 門檻（工程起始值，待更多 replay 校準；不得為了特定案例硬調）
_REVERSAL_RATIO_MIN = 0.5            # 今日反轉賣超 / 前段（扣除今日）累積買超 的最低比例
_REVERSAL_EXCESS_RETURN_MAX = -1.5   # 個股相對大盤當日超額報酬門檻（沿用舊 -1.5 數值，語意改為相對）
_REVERSAL_VOLUME_DECLINE_RATIO = 1.5
_REVERSAL_CLOSE_NEAR_LOW_PCT = 0.8   # (high-close)/(high-low) >= 此值 → 收在當日低檔
_REVERSAL_SECTOR_DIVERGENCE_PRICE_PCT = -1.0


def has_distribution_hint(candidate: Dict[str, Any]) -> bool:
    return "distribution" in (candidate.get("soft_hints") or [])


def liquidity_eligible(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """§六：流動性 Hard Gate，集中成獨立 helper，為未來 NEWLY_ACTIVATED 分級留接口。

    第一版：5 日均成交金額 < 5,000 萬 TWD 視為不合格；資料缺值 → 視為合格
    （沿用既有「資料缺漏不清池」慣例，避免資料缺漏日整池被剔）。
    """
    turnover_5d = candidate.get("avg_turnover_5d")
    if turnover_5d is None:
        return {"eligible": True, "state": "UNKNOWN"}
    if turnover_5d < _HARD_LIQUIDITY_MIN_TWD:
        return {"eligible": False, "state": "INSUFFICIENT"}
    return {"eligible": True, "state": "NORMAL"}


def _institution_reversal_ratio(candidate: Dict[str, Any]) -> Optional[float]:
    """今日法人賣超 / 前段（扣除今日的 3 日累計）買超，越大代表反轉越實質。

    今日非賣超、或前段本來就沒有淨買超（<=0）→ None（無從定義「反轉」，不可用
    非正值分母硬算出一個誤導的比例）。
    """
    flow_1d = candidate.get("total_institution_flow_1d")
    flow_3d = candidate.get("total_institution_flow_3d")
    if flow_1d is None or flow_3d is None or flow_1d >= 0:
        return None
    prior = flow_3d - flow_1d
    if prior <= 0:
        return None
    return abs(flow_1d) / prior


def _excess_return_vs_market(
    candidate: Dict[str, Any],
    taiex_return_1d_pct: Optional[float],
) -> Optional[float]:
    """個股當日報酬 - 大盤當日報酬。缺任一方 → None（不可用絕對報酬冒充相對報酬，
    這正是舊 Rule 9「大盤 -5%、股票 -2% 卻被當出貨確認」的根因）。"""
    pct_1d = candidate.get("price_change_1d")
    if pct_1d is None or taiex_return_1d_pct is None:
        return None
    return pct_1d - taiex_return_1d_pct


def _has_high_volume_decline(candidate: Dict[str, Any]) -> bool:
    vol = candidate.get("volume_1d_to_5d_ratio")
    pct = candidate.get("price_change_1d")
    return (
        vol is not None and vol > _REVERSAL_VOLUME_DECLINE_RATIO
        and pct is not None and pct < 0
    )


def _has_close_near_low(candidate: Dict[str, Any]) -> bool:
    high = candidate.get("high_1d")
    low = candidate.get("low_1d")
    close = candidate.get("close_1d")
    if high is None or low is None or close is None or high <= low:
        return False
    return (high - close) / (high - low) >= _REVERSAL_CLOSE_NEAR_LOW_PCT


def _has_sector_divergence(candidate: Dict[str, Any]) -> bool:
    """產業當日仍是淨買超（沒有輪動失敗），但個股自己明顯下跌 → 個股層級的獨立異常。"""
    industry_flow_1d = candidate.get("industry_flow_1d")
    pct_1d = candidate.get("price_change_1d")
    return (
        industry_flow_1d is not None and industry_flow_1d > 0
        and pct_1d is not None and pct_1d < _REVERSAL_SECTOR_DIVERGENCE_PRICE_PCT
    )


def compute_risk_warnings(
    candidate: Dict[str, Any],
    taiex_return_1d_pct: Optional[float] = None,
) -> List[str]:
    """§五~十一：原本會單獨 Hard Exclude 的條件，重構後只標成 warning，不剔除。

    `taiex_return_1d_pct` 目前保留參數位（暫未使用在 warning 層，REVERSAL_FAILURE
    才需要），維持函式簽章一致，未來若要把 warning 也改成相對報酬可直接使用。
    """
    warnings: List[str] = []

    pct_3d = candidate.get("price_change_3d")
    if pct_3d is not None and pct_3d > _HARD_PRICE_3D_OVERHEAT_PCT:
        warnings.append(WARNING_EXTENDED_3D)

    pct_10d = candidate.get("price_change_10d")
    flow_1d = candidate.get("total_institution_flow_1d")
    if (
        pct_10d is not None and pct_10d > _HARD_PRICE_EXTENDED_10D_PCT
        and flow_1d is not None and flow_1d < 0
    ):
        warnings.append(WARNING_EXTENDED_PROFIT_TAKING)

    flow_3d = candidate.get("total_institution_flow_3d")
    pct_1d = candidate.get("price_change_1d")
    if (
        flow_3d is not None and flow_3d > 0
        and flow_1d is not None and flow_1d < 0
        and pct_1d is not None and pct_1d < -1.5
    ):
        warnings.append(WARNING_INSTITUTION_REVERSAL)

    if _below_volume_deadline(candidate):
        warnings.append(WARNING_LOW_RAW_VOLUME)

    return warnings


def _is_reversal_failure(
    candidate: Dict[str, Any],
    taiex_return_1d_pct: Optional[float],
) -> Dict[str, Any]:
    """§九~十三：真正的「當日反轉失效」，需要三個條件同時成立：
        A. 法人反轉具實質性（`institution_reversal_ratio >= 0.5`）
        B. 相對大盤明顯轉弱（`excess_return_vs_market <= -1.5`，非絕對報酬）
        C. 至少再有一個獨立 deterioration confirmation（非 A/B 所屬 family：
           PRICE_STRUCTURE / VOLUME_PRICE / SECTOR_ROTATION 任一）

    任一條件因資料缺值無法確認 → 不觸發（不可幻想）。
    """
    ratio = _institution_reversal_ratio(candidate)
    condition_a = ratio is not None and ratio >= _REVERSAL_RATIO_MIN

    excess = _excess_return_vs_market(candidate, taiex_return_1d_pct)
    condition_b = excess is not None and excess <= _REVERSAL_EXCESS_RETURN_MAX

    if not (condition_a and condition_b):
        return {"triggered": False, "evidence_families": [], "institution_reversal_ratio": ratio, "excess_return_vs_market": excess}

    families = [FAMILY_INSTITUTION_FLOW, FAMILY_RELATIVE_STRENGTH]
    if candidate.get("entry_state") == entry_state_mod.ENTRY_STRUCTURE_DAMAGED:
        families.append(FAMILY_PRICE_STRUCTURE)
    if _has_high_volume_decline(candidate) or _has_close_near_low(candidate):
        families.append(FAMILY_VOLUME_PRICE)
    if _has_sector_divergence(candidate):
        families.append(FAMILY_SECTOR_ROTATION)

    condition_c = len(families) > 2  # 除了 A(INSTITUTION_FLOW) / B(RELATIVE_STRENGTH) 外還有第三個獨立 family
    return {
        "triggered": condition_c,
        "evidence_families": families if condition_c else [FAMILY_INSTITUTION_FLOW, FAMILY_RELATIVE_STRENGTH],
        "institution_reversal_ratio": ratio,
        "excess_return_vs_market": excess,
    }


def _composite_risk_evidence_families(candidate: Dict[str, Any]) -> List[str]:
    """把 `deterministic_signals.risk_flags` 映射成 evidence family（純標記，不改
    `_risk_gate_action` 的判斷邏輯——目前兩條路徑本來就已橫跨兩個獨立 family：
    distribution(VOLUME_PRICE)+institution_flow_reversal(INSTITUTION_FLOW)，或
    failed_rotation(SECTOR_ROTATION)+momentum_phase=weakening(RELATIVE_STRENGTH，
    risk_flags 本身沒有獨立標記 weakening，這裡補上)。"""
    signals = candidate.get("deterministic_signals") or {}
    flags = signals.get("risk_flags") or []
    families = {_RISK_FLAG_TO_FAMILY[f] for f in flags if f in _RISK_FLAG_TO_FAMILY}
    if signals.get("risk_gate_action") == "EXCLUDE" and candidate.get("momentum_phase") == "weakening":
        families.add(FAMILY_RELATIVE_STRENGTH)
    return sorted(families)


def build_hard_exclusion_result(
    candidate: Dict[str, Any],
    *,
    taiex_return_1d_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """§十八：Explain Trace 用的完整 hard exclusion 結果，也是
    `is_true_hard_exclusion()` 的實作基礎。

    優先序（找到第一個成立的就回傳，後面條件不再檢查）：
        1. MANUAL_BLACKLIST
        2. FAILED_FOLLOW_THROUGH_CURRENT_EPISODE
        3. STRUCTURE_DAMAGED
        4. LIQUIDITY_FAILURE
        5. COMPOSITE_RISK_EXCLUDE
        6. REVERSAL_FAILURE

    回傳結構固定：
        {
          "excluded": bool,
          "reason": str | None,
          "matched_hard_rules": [str],
          "risk_warnings": [str],
          "liquidity_state": str,
          "evidence_families": [str],
        }
    """
    sid = candidate.get("stock_id") or ""
    warnings = compute_risk_warnings(candidate, taiex_return_1d_pct)
    liquidity = liquidity_eligible(candidate)

    def _result(reason: Optional[str], evidence_families: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "excluded": reason is not None,
            "reason": reason,
            "matched_hard_rules": [reason] if reason else [],
            "risk_warnings": warnings,
            "liquidity_state": liquidity["state"],
            "evidence_families": evidence_families or [],
        }

    if is_blacklisted(sid):
        return _result(REASON_MANUAL_BLACKLIST)

    if candidate.get("failed_follow_through"):
        return _result(REASON_FAILED_FOLLOW_THROUGH)

    if candidate.get("entry_state") in _TRUE_HARD_EXCLUSION_ENTRY_STATES:
        return _result(REASON_STRUCTURE_DAMAGED, [FAMILY_PRICE_STRUCTURE, FAMILY_RELATIVE_STRENGTH])

    if not liquidity["eligible"]:
        return _result(REASON_LIQUIDITY_FAILURE)

    signals = candidate.get("deterministic_signals") or {}
    if signals.get("risk_gate_action") == "EXCLUDE":
        return _result(REASON_COMPOSITE_RISK_EXCLUDE, _composite_risk_evidence_families(candidate))

    reversal = _is_reversal_failure(candidate, taiex_return_1d_pct)
    if reversal["triggered"]:
        return _result(REASON_REVERSAL_FAILURE, reversal["evidence_families"])

    return _result(None)


def is_true_hard_exclusion(
    candidate: Dict[str, Any],
    *,
    taiex_return_1d_pct: Optional[float] = None,
) -> Optional[str]:
    """向後相容的簡易介面：只回傳 reason 字串或 None（找不到排除原因）。

    完整結構（risk_warnings / evidence_families / liquidity_state）請用
    `build_hard_exclusion_result()`——`pipeline_v2.build_phase2_pool` 用那個組
    explain trace，這裡只給既有呼叫端（例如 `apply_regime_gate_v2` 的二次防禦性
    檢查）維持原本的布林/字串介面。
    """
    return build_hard_exclusion_result(candidate, taiex_return_1d_pct=taiex_return_1d_pct)["reason"]


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
        - 真正的 hard exclusion 抽到 `is_true_hard_exclusion()`（二次防禦性檢查；
          候選在 `build_phase2_pool` 已經過一次，這裡的候選理論上不會再命中），
          任何 regime 都適用
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
