"""Phase 2 §J：entry_state 測試（ATR normalize 取代 distance_to_high 固定 3% cliff）。"""
from app.signals.phase2 import entry_state as es


def test_missing_atr_returns_none_not_guess():
    out = es.compute_entry_state({"distance_to_20d_high": -3.0, "atr_pct_14d": None})
    assert out["entry_state"] is None
    assert out["pullback_atr_multiple"] is None


def test_near_high_within_half_atr():
    # ATR 7% → 0.5 倍 = 3.5%；距高點 -3% 在門檻內
    out = es.compute_entry_state({"distance_to_20d_high": -3.0, "atr_pct_14d": 7.0, "rs_rank_improvement_5d": 0})
    assert out["entry_state"] == es.ENTRY_NEAR_HIGH
    assert out["pullback_atr_multiple"] == round(3.0 / 7.0, 2)


def test_no_more_hard_3_pct_cliff():
    """舊版 -3.01% 直接 FAIL、-2.99% 直接 PASS 的 cliff 不應存在——
    兩者在 ATR normalize 下應該落在同一個 entry_state（差距在雜訊範圍內）。"""
    a = es.compute_entry_state({"distance_to_20d_high": -3.01, "atr_pct_14d": 7.0, "rs_rank_improvement_5d": 0})
    b = es.compute_entry_state({"distance_to_20d_high": -2.99, "atr_pct_14d": 7.0, "rs_rank_improvement_5d": 0})
    assert a["entry_state"] == b["entry_state"]


def test_deep_pullback_with_deteriorating_rs_is_structure_damaged():
    out = es.compute_entry_state({
        "distance_to_20d_high": -30.0, "atr_pct_14d": 5.0, "rs_rank_improvement_5d": -50,
    })
    assert out["pullback_atr_multiple"] == 6.0
    assert out["entry_state"] == es.ENTRY_STRUCTURE_DAMAGED


def test_reaccelerating_when_pulled_back_but_rs_improving_fast():
    out = es.compute_entry_state({
        "distance_to_20d_high": -8.0, "atr_pct_14d": 5.0, "rs_rank_improvement_5d": 40,
    })
    assert out["entry_state"] == es.ENTRY_REACCELERATING


def test_normal_pullback():
    out = es.compute_entry_state({
        "distance_to_20d_high": -6.0, "atr_pct_14d": 5.0, "rs_rank_improvement_5d": 0,
    })
    assert out["pullback_atr_multiple"] == 1.2
    assert out["entry_state"] == es.ENTRY_NORMAL_PULLBACK
