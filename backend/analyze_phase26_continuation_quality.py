"""Phase 2.6 Continuation / Hold Quality Research（2026-07-24）。

**只做 research/shadow，不修改任何 production 程式碼、不重跑 617 檔 full pipeline、
不修改既有 tracking_state。**

核心問題：「股票第一次被魚尾抓到之後，如何判斷它是正常續強、健康整理、開始惡化，
還是真正失敗？」——不是重新選股（Day0 selection），是回答「原本成立的 Momentum
thesis，到今天還成立多少？」

沿用既有 40 檔 matched sample（`/tmp/phase26_matched_40.json`，20 WINNER
future_return_10d>=+10% + 20 BIG_LOSER future_return_10d<=-10%）。對每檔股票
在 Day0/1/2/3/5/7/10（相對 first_seen 的交易日 offset）算：

    PROFIT_PATH：current_return / max_return_so_far(MFE) / max_loss_so_far(MAE) /
                 drawdown_from_max
    RELATIVE_PERFORMANCE：excess_return_vs_market_1d / _3d（vs TAIEX）
    MOMENTUM_STATE：momentum_freshness（簡化版，見下）/ tracking_state（簡化版）
    HARD_FAILURE：tracking_state=INVALIDATED（沿用 candidate_pool 既有
                  TRACKING_FAILED_* 門檻）/ entry_state=STRUCTURE_DAMAGED /
                  REVERSAL_FAILURE-like（法人反轉 + 相對大盤轉弱同時成立）

**已知簡化（誠實揭露，非隱藏限制）**：momentum_freshness / entry_state /
tracking_state 的完整 production 版本需要全市場 momentum frame 算
`rs_market_percentile_20d` / `rs_rank_improvement_5d` / `peer_rs_percentile_20d`
等橫斷面排名——這份研究刻意不重跑全市場 frame（成本過高，且 spec 明確要求
「不要每天完整重跑 pipeline」），改用**單一股票自己的歷史序列**就能算出的量
（20 日 rolling high、14 日 ATR、close location value、法人流量、相對大盤超額
報酬）餵給同一套 production pure function，橫斷面相關的欄位（rs_rank_improvement_5d
等）留 None，讓 function 依既有邏輯優雅降級（entry_state 因此只會落在
NEAR_HIGH/NORMAL_PULLBACK/DEEP_PULLBACK，STRUCTURE_DAMAGED/REACCELERATING 兩態
理論上不會觸發，因為那兩態需要 rs_rank_improvement 才能判斷——這點在報告中會
清楚說明，不是本研究的最終結論依據）。

用法：
    python analyze_phase26_continuation_quality.py
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice, InstStockFlow
from app.signals import deterministic_signals as det_signals
from app.signals.candidate_pool import (
    TRACKING_FAILED_DAYS_THRESHOLD,
    TRACKING_FAILED_MAX_NEGATIVE_PCT,
    TRACKING_FAILED_MAX_POSITIVE_PCT,
)
from app.signals.phase2 import entry_state as entry_state_mod
from app.signals.phase2 import momentum_freshness as freshness_mod
from app.signals.phase2 import tracking_state as tracking_mod

MATCHED_40_PATH = "/tmp/phase26_matched_40.json"
REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
OUT_CSV = "/tmp/continuation_quality_matched40.csv"

OFFSETS = (0, 1, 2, 3, 5, 7, 10)
_INST_TYPES = ("foreign", "trust", "dealer")

# ---- Continuation quality shadow rule 門檻（工程起始值，不為結果反覆調整）----
_PROFIT_GIVEBACK_MIN_PEAK_PCT = 3.0     # 曾經至少賺過這麼多才談得上「吐回」
_PROFIT_GIVEBACK_DRAWDOWN_PCT = -8.0    # 從高點回落達此值 → PROFIT_PATH 惡化
_PROFIT_NEVER_WORKED_CURRENT_PCT = -5.0  # 從未真正賺錢 + 目前已顯著虧損
_PROFIT_NEVER_WORKED_MAX_PCT = 3.0
_RELATIVE_WEAK_1D_PCT = -1.0
_RELATIVE_WEAK_3D_PCT = -2.0
_REVERSAL_RATIO_MIN = 0.5
_REVERSAL_EXCESS_MAX = -1.5


def load_cohort() -> Dict[str, Dict[str, Any]]:
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return first_seen


def load_taiex_returns(db, all_days: List[date]) -> Dict[date, Dict[str, Optional[float]]]:
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(DailyPrice.stock_id == "TAIEX", DailyPrice.trade_date.in_(all_days))
        .all()
    )
    by_date = {d: float(c) for d, c in rows if c is not None}
    ordered = [d for d in all_days if d in by_date]
    idx = {d: i for i, d in enumerate(ordered)}
    out: Dict[date, Dict[str, Optional[float]]] = {}
    for d in ordered:
        i = idx[d]
        r1 = (by_date[d] / by_date[ordered[i - 1]] - 1.0) * 100.0 if i >= 1 and by_date[ordered[i - 1]] else None
        r3 = (by_date[d] / by_date[ordered[i - 3]] - 1.0) * 100.0 if i >= 3 and by_date[ordered[i - 3]] else None
        out[d] = {"return_1d_pct": r1, "return_3d_pct": r3}
    return out


def compute_atr_pct(ohlc: List[Any]) -> Optional[float]:
    """ohlc: [(high, low, close), ...] 升序，需要 >=15 筆。"""
    if len(ohlc) < 15:
        return None
    trs = []
    for i in range(1, len(ohlc)):
        high, low, close = ohlc[i]
        prev_close = ohlc[i - 1][2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    last_close = ohlc[-1][2]
    if last_close <= 0:
        return None
    atr = sum(trs[-14:]) / min(14, len(trs))
    return atr / last_close * 100.0


def build_stock_trajectory(
    db,
    stock_id: str,
    day0: date,
    all_days: List[date],
    day_index: Dict[date, int],
    taiex_ret: Dict[date, Dict[str, Optional[float]]],
) -> List[Dict[str, Any]]:
    if day0 not in day_index:
        return []
    i0 = day_index[day0]
    min_j = i0 - 24  # 20 日高點回看 + 一點緩衝
    max_j = i0 + max(OFFSETS)
    if min_j < 0 or max_j >= len(all_days):
        max_j = min(max_j, len(all_days) - 1)
        if min_j < 0:
            return []
    query_days = all_days[min_j: max_j + 1]

    price_rows = (
        db.query(DailyPrice.trade_date, DailyPrice.open_price, DailyPrice.high_price,
                  DailyPrice.low_price, DailyPrice.close_price, DailyPrice.volume)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date.in_(query_days))
        .order_by(DailyPrice.trade_date)
        .all()
    )
    price_by_date: Dict[date, Dict[str, Optional[float]]] = {}
    for r in price_rows:
        price_by_date[r.trade_date] = {
            "open": float(r.open_price) if r.open_price is not None else None,
            "high": float(r.high_price) if r.high_price is not None else None,
            "low": float(r.low_price) if r.low_price is not None else None,
            "close": float(r.close_price) if r.close_price is not None else None,
            "volume": float(r.volume) if r.volume is not None else None,
        }

    flow_rows = (
        db.query(InstStockFlow.trade_date, InstStockFlow.net_amount_est)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date.in_(query_days),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )
    flow_by_date: Dict[date, float] = {}
    for d, amt in flow_rows:
        flow_by_date[d] = flow_by_date.get(d, 0.0) + float(amt or 0.0)

    day0_close = price_by_date.get(day0, {}).get("close")
    if day0_close is None:
        return []

    daily_returns_since_day0: List[float] = []  # index 對應每個交易日 offset（含 day0）
    rows_out = []
    last_emitted_offset = -1

    for off in range(0, max(OFFSETS) + 1):
        j = i0 + off
        if j >= len(all_days):
            break
        d = all_days[j]
        prices = price_by_date.get(d)
        if prices is None or prices.get("close") is None:
            continue
        close = prices["close"]
        cur_return = (close / day0_close - 1.0) * 100.0
        daily_returns_since_day0.append(cur_return)

        if off not in OFFSETS:
            continue

        max_return_so_far = max(daily_returns_since_day0)
        max_loss_so_far = min(daily_returns_since_day0)
        drawdown_from_max = cur_return - max_return_so_far  # <= 0

        prev_day = all_days[j - 1] if j >= 1 else None
        prev_close = price_by_date.get(prev_day, {}).get("close") if prev_day else None
        return_1d = (close / prev_close - 1.0) * 100.0 if prev_close else None
        d3 = all_days[j - 3] if j >= 3 else None
        close_d3 = price_by_date.get(d3, {}).get("close") if d3 else None
        return_3d = (close / close_d3 - 1.0) * 100.0 if close_d3 else None

        taiex_today = taiex_ret.get(d, {})
        excess_1d = (return_1d - taiex_today["return_1d_pct"]) if (return_1d is not None and taiex_today.get("return_1d_pct") is not None) else None
        excess_3d = (return_3d - taiex_today["return_3d_pct"]) if (return_3d is not None and taiex_today.get("return_3d_pct") is not None) else None

        # 20 日高點 + ATR：只用該股自己的歷史（不需要全市場 frame）
        window_closes = [price_by_date.get(all_days[k], {}).get("close") for k in range(max(0, j - 19), j + 1)]
        window_closes = [c for c in window_closes if c is not None]
        distance_to_20d_high = None
        if len(window_closes) >= 20:
            high20 = max(window_closes)
            if high20:
                distance_to_20d_high = (close / high20 - 1.0) * 100.0

        ohlc_window = []
        for k in range(max(0, j - 20), j + 1):
            p = price_by_date.get(all_days[k])
            if p and p.get("high") is not None and p.get("low") is not None and p.get("close") is not None:
                ohlc_window.append((p["high"], p["low"], p["close"]))
        atr_pct = compute_atr_pct(ohlc_window)

        vol_window = [price_by_date.get(all_days[k], {}).get("volume") for k in range(max(0, j - 4), j + 1)]
        vol_window = [v for v in vol_window if v is not None and v > 0]
        vol_ratio = None
        if prices.get("volume") is not None and vol_window:
            avg5 = sum(vol_window) / len(vol_window)
            if avg5 > 0:
                vol_ratio = prices["volume"] / avg5

        flow_1d = flow_by_date.get(d)
        flow_3d_window = [flow_by_date.get(all_days[k]) for k in range(max(0, j - 2), j + 1)]
        flow_3d = sum(f for f in flow_3d_window if f is not None) if any(f is not None for f in flow_3d_window) else None

        # entry_state（缺 rs_rank_improvement_5d，STRUCTURE_DAMAGED/REACCELERATING 不會觸發——已知簡化）
        entry_candidate = {
            "distance_to_20d_high": distance_to_20d_high,
            "atr_pct_14d": atr_pct,
            "rs_rank_improvement_5d": None,
        }
        entry_result = entry_state_mod.compute_entry_state(entry_candidate)
        entry_state = entry_result["entry_state"]

        # deterministic_signals 簡化版：只算 institution_flow_momentum（單股可算），
        # sector_rotation_status 留 neutral（需要產業層級資金流，本輪不查）
        det_candidate = {
            "total_institution_flow_1d": flow_1d,
            "total_institution_flow_3d": flow_3d,
            "industry_flow_1d": None,
            "industry_flow_3d": None,
        }
        institution_flow_momentum = det_signals._institution_flow_momentum(det_candidate)

        # momentum_freshness（簡化版）
        fresh_candidate = {
            "price_change_1d": return_1d,
            "high_1d": prices.get("high"),
            "low_1d": prices.get("low"),
            "close_1d": close,
            "volume_1d_to_5d_ratio": vol_ratio,
            "rs_rank_improvement_5d": None,
            "entry_state": entry_state,
            "deterministic_signals": {
                "institution_flow_momentum": institution_flow_momentum,
                "sector_rotation_status": "neutral",
            },
            "momentum_score": None,
        }
        fresh_result = freshness_mod.compute_momentum_freshness(
            fresh_candidate, taiex_return_1d_pct=taiex_today.get("return_1d_pct")
        )
        momentum_freshness = fresh_result["momentum_freshness"]

        # tracking_state（簡化版，沿用既有 TRACKING_FAILED_* 門檻判斷 failed_follow_through）
        failed_follow_through = (
            off >= TRACKING_FAILED_DAYS_THRESHOLD
            and max_return_so_far < TRACKING_FAILED_MAX_POSITIVE_PCT
            and max_loss_so_far < TRACKING_FAILED_MAX_NEGATIVE_PCT
        )
        tracking_candidate = {
            "is_tracked": off > 0,
            "failed_follow_through": failed_follow_through,
            "momentum_phase": None,
            "entry_state": entry_state,
            "rs_rank_improvement_5d": None,
            "max_negative_return_pct": max_loss_so_far,
        }
        tracking_state = tracking_mod.compute_tracking_state(tracking_candidate) if off > 0 else None

        # REVERSAL_FAILURE-like（只用單股可得資料：法人反轉強度 + 相對大盤轉弱）
        institution_reversal_ratio = None
        if flow_1d is not None and flow_3d is not None and flow_1d < 0:
            prior = flow_3d - flow_1d
            if prior > 0:
                institution_reversal_ratio = abs(flow_1d) / prior
        reversal_failure_like = (
            institution_reversal_ratio is not None and institution_reversal_ratio >= _REVERSAL_RATIO_MIN
            and excess_1d is not None and excess_1d <= _REVERSAL_EXCESS_MAX
        )

        hard_failure = bool(
            tracking_state == tracking_mod.TRACKING_INVALIDATED
            or entry_state == entry_state_mod.ENTRY_STRUCTURE_DAMAGED
            or reversal_failure_like
        )

        # ---- 4 evidence families ----
        profit_path_bad = bool(
            (max_return_so_far >= _PROFIT_GIVEBACK_MIN_PEAK_PCT and drawdown_from_max <= _PROFIT_GIVEBACK_DRAWDOWN_PCT)
            or (cur_return <= _PROFIT_NEVER_WORKED_CURRENT_PCT and max_return_so_far <= _PROFIT_NEVER_WORKED_MAX_PCT)
        )
        relative_perf_bad = bool(
            (excess_1d is not None and excess_1d <= _RELATIVE_WEAK_1D_PCT)
            or (excess_3d is not None and excess_3d <= _RELATIVE_WEAK_3D_PCT)
        )
        momentum_state_bad = bool(
            momentum_freshness in (freshness_mod.STALE, freshness_mod.DETERIORATING)
            or tracking_state in (tracking_mod.TRACKING_DETERIORATING, tracking_mod.TRACKING_INVALIDATED)
        )

        bad_family_count = sum([profit_path_bad, relative_perf_bad, momentum_state_bad])

        if hard_failure:
            continuation_state = "FAILED"
        elif bad_family_count >= 2:
            continuation_state = "AT_RISK"
        elif bad_family_count == 1:
            continuation_state = "CAUTION"
        else:
            continuation_state = "HEALTHY"

        reasons = []
        if hard_failure:
            if tracking_state == tracking_mod.TRACKING_INVALIDATED:
                reasons.append("failed_follow_through")
            if entry_state == entry_state_mod.ENTRY_STRUCTURE_DAMAGED:
                reasons.append("structure_damaged")
            if reversal_failure_like:
                reasons.append("reversal_failure_like")
        if profit_path_bad:
            reasons.append("profit_path_deteriorating")
        if relative_perf_bad:
            reasons.append("relative_performance_weak")
        if momentum_state_bad:
            reasons.append("momentum_state_deteriorating")

        rows_out.append({
            "day_offset": off,
            "date": d.isoformat(),
            "current_return": round(cur_return, 2),
            "max_return_so_far": round(max_return_so_far, 2),
            "max_loss_so_far": round(max_loss_so_far, 2),
            "drawdown_from_max": round(drawdown_from_max, 2),
            "excess_return_vs_market_1d": round(excess_1d, 2) if excess_1d is not None else None,
            "excess_return_vs_market_3d": round(excess_3d, 2) if excess_3d is not None else None,
            "momentum_freshness": momentum_freshness,
            "tracking_state": tracking_state,
            "hard_failure_state": "/".join(r for r in ("failed_follow_through" if tracking_state == tracking_mod.TRACKING_INVALIDATED else None,
                                                          "structure_damaged" if entry_state == entry_state_mod.ENTRY_STRUCTURE_DAMAGED else None,
                                                          "reversal_failure_like" if reversal_failure_like else None) if r) or None,
            "continuation_quality_state": continuation_state,
            "continuation_reasons": ",".join(reasons) if reasons else "none",
        })
        last_emitted_offset = off

    return rows_out


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--matched", type=str, default=MATCHED_40_PATH)
    parser.add_argument("--out", type=str, default=OUT_CSV)
    parser.add_argument("--out-json", type=str, default="/tmp/continuation_quality_raw.json")
    args = parser.parse_args()

    cohort = load_cohort()
    with open(args.matched, encoding="utf-8") as f:
        matched = json.load(f)
    all_stock_ids = matched["losers"] + matched["winners"]
    outcome = {sid: "BIG_LOSER" for sid in matched["losers"]}
    outcome.update({sid: "WINNER" for sid in matched["winners"]})
    print(f"target: {len(matched['losers'])} BIG_LOSER + {len(matched['winners'])} WINNER = {len(all_stock_ids)} stocks")

    db = SessionLocal()
    try:
        anchor_end = date(2026, 7, 22)
        all_days = get_recent_trade_dates(db, anchor_end, 140)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")
        taiex_ret = load_taiex_returns(db, all_days)

        all_rows = []
        for sid in all_stock_ids:
            rec = cohort[sid]
            day0 = date.fromisoformat(rec["catch_date"])
            traj = build_stock_trajectory(db, sid, day0, all_days, day_index, taiex_ret)
            for row in traj:
                all_rows.append({
                    "stock_id": sid,
                    "outcome_group": outcome[sid],
                    "first_seen_date": rec["catch_date"],
                    "future_return_10d": rec["forward_return_pct"],
                    **row,
                })
            states = [r["continuation_quality_state"] for r in traj]
            print(f"{sid} ({outcome[sid]}, ret={rec['forward_return_pct']:.1f}%): "
                  f"offsets={[r['day_offset'] for r in traj]} states={states}")
    finally:
        db.close()

    columns = [
        "stock_id", "outcome_group", "first_seen_date", "future_return_10d", "day_offset", "date",
        "current_return", "max_return_so_far", "max_loss_so_far", "drawdown_from_max",
        "excess_return_vs_market_1d", "excess_return_vs_market_3d",
        "momentum_freshness", "tracking_state", "hard_failure_state",
        "continuation_quality_state", "continuation_reasons",
    ]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"\nwrote {len(all_rows)} rows -> {args.out}")

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
