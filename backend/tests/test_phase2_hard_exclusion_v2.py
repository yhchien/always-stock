"""Phase 2 Hard Exclusion 重構（2026-07-22）regression tests，對應 spec §二十
Test A~J：過熱 != 失敗、ETF/金融不再是排除理由、真正反轉需要多維度證據。"""
from app.signals import deterministic_signals
from app.signals.phase2 import regime_gate as rg


def _candidate(stock_id, **overrides):
    base = {
        "stock_id": stock_id,
        "role": rg.roles_mod.ROLE_SECTOR_LEADER,
        "rs_market_percentile_20d": 91.5,
        "hit_count": 2,
        "failed_follow_through": False,
        "soft_hints": [],
        "entry_state": None,
        "deterministic_signals": {},
        "is_etf": False,
        "is_financial": False,
        "price_change_1d": 0.0,
        "price_change_3d": 0.0,
        "price_change_10d": 0.0,
        "total_institution_flow_1d": 0.0,
        "total_institution_flow_3d": 0.0,
        "avg_turnover_5d": 1e8,
        "volume_1d": 5_000_000,
        "close_1d": 50.0,
    }
    base.update(overrides)
    return base


# ---------------- Test A：3D>15% 但仍強勢 → 只是 warning ----------------

def test_a_extended_3d_but_strong_not_hard_excluded():
    c = _candidate(
        "A",
        price_change_3d=18.0,
        rs_market_percentile_20d=95.0,
        price_change_1d=1.0,
        total_institution_flow_1d=100.0,
        total_institution_flow_3d=100.0,
    )
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is False
    assert result["reason"] is None
    assert rg.WARNING_EXTENDED_3D in result["risk_warnings"]


# ---------------- Test B：3D>15% + 真正反轉 → REVERSAL_FAILURE ----------------

def test_b_extended_3d_with_true_reversal_is_hard_excluded():
    c = _candidate(
        "A",
        price_change_3d=20.0,
        price_change_1d=-3.0,
        total_institution_flow_3d=500.0,
        total_institution_flow_1d=-700.0,  # prior=500-(-700)=1200；ratio=700/1200=0.58 >= 0.5
        volume_1d_to_5d_ratio=2.0,  # + price_change_1d<0 → high_volume_decline（第三個 family）
    )
    result = rg.build_hard_exclusion_result(c, taiex_return_1d_pct=-0.5)  # excess = -3-(-0.5) = -2.5 <= -1.5
    assert result["excluded"] is True
    assert result["reason"] == rg.REASON_REVERSAL_FAILURE
    assert rg.FAMILY_INSTITUTION_FLOW in result["evidence_families"]
    assert rg.FAMILY_RELATIVE_STRENGTH in result["evidence_families"]
    assert rg.FAMILY_VOLUME_PRICE in result["evidence_families"]
    # 不是因為 3D+20% 本身 hard——EXTENDED_3D 只會是 warning，不是 matched_hard_rules
    assert result["matched_hard_rules"] == [rg.REASON_REVERSAL_FAILURE]


# ---------------- Test C：10D>25% + 法人小幅賣 → 只是 warning ----------------

def test_c_10d_extended_small_institution_sell_not_hard():
    c = _candidate(
        "A",
        price_change_10d=30.0,
        total_institution_flow_3d=10000.0,
        total_institution_flow_1d=-300.0,  # ratio = 300/(10000-(-300)) = 0.029，遠低於 0.5
        rs_market_percentile_20d=90.0,
    )
    result = rg.build_hard_exclusion_result(c, taiex_return_1d_pct=0.0)
    assert result["excluded"] is False
    assert rg.WARNING_EXTENDED_PROFIT_TAKING in result["risk_warnings"]


# ---------------- Test D：10D>25% + 法人大幅反轉 + 價格惡化 → HARD ----------------

