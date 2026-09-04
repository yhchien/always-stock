"""M27 Market Regime v2 — Market Stress Overlay 測試。

涵蓋：4 個 Family 分類純函式、state machine、effective state mapping、
DB-backed 的 orchestrator（`compute_market_stress`）與資料缺失政策。
"""

from datetime import date, timedelta

from app.models import MarketStressIndicator
from app.signals import market_stress as ms
from app.signals.market_regime import (
    REGIME_BULL_TREND,
    REGIME_RISK_OFF,
    REGIME_VOLATILE_RANGE,
)


# ===================== Family A: LOCAL_MARKET_INTERNALS =====================


def test_family_local_healthy_when_breadth_high():
    result = ms.classify_family_local({"breadth_score": 75.0}, None)
    assert result["status"] == ms.STATUS_HEALTHY
    assert result["data_complete"] is True


def test_family_local_stress_when_breadth_low():
    result = ms.classify_family_local({"breadth_score": 20.0}, None)
    assert result["status"] == ms.STATUS_STRESS
    assert "BREADTH_DETERIORATION" in result["reason_codes"]


def test_family_local_unknown_when_breadth_score_none():
    result = ms.classify_family_local({"breadth_score": None}, None)
    assert result["status"] == ms.STATUS_UNKNOWN
    assert result["data_available_count"] == 0
    assert result["data_complete"] is False


def test_family_local_index_concentration_warning_bumps_one_level_only():
    """權值股撐指數但個股中位數走弱：上調一級，但不會直接跳到 STRESS。"""
    result = ms.classify_family_local({"breadth_score": 75.0}, 2.0)
    assert result["status"] == ms.STATUS_NEUTRAL  # HEALTHY -> NEUTRAL，只上調一級
    assert "INDEX_CONCENTRATION_WARNING" in result["reason_codes"]


def test_compute_cap_weight_divergence_needs_min_sample():
    frame = {f"s{i}": {"_ret_1d": 1.0} for i in range(50)}
    assert ms.compute_cap_weight_divergence(frame, 2.0) is None  # < 100 檔


def test_compute_cap_weight_divergence_positive_when_index_outpaces_median():
    frame = {f"s{i}": {"_ret_1d": -1.0} for i in range(150)}
    # TAIEX +2.0%，個股中位數 -1.0% -> divergence = 3.0
    assert ms.compute_cap_weight_divergence(frame, 2.0) == 3.0


# ================= Family B: TAIWAN_FLOW_AND_DERIVATIVES =================


def test_family_flow_unknown_when_no_data():
    result = ms.classify_family_flow([], [])
    assert result["status"] == ms.STATUS_UNKNOWN
    assert result["data_available_count"] == 0
    # 台灣 VIX 永久缺席，即使其餘 3 項都有資料，data_complete 也不可能是 True
    assert result["data_expected_count"] == 4


def test_family_flow_taiwan_vix_always_unknown_even_with_full_data():
    history = [(date(2026, 1, d), 100.0) for d in range(1, 21)]
    result = ms.classify_family_flow(history, [])
    assert result["raw_values"]["taiwan_vix_close"] is None
    assert result["data_available_count"] < result["data_expected_count"]
    assert result["data_complete"] is False


def test_family_flow_moderate_outflow_day_is_not_automatically_stress():
    """規格書明確要求「不要單看固定金額」：外資當天賣超，但幅度落在歷史常態
    分布內（不是統計極端值），不該只因為是負值就自動判 STRESS——percentile
    機制本身就是為了取代「賣超 = STRESS」這種固定門檻判斷。用 shuffle 過的
    非單調歷史（避免緊鄰「今天」的那幾天剛好是極端值這種人為假象），驗證
    今天中等幅度賣超落在中段百分位，不會被誤判成極端值。"""
    import random

    spread = [(i - 14) * 10_000_000.0 for i in range(29)]  # -140M ~ +140M
    random.Random(42).shuffle(spread)
    history = [(date(2026, 1, d + 1), v) for d, v in enumerate(spread)]
    history.append((date(2026, 2, 1), -30_000_000.0))  # 中等幅度賣超，非極端值
    result = ms.classify_family_flow(history, [])
    assert result["status"] != ms.STATUS_STRESS


