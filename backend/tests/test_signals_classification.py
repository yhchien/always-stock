"""魚尾 v2.1：classification.py 規則測試（fishtail momentum upgrade spec §6.3）。

LEADER：六條件全部滿足（產業 RS / 個股產業內 RS / momentum_score>=70 /
        法人（連買 2 日或買超佔成交比前 20%）/ 量比 >=1.3 / 距 20 日高點 <=3%）
FOLLOWER：同產業有 LEADER + score 55~69 + 5 日漲幅低於 LEADER +
        rs_rank_improvement_5d > 0 + 3d 法人正 + 無爆量長上影
ROTATION_LAGGARD：同產業有 LEADER + 產業強勢 + 20 日落後產業 >=5pct +
        RS 改善 + 法人轉買或量能轉強 + 站回 10MA 或創 20 日新高 + score>=50
"""
from datetime import date

from app.signals.classification import (
    PRELIM_TYPE_FOLLOWER,
    PRELIM_TYPE_LAGGARD_CANDIDATE,
    PRELIM_TYPE_LEADER,
    PRELIM_TYPE_ROTATION_LAGGARD,
    classify_stocks,
)


# ---------- helpers ----------


def _leader_template(**overrides):
    """符合 v2.1 LEADER 六條件的最小 dict。"""
    base = {
        "stock_id": "1101",
        "industry": "水泥",
        "industry_rs_percentile_20d": 85.0,
        "rs_industry_percentile_20d": 90.0,
        "momentum_score": 78.0,
        "consecutive_buy_days_3d": 3,
        "inst_buy_to_turnover_percentile_2d": 50.0,
        "volume_5d_to_60d_ratio": 1.8,
        "distance_to_20d_high": -1.0,
        "price_change_5d": 12.0,
        "total_institution_flow_3d": 1.0e8,
    }
    base.update(overrides)
    return base


def _follower_template(**overrides):
    """同產業 LEADER price_change_5d=12；自己 5 日漲幅較低 + RS 改善 + 3d 法人正。"""
    base = {
        "stock_id": "1102",
        "industry": "水泥",
        "industry_rs_percentile_20d": 85.0,
        "rs_industry_percentile_20d": 60.0,
        "momentum_score": 62.0,
        "rs_rank_improvement_5d": 50,
        "price_change_5d": 5.0,
        "total_institution_flow_3d": 5.0e7,
        "volume_5d_to_60d_ratio": 1.0,
        "consecutive_buy_days_3d": 1,
    }
    base.update(overrides)
    return base


def _laggard_template(**overrides):
    """v2.1 ROTATION_LAGGARD：產業強勢 + 20 日落後產業 + RS 改善 + 量能轉強 + 站回 10MA。"""
    base = {
        "stock_id": "1103",
        "industry": "水泥",
        "industry_rs_percentile_20d": 80.0,
        "in_top_industries_3d": False,
        "industry_return_20d": 15.0,
        "return_20d": 5.0,  # 落後產業 10 pct >= 5
        "rs_rank_improvement_5d": 30,
        "total_institution_flow_1d": 1.0e7,
        "total_institution_flow_5d": -2.0e7,  # 由賣轉買
        "volume_1d_to_5d_ratio": 1.0,
        "close_1d": 50.0,
        "ma_10d": 48.0,  # 站回 10MA
        "distance_to_20d_high": -8.0,
        "momentum_score": 55.0,
        "price_change_5d": 1.0,
    }
    base.update(overrides)
    return base


# ---------- LEADER ----------


def test_leader_all_conditions_pass():
    pool = [_leader_template()]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    assert len(out) == 1
    assert out[0]["prelim_type"] == PRELIM_TYPE_LEADER


