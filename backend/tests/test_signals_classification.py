"""M23 slice 5：classification.py 規則測試（spec §7）。

LEADER：四條件全滿足
FOLLOWER：同產業已有 LEADER + 漲幅 < 0.7 倍 + 3d 法人買超
LAGGARD_CANDIDATE：guard 成立 + 4 條件中 ≥ 2 條
"""
from datetime import date

import pytest

from app.signals.classification import (
    PRELIM_TYPE_FOLLOWER,
    PRELIM_TYPE_LAGGARD_CANDIDATE,
    PRELIM_TYPE_LEADER,
    classify_stocks,
)


# ---------- helpers ----------


def _leader_template(**overrides):
    """產生符合 LEADER 4 條件的最小 dict。"""
    base = {
        "stock_id": "1101",
        "industry": "水泥",
        "industry_count": 10,
        "industry_rank_5d": 1,
        "industry_rank_net_3d": 1,
        "consecutive_buy_days_3d": 3,
        "volume_5d_to_60d_ratio": 2.0,
        "price_change_5d": 12.0,
        "total_institution_flow_3d": 1.0e8,
    }
    base.update(overrides)
    return base


def _follower_template(**overrides):
    """同產業 LEADER price_change_5d=12，自己 < 12 × 0.7 = 8.4，3d 法人正。"""
    base = {
        "stock_id": "1102",
        "industry": "水泥",
        "industry_count": 10,
        "industry_rank_5d": 5,
        "industry_rank_net_3d": 5,
        "consecutive_buy_days_3d": 1,
        "volume_5d_to_60d_ratio": 1.0,
        "price_change_5d": 5.0,
        "total_institution_flow_3d": 5.0e7,
    }
    base.update(overrides)
    return base


def _laggard_template(**overrides):
    """gap >= 5 + net_1d > 0 → 1+1+1 = 3 hits（含 guard）。"""
    base = {
        "stock_id": "1103",
        "industry": "水泥",
        "industry_count": 10,
        "industry_rank_5d": 8,
        "industry_rank_net_3d": 8,
        "consecutive_buy_days_3d": 0,
        "volume_5d_to_60d_ratio": 0.9,
        "price_change_5d": 1.0,  # leader 12 → gap 11 >= 5
        "total_institution_flow_1d": 1.0e7,
        "total_institution_flow_3d": -1.0e7,
        "close_1d": 50.0,
        "ma_5d": 49.0,
        "ma_10d": 48.0,
    }
    base.update(overrides)
    return base


# ---------- LEADER ----------


def test_leader_all_four_conditions_pass():
    pool = [_leader_template()]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert len(out) == 1
    assert out[0]["prelim_type"] == PRELIM_TYPE_LEADER