def test_family_flow_persistent_outflow_at_low_percentile_is_stress():
    history = [(date(2026, 1, d % 28 + 1), 500_000_000.0) for d in range(1, 30)]
    history[-1] = (date(2026, 2, 28), -900_000_000.0)  # 遠低於過去水準
    result = ms.classify_family_flow(history, [])
    assert result["status"] == ms.STATUS_STRESS
    assert "FOREIGN_CASH_OUTFLOW" in result["reason_codes"]


def test_family_flow_foreign_futures_needs_already_short_and_deteriorating():
    """規格書明確要求：不能只看絕對淨空，需要「已經淨空 + 3 日內轉弱」雙重確認。"""
    rows = []
    base_date = date(2026, 1, 1)
    for i in range(30):
        row = MarketStressIndicator(trade_date=base_date + timedelta(days=i))
        row.foreign_tx_net_oi = -1000.0  # 長期都淨空（不該單獨觸發）
        rows.append(row)
    result = ms.classify_family_flow([], rows)
    # 長期穩定淨空、沒有轉弱 -> 不應該是 STRESS（percentile 落在中段）
    assert result["status"] != ms.STATUS_STRESS


def test_family_flow_foreign_futures_stress_when_short_and_deteriorating_sharply():
    rows = []
    base_date = date(2026, 1, 1)
    for i in range(30):
        row = MarketStressIndicator(trade_date=base_date + timedelta(days=i))
        row.foreign_tx_net_oi = -1000.0
        rows.append(row)
    # 最後幾天淨空部位急速擴大
    rows[-1].foreign_tx_net_oi = -20000.0
    rows[-2].foreign_tx_net_oi = -15000.0
    result = ms.classify_family_flow([], rows)
    assert result["status"] == ms.STATUS_STRESS
    assert "FOREIGN_FUTURES_SHORT_EXPANSION" in result["reason_codes"]


def test_family_flow_pcr_extreme_alone_does_not_cause_stress():
    """規格書明確要求：PCR 極端值不可單獨造成 market_stress = STRESS。"""
    rows = []
    base_date = date(2026, 1, 1)
    for i in range(30):
        row = MarketStressIndicator(trade_date=base_date + timedelta(days=i))
        row.txo_put_volume = 100.0
        row.txo_call_volume = 100.0
        rows.append(row)
    rows[-1].txo_put_volume = 900.0  # PCR 突然暴衝
    result = ms.classify_family_flow([], rows)
    assert result["status"] != ms.STATUS_STRESS


# ========================= Family C: GLOBAL_RISK =========================


def _indicator_rows(field: str, values: list) -> list:
    base_date = date(2026, 1, 1)
    rows = []
    for i, v in enumerate(values):
        row = MarketStressIndicator(trade_date=base_date + timedelta(days=i))
        setattr(row, field, v)
        rows.append(row)
    return rows


def test_family_global_unknown_when_no_data():
    result = ms.classify_family_global([])
    assert result["status"] == ms.STATUS_UNKNOWN


def test_family_global_us_vix_high_percentile_is_stress():
    values = [15.0] * 29 + [40.0]  # 最新一天遠高於過去
    result = ms.classify_family_global(_indicator_rows("us_vix_close", values))
    assert result["status"] == ms.STATUS_STRESS
    assert "US_VIX_ELEVATED" in result["reason_codes"]


def test_family_global_us10y_structurally_unknown():
    values = [15.0] * 10
    result = ms.classify_family_global(_indicator_rows("us_vix_close", values))
    assert result["raw_values"]["us10y_yield"] is None
    assert result["data_expected_count"] == 4


def test_family_global_sox_sharp_decline_is_stress():
    values = [100.0] * 5 + [90.0]  # 5 日跌幅 -10%
    result = ms.classify_family_global(_indicator_rows("sox_close", values))
    assert result["status"] == ms.STATUS_STRESS
    assert "SOX_SHARP_DECLINE" in result["reason_codes"]


# ===================== Family D: MACRO_COMMODITY_RISK =====================