def test_d_10d_extended_with_meaningful_reversal_and_deterioration_is_hard():
    c = _candidate(
        "A",
        price_change_10d=30.0,
        price_change_1d=-2.5,
        total_institution_flow_3d=500.0,
        total_institution_flow_1d=-700.0,  # ratio 0.58
        high_1d=110.0,
        low_1d=100.0,
        close_1d=101.0,  # (110-101)/(110-100)=0.9 >= 0.8 → close_near_low（第三個 family）
    )
    result = rg.build_hard_exclusion_result(c, taiex_return_1d_pct=-0.5)  # excess=-2.5-(-0.5)=-2.0
    assert result["excluded"] is True
    assert result["reason"] == rg.REASON_REVERSAL_FAILURE
    assert rg.FAMILY_VOLUME_PRICE in result["evidence_families"]


# ---------------- Test E：股票跌 -2%、大盤跌 -5% → 相對抗跌，不可判出貨 ----------------

def test_e_relative_outperformance_not_reversal_failure():
    c = _candidate(
        "A",
        price_change_1d=-2.0,
        total_institution_flow_3d=500.0,
        total_institution_flow_1d=-700.0,  # institution reversal 條件本身成立（ratio 0.58）
    )
    # 大盤跌更多（-5%），個股相對強度其實是 +3%
    result = rg.build_hard_exclusion_result(c, taiex_return_1d_pct=-5.0)
    assert result["excluded"] is False
    assert result["reason"] != rg.REASON_REVERSAL_FAILURE


# ---------------- Test F：ETF ----------------

def test_f_etf_not_hard_excluded():
    c = _candidate("A", is_etf=True)
    assert rg.is_true_hard_exclusion(c) is None
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is False


# ---------------- Test G：金融股 ----------------

def test_g_financial_not_hard_excluded():
    c = _candidate("A", is_financial=True)
    assert rg.is_true_hard_exclusion(c) is None


def test_hard_exclusion_is_asset_type_invariant_for_equal_evidence():
    variants = [
        _candidate("COMMON", asset_type="COMMON_STOCK"),
        _candidate("FIN", asset_type="FINANCIAL", is_financial=True),
        _candidate("ETF", asset_type="ETF", is_etf=True),
    ]
    results = [rg.build_hard_exclusion_result(c) for c in variants]
    assert [r["excluded"] for r in results] == [False, False, False]
    assert [r["reason"] for r in results] == [None, None, None]


def test_candidate_source_combination_does_not_change_hard_exclusion():
    """P3B：只改來源通道，不得讓 backend 從存活變成自動 REMOVE。"""
    source_a = _candidate(
        "A",
        candidate_sources=["A"],
        source_A=True,
        source_C=False,
        in_top_stocks_3d=True,
        in_acceleration_pool=False,
    )
    source_ac = _candidate(
        "A",
        candidate_sources=["A", "C"],
        source_A=True,
        source_C=True,
        in_top_stocks_3d=True,
        in_acceleration_pool=True,
    )

    result_a = rg.build_hard_exclusion_result(source_a)
    result_ac = rg.build_hard_exclusion_result(source_ac)
    assert result_a["excluded"] is False
    assert result_ac["excluded"] is False
    assert result_a["reason"] == result_ac["reason"] is None
    assert deterministic_signals.build_deterministic_signals(source_a)["max_decision"] == "WATCH"
    assert deterministic_signals.build_deterministic_signals(source_ac)["max_decision"] == "WATCH"


# ---------------- Test H：人工黑名單 ----------------

def test_h_manual_blacklist_hard_excluded(monkeypatch):
    monkeypatch.setattr(rg, "is_blacklisted", lambda sid: sid == "A")
    c = _candidate("A")
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is True
    assert result["reason"] == rg.REASON_MANUAL_BLACKLIST

    other = _candidate("B")
    assert rg.build_hard_exclusion_result(other)["excluded"] is False


# ---------------- Test I：raw shares 不足但 traded value 足夠 ----------------

def test_i_low_raw_volume_but_sufficient_turnover_not_hard():
    c = _candidate(
        "A",
        close_1d=50.0,       # < 1000 元級距 → 門檻 1500 張
        volume_1d=1_000_000,  # = 1000 張，低於舊門檻，會觸發 LOW_RAW_VOLUME warning
        avg_turnover_5d=6e7,  # 6000 萬 >= 5000 萬門檻 → 流動性合格
    )
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is False
    assert result["liquidity_state"] == "NORMAL"
    assert rg.WARNING_LOW_RAW_VOLUME in result["risk_warnings"]


