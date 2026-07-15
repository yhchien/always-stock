"""魚尾 v2.1 動能特徵層（fishtail momentum upgrade spec §6.1B/C + §6.2）。

把魚尾從「法人異常訊號」升級成「動能選股」的 feature engine：

1. `compute_market_momentum_frame`：全市場（active、非 ETF / 金融 / 黑名單）
   近 66 個交易日的價格動能 / 相對強度 / 排名改善 / 法人買超佔成交比特徵。
   percentile 一律 0~100、越高越強；樣本不足時回 None（不硬給 50）。
2. `select_momentum_candidates`：候選池 B（價格動能）/ C（動能加速）兩通道。
3. `compute_momentum_score`：deterministic 0~100 分數（percentile-based），
   權重 = 價格動能 30 / 相對強度 25 / 法人資金 20 / 量價品質 15 / 基本面動能 10
   （v2.1 基本面尚未接 announcement_date，固定 0 分）+ 風險扣分。

設計原則（spec §2）：
- deterministic、可測試、可落 snapshot；嚴禁未來函數（只用 target_date 及以前資料）
- percentile-based，不直接拿 raw return 當分數
- 樣本不足（新上市 / 資料缺漏）→ 對應子分數 0 分、percentile None，
  不會讓資料缺漏股在震盪盤的 score gate 下混進清單
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy.orm import Session

from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice, InstStockFlow, StockMaster
from app.signals.exclusions import should_exclude

logger = logging.getLogger(__name__)

_INST_TYPES = ("foreign", "trust", "dealer")

# 66 = return_60d 需 61 根 + rs_rank_improvement_5d 需回看 5 日的 20d return（26 根），取上限再留 buffer
MOMENTUM_LOOKBACK_DAYS = 66

# percentile 有效性 guard：樣本太小 percentile 沒有意義（測試 / 資料缺漏日防護）
MIN_SAMPLES_FOR_PERCENTILE = 20
MIN_INDUSTRY_MEMBERS_FOR_PERCENTILE = 3
MIN_INDUSTRIES_FOR_PERCENTILE = 5

# spec §6.1 B 價格動能候選門檻
CHANNEL_B_RS_MARKET_MIN = 85.0
CHANNEL_B_RS_INDUSTRY_MIN = 80.0
CHANNEL_B_NEW_HIGH_VOLUME_RATIO_MIN = 1.2
CHANNEL_B_RETURN_60D_PERCENTILE_MIN = 85.0
# spec §6.1 C 動能加速候選門檻
CHANNEL_C_RANK_IMPROVEMENT_MIN = 200
CHANNEL_C_RS_MARKET_MIN = 70.0

# 通道上限（spec 未定；工程決策避免 percentile>=85 一次灌 200+ 檔爆掉 POOL_HARD_LIMIT）
CHANNEL_B_LIMIT = 40
CHANNEL_C_LIMIT = 20

# spec §6.2 momentum_score 權重
_W_PRICE = 30.0
_W_RS = 25.0
_W_INSTITUTION = 20.0
_W_VOLUME = 15.0
_W_FUNDAMENTAL = 10.0  # v2.1 未接 announcement_date → 恆 0，保留權重位

# 風險扣分
_PENALTY_BLOWOFF_SHADOW = 10.0       # 爆量長上影（派發嫌疑）
_PENALTY_RS_COLLAPSE = 10.0          # RS 排名 5 日內急速惡化
_PENALTY_OVERHEAT_3D = 5.0           # 3 日漲幅逼近 15% 剔除線
_RS_COLLAPSE_RANK_DROP = -200
_OVERHEAT_3D_PCT = 12.0
_BLOWOFF_VOL_RATIO = 2.0
_BLOWOFF_SHADOW_BODY_RATIO = 2.0
_BLOWOFF_PULLBACK_PCT = 0.97


# ---------- 空特徵模板 ----------


def empty_momentum_features() -> Dict[str, Any]:
    """frame 缺該股（資料不足 / 新上市）時的預設值。"""
    return {
        "return_5d": None,
        "return_20d": None,
        "return_60d": None,
        "relative_strength_market_20d": None,
        "relative_strength_industry_20d": None,
        "rs_market_percentile_20d": None,
        "rs_industry_percentile_20d": None,
        "return_percentile_5d": None,
        "return_percentile_60d": None,
        "rs_rank_20d_current": None,
        "rs_rank_20d_previous_5d": None,
        "rs_rank_improvement_5d": None,
        "distance_to_20d_high": None,
        "distance_to_60d_high": None,
        "distance_to_ma20": None,
        "trend_efficiency_20d": None,
        "volume_1d_to_20d_avg": None,
        "volume_ratio_percentile_5d_60d": None,
        "industry_rs_percentile_20d": None,
        "industry_return_20d": None,
        "institution_net_buy_amount_2d": None,
        "institution_buy_to_turnover_2d": None,
        "inst_buy_to_turnover_percentile_2d": None,
    }


# ---------- Frame：全市場動能特徵 ----------


def compute_market_momentum_frame(
    db: Session,
    target_date: date,
    masters: Dict[str, StockMaster],
) -> Dict[str, Dict[str, Any]]:
    """全市場動能特徵 frame：{stock_id: features}。

    universe = stocks_master active 且非 ETF / 金融 / 黑名單（與候選池排除規則一致，
    spec §7.1 注意事項：breadth / percentile universe 必須與 candidate pool 一致）。

    benchmark 說明：`relative_strength_market_20d` 用「universe 20 日報酬中位數」當
    market benchmark（而非 TAIEX），因為 percentile 對常數 benchmark 平移不變，
    且中位數對權值股極端值更穩健；`rs_market_percentile_20d` 數學上等價於
    return_20d 的全市場 percentile。
    """
    universe_ids = {
        sid
        for sid, m in masters.items()
        if not should_exclude(sid, m.stock_name, m.industry_name)
    }
    if not universe_ids:
        return {}

    trade_dates = get_recent_trade_dates(db, target_date, MOMENTUM_LOOKBACK_DAYS)
    if not trade_dates:
        return {}

    rows = (
        db.query(
            DailyPrice.stock_id,
            DailyPrice.trade_date,
            DailyPrice.close_price,
            DailyPrice.volume,
            DailyPrice.turnover,
        )
        .filter(DailyPrice.trade_date.in_(list(trade_dates)))
        .all()
    )

    closes_by_stock: Dict[str, List[Any]] = {}
    for sid, td, close, volume, turnover in rows:
        if sid not in universe_ids:
            continue
        closes_by_stock.setdefault(sid, []).append((td, close, volume, turnover))

    frame: Dict[str, Dict[str, Any]] = {}
    last_2_dates = set(trade_dates[-2:])
    turnover_2d_by_stock: Dict[str, float] = {}

    for sid, series in closes_by_stock.items():
        series.sort(key=lambda item: item[0])
        closes = [float(c) for (_, c, _, _) in series if c is not None]
        volumes = [float(v) for (_, _, v, _) in series if v is not None and v > 0]
        turnover_2d = sum(
            float(t) for (td, _, _, t) in series if td in last_2_dates and t is not None
        )
        turnover_2d_by_stock[sid] = turnover_2d

        feats = empty_momentum_features()
        feats.update(_price_features(closes, volumes))
        frame[sid] = feats

    if not frame:
        return {}

    # 全市場 percentile / rank
    _attach_market_percentiles(frame)

    # 產業內 percentile + 產業層級 RS percentile
    _attach_industry_percentiles(frame, masters)

    # 法人 2 日買超佔成交比
    _attach_institution_turnover(db, frame, trade_dates[-2:], turnover_2d_by_stock)

    return frame


def _price_features(closes: List[float], volumes: List[float]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    n = len(closes)
    if n == 0:
        return out
    close = closes[-1]

    def _ret(days: int) -> Optional[float]:
        if n < days + 1 or not closes[-(days + 1)]:
            return None
        return (close / closes[-(days + 1)] - 1.0) * 100.0

    out["return_5d"] = _ret(5)
    out["return_20d"] = _ret(20)
    out["return_60d"] = _ret(60)

    # 回看 5 日的 20d return（rs_rank_20d_previous_5d 用）；rank 在 frame 層統一算
    if n >= 26 and closes[-26]:
        out["_return_20d_prev5"] = (closes[-6] / closes[-26] - 1.0) * 100.0

    # rolling high（用收盤價；含當日 → distance=0 表示收盤創 N 日新高）
    if n >= 20:
        high20 = max(closes[-20:])
        if high20:
            out["distance_to_20d_high"] = (close / high20 - 1.0) * 100.0
    if n >= 60:
        high60 = max(closes[-60:])
        if high60:
            out["distance_to_60d_high"] = (close / high60 - 1.0) * 100.0

    # MA20 距離
    if n >= 20:
        ma20 = sum(closes[-20:]) / 20.0
        if ma20:
            out["distance_to_ma20"] = (close / ma20 - 1.0) * 100.0

    # 趨勢效率（20 日淨位移 / 路徑總長；0~1，越高越像單邊趨勢）
    if n >= 21:
        path = sum(abs(closes[i] - closes[i - 1]) for i in range(n - 20, n))
        if path > 0:
            out["trend_efficiency_20d"] = abs(close - closes[-21]) / path

    # 量能：當日 / 20 日均量
    if volumes:
        avg_20 = sum(volumes[-20:]) / min(20, len(volumes))
        if avg_20 > 0:
            out["volume_1d_to_20d_avg"] = volumes[-1] / avg_20
        avg_60 = sum(volumes) / len(volumes)
        avg_5 = sum(volumes[-5:]) / min(5, len(volumes))
        if avg_60 > 0:
            out["_volume_5d_to_60d"] = avg_5 / avg_60

    return out


def _percentile_map(values_by_id: Dict[str, float]) -> Dict[str, float]:
    """0~100 percentile（越高越強）；用「嚴格小於的數量 / (n-1)」，ties 同分。"""
    n = len(values_by_id)
    if n < 2:
        return {}
    sorted_vals = sorted(values_by_id.values())
    out: Dict[str, float] = {}
    for sid, v in values_by_id.items():
        # bisect_left = 嚴格小於 v 的個數
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_vals[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        out[sid] = 100.0 * lo / (n - 1)
    return out


def _attach_market_percentiles(frame: Dict[str, Dict[str, Any]]) -> None:
    for src_key, dst_key in (
        ("return_20d", "rs_market_percentile_20d"),
        ("return_5d", "return_percentile_5d"),
        ("return_60d", "return_percentile_60d"),
        ("_volume_5d_to_60d", "volume_ratio_percentile_5d_60d"),
    ):
        values = {
            sid: feats[src_key]
            for sid, feats in frame.items()
            if feats.get(src_key) is not None
        }
        if len(values) < MIN_SAMPLES_FOR_PERCENTILE:
            continue
        pct = _percentile_map(values)
        for sid, p in pct.items():
            frame[sid][dst_key] = p

    # relative_strength_market_20d：對 universe 中位數的超額報酬
    ret_values = {
        sid: feats["return_20d"]
        for sid, feats in frame.items()
        if feats.get("return_20d") is not None
    }
    if len(ret_values) >= MIN_SAMPLES_FOR_PERCENTILE:
        med = _median(list(ret_values.values()))
        for sid, r in ret_values.items():
            frame[sid]["relative_strength_market_20d"] = r - med

    # rs_rank（1 = 最強）：當前 + 回看 5 日，兩者都有才算 improvement
    _attach_rs_ranks(frame)


def _attach_rs_ranks(frame: Dict[str, Dict[str, Any]]) -> None:
    current = {
        sid: feats["return_20d"]
        for sid, feats in frame.items()
        if feats.get("return_20d") is not None
    }
    previous = {
        sid: feats["_return_20d_prev5"]
        for sid, feats in frame.items()
        if feats.get("_return_20d_prev5") is not None
    }
    if len(current) < MIN_SAMPLES_FOR_PERCENTILE:
        return

    cur_rank = _rank_map(current)
    for sid, rank in cur_rank.items():
        frame[sid]["rs_rank_20d_current"] = rank

    if len(previous) < MIN_SAMPLES_FOR_PERCENTILE:
        return
    prev_rank = _rank_map(previous)
    for sid in frame:
        cur = frame[sid].get("rs_rank_20d_current")
        prev = prev_rank.get(sid)
        if cur is None or prev is None:
            continue
        frame[sid]["rs_rank_20d_previous_5d"] = prev
        # 正值 = 排名前進（例如從 500 名進到 250 名 → +250）
        frame[sid]["rs_rank_improvement_5d"] = prev - cur


def _rank_map(values_by_id: Dict[str, float]) -> Dict[str, int]:
    """1 = 值最大（最強）。ties 依 stock_id 穩定排序，保證 deterministic。"""
    ordered = sorted(values_by_id.items(), key=lambda kv: (-kv[1], kv[0]))
    return {sid: idx for idx, (sid, _) in enumerate(ordered, start=1)}


def _attach_industry_percentiles(
    frame: Dict[str, Dict[str, Any]],
    masters: Dict[str, StockMaster],
) -> None:
    by_industry: Dict[str, Dict[str, float]] = {}
    for sid, feats in frame.items():
        master = masters.get(sid)
        ret = feats.get("return_20d")
        if master is None or not master.industry_name or ret is None:
            continue
        by_industry.setdefault(master.industry_name, {})[sid] = ret

    industry_avg: Dict[str, float] = {}
    for ind, members in by_industry.items():
        avg = sum(members.values()) / len(members)
        industry_avg[ind] = avg
        med = _median(list(members.values()))
        if len(members) < MIN_INDUSTRY_MEMBERS_FOR_PERCENTILE:
            continue
        pct = _percentile_map(members)
        for sid, p in pct.items():
            frame[sid]["rs_industry_percentile_20d"] = p
            frame[sid]["relative_strength_industry_20d"] = members[sid] - med

    # 產業層級 RS percentile（產業平均 20d 報酬在所有產業間的 percentile）
    if len(industry_avg) >= MIN_INDUSTRIES_FOR_PERCENTILE:
        ind_pct = _percentile_map(industry_avg)
        for sid, feats in frame.items():
            master = masters.get(sid)
            if master is None or not master.industry_name:
                continue
            if master.industry_name in ind_pct:
                feats["industry_rs_percentile_20d"] = ind_pct[master.industry_name]
                feats["industry_return_20d"] = industry_avg[master.industry_name]


def _attach_institution_turnover(
    db: Session,
    frame: Dict[str, Dict[str, Any]],
    trade_dates_2d: Sequence[date],
    turnover_2d_by_stock: Dict[str, float],
) -> None:
    if not trade_dates_2d:
        return
    rows = (
        db.query(InstStockFlow.stock_id, InstStockFlow.net_amount_est)
        .filter(
            InstStockFlow.trade_date.in_(list(trade_dates_2d)),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )
    flow_2d: Dict[str, float] = {}
    for sid, amt in rows:
        if sid not in frame:
            continue
        flow_2d[sid] = flow_2d.get(sid, 0.0) + float(amt or 0.0)

    ratio_by_id: Dict[str, float] = {}
    for sid, flow in flow_2d.items():
        frame[sid]["institution_net_buy_amount_2d"] = flow
        turnover = turnover_2d_by_stock.get(sid) or 0.0
        if turnover > 0:
            ratio = flow / turnover
            frame[sid]["institution_buy_to_turnover_2d"] = ratio
            ratio_by_id[sid] = ratio

    if len(ratio_by_id) < MIN_SAMPLES_FOR_PERCENTILE:
        return
    pct = _percentile_map(ratio_by_id)
    for sid, p in pct.items():
        frame[sid]["inst_buy_to_turnover_percentile_2d"] = p


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# ---------- 候選通道 B / C ----------


def select_momentum_candidates(
    frame: Dict[str, Dict[str, Any]],
) -> Dict[str, List[str]]:
    """spec §6.1 B（價格動能）/ C（動能加速）兩通道候選。

    回傳 {"price_momentum": [...], "acceleration": [...]}；
    各通道有上限（依強度排序取前 N），避免 percentile 門檻在大 universe 一次灌爆候選池。
    """
    b_matches: List[str] = []
    c_matches: List[str] = []
    for sid, feats in frame.items():
        if _is_price_momentum_candidate(feats):
            b_matches.append(sid)
        if _is_acceleration_candidate(feats):
            c_matches.append(sid)

    b_matches.sort(
        key=lambda sid: (
            -(frame[sid].get("rs_market_percentile_20d") or 0.0),
            -(frame[sid].get("return_20d") or 0.0),
            sid,
        )
    )
    c_matches.sort(
        key=lambda sid: (-(frame[sid].get("rs_rank_improvement_5d") or 0), sid)
    )
    return {
        "price_momentum": b_matches[:CHANNEL_B_LIMIT],
        "acceleration": c_matches[:CHANNEL_C_LIMIT],
    }


def _is_price_momentum_candidate(feats: Dict[str, Any]) -> bool:
    rs_mkt = feats.get("rs_market_percentile_20d")
    if rs_mkt is not None and rs_mkt >= CHANNEL_B_RS_MARKET_MIN:
        return True
    rs_ind = feats.get("rs_industry_percentile_20d")
    if rs_ind is not None and rs_ind >= CHANNEL_B_RS_INDUSTRY_MIN:
        return True
    dist_high = feats.get("distance_to_20d_high")
    vol_ratio = feats.get("volume_1d_to_20d_avg")
    if (
        dist_high is not None
        and dist_high >= 0.0  # 收盤創 20 日新高
        and vol_ratio is not None
        and vol_ratio >= CHANNEL_B_NEW_HIGH_VOLUME_RATIO_MIN
    ):
        return True
    p60 = feats.get("return_percentile_60d")
    ret5 = feats.get("return_5d")
    if (
        p60 is not None
        and p60 >= CHANNEL_B_RETURN_60D_PERCENTILE_MIN
        and ret5 is not None
        and ret5 > 0
    ):
        return True
    return False


def _is_acceleration_candidate(feats: Dict[str, Any]) -> bool:
    improvement = feats.get("rs_rank_improvement_5d")
    rs_mkt = feats.get("rs_market_percentile_20d")
    return (
        improvement is not None
        and improvement >= CHANNEL_C_RANK_IMPROVEMENT_MIN
        and rs_mkt is not None
        and rs_mkt >= CHANNEL_C_RS_MARKET_MIN
    )


# ---------- momentum_score ----------


def compute_momentum_score(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """spec §6.2：deterministic momentum_score（0~100）+ 子分數明細。

    輸入為「候選池 dict」（已 merge frame 特徵 + pool metrics：consecutive_buy_days_3d /
    OHLC / volume ratios）。純函式、無 DB 依賴，方便單元測試。

    缺資料的子項給 0 分（不硬給中性 50）：新上市 / 資料缺漏股不應靠「未知」得分，
    在震盪盤的 score gate（>= 60）下會自然被擋掉。
    """
    price_score = _W_PRICE * _weighted_percentile(
        candidate,
        (("rs_market_percentile_20d", 0.5), ("return_percentile_60d", 0.3), ("return_percentile_5d", 0.2)),
    )
    rs_score = _W_RS * _weighted_percentile(
        candidate,
        (("rs_market_percentile_20d", 0.6), ("rs_industry_percentile_20d", 0.4)),
    )

    inst_pct = candidate.get("inst_buy_to_turnover_percentile_2d")
    buy_days = candidate.get("consecutive_buy_days_3d")
    inst_component = 0.0
    if inst_pct is not None:
        inst_component += 0.6 * (inst_pct / 100.0)
    if buy_days is not None:
        inst_component += 0.4 * (min(int(buy_days), 3) / 3.0)
    inst_score = _W_INSTITUTION * inst_component

    vol_pct = candidate.get("volume_ratio_percentile_5d_60d")
    volume_score = _W_VOLUME * ((vol_pct / 100.0) if vol_pct is not None else 0.0)

    fundamental_score = 0.0  # v2.1：monthly_revenue 無 announcement_date，不接主流程（spec §9.4）

    penalty = 0.0
    penalty_reasons: List[str] = []
    if _has_blowoff_upper_shadow(candidate):
        penalty += _PENALTY_BLOWOFF_SHADOW
        penalty_reasons.append("blowoff_upper_shadow")
    improvement = candidate.get("rs_rank_improvement_5d")
    if improvement is not None and improvement <= _RS_COLLAPSE_RANK_DROP:
        penalty += _PENALTY_RS_COLLAPSE
        penalty_reasons.append("rs_rank_collapse")
    pct_3d = candidate.get("price_change_3d")
    if pct_3d is not None and pct_3d > _OVERHEAT_3D_PCT:
        penalty += _PENALTY_OVERHEAT_3D
        penalty_reasons.append("overheat_3d")

    raw = price_score + rs_score + inst_score + volume_score + fundamental_score - penalty
    score = round(max(0.0, min(100.0, raw)), 1)

    return {
        "momentum_score": score,
        "momentum_score_detail": {
            "price": round(price_score, 1),
            "relative_strength": round(rs_score, 1),
            "institution": round(inst_score, 1),
            "volume_quality": round(volume_score, 1),
            "fundamental": None,  # v2.1 未接（announcement_date 缺）
            "risk_penalty": round(penalty, 1),
            "penalty_reasons": penalty_reasons,
        },
    }


def _weighted_percentile(
    candidate: Dict[str, Any],
    keys_weights: Sequence[Any],
) -> float:
    """percentile（0~100）加權後轉 0~1；缺值項貢獻 0。"""
    total = 0.0
    for key, weight in keys_weights:
        value = candidate.get(key)
        if value is None:
            continue
        total += weight * (float(value) / 100.0)
    return total


def _has_blowoff_upper_shadow(candidate: Dict[str, Any]) -> bool:
    """爆量長上影：當日量 > 60 日均量 ×2 且上影線 > 實體 ×2 且收盤 < 高點 ×0.97。"""
    vol_ratio = candidate.get("volume_1d_to_60d_ratio")
    if vol_ratio is None or vol_ratio <= _BLOWOFF_VOL_RATIO:
        return False
    high = candidate.get("high_1d")
    open_ = candidate.get("open_1d")
    close = candidate.get("close_1d")
    if high is None or open_ is None or close is None or high <= 0:
        return False
    upper_shadow = high - close
    body = close - open_
    return (
        upper_shadow > body * _BLOWOFF_SHADOW_BODY_RATIO
        and close < high * _BLOWOFF_PULLBACK_PCT
    )


# ---------- persistence view（spec §9.2 第一批落地欄位） ----------


def build_signal_metrics(
    candidate: Dict[str, Any],
    regime_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """spec §9.2：組出要寫進 SignalWatchHit.signal_metrics / snapshot 的 JSON。

    全部是 float / str / None，保證 JSON 可序列化（無 date 物件）。
    breadth_score 為 v2.2 佔位（目前 None）。
    """
    regime_detail = None
    if regime_info:
        regime_detail = {
            "regime": regime_info.get("regime"),
            "reason": regime_info.get("reason"),
        }
    return {
        "return_5d": candidate.get("return_5d"),
        "return_20d": candidate.get("return_20d"),
        "return_60d": candidate.get("return_60d"),
        "rs_market_percentile_20d": candidate.get("rs_market_percentile_20d"),
        "rs_industry_percentile_20d": candidate.get("rs_industry_percentile_20d"),
        "rs_rank_improvement_5d": candidate.get("rs_rank_improvement_5d"),
        "institution_buy_to_turnover_2d": candidate.get("institution_buy_to_turnover_2d"),
        "trend_efficiency_20d": candidate.get("trend_efficiency_20d"),
        "distance_to_high_20d": candidate.get("distance_to_20d_high"),
        "distance_to_ma20": candidate.get("distance_to_ma20"),
        "momentum_score": candidate.get("momentum_score"),
        "momentum_score_detail": candidate.get("momentum_score_detail"),
        "market_regime_detail": regime_detail,
        "breadth_score": None,  # v2.2
    }
