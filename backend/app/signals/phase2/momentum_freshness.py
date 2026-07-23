"""
Phase 2.5 §4~§13：Momentum Freshness。

核心問題：`momentum_score` / `rs_market_percentile_20d` / `momentum_phase` 這些既有
欄位衡量的是「過去 5~60 天的動能強度」，對「最近 1~3 天這個動能是否還新鮮」不夠
敏感——一檔股票可能 20 日報酬仍然亮眼，但最近 1~3 天已經開始轉弱，既有欄位要再過
好幾天才會反映出來（`momentum_phase` 的 weakening 需要 RS 排名 5 日掉 100 名以上）。

`compute_momentum_freshness()` 用**相對報酬優先於絕對報酬**（大盤跌 5%、個股跌 2% 是
相對抗跌，不是轉弱）+ 多維度證據聚合（不是單一固定門檻）判斷五種新鮮度狀態：
    FRESH_STRONG / FRESH_STABLE / HEALTHY_PULLBACK / STALE / DETERIORATING

刻意不新增獨立的 "REACCELERATING" 新鮮度狀態（避免與 `entry_state.py` 的
`ENTRY_REACCELERATING` 名稱混淆，spec §7 明文要求）——`entry_state ==
ENTRY_REACCELERATING` 直接作為 FRESH_STRONG 的一項證據。

輸入需求（全部沿用既有 candidate 欄位，不重新查 DB）：
    price_change_1d, price_change_3d, rs_rank_improvement_5d,
    high_1d/low_1d/close_1d, volume_1d_to_5d_ratio, entry_state,
    deterministic_signals.institution_flow_momentum,
    deterministic_signals.sector_rotation_status

`taiex_return_1d_pct`：大盤當日報酬（沿用既有 REVERSAL_FAILURE 同一個輸入來源，
`market_regime.compute_market_regime()` 算好後由呼叫端傳入）——用來算
`excess_return_vs_market_1d`（個股 - 大盤，相對報酬）。缺值時 relative-return 證據
一律不觸發（不可用絕對報酬冒充相對報酬）。

**刻意未實作的 spec 建議欄位**（記錄於 `docs/plans/phase25_future_recommendations.md`，
非本次遺漏，是範圍內的工程判斷）：
    - excess_return_vs_market_3d：需要大盤 3 日報酬，目前 `market_regime.py` 只算
      `return_1d_pct`；新增 3 日大盤報酬需要額外查詢，改用 `rs_rank_change_5d`
      （既有欄位）作為中期相對強度的替代證據
    - excess_return_vs_sector_1d/3d：需要「產業每日平均報酬」，目前只有
      `industry_return_20d`（20 日聚合）與 `industry_flow_1d/3d`（資金流，非價格報酬）；
      改用 `sector_rotation_status`（既有 deterministic_signals 欄位）作為產業層級
      confirmation 的替代證據
    - rs_rank_change_1d/3d：目前只有 5 日排名變化（`rs_rank_improvement_5d`），1/3 日
      粒度需要重算每日全市場排名快照，成本較高，本次以 5 日粒度 + 相對報酬證據組合
      判斷「是否還新鮮」，足夠支撐多維度證據聚合分類
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.signals.phase2 import entry_state as entry_state_mod

FRESH_STRONG = "FRESH_STRONG"
FRESH_STABLE = "FRESH_STABLE"
HEALTHY_PULLBACK = "HEALTHY_PULLBACK"
STALE = "STALE"
DETERIORATING = "DETERIORATING"

ALL_FRESHNESS_STATES = (FRESH_STRONG, FRESH_STABLE, HEALTHY_PULLBACK, STALE, DETERIORATING)

# 證據門檻（工程起始值，待 replay 觀察後校準；不得為了單一案例硬調）
_RELATIVE_RETURN_STRONG_PCT = 1.0     # 相對大盤超額報酬 >= 此值 → 強勢確認
_RELATIVE_RETURN_WEAK_PCT = -1.0      # 相對大盤超額報酬 <= 此值 → 相對轉弱
_RS_RANK_DETERIORATE_DROP = -50       # 5 日排名退步（比 momentum_phase weakening 的 -100 更敏感的早期警訊）
_CLOSE_LOCATION_STRONG = 0.6          # 收在當日區間上 40%
_CLOSE_LOCATION_WEAK = 0.3            # 收在當日區間下 30%
_VOLUME_CONFIRM_STRONG = 1.1          # 上漲日量能擴張倍數
_VOLUME_CONFIRM_WEAK = -1.3           # 下跌日量能擴張（signed ratio 為負代表下跌日放量）
_STALE_MOMENTUM_SCORE_MIN = 55.0      # 「過去曾經很強」的門檻（用於 STALE 判斷的前提）


def _excess_return_vs_market_1d(
    candidate: Dict[str, Any],
    taiex_return_1d_pct: Optional[float],
) -> Optional[float]:
    pct_1d = candidate.get("price_change_1d")
    if pct_1d is None or taiex_return_1d_pct is None:
        return None
    return pct_1d - taiex_return_1d_pct


def _close_location_value(candidate: Dict[str, Any]) -> Optional[float]:
    """CLV：(close - low) / (high - low)，0~1，越高代表收在當日區間越上緣。"""
    high = candidate.get("high_1d")
    low = candidate.get("low_1d")
    close = candidate.get("close_1d")
    if high is None or low is None or close is None or high <= low:
        return None
    return (close - low) / (high - low)


def _signed_volume_ratio(candidate: Dict[str, Any]) -> Optional[float]:
    """量能訊號依當日漲跌方向定號：上漲日放量為正（健康），下跌日放量為負（示警）。"""
    ratio = candidate.get("volume_1d_to_5d_ratio")
    pct_1d = candidate.get("price_change_1d")
    if ratio is None or pct_1d is None:
        return None
    return ratio if pct_1d >= 0 else -ratio


def compute_momentum_freshness(
    candidate: Dict[str, Any],
    *,
    taiex_return_1d_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """回傳 `{"momentum_freshness": ..., "evidence": {...}, "excess_return_vs_market_1d": ...,
    "close_location_value": ..., "relative_volume_signed": ...}`。

    多維度證據聚合，不是單一固定門檻——每個 boolean 都只是其中一票，最終狀態由
    「哪些證據同時成立」決定，而非任何單一欄位跨過某條線就直接判定。
    """
    excess_1d = _excess_return_vs_market_1d(candidate, taiex_return_1d_pct)
    clv = _close_location_value(candidate)
    signed_vol = _signed_volume_ratio(candidate)
    rs_change_5d = candidate.get("rs_rank_improvement_5d")
    entry_state = candidate.get("entry_state")
    det_signals = candidate.get("deterministic_signals") or {}
    inst_momentum = det_signals.get("institution_flow_momentum")
    sector_status = det_signals.get("sector_rotation_status")
    momentum_score = candidate.get("momentum_score")

    evidence = {
        "relative_return_strong": excess_1d is not None and excess_1d >= _RELATIVE_RETURN_STRONG_PCT,
        "relative_return_weak": excess_1d is not None and excess_1d <= _RELATIVE_RETURN_WEAK_PCT,
        "rs_rank_improving": rs_change_5d is not None and rs_change_5d > 0,
        "rs_rank_deteriorating": rs_change_5d is not None and rs_change_5d <= _RS_RANK_DETERIORATE_DROP,
        "close_strong": clv is not None and clv >= _CLOSE_LOCATION_STRONG,
        "close_weak": clv is not None and clv <= _CLOSE_LOCATION_WEAK,
        "volume_confirms_strength": signed_vol is not None and signed_vol >= _VOLUME_CONFIRM_STRONG,
        "volume_confirms_weakness": signed_vol is not None and signed_vol <= _VOLUME_CONFIRM_WEAK,
        "institution_confirming": inst_momentum in ("accelerating", "stable"),
        "institution_reversal": inst_momentum == "reversal",
        "sector_confirming": sector_status == "inflow",
        "sector_failed": sector_status == "failed_rotation",
        "entry_reaccelerating": entry_state == entry_state_mod.ENTRY_REACCELERATING,
        "entry_pullback_intact": entry_state in (
            entry_state_mod.ENTRY_NORMAL_PULLBACK,
            entry_state_mod.ENTRY_DEEP_PULLBACK,
        ),
        "entry_near_high": entry_state == entry_state_mod.ENTRY_NEAR_HIGH,
    }

    negative_votes = sum(
        1
        for k in ("rs_rank_deteriorating", "close_weak", "volume_confirms_weakness", "institution_reversal", "sector_failed")
        if evidence[k]
    )
    positive_confirmation_votes = sum(
        1
        for k in ("rs_rank_improving", "institution_confirming", "sector_confirming", "entry_reaccelerating")
        if evidence[k]
    )
    strength_votes = sum(1 for k in ("close_strong", "volume_confirms_strength") if evidence[k])

    # 1) DETERIORATING：至少 2 個獨立負面證據同時成立，且沒有夠強的相對表現抵銷
    if negative_votes >= 2 and not evidence["relative_return_strong"]:
        state = DETERIORATING
    # 2) FRESH_STRONG：相對強勢 + 至少一項確認證據 + 至少一項強度證據，且無負面訊號
    #    （優先於 HEALTHY_PULLBACK：即使 entry_state 仍落在拉回區間，只要今天的多維度
    #    證據已經明確共振轉強，應該反映「正在重新加速」而非停留在單純的拉回描述）
    elif (
        evidence["relative_return_strong"]
        and positive_confirmation_votes >= 1
        and strength_votes >= 1
        and negative_votes == 0
    ):
        state = FRESH_STRONG
    # 3) HEALTHY_PULLBACK：正在拉回但相對報酬不差、且沒有多重負面訊號
    elif evidence["entry_pullback_intact"] and not evidence["relative_return_weak"] and negative_votes == 0:
        state = HEALTHY_PULLBACK
    # 4) STALE：曾經很強（momentum_score 高）但目前缺乏任何新鮮確認，也還沒到轉弱
    elif (
        momentum_score is not None
        and momentum_score >= _STALE_MOMENTUM_SCORE_MIN
        and positive_confirmation_votes == 0
        and strength_votes == 0
        and negative_votes <= 1
    ):
        state = STALE
    # 5) 預設 FRESH_STABLE：資格內、無明顯轉弱，但也未達 FRESH_STRONG 的多維共振
    else:
        state = FRESH_STABLE

    return {
        "momentum_freshness": state,
        "momentum_freshness_evidence": evidence,
        "excess_return_vs_market_1d": excess_1d,
        "close_location_value": clv,
        "relative_volume_signed": signed_vol,
    }


def freshness_rank(state: Optional[str]) -> int:
    """供 debug / 排序用的強弱序（0 最強）。未知狀態視為最弱。"""
    order = {
        FRESH_STRONG: 0,
        FRESH_STABLE: 1,
        HEALTHY_PULLBACK: 2,
        STALE: 3,
        DETERIORATING: 4,
    }
    return order.get(state, 5)