# ---------------- Test J：failed_follow_through 不可永久封殺（episode-scoped）----------------

def test_j_failed_follow_through_current_episode_hard_but_not_permanent():
    """`failed_follow_through` 是 candidate_pool._load_tracking_status 從**目前
    active 的 signal_watch_hits**算出的旗標——一旦該 cycle 結束（30 日到期或
    early-exit），對應的 hits 會被 archive.py 刪除；同一檔股票之後若重新被抓到，
    會是全新 first_seen_date 的新 episode，candidate_pool 給的 failed_follow_
    through 會是全新算出的 False（不會延續舊 episode 的失敗判定）。這裡直接驗證
    旗標本身的判斷：True → hard exclude 當前 episode；新 episode（False）→
    不受影響，證明機制不是「一次失敗永久封殺」。"""
    failed_this_episode = _candidate("A", failed_follow_through=True)
    assert rg.is_true_hard_exclusion(failed_this_episode) == rg.REASON_FAILED_FOLLOW_THROUGH

    new_episode_same_stock = _candidate("A", failed_follow_through=False)
    assert rg.is_true_hard_exclusion(new_episode_same_stock) is None


# ---------------- 額外：COMPOSITE_RISK_EXCLUDE 需要巢狀 deterministic_signals ----------------

def test_composite_risk_exclude_reads_nested_deterministic_signals():
    """`risk_gate_action` 存在 `candidate["deterministic_signals"]["risk_gate_action"]`
    （巢狀），不是扁平的 `candidate["risk_gate_action"]`——2026-07-22 重構時發現
    舊版程式碼讀的是扁平欄位，從未真正讀到值，這條 hard exclusion 從未在
    production 真正觸發過。順手修正（屬於 hard exclusion 直接相關範圍）。"""
    c = _candidate(
        "A",
        deterministic_signals={
            "risk_gate_action": "EXCLUDE",
            "risk_flags": ["distribution", "institution_flow_reversal"],
        },
    )
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is True
    assert result["reason"] == rg.REASON_COMPOSITE_RISK_EXCLUDE
    assert rg.FAMILY_VOLUME_PRICE in result["evidence_families"]
    assert rg.FAMILY_INSTITUTION_FLOW in result["evidence_families"]
    assert len(result["evidence_families"]) >= 2

    # 扁平欄位（舊 bug 的寫法）不應該再被讀取
    flat_only = _candidate("B", risk_gate_action="EXCLUDE")
    assert rg.build_hard_exclusion_result(flat_only)["excluded"] is False


def test_liquidity_failure_hard_excluded():
    c = _candidate("A", avg_turnover_5d=1e7)  # 1000 萬 < 5000 萬門檻
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is True
    assert result["reason"] == rg.REASON_LIQUIDITY_FAILURE
    assert result["liquidity_state"] == "INSUFFICIENT"


def test_liquidity_eligible_missing_data_not_excluded():
    c = _candidate("A", avg_turnover_5d=None)
    result = rg.build_hard_exclusion_result(c)
    assert result["excluded"] is False
    assert result["liquidity_state"] == "UNKNOWN"


def test_old_hard_gate_reasons_no_longer_exist():
    """確認舊版字串（3D_RETURN_GT_15 之類）不再是任何合法 hard exclusion reason。"""
    assert "3D_RETURN_GT_15" not in rg.ALL_HARD_EXCLUSION_REASONS
    assert "overheat_3d" not in rg.ALL_HARD_EXCLUSION_REASONS
    assert set(rg.ALL_HARD_EXCLUSION_REASONS) == {
        rg.REASON_MANUAL_BLACKLIST,
        rg.REASON_FAILED_FOLLOW_THROUGH,
        rg.REASON_STRUCTURE_DAMAGED,
        rg.REASON_COMPOSITE_RISK_EXCLUDE,
        rg.REASON_LIQUIDITY_FAILURE,
        rg.REASON_REVERSAL_FAILURE,
    }