def test_leader_fails_when_industry_rs_below_70():
    pool = [_leader_template(industry_rs_percentile_20d=69.9)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_fails_when_stock_industry_rs_below_80():
    pool = [_leader_template(rs_industry_percentile_20d=79.9)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_fails_when_momentum_score_below_70():
    pool = [_leader_template(momentum_score=69.9)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_fails_when_momentum_score_missing():
    pool = [_leader_template(momentum_score=None)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_inst_condition_passes_via_turnover_percentile():
    """連買不足 2 日，但 institution_buy_to_turnover_2d 位於前 20% → 仍過法人條件。"""
    pool = [
        _leader_template(
            consecutive_buy_days_3d=1,
            inst_buy_to_turnover_percentile_2d=85.0,
        )
    ]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    assert len(out) == 1
    assert out[0]["prelim_type"] == PRELIM_TYPE_LEADER


def test_leader_fails_when_both_inst_conditions_fail():
    pool = [
        _leader_template(
            consecutive_buy_days_3d=1,
            inst_buy_to_turnover_percentile_2d=70.0,
        )
    ]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_fails_when_volume_ratio_below_1_3():
    pool = [_leader_template(volume_5d_to_60d_ratio=1.2)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_fails_when_too_far_from_20d_high():
    pool = [_leader_template(distance_to_20d_high=-3.1)]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_leader_distance_exactly_minus_3_passes():
    pool = [_leader_template(distance_to_20d_high=-3.0)]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    assert len(out) == 1


# ---------- FOLLOWER ----------


def test_follower_classified_when_paired_with_leader():
    pool = [_leader_template(), _follower_template()]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1101"] == PRELIM_TYPE_LEADER
    assert types["1102"] == PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_no_leader_in_industry():
    pool = [_follower_template()]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_follower_dropped_when_score_below_55():
    pool = [_leader_template(), _follower_template(momentum_score=54.9)]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_score_70_or_above():
    """score >= 70 不再是 FOLLOWER 區間（55~69）。"""
    pool = [_leader_template(), _follower_template(momentum_score=70.0)]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_gain_not_below_leader():
    pool = [_leader_template(), _follower_template(price_change_5d=12.0)]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_rs_not_improving():
    pool = [_leader_template(), _follower_template(rs_rank_improvement_5d=0)]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_when_3d_flow_not_positive():
    pool = [_leader_template(), _follower_template(total_institution_flow_3d=0.0)]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


def test_follower_dropped_on_blowoff_upper_shadow():
    """爆量長上影：量比 > 2 + 上影線 > 實體 ×2 + 收盤 < 高點 ×0.97。"""
    pool = [
        _leader_template(),
        _follower_template(
            volume_1d_to_60d_ratio=2.5,
            open_1d=100.0,
            close_1d=101.0,   # body = 1
            high_1d=108.0,    # upper shadow = 7 > 2；101 < 108×0.97=104.76
        ),
    ]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1102") != PRELIM_TYPE_FOLLOWER


# ---------- ROTATION_LAGGARD ----------


def test_rotation_laggard_classified():
    pool = [_leader_template(), _laggard_template()]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["1103"] == PRELIM_TYPE_ROTATION_LAGGARD
    # 向後相容 alias 指向同一個值
    assert PRELIM_TYPE_LAGGARD_CANDIDATE == PRELIM_TYPE_ROTATION_LAGGARD


def test_rotation_laggard_dropped_when_no_leader():
    pool = [_laggard_template()]
    assert classify_stocks(None, date(2026, 7, 15), pool) == []


def test_rotation_laggard_passes_when_industry_weak_but_in_top_industries():
    """industry_rs_percentile < 70 但 in_top_industries_3d=True → 產業強勢條件仍成立。"""
    pool = [
        _leader_template(),
        _laggard_template(industry_rs_percentile_20d=50.0, in_top_industries_3d=True),
    ]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1103") == PRELIM_TYPE_ROTATION_LAGGARD


def test_rotation_laggard_dropped_when_industry_not_strong():
    pool = [
        _leader_template(),
        _laggard_template(industry_rs_percentile_20d=50.0, in_top_industries_3d=False),
    ]
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


def test_rotation_laggard_dropped_when_gap_below_5pct():
    pool = [_leader_template(), _laggard_template(return_20d=11.0)]  # gap=4 < 5
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


def test_rotation_laggard_dropped_when_rs_not_improving():
    pool = [_leader_template(), _laggard_template(rs_rank_improvement_5d=-10)]
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


def test_rotation_laggard_volume_turn_substitutes_inst_turn():
    """法人未轉買，但量能轉強（vol_1d/5d > 1.2）→ 條件仍成立。"""
    pool = [
        _leader_template(),
        _laggard_template(
            total_institution_flow_1d=0.0,
            total_institution_flow_5d=1.0e7,
            volume_1d_to_5d_ratio=1.5,
        ),
    ]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1103") == PRELIM_TYPE_ROTATION_LAGGARD


def test_rotation_laggard_dropped_when_no_inst_turn_and_no_volume_turn():
    pool = [
        _leader_template(),
        _laggard_template(
            total_institution_flow_1d=0.0,
            volume_1d_to_5d_ratio=1.0,
        ),
    ]
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


def test_rotation_laggard_breakout_substitutes_ma10():
    """未站回 10MA，但收盤創 20 日新高（distance >= 0）→ 技術條件仍成立。"""
    pool = [
        _leader_template(),
        _laggard_template(close_1d=47.0, ma_10d=48.0, distance_to_20d_high=0.0),
    ]
    types = {c["stock_id"]: c["prelim_type"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert types.get("1103") == PRELIM_TYPE_ROTATION_LAGGARD


def test_rotation_laggard_dropped_when_below_ma10_and_no_breakout():
    pool = [
        _leader_template(),
        _laggard_template(close_1d=47.0, ma_10d=48.0, distance_to_20d_high=-8.0),
    ]
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


def test_rotation_laggard_dropped_when_score_below_50():
    pool = [_leader_template(), _laggard_template(momentum_score=49.9)]
    types = {c["stock_id"] for c in classify_stocks(None, date(2026, 7, 15), pool)}
    assert "1103" not in types


# ---------- 整體優先序 ----------


def test_leader_takes_precedence_over_follower():
    """同檔同時符合 LEADER + FOLLOWER 條件，應分到 LEADER。"""
    pool = [_leader_template(), _leader_template(stock_id="1101_alt")]
    out = classify_stocks(None, date(2026, 7, 15), pool)
    types = {c["prelim_type"] for c in out}
    assert types == {PRELIM_TYPE_LEADER}


def test_follower_compares_against_strongest_leader():
    """同產業兩個 LEADER → FOLLOWER 只需低於最強 LEADER 的 5 日漲幅。"""
    leader_strong = _leader_template(stock_id="A", price_change_5d=20.0)
    leader_weak = _leader_template(stock_id="B", price_change_5d=10.0)
    follower = _follower_template(stock_id="C", price_change_5d=15.0)  # < 20（最強）
    out = classify_stocks(None, date(2026, 7, 15), [leader_strong, leader_weak, follower])
    types = {c["stock_id"]: c["prelim_type"] for c in out}
    assert types["C"] == PRELIM_TYPE_FOLLOWER


def test_empty_pool_returns_empty_list():
    assert classify_stocks(None, date(2026, 7, 15), []) == []


def test_unmatched_stocks_are_dropped():
    """既非 LEADER 也非 FOLLOWER 也非 ROTATION_LAGGARD → 不應原地保留。"""
    plain = {
        "stock_id": "9999",
        "industry": "未分類",
        "industry_rs_percentile_20d": 30.0,
        "rs_industry_percentile_20d": 20.0,
        "momentum_score": 20.0,
        "consecutive_buy_days_3d": 0,
        "volume_5d_to_60d_ratio": 0.5,
        "price_change_5d": 0.0,
        "total_institution_flow_3d": 0.0,
    }
    assert classify_stocks(None, date(2026, 7, 15), [plain]) == []
