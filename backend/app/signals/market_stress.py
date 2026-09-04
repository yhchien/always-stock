"""M27 Market Regime v2 — Market Stress Overlay（deterministic，backend authoritative）。

解「TAIEX 均線仍為多頭 ≠ 個股動能環境健康」的落差：`market_regime.py` 的
trend_regime（BULL_TREND / VOLATILE_RANGE / RISK_OFF）只描述指數技術趨勢；
本模組疊一層獨立維度 `market_stress`（NORMAL / CAUTION / STRESS / UNKNOWN），
描述「當下市場實際交易環境（內部結構、資金、全球風險、總體商品）是否已經
惡化」，兩者合成 `effective_market_state` 給 Global Selector 當背景參考。

**唯一權威**：全部 deterministic 計算，LLM 不參與、不能改寫。

四個獨立 Evidence Family（各自只能輸出 HEALTHY/NEUTRAL/WARNING/STRESS/UNKNOWN，
不做加權合成單一分數 — 見規格書 §7「不要建立 market_stress_score = 73」）：
- LOCAL_MARKET_INTERNALS：沿用既有 `market_breadth.py`（全市場漲跌家數/均線
  上方比例/創高低家數），加一項 cap-weight divergence（指數 vs 個股中位數
  報酬落差，抓「權值股撐指數、個股普遍轉弱」）
- TAIWAN_FLOW_AND_DERIVATIVES：外資現貨（`inst_stock_flow`，percentile-based，
  不看固定金額）+ 外資臺指期未平倉（`market_stress_indicators`，看部位水位
  百分位 + 轉弱幅度，不是「淨空 = 一定壓力」）+ TXO Put/Call（只能當
  confirmation，不可單獨判 STRESS）+ 台灣 VIX（**結構性缺席**，FinMind 無此
  dataset，永久 UNKNOWN，不假裝正常）
- GLOBAL_RISK：美國 VIX + Nasdaq + SOX（半導體權重優先於大盤指數，貼近台股
  電子供應鏈背景）；US10Y **結構性缺席**（FinMind 無對應 dataset）；本 Family
  單獨 STRESS 不可讓整體 market_stress 變 STRESS（見 §10「避免美國市場震盪
  自動讓台灣進入高壓」）
- MACRO_COMMODITY_RISK：原油（區分 SUPPLY_INFLATION_STRESS / DEMAND_GROWTH /
  DEMAND_DESTRUCTION，油價本身不可單獨判斷）+ 黃金（只能 SAFE_HAVEN_
  CONFIRMATION，不可單獨造成 STRESS）+ USD/TWD（貶值只能當資金壓力
  confirmation，不可單獨造成 STRESS）

State machine（§十二）：
- NORMAL：0 個 Family STRESS 且 WARNING Family 數 <= 1
- STRESS：>= 2 個 Family STRESS，且至少一個來自 LOCAL_MARKET_INTERNALS 或
  TAIWAN_FLOW_AND_DERIVATIVES（純海外/總體壓力不足以讓台股判定 STRESS）
- CAUTION：介於中間（>=1 Family STRESS，或 >=2 Family WARNING）

`effective_market_state`（§十三）：trend_regime × market_stress 的 deterministic
mapping；RISK_OFF 維持最高風險語意，不拆 RISK_OFF_STRESSED（避免 state
explosion）。

資料缺失政策（§二十五）：缺值一律 UNKNOWN，不可假裝 NORMAL / 0；每個 Family
存 `data_available_count`/`data_expected_count`/`data_complete`；只有全部四個
Family 都 UNKNOWN，整體 `market_stress` 才是 UNKNOWN。

**Rollout（本輪不落三階段流程，但保留 mode 開關本身）**：`MARKET_REGIME_V2_MODE`
env（off/shadow/global_only/production），預設 `shadow`——完整計算、寫進
snapshot／debug，但**不傳進 Global Selector、不改 conviction、不影響 P4 停止
判斷、對現有選股結果零影響**。要不要切到 global_only／production 由使用者
自行決定調整 env，不由本次改動自動切換。
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import MarketStressIndicator
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)

# ---- Mode ----
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_GLOBAL_ONLY = "global_only"
MODE_PRODUCTION = "production"
_VALID_MODES = {MODE_OFF, MODE_SHADOW, MODE_GLOBAL_ONLY, MODE_PRODUCTION}


def market_regime_v2_mode() -> str:
    raw = (os.getenv("MARKET_REGIME_V2_MODE") or MODE_SHADOW).strip().lower()
    return raw if raw in _VALID_MODES else MODE_SHADOW


# ---- Family status enum ----
STATUS_HEALTHY = "HEALTHY"
STATUS_NEUTRAL = "NEUTRAL"
STATUS_WARNING = "WARNING"
STATUS_STRESS = "STRESS"
STATUS_UNKNOWN = "UNKNOWN"
_STATUS_ORDER = [STATUS_HEALTHY, STATUS_NEUTRAL, STATUS_WARNING, STATUS_STRESS]

FAMILY_LOCAL = "LOCAL_MARKET_INTERNALS"
FAMILY_FLOW = "TAIWAN_FLOW_AND_DERIVATIVES"
FAMILY_GLOBAL = "GLOBAL_RISK"
FAMILY_MACRO = "MACRO_COMMODITY_RISK"
ALL_FAMILIES = [FAMILY_LOCAL, FAMILY_FLOW, FAMILY_GLOBAL, FAMILY_MACRO]

# ---- Market stress state ----
STRESS_NORMAL = "NORMAL"
STRESS_CAUTION = "CAUTION"
STRESS_STRESS = "STRESS"
STRESS_UNKNOWN = "UNKNOWN"

# ---- Effective market state ----
EFFECTIVE_BULL_HEALTHY = "BULL_HEALTHY"
EFFECTIVE_BULL_CAUTION = "BULL_CAUTION"
EFFECTIVE_BULL_STRESSED = "BULL_STRESSED"
EFFECTIVE_VOLATILE_RANGE = "VOLATILE_RANGE"
EFFECTIVE_VOLATILE_STRESSED = "VOLATILE_STRESSED"
EFFECTIVE_RISK_OFF = "RISK_OFF"

_ROLLING_WINDOW_60D = 60

MARKET_REGIME_V2_VERSION = "market_regime_v2"


def _family_result(
    status: str,
    reason_codes: Sequence[str],
    *,
    raw_values: Dict[str, Any],
    data_available_count: int,
    data_expected_count: int,
) -> Dict[str, Any]:
    return {
        "status": status,
        "reason_codes": list(reason_codes),
        "raw_values": raw_values,
        "data_available_count": data_available_count,
        "data_expected_count": data_expected_count,
        "data_complete": data_available_count >= data_expected_count,
    }


def _percentile_rank(current: Optional[float], history: Sequence[float]) -> Optional[float]:
    """0（最低）~100（最高）：current 在 history（含自身）裡的百分位排名。

    用「mean rank」處理重複值（tie-aware）：`below + 0.5*equal`，不是單純
    `<=` 計數——否則資料完全持平（例如 VIX 連續多天都是同一個值）時，
    每個值都會因為 `h <= current` 對自己也成立而被誤判成「100 百分位」
    （看起來像極端值，但其實是完全沒波動），對照 spec §8.1「rolling
    percentile」的用意本來就是要正確反映「跟歷史比起來有沒有異常」，不是
    「剛好等於自己」這種平凡情況。
    """
    if current is None or not history:
        return None
    pool = list(history) + [current]
    n = len(pool)
    below = sum(1 for h in pool if h < current)
    equal = sum(1 for h in pool if h == current)
    return round(100.0 * (below + 0.5 * equal) / n, 1)


# ===================== Family A: LOCAL_MARKET_INTERNALS =====================


def compute_cap_weight_divergence(
    momentum_frame: Optional[Dict[str, Dict[str, Any]]],
    taiex_return_1d_pct: Optional[float],
) -> Optional[float]:
    """`cap_weight_divergence = taiex_return_1d - equal_weight_return_1d`。

    `equal_weight_return_1d` 用 momentum frame 全市場個股 `_ret_1d` 的中位數
    近似（frame 已是 market_breadth.py 同一份資料，不重複掃 daily_price）。
    正值大 → 權值股撐指數但多數股票實際更弱（INDEX_CONCENTRATION_WARNING）。
    """
    if not momentum_frame or taiex_return_1d_pct is None:
        return None
    rets = [
        float(feats["_ret_1d"])
        for feats in momentum_frame.values()
        if feats.get("_ret_1d") is not None
    ]
    if len(rets) < 100:
        return None
    rets.sort()
    n = len(rets)
    mid = n // 2
    equal_weight_return_1d = (
        rets[mid] if n % 2 == 1 else (rets[mid - 1] + rets[mid]) / 2.0
    )
    return round(taiex_return_1d_pct - equal_weight_return_1d, 2)


def classify_family_local(
    breadth: Optional[Dict[str, Any]],
    cap_weight_divergence: Optional[float],
) -> Dict[str, Any]:
    """Family A：直接 reuse `market_breadth.compute_breadth_from_frame()` 的
    `breadth_score`（0~100，加權漲跌家數/均線上方比例/創高低家數/強勢產業比）。

    Mapping（`breadth_score` 是既有 0~100 加權分數，非本模組發明）：
        >= 60          → HEALTHY
        45 ~ 59.9      → NEUTRAL
        30 ~ 44.9      → WARNING
        < 30           → STRESS
        None（樣本不足）→ UNKNOWN

    cap_weight_divergence 明顯偏高（權值股撐盤但個股中位數走弱）時，額外標
    `INDEX_CONCENTRATION_WARNING`，並把狀態上調最多一級（不會單獨造成 STRESS）。
    """
    breadth_score = (breadth or {}).get("breadth_score")
    raw_values = {
        "breadth_score": breadth_score,
        "pct_above_ma20": (breadth or {}).get("pct_above_ma20"),
        "advance_decline_ratio": (breadth or {}).get("advance_decline_ratio"),
        "new_high_20d_count": (breadth or {}).get("new_high_20d_count"),
        "new_low_20d_count": (breadth or {}).get("new_low_20d_count"),
        "cap_weight_divergence": cap_weight_divergence,
    }
    if breadth_score is None:
        return _family_result(
            STATUS_UNKNOWN,
            ["BREADTH_DATA_INSUFFICIENT"],
            raw_values=raw_values,
            data_available_count=0,
            data_expected_count=1,
        )

    if breadth_score >= 60:
        status = STATUS_HEALTHY
    elif breadth_score >= 45:
        status = STATUS_NEUTRAL
    elif breadth_score >= 30:
        status = STATUS_WARNING
    else:
        status = STATUS_STRESS

    reason_codes: List[str] = []
    if breadth_score < 45:
        reason_codes.append("BREADTH_DETERIORATION")

    # 權值股撐盤但個股普遍走弱：divergence > 1.5pct 視為顯著（TAIEX 比中位數股票
    # 多漲 1.5 個百分點以上），上調一級（不超過 STRESS）
    if cap_weight_divergence is not None and cap_weight_divergence > 1.5:
        reason_codes.append("INDEX_CONCENTRATION_WARNING")
        idx = _STATUS_ORDER.index(status)
        status = _STATUS_ORDER[min(idx + 1, len(_STATUS_ORDER) - 1)]

    return _family_result(
        status,
        reason_codes,
        raw_values=raw_values,
        data_available_count=1,
        data_expected_count=1,
    )


# ================= Family B: TAIWAN_FLOW_AND_DERIVATIVES =================


def _load_foreign_flow_history(
    db: Session, target_date: date, *, window: int = _ROLLING_WINDOW_60D
) -> List[tuple]:
    """近 window 個交易日（含 target_date）的全市場外資現貨單日淨買超金額
    （元），依日期升序。用 `inst_stock_flow`（既有表，不新增資料）。
    """
    rows = db.execute(
        text(
            """
            SELECT trade_date, SUM(net_amount_est) AS net_amount
            FROM inst_stock_flow
            WHERE inst_type = 'foreign' AND trade_date <= :d
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT :n
            """
        ),
        {"d": target_date, "n": window},
    ).all()
    return sorted([(r.trade_date, float(r.net_amount or 0.0)) for r in rows])


def _load_indicator_history(
    db: Session, target_date: date, *, window: int = _ROLLING_WINDOW_60D
) -> List[MarketStressIndicator]:
    rows = (
        db.query(MarketStressIndicator)
        .filter(MarketStressIndicator.trade_date <= target_date)
        .order_by(MarketStressIndicator.trade_date.desc())
        .limit(window)
        .all()
    )
    rows.reverse()
    return rows


def classify_family_flow(
    foreign_flow_history: List[tuple],
    indicator_history: List[MarketStressIndicator],
) -> Dict[str, Any]:
    """Family B：外資現貨（percentile-based，不看固定金額）+ 外資臺指期未平倉
    （水位百分位 + 轉弱幅度確認，不是「淨空 = 壓力」）+ TXO Put/Call
    （只能 confirmation，不可單獨判 STRESS）+ 台灣 VIX（結構性缺席，FinMind
    無對應 dataset，永久 UNKNOWN，計入 data_expected_count 但永遠不算
    data_available，讓 `data_complete` 誠實反映這個已知資料缺口）。
    """
    expected = 4  # foreign_spot / foreign_tx_oi / txo_pcr / taiwan_vix
    available = 0
    reason_codes: List[str] = []
    statuses: List[str] = []
    raw_values: Dict[str, Any] = {}

    # --- 1) 外資現貨：percentile-based（自身歷史百分位即是正規化，不需要另外
    #     除以成交額；規格書要求的重點是「不要用固定金額判斷」，percentile
    #     本身已經達成這個目的）。**用 3 日滾動加總**（不是單日值）當判斷
    #     依據——規格書明確要求「單一外資賣超日：不得自動 STRESS」，用 3 日
    #     窗口本身就要求持續性，單一極端日會被窗口內其他日子稀釋，不會單獨
    #     觸發（1 日值仍保留在 raw_values 供參考）---
    if foreign_flow_history:
        values = [v for _, v in foreign_flow_history]
        latest_1d = values[-1]
        rolling_3d = [
            sum(values[max(0, i - 2): i + 1]) for i in range(len(values))
        ]
        latest_3d = rolling_3d[-1]
        history_3d_before_latest = rolling_3d[:-1]
        pct = _percentile_rank(latest_3d, history_3d_before_latest)
        raw_values["foreign_net_flow_1d"] = latest_1d
        raw_values["foreign_net_flow_3d"] = latest_3d
        raw_values["foreign_flow_percentile_60d"] = pct
        if pct is not None:
            available += 1
            if pct <= 10:
                statuses.append(STATUS_STRESS)
                reason_codes.append("FOREIGN_CASH_OUTFLOW")
            elif pct <= 25:
                statuses.append(STATUS_WARNING)
                reason_codes.append("FOREIGN_CASH_OUTFLOW_MILD")
            elif pct >= 90:
                statuses.append(STATUS_HEALTHY)
            else:
                statuses.append(STATUS_NEUTRAL)

    # --- 2) 外資臺指期未平倉：水位百分位 + 轉弱幅度雙重確認 ---
    net_oi_series = [
        (r.trade_date, r.foreign_tx_net_oi)
        for r in indicator_history
        if r.foreign_tx_net_oi is not None
    ]
    if net_oi_series:
        latest_net_oi = net_oi_series[-1][1]
        history_net_oi = [v for _, v in net_oi_series[:-1]]
        net_oi_pct = _percentile_rank(latest_net_oi, history_net_oi)
        oi_change_3d = None
        if len(net_oi_series) >= 4:
            oi_change_3d = latest_net_oi - net_oi_series[-4][1]
        raw_values["foreign_tx_net_oi"] = latest_net_oi
        raw_values["foreign_tx_net_oi_percentile_60d"] = net_oi_pct
        raw_values["foreign_tx_net_oi_change_3d"] = oi_change_3d
        if net_oi_pct is not None:
            available += 1
            already_net_short = latest_net_oi < 0
            deteriorating = oi_change_3d is not None and oi_change_3d < 0
            if net_oi_pct <= 15 and already_net_short and deteriorating:
                statuses.append(STATUS_STRESS)
                reason_codes.append("FOREIGN_FUTURES_SHORT_EXPANSION")
            elif net_oi_pct <= 30 and already_net_short:
                statuses.append(STATUS_WARNING)
            elif net_oi_pct >= 85:
                statuses.append(STATUS_HEALTHY)
            else:
                statuses.append(STATUS_NEUTRAL)

    # --- 3) TXO Put/Call：只能 confirmation / dislocation warning，不可單獨
    #     形成 STRESS（不 append 進 statuses，只在既有壓力偏高時附加 reason）
    pcr_series = [
        (r.trade_date, r.txo_put_volume, r.txo_call_volume)
        for r in indicator_history
        if r.txo_put_volume is not None and r.txo_call_volume and r.txo_call_volume > 0
    ]
    if pcr_series:
        latest_date, latest_put, latest_call = pcr_series[-1]
        pcr = latest_put / latest_call
        pcr_history = [
            p / c for _, p, c in pcr_series[:-1] if c
        ]
        pcr_pct = _percentile_rank(pcr, pcr_history)
        raw_values["txo_pc_volume_ratio"] = round(pcr, 3)
        raw_values["txo_pc_volume_percentile_60d"] = pcr_pct
        if pcr_pct is not None:
            available += 1
            if statuses and max(
                (_STATUS_ORDER.index(s) for s in statuses), default=0
            ) >= _STATUS_ORDER.index(STATUS_WARNING) and (
                pcr_pct >= 90 or pcr_pct <= 10
            ):
                reason_codes.append("PCR_DISLOCATION_WARNING")

    # --- 4) 台灣 VIX：結構性缺席（FinMind 無對應 dataset）---
    raw_values["taiwan_vix_close"] = None
    raw_values["taiwan_vix_data_status"] = "UNKNOWN_NO_DATASOURCE"

    if available == 0:
        status = STATUS_UNKNOWN
    else:
        worst_idx = max(_STATUS_ORDER.index(s) for s in statuses) if statuses else (
            _STATUS_ORDER.index(STATUS_NEUTRAL)
        )
        status = _STATUS_ORDER[worst_idx]

    return _family_result(
        status,
        reason_codes,
        raw_values=raw_values,
        data_available_count=available,
        data_expected_count=expected,
    )


# ========================= Family C: GLOBAL_RISK =========================


def classify_family_global(indicator_history: List[MarketStressIndicator]) -> Dict[str, Any]:
    """Family C：美國 VIX + Nasdaq + SOX（半導體優先於大盤，貼近台股電子背景）。
    US10Y **結構性缺席**（FinMind 無對應 dataset）。此 Family 單獨 STRESS
    **不可**讓 market_stress 整體變 STRESS（由 state machine 保證，見
    `determine_market_stress`），只在此標記真實狀態。
    """
    expected = 4  # us_vix / sox / nasdaq / us10y
    available = 0
    statuses: List[str] = []
    reason_codes: List[str] = []
    raw_values: Dict[str, Any] = {}

    vix_series = [
        (r.trade_date, r.us_vix_close) for r in indicator_history if r.us_vix_close is not None
    ]
    if vix_series:
        latest = vix_series[-1][1]
        history = [v for _, v in vix_series[:-1]]
        pct = _percentile_rank(latest, history)
        raw_values["us_vix_close"] = latest
        raw_values["us_vix_percentile_252d"] = pct
        if pct is not None:
            available += 1
            if pct >= 90:
                statuses.append(STATUS_STRESS)
                reason_codes.append("US_VIX_ELEVATED")
            elif pct >= 75:
                statuses.append(STATUS_WARNING)
            elif pct <= 25:
                statuses.append(STATUS_HEALTHY)
            else:
                statuses.append(STATUS_NEUTRAL)

    sox_series = [
        (r.trade_date, r.sox_close) for r in indicator_history if r.sox_close is not None
    ]
    if len(sox_series) >= 6:
        latest = sox_series[-1][1]
        ret_5d = (latest / sox_series[-6][1] - 1.0) * 100.0 if sox_series[-6][1] else None
        raw_values["sox_close"] = latest
        raw_values["sox_return_5d_pct"] = ret_5d
        if ret_5d is not None:
            available += 1
            if ret_5d <= -8.0:
                statuses.append(STATUS_STRESS)
                reason_codes.append("SOX_SHARP_DECLINE")
            elif ret_5d <= -4.0:
                statuses.append(STATUS_WARNING)
            elif ret_5d >= 4.0:
                statuses.append(STATUS_HEALTHY)
            else:
                statuses.append(STATUS_NEUTRAL)

    nasdaq_series = [
        (r.trade_date, r.nasdaq_close) for r in indicator_history if r.nasdaq_close is not None
    ]
    if len(nasdaq_series) >= 6:
        latest = nasdaq_series[-1][1]
        ret_5d = (
            (latest / nasdaq_series[-6][1] - 1.0) * 100.0 if nasdaq_series[-6][1] else None
        )
        raw_values["nasdaq_close"] = latest
        raw_values["nasdaq_return_5d_pct"] = ret_5d
        if ret_5d is not None:
            available += 1
            if ret_5d <= -6.0:
                statuses.append(STATUS_STRESS)
            elif ret_5d <= -3.0:
                statuses.append(STATUS_WARNING)

    raw_values["us10y_yield"] = None
    raw_values["us10y_data_status"] = "UNKNOWN_NO_DATASOURCE"

    if available == 0:
        status = STATUS_UNKNOWN
    else:
        worst_idx = max(_STATUS_ORDER.index(s) for s in statuses) if statuses else (
            _STATUS_ORDER.index(STATUS_NEUTRAL)
        )
        status = _STATUS_ORDER[worst_idx]

    return _family_result(
        status,
        reason_codes,
        raw_values=raw_values,
        data_available_count=available,
        data_expected_count=expected,
    )


# ===================== Family D: MACRO_COMMODITY_RISK =====================

OIL_CONTEXT_SUPPLY_INFLATION_STRESS = "SUPPLY_INFLATION_STRESS"
OIL_CONTEXT_DEMAND_GROWTH = "DEMAND_GROWTH"
OIL_CONTEXT_DEMAND_DESTRUCTION = "DEMAND_DESTRUCTION"
OIL_CONTEXT_NEUTRAL = "NEUTRAL"
OIL_CONTEXT_UNKNOWN = "UNKNOWN"


def classify_oil_context(
    oil_return_5d_pct: Optional[float],
    *,
    equities_weak: Optional[bool],
    vix_up: Optional[bool],
) -> str:
    """油價單獨不可判——需搭配公債殖利率／股市／VIX 至少一項確認方向。
    US10Y 結構性缺席，這裡只用 equities/VIX 兩項可得證據。
    """
    if oil_return_5d_pct is None:
        return OIL_CONTEXT_UNKNOWN
    if oil_return_5d_pct >= 5.0 and (equities_weak or vix_up):
        return OIL_CONTEXT_SUPPLY_INFLATION_STRESS
    if oil_return_5d_pct >= 5.0 and equities_weak is False and not vix_up:
        return OIL_CONTEXT_DEMAND_GROWTH
    if oil_return_5d_pct <= -8.0 and equities_weak:
        return OIL_CONTEXT_DEMAND_DESTRUCTION
    return OIL_CONTEXT_NEUTRAL


def classify_family_macro(
    indicator_history: List[MarketStressIndicator],
    *,
    equities_weak: Optional[bool],
    vix_up: Optional[bool],
) -> Dict[str, Any]:
    """Family D：原油（context 判斷，見 `classify_oil_context`）+ 黃金（只能
    SAFE_HAVEN_CONFIRMATION，不可單獨造成 STRESS）+ USD/TWD（貶值只能當資金
    壓力 confirmation，不可單獨造成 STRESS）。
    """
    expected = 3  # oil / gold / usdtwd
    available = 0
    reason_codes: List[str] = []
    raw_values: Dict[str, Any] = {}
    status = STATUS_NEUTRAL

    wti_series = [
        (r.trade_date, r.wti_price) for r in indicator_history if r.wti_price is not None
    ]
    oil_context = OIL_CONTEXT_UNKNOWN
    if len(wti_series) >= 6:
        latest = wti_series[-1][1]
        ret_5d = (latest / wti_series[-6][1] - 1.0) * 100.0 if wti_series[-6][1] else None
        raw_values["wti_return_5d_pct"] = ret_5d
        if ret_5d is not None:
            available += 1
            oil_context = classify_oil_context(
                ret_5d, equities_weak=equities_weak, vix_up=vix_up
            )
    raw_values["oil_stress_context"] = oil_context
    if oil_context == OIL_CONTEXT_SUPPLY_INFLATION_STRESS:
        status = STATUS_WARNING
        reason_codes.append("OIL_SUPPLY_INFLATION_STRESS")

    gold_series = [
        (r.trade_date, r.gold_price) for r in indicator_history if r.gold_price is not None
    ]
    if len(gold_series) >= 6:
        latest = gold_series[-1][1]
        ret_5d = (latest / gold_series[-6][1] - 1.0) * 100.0 if gold_series[-6][1] else None
        raw_values["gold_return_5d_pct"] = ret_5d
        if ret_5d is not None:
            available += 1
            if ret_5d >= 5.0:
                reason_codes.append("SAFE_HAVEN_CONFIRMATION")

    usdtwd_series = [
        (r.trade_date, r.usdtwd_spot) for r in indicator_history if r.usdtwd_spot is not None
    ]
    if len(usdtwd_series) >= 6:
        latest = usdtwd_series[-1][1]
        ret_5d = (
            (latest / usdtwd_series[-6][1] - 1.0) * 100.0 if usdtwd_series[-6][1] else None
        )
        raw_values["usdtwd_return_5d_pct"] = ret_5d
        if ret_5d is not None:
            available += 1
            if ret_5d >= 1.5:
                reason_codes.append("FOREIGN_FLOW_STRESS_CONFIRMATION")

    if available == 0:
        status = STATUS_UNKNOWN

    return _family_result(
        status,
        reason_codes,
        raw_values=raw_values,
        data_available_count=available,
        data_expected_count=expected,
    )


# ========================= State machine =========================


def determine_market_stress(family_states: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§十二：純函式 state machine，只有 backend 決定，LLM 不參與。

    - 全部四個 Family 都 UNKNOWN → market_stress = UNKNOWN
    - >=2 Family STRESS 且至少一個來自 LOCAL/TAIWAN_FLOW → STRESS
    - >=1 Family STRESS，或 >=2 Family WARNING → CAUTION
    - 其餘 → NORMAL
    """
    statuses = {name: fs.get("status", STATUS_UNKNOWN) for name, fs in family_states.items()}
    if all(s == STATUS_UNKNOWN for s in statuses.values()):
        return {"market_stress": STRESS_UNKNOWN, "reason": "四個 Family 全部無法取得資料。"}

    stress_families = [name for name, s in statuses.items() if s == STATUS_STRESS]
    warning_families = [name for name, s in statuses.items() if s == STATUS_WARNING]

    local_or_flow_stress = any(
        name in (FAMILY_LOCAL, FAMILY_FLOW) for name in stress_families
    )

    if len(stress_families) >= 2 and local_or_flow_stress:
        return {
            "market_stress": STRESS_STRESS,
            "reason": (
                f"{len(stress_families)} 個市場壓力面向同時亮 STRESS"
                f"（{'、'.join(stress_families)}），且台股本地結構或資金面本身"
                "已經惡化，非純海外/總體因素。"
            ),
        }

    if len(stress_families) >= 1 or len(warning_families) >= 2:
        parts = []
        if stress_families:
            parts.append(f"STRESS：{'、'.join(stress_families)}")
        if warning_families:
            parts.append(f"WARNING：{'、'.join(warning_families)}")
        return {
            "market_stress": STRESS_CAUTION,
            "reason": "部分市場面向出現壓力訊號（" + "；".join(parts) + "），但尚未構成全面壓力。",
        }

    return {"market_stress": STRESS_NORMAL, "reason": "四個市場壓力面向大致健康，無顯著壓力訊號。"}