def test_oil_context_unknown_without_data():
    assert ms.classify_oil_context(None, equities_weak=None, vix_up=None) == ms.OIL_CONTEXT_UNKNOWN


def test_oil_context_supply_inflation_stress_needs_confirmation():
    assert (
        ms.classify_oil_context(6.0, equities_weak=True, vix_up=False)
        == ms.OIL_CONTEXT_SUPPLY_INFLATION_STRESS
    )
    assert (
        ms.classify_oil_context(6.0, equities_weak=False, vix_up=True)
        == ms.OIL_CONTEXT_SUPPLY_INFLATION_STRESS
    )


def test_oil_context_demand_growth_when_equities_healthy_and_vix_stable():
    assert (
        ms.classify_oil_context(6.0, equities_weak=False, vix_up=False)
        == ms.OIL_CONTEXT_DEMAND_GROWTH
    )


def test_oil_context_demand_destruction_when_oil_and_equities_both_weak():
    assert (
        ms.classify_oil_context(-10.0, equities_weak=True, vix_up=False)
        == ms.OIL_CONTEXT_DEMAND_DESTRUCTION
    )


def test_oil_context_neutral_when_no_sharp_move():
    assert ms.classify_oil_context(1.0, equities_weak=None, vix_up=None) == ms.OIL_CONTEXT_NEUTRAL


def test_family_macro_gold_alone_does_not_cause_stress():
    values = [2000.0] * 5 + [2200.0]  # +10% 黃金急漲
    result = ms.classify_family_macro(
        _indicator_rows("gold_price", values), equities_weak=None, vix_up=None
    )
    assert result["status"] != ms.STATUS_STRESS
    assert "SAFE_HAVEN_CONFIRMATION" in result["reason_codes"]


def test_family_macro_usdtwd_depreciation_alone_does_not_cause_stress():
    values = [31.0] * 5 + [31.6]  # 貶值 ~2%
    result = ms.classify_family_macro(
        _indicator_rows("usdtwd_spot", values), equities_weak=None, vix_up=None
    )
    assert result["status"] != ms.STATUS_STRESS
    assert "FOREIGN_FLOW_STRESS_CONFIRMATION" in result["reason_codes"]


def test_family_macro_supply_inflation_stress_produces_warning():
    values = [80.0] * 5 + [90.0]  # WTI +12.5%
    result = ms.classify_family_macro(
        _indicator_rows("wti_price", values), equities_weak=True, vix_up=False
    )
    assert result["status"] == ms.STATUS_WARNING
    assert result["raw_values"]["oil_stress_context"] == ms.OIL_CONTEXT_SUPPLY_INFLATION_STRESS


# ========================= State machine =========================


def _family(status: str) -> dict:
    return {"status": status, "reason_codes": []}


def test_state_machine_normal_when_no_stress_and_at_most_one_warning():
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_FLOW: _family(ms.STATUS_WARNING),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_NEUTRAL),
        ms.FAMILY_MACRO: _family(ms.STATUS_HEALTHY),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_NORMAL


def test_state_machine_caution_when_two_warnings():
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_WARNING),
        ms.FAMILY_FLOW: _family(ms.STATUS_WARNING),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_NEUTRAL),
        ms.FAMILY_MACRO: _family(ms.STATUS_HEALTHY),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_CAUTION


def test_state_machine_global_and_macro_stress_alone_is_only_caution():
    """規格書明確範例：GLOBAL_RISK=STRESS + MACRO=STRESS + LOCAL/FLOW 健康
    → CAUTION，不是 STRESS（避免海外/總體震盪自動讓台股進入高壓）。"""
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_FLOW: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_STRESS),
        ms.FAMILY_MACRO: _family(ms.STATUS_STRESS),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_CAUTION


def test_state_machine_local_and_flow_stress_is_stress():
    """規格書明確範例：LOCAL + TAIWAN_FLOW 都 STRESS -> STRESS 直接成立。"""
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_STRESS),
        ms.FAMILY_FLOW: _family(ms.STATUS_STRESS),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_MACRO: _family(ms.STATUS_HEALTHY),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_STRESS