def test_leader_fails_when_price_rank_outside_top_30pct():
    # industry_count=10, threshold=ceil(10 * 0.3)=3；rank=4 不通過
    pool = [_leader_template(industry_rank_5d=4)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_leader_fails_when_flow_rank_outside_top_20pct():
    # threshold=ceil(10 * 0.2)=2；rank=3 不通過
    pool = [_leader_template(industry_rank_net_3d=3)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_leader_fails_when_buy_days_below_2():
    pool = [_leader_template(consecutive_buy_days_3d=1)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_leader_fails_when_volume_ratio_below_1_5():
    pool = [_leader_template(volume_5d_to_60d_ratio=1.4)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_leader_volume_ratio_none_treated_as_fail():
    pool = [_leader_template(volume_5d_to_60d_ratio=None)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_leader_industry_count_zero_fails():
    pool = [_leader_template(industry_count=0)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


# ---------- FOLLOWER ----------


def test_follower_classified_when_paired_with_leader():
    pool = [_leader_template(), _follower_template()]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1101"] == PRELIM_TYPE_LEADER
    assert types["1102"] == PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_no_leader_in_industry():
    pool = [_follower_template()]  # 沒有 leader
    out = classify_stocks(None, date(2026, 4, 25), pool)
    assert out == []


def test_follower_dropped_when_price_change_5d_not_positive():
    """price_change_5d <= 0 → FOLLOWER 不通過；可能落入 LAGGARD（視 gap），
    但本測試只關心「不是 FOLLOWER」。"""
    pool = [_leader_template(), _follower_template(price_change_5d=0)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_close_to_leader_pace():
    # leader=12 × 0.7 = 8.4；自己 9 不夠落後 → 不算 FOLLOWER
    pool = [_leader_template(), _follower_template(price_change_5d=9.0)]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"] for c in out}
    # 9 也未滿足 LAGGARD（gap=3<5），條件 3 net_1d 缺，所以也被 drop
    assert "1102" not in types


def test_follower_dropped_when_3d_flow_not_positive():
    """3 日法人 net <= 0 → FOLLOWER 不通過；本測試只關心「不是 FOLLOWER」。"""
    pool = [
        _leader_template(),
        _follower_template(total_institution_flow_3d=0.0),
    ]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


# ---------- LAGGARD_CANDIDATE ----------


def test_laggard_classified_with_two_hits():
    pool = [_leader_template(), _laggard_template()]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1103"] == PRELIM_TYPE_LAGGARD_CANDIDATE


def test_laggard_dropped_when_leader_gain_below_5pct():
    weak_leader = _leader_template(price_change_5d=4.0)
    pool = [weak_leader, _laggard_template()]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"] for c in out}
    assert "1103" not in types  # guard 失敗
    assert "1101" in types  # leader 仍存在（其他條件滿足）


def test_laggard_uses_volume_signal_when_net_1d_zero():
    laggard = _laggard_template(
        total_institution_flow_1d=0.0,
        volume_1d_to_5d_ratio=1.5,  # > 1.2
    )
    pool = [_leader_template(), laggard]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1103"] == PRELIM_TYPE_LAGGARD_CANDIDATE


def test_laggard_uses_ma10_breakout_when_others_miss():
    """guard + close>ma_10d → 1 + 1 = 2 hits."""
    laggard = _laggard_template(
        price_change_5d=10.0,  # gap=2<5
        total_institution_flow_1d=-1.0e6,  # 非正
        volume_1d_to_5d_ratio=None,
        close_1d=51.0,
        ma_5d=52.0,  # 不站上 5MA
        ma_10d=50.0,  # 站上 10MA → hit
    )
    pool = [_leader_template(), laggard]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1103"] == PRELIM_TYPE_LAGGARD_CANDIDATE


def test_laggard_dropped_when_only_guard_no_other_hits():
    laggard = _laggard_template(
        price_change_5d=10.0,            # gap=2<5
        total_institution_flow_1d=0.0,    # 非正
        volume_1d_to_5d_ratio=None,
        close_1d=40.0,
        ma_5d=45.0,                       # 不站上 5MA
        ma_10d=46.0,                      # 不站上 10MA
    )
    pool = [_leader_template(), laggard]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"] for c in out}
    assert "1103" not in types  # 只有 guard 一條 hit < 2


# ---------- 整體優先序 ----------


def test_leader_takes_precedence_over_follower():
    """同檔同時符合 LEADER + FOLLOWER 條件，應分到 LEADER。"""
    pool = [_leader_template(), _leader_template(stock_id="1101_alt")]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["prelim_type"] for c in out}
    assert types == {PRELIM_TYPE_LEADER}


def test_industry_top_leader_gain_uses_max_when_multiple_leaders():
    """同產業有兩個 LEADER 漲幅不同 → 後續 FOLLOWER 應比對最強 LEADER。"""
    leader_strong = _leader_template(stock_id="A", price_change_5d=20.0)
    leader_weak = _leader_template(stock_id="B", price_change_5d=10.0)
    # follower 6.5 < 10 × 0.7 = 7 但 < 20 × 0.7 = 14；用 max(20) 才合條件
    follower = _follower_template(stock_id="C", price_change_5d=6.5)
    pool = [leader_strong, leader_weak, follower]
    out = classify_stocks(None, date(2026, 4, 25), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["C"] == PRELIM_TYPE_FOLLOWER


def test_empty_pool_returns_empty_list():
    assert classify_stocks(None, date(2026, 4, 25), []) == []


def test_unmatched_stocks_are_dropped():
    """既非 LEADER 也非 FOLLOWER 也非 LAGGARD → 不應原地保留。"""
    plain = {
        "stock_id": "9999",
        "industry": "未分類",
        "industry_count": 5,
        "industry_rank_5d": 5,
        "industry_rank_net_3d": 5,
        "consecutive_buy_days_3d": 0,
        "volume_5d_to_60d_ratio": 0.5,
        "price_change_5d": 0.0,
        "total_institution_flow_3d": 0.0,
    }
    out = classify_stocks(None, date(2026, 4, 25), [plain])
    assert out == []