def resolve_effective_market_state(trend_regime: str, market_stress: str) -> str:
    """§十三 deterministic mapping。RISK_OFF 不論 stress 為何一律回 RISK_OFF
    （維持最高趨勢風險語意，避免 state explosion）。"""
    if trend_regime == REGIME_RISK_OFF:
        return EFFECTIVE_RISK_OFF
    if trend_regime == REGIME_BULL_TREND:
        if market_stress == STRESS_STRESS:
            return EFFECTIVE_BULL_STRESSED
        if market_stress == STRESS_CAUTION:
            return EFFECTIVE_BULL_CAUTION
        return EFFECTIVE_BULL_HEALTHY
    # REGIME_VOLATILE_RANGE（或未知，保守走這條）
    if market_stress == STRESS_STRESS:
        return EFFECTIVE_VOLATILE_STRESSED
    return EFFECTIVE_VOLATILE_RANGE


# ========================= Orchestrator =========================


def compute_market_stress(
    db: Session,
    target_date: date,
    *,
    trend_regime: str,
    momentum_frame: Optional[Dict[str, Dict[str, Any]]] = None,
    breadth: Optional[Dict[str, Any]] = None,
    taiex_return_1d_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """主入口：組出完整 Market Stress Overlay 結果。純 backend 計算，不呼叫
    LLM、不查詢外部即時資料（read-only 讀 DB 既有／已 ETL 的資料）。
    """
    indicator_history = _load_indicator_history(db, target_date)
    foreign_flow_history = _load_foreign_flow_history(db, target_date)

    cap_weight_divergence = compute_cap_weight_divergence(
        momentum_frame, taiex_return_1d_pct
    )
    family_local = classify_family_local(breadth, cap_weight_divergence)
    family_flow = classify_family_flow(foreign_flow_history, indicator_history)
    family_global = classify_family_global(indicator_history)

    sox_ret_5d = family_global["raw_values"].get("sox_return_5d_pct")
    us_vix_pct = family_global["raw_values"].get("us_vix_percentile_252d")
    equities_weak = None if sox_ret_5d is None else sox_ret_5d < -2.0
    vix_up = None if us_vix_pct is None else us_vix_pct >= 70

    family_macro = classify_family_macro(
        indicator_history, equities_weak=equities_weak, vix_up=vix_up
    )

    family_states = {
        FAMILY_LOCAL: family_local,
        FAMILY_FLOW: family_flow,
        FAMILY_GLOBAL: family_global,
        FAMILY_MACRO: family_macro,
    }
    stress_result = determine_market_stress(family_states)
    market_stress = stress_result["market_stress"]
    effective_state = resolve_effective_market_state(trend_regime, market_stress)

    key_reason_codes: List[str] = []
    for fs in family_states.values():
        key_reason_codes.extend(fs.get("reason_codes") or [])

    total_available = sum(fs["data_available_count"] for fs in family_states.values())
    total_expected = sum(fs["data_expected_count"] for fs in family_states.values())

    return {
        "trend_regime": trend_regime,
        "market_stress": market_stress,
        "market_stress_reason": stress_result["reason"],
        "effective_market_state": effective_state,
        "stress_families": {
            name: fs["status"] for name, fs in family_states.items()
        },
        "stress_family_detail": family_states,
        "key_reason_codes": key_reason_codes,
        "market_stress_data_complete": total_available >= total_expected,
        "market_regime_v2_version": MARKET_REGIME_V2_VERSION,
        "market_regime_v2_mode": market_regime_v2_mode(),
    }


def empty_market_stress(trend_regime: str) -> Dict[str, Any]:
    """DB 完全查不到任何 market_stress_indicators 資料時的保守 fallback。"""
    return {
        "trend_regime": trend_regime,
        "market_stress": STRESS_UNKNOWN,
        "market_stress_reason": "尚無市場壓力指標資料可供判斷。",
        "effective_market_state": resolve_effective_market_state(
            trend_regime, STRESS_UNKNOWN
        ),
        "stress_families": {name: STATUS_UNKNOWN for name in ALL_FAMILIES},
        "stress_family_detail": {},
        "key_reason_codes": [],
        "market_stress_data_complete": False,
        "market_regime_v2_version": MARKET_REGIME_V2_VERSION,
        "market_regime_v2_mode": market_regime_v2_mode(),
    }