def test_state_machine_local_stress_plus_global_stress_is_stress():
    """>=2 Family STRESS 且至少一個來自 LOCAL/FLOW 即成立，不要求兩者都是。"""
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_STRESS),
        ms.FAMILY_FLOW: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_STRESS),
        ms.FAMILY_MACRO: _family(ms.STATUS_HEALTHY),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_STRESS


def test_state_machine_unknown_when_all_families_unknown():
    states = {name: _family(ms.STATUS_UNKNOWN) for name in ms.ALL_FAMILIES}
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_UNKNOWN


def test_state_machine_single_stress_family_is_caution_not_stress():
    """只有 1 個 Family STRESS（未達 >=2 門檻）-> CAUTION。"""
    states = {
        ms.FAMILY_LOCAL: _family(ms.STATUS_STRESS),
        ms.FAMILY_FLOW: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_GLOBAL: _family(ms.STATUS_HEALTHY),
        ms.FAMILY_MACRO: _family(ms.STATUS_HEALTHY),
    }
    result = ms.determine_market_stress(states)
    assert result["market_stress"] == ms.STRESS_CAUTION


# ========================= Effective market state =========================


def test_effective_state_bull_normal_is_healthy():
    assert (
        ms.resolve_effective_market_state(REGIME_BULL_TREND, ms.STRESS_NORMAL)
        == ms.EFFECTIVE_BULL_HEALTHY
    )


def test_effective_state_bull_caution():
    assert (
        ms.resolve_effective_market_state(REGIME_BULL_TREND, ms.STRESS_CAUTION)
        == ms.EFFECTIVE_BULL_CAUTION
    )


def test_effective_state_bull_stress_is_bull_stressed():
    assert (
        ms.resolve_effective_market_state(REGIME_BULL_TREND, ms.STRESS_STRESS)
        == ms.EFFECTIVE_BULL_STRESSED
    )


def test_effective_state_volatile_range_stress_is_volatile_stressed():
    assert (
        ms.resolve_effective_market_state(REGIME_VOLATILE_RANGE, ms.STRESS_STRESS)
        == ms.EFFECTIVE_VOLATILE_STRESSED
    )


def test_effective_state_volatile_range_normal_stays_volatile_range():
    assert (
        ms.resolve_effective_market_state(REGIME_VOLATILE_RANGE, ms.STRESS_NORMAL)
        == ms.EFFECTIVE_VOLATILE_RANGE
    )


def test_effective_state_risk_off_always_risk_off_regardless_of_stress():
    for stress in (ms.STRESS_NORMAL, ms.STRESS_CAUTION, ms.STRESS_STRESS, ms.STRESS_UNKNOWN):
        assert ms.resolve_effective_market_state(REGIME_RISK_OFF, stress) == ms.EFFECTIVE_RISK_OFF


# ========================= Orchestrator (DB-backed) =========================


def test_compute_market_stress_empty_db_is_unknown(db):
    result = ms.compute_market_stress(db, date(2026, 1, 1), trend_regime=REGIME_BULL_TREND)
    assert result["market_stress"] == ms.STRESS_UNKNOWN
    assert result["market_stress_data_complete"] is False
    assert result["effective_market_state"] == ms.EFFECTIVE_BULL_HEALTHY  # UNKNOWN 疊多頭 -> healthy 分支


def test_compute_market_stress_reads_indicator_table(db):
    base_date = date(2026, 1, 1)
    for i in range(25):
        row = MarketStressIndicator(trade_date=base_date + timedelta(days=i))
        row.us_vix_close = 15.0
        db.add(row)
    db.commit()
    result = ms.compute_market_stress(
        db, base_date + timedelta(days=24), trend_regime=REGIME_BULL_TREND
    )
    assert result["stress_families"][ms.FAMILY_GLOBAL] in (
        ms.STATUS_HEALTHY,
        ms.STATUS_NEUTRAL,
    )
    assert result["market_regime_v2_version"] == "market_regime_v2"


def test_market_regime_v2_mode_defaults_to_production(monkeypatch):
    """2026-09-04 Production Integration：正式預設改 production。"""
    monkeypatch.delenv("MARKET_REGIME_V2_MODE", raising=False)
    assert ms.market_regime_v2_mode() == ms.MODE_PRODUCTION


def test_market_regime_v2_mode_reads_env(monkeypatch):
    monkeypatch.setenv("MARKET_REGIME_V2_MODE", "global_only")
    assert ms.market_regime_v2_mode() == ms.MODE_GLOBAL_ONLY


def test_market_regime_v2_mode_can_roll_back_to_shadow(monkeypatch):
    monkeypatch.setenv("MARKET_REGIME_V2_MODE", "shadow")
    assert ms.market_regime_v2_mode() == ms.MODE_SHADOW


def test_market_regime_v2_mode_invalid_value_falls_back_to_production(monkeypatch):
    monkeypatch.setenv("MARKET_REGIME_V2_MODE", "not_a_real_mode")
    assert ms.market_regime_v2_mode() == ms.MODE_PRODUCTION


# ========================= §20 Market Context Severity =========================


def test_market_context_severity_unknown_when_market_stress_unknown():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_BULL_HEALTHY, ms.STRESS_UNKNOWN)
        == ms.CONTEXT_SEVERITY_UNKNOWN
    )


def test_market_context_severity_stress_for_bull_stressed():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_BULL_STRESSED, ms.STRESS_CAUTION)
        == ms.CONTEXT_SEVERITY_STRESS
    )


def test_market_context_severity_stress_for_volatile_stressed():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_VOLATILE_STRESSED, ms.STRESS_NORMAL)
        == ms.CONTEXT_SEVERITY_STRESS
    )


def test_market_context_severity_stress_for_risk_off_regardless_of_market_stress_value():
    """RISK_OFF 不論 market_stress 算出什麼，一律視為 STRESS 嚴重度。"""
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_RISK_OFF, ms.STRESS_NORMAL)
        == ms.CONTEXT_SEVERITY_STRESS
    )


def test_market_context_severity_warning_for_bull_caution():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_BULL_CAUTION, ms.STRESS_CAUTION)
        == ms.CONTEXT_SEVERITY_WARNING
    )


def test_market_context_severity_normal_for_bull_healthy():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_BULL_HEALTHY, ms.STRESS_NORMAL)
        == ms.CONTEXT_SEVERITY_NORMAL
    )


def test_market_context_severity_normal_for_volatile_range_normal():
    assert (
        ms.compute_market_context_severity(ms.EFFECTIVE_VOLATILE_RANGE, ms.STRESS_NORMAL)
        == ms.CONTEXT_SEVERITY_NORMAL
    )


# ========================= §6 Conviction Adjustment =========================


def test_conviction_adjustment_no_change_for_bull_healthy():
    assert ms.apply_conviction_adjustment("high", ms.EFFECTIVE_BULL_HEALTHY) == "high"


def test_conviction_adjustment_no_change_for_bull_caution():
    assert ms.apply_conviction_adjustment("high", ms.EFFECTIVE_BULL_CAUTION) == "high"


def test_conviction_adjustment_no_change_for_risk_off():
    """RISK_OFF 沿用既有 conviction／survival policy，不重複降級。"""
    assert ms.apply_conviction_adjustment("high", ms.EFFECTIVE_RISK_OFF) == "high"


def test_conviction_adjustment_no_change_for_volatile_range():
    assert ms.apply_conviction_adjustment("medium", ms.EFFECTIVE_VOLATILE_RANGE) == "medium"


def test_conviction_adjustment_downgrades_one_level_for_bull_stressed():
    assert ms.apply_conviction_adjustment("high", ms.EFFECTIVE_BULL_STRESSED) == "medium"
    assert ms.apply_conviction_adjustment("medium", ms.EFFECTIVE_BULL_STRESSED) == "low"
    assert ms.apply_conviction_adjustment("low", ms.EFFECTIVE_BULL_STRESSED) == "low"


def test_conviction_adjustment_downgrades_one_level_for_volatile_stressed():
    assert ms.apply_conviction_adjustment("high", ms.EFFECTIVE_VOLATILE_STRESSED) == "medium"
