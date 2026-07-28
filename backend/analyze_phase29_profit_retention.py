"""Phase 2.9: Profit Retention Event-Based Validation（2026-07-24）。

**純研究 / Shadow Validation**，不修改任何 production 程式碼、Candidate Selection、
Phase 2、Phase 2.5、`continuation_quality_state`、`tracking_state`、
`momentum_freshness`、Hard Exclusion、LLM、既有 threshold。

**Event-Based（非 outcome-first）**：不先分 WINNER/LOSER 再配對，而是對既有
617-dedup replay cohort 全部股票，用「當下即可知道」的資訊掃描
FIRST_MEANINGFUL_PULLBACK_EVENT（running_peak_return>=+3% 之後，第一次
drawdown_from_running_max<=-5%），站在事件當下不看未來答案，事後才觀察
Event+5 / Event+10 的修復或失敗結果。

**Feature-only Event Replay**：沿用 `analyze_phase26_continuation_quality.py`
verified 過的單股 DB 查詢邏輯（不需要全市場 momentum frame），只是把「7 個
離散 offset」改成「逐日」以便精準定位事件發生的那一天，並把研究母體從
132 檔（pre-matched WINNER/LOSER）擴大到全部 617 檔 dedup cohort。

用法：
    python analyze_phase29_profit_retention.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

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

from analyze_phase26_continuation_quality import compute_atr_pct

REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
ANCHOR_END = date(2026, 7, 23)
CALENDAR_DAYS = 220
FORWARD_DAYS_MAX = 30  # 每檔股票從 first_seen 起最多追蹤這麼多交易日
_INST_TYPES = ("foreign", "trust", "dealer")

# ---- Event 定義（research-only，沿用 Phase 2.7/2.8 既有研究概念，非 production rule）----
_EVENT_MIN_PEAK_PCT = 3.0
_EVENT_MIN_PULLBACK_PCT = -5.0

OUT_EVENTS_CSV = "/tmp/phase29_pullback_events.csv"
OUT_STRAT_CSV = "/tmp/phase29_event_stratification.csv"
OUT_MATRIX_CSV = "/tmp/phase29_retention_persistence_matrix.csv"
OUT_REPORT = "/Users/brian.yh.chien/.gstack/projects/always-stock/docs/plans/phase29_profit_retention_report.md"

RISK_STATES = ("AT_RISK", "FAILED")


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


def build_daily_trajectory(
    db,
    stock_id: str,
    day0: date,
    all_days: List[date],
    day_index: Dict[date, int],
    taiex_ret: Dict[date, Dict[str, Optional[float]]],
) -> List[Dict[str, Any]]:
    """逐日版本的 build_stock_trajectory（沿用 Phase 2.6 verified 邏輯，只改
    emission 粒度：每天都輸出，不是只在 OFFSETS 那 7 天）。"""
    if day0 not in day_index:
        return []
    i0 = day_index[day0]
    min_j = i0 - 24
    max_j = i0 + FORWARD_DAYS_MAX
    if min_j < 0:
        return []
    max_j = min(max_j, len(all_days) - 1)
    if max_j <= i0:
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

    daily_returns_since_day0: List[float] = []
    rows_out = []

    for off in range(0, FORWARD_DAYS_MAX + 1):
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

        max_return_so_far = max(daily_returns_since_day0)
        max_loss_so_far = min(daily_returns_since_day0)
        drawdown_from_max = cur_return - max_return_so_far

        prev_day = all_days[j - 1] if j >= 1 else None
        prev_close = price_by_date.get(prev_day, {}).get("close") if prev_day else None
        return_1d = (close / prev_close - 1.0) * 100.0 if prev_close else None
        d3 = all_days[j - 3] if j >= 3 else None
        close_d3 = price_by_date.get(d3, {}).get("close") if d3 else None
        return_3d = (close / close_d3 - 1.0) * 100.0 if close_d3 else None

        taiex_today = taiex_ret.get(d, {})
        excess_1d = (return_1d - taiex_today["return_1d_pct"]) if (return_1d is not None and taiex_today.get("return_1d_pct") is not None) else None
        excess_3d = (return_3d - taiex_today["return_3d_pct"]) if (return_3d is not None and taiex_today.get("return_3d_pct") is not None) else None

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

        entry_candidate = {
            "distance_to_20d_high": distance_to_20d_high,
            "atr_pct_14d": atr_pct,
            "rs_rank_improvement_5d": None,
        }
        entry_result = entry_state_mod.compute_entry_state(entry_candidate)
        entry_state = entry_result["entry_state"]

        det_candidate = {
            "total_institution_flow_1d": flow_1d,
            "total_institution_flow_3d": flow_3d,
            "industry_flow_1d": None,
            "industry_flow_3d": None,
        }
        institution_flow_momentum = det_signals._institution_flow_momentum(det_candidate)

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

        institution_reversal_ratio = None
        if flow_1d is not None and flow_3d is not None and flow_1d < 0:
            prior = flow_3d - flow_1d
            if prior > 0:
                institution_reversal_ratio = abs(flow_1d) / prior
        reversal_failure_like = (
            institution_reversal_ratio is not None and institution_reversal_ratio >= 0.5
            and excess_1d is not None and excess_1d <= -1.5
        )

        hard_failure = bool(
            tracking_state == tracking_mod.TRACKING_INVALIDATED
            or entry_state == entry_state_mod.ENTRY_STRUCTURE_DAMAGED
            or reversal_failure_like
        )

        profit_path_bad = bool(
            (max_return_so_far >= 3.0 and drawdown_from_max <= -8.0)
            or (cur_return <= -5.0 and max_return_so_far <= 3.0)
        )
        relative_perf_bad = bool(
            (excess_1d is not None and excess_1d <= -1.0)
            or (excess_3d is not None and excess_3d <= -2.0)
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

        rows_out.append({
            "day_offset": off,
            "date": d,
            "current_return": cur_return,
            "max_return_so_far": max_return_so_far,
            "max_loss_so_far": max_loss_so_far,
            "drawdown_from_max": drawdown_from_max,
            "excess_return_vs_market_1d": excess_1d,
            "excess_return_vs_market_3d": excess_3d,
            "momentum_freshness": momentum_freshness,
            "tracking_state": tracking_state,
            "continuation_quality_state": continuation_state,
        })

    return rows_out


def find_first_meaningful_pullback_event(traj: List[Dict[str, Any]]) -> Optional[int]:
    """回傳 EVENT_DAY 在 traj 中的 index（不是 day_offset）。只用當下以前的
    running peak，不偷看未來。"""
    peak_qualified = False
    for i, r in enumerate(traj):
        if not peak_qualified and r["max_return_so_far"] >= _EVENT_MIN_PEAK_PCT:
            peak_qualified = True
            continue  # 剛達到門檻的當天不算回檔事件（drawdown 必為 0）
        if peak_qualified and r["drawdown_from_max"] <= _EVENT_MIN_PULLBACK_PCT:
            return i
    return None


def determine_outcome(traj: List[Dict[str, Any]], event_idx: int, horizon: int) -> Tuple[str, bool]:
    """回傳 (outcome, has_enough_data)。outcome in RECOVERED/FAILED/UNRESOLVED。"""
    peak_return_at_event = traj[event_idx]["max_return_so_far"]
    end_idx = event_idx + horizon
    if end_idx >= len(traj):
        return "UNRESOLVED", False  # 資料不足，無法判斷（見 §32.5 完整率揭露）
    window = traj[event_idx: end_idx + 1]
    rebreak = any(r["current_return"] >= peak_return_at_event for r in window[1:])
    if rebreak:
        return "RECOVERED", True
    end_return = window[-1]["current_return"]
    hard_failure_hit = any(r["tracking_state"] == tracking_mod.TRACKING_INVALIDATED for r in window[1:])
    if end_return <= -10.0 or hard_failure_hit:
        return "FAILED", True
    return "UNRESOLVED", True


def compute_persistence_after_event(traj: List[Dict[str, Any]], event_idx: int) -> Dict[str, Any]:
    """CONFIRMATION SIGNAL：只用 EVENT_DAY 之後的資料。"""
    post = traj[event_idx:]
    states = [r["continuation_quality_state"] for r in post]
    n = len(states)
    first_risk_idx = next((i for i, s in enumerate(states) if s in RISK_STATES), None)
    at_risk_persistence = 0
    if first_risk_idx is not None:
        at_risk_persistence = 1
        for i in range(first_risk_idx + 1, n):
            if states[i] in RISK_STATES:
                at_risk_persistence += 1
            else:
                break
    first_failed_idx = next((i for i, s in enumerate(states) if s == "FAILED"), None)
    failed_persistence = 0
    if first_failed_idx is not None:
        failed_persistence = 1
        for i in range(first_failed_idx + 1, n):
            if states[i] == "FAILED":
                failed_persistence += 1
            else:
                break
    return {
        "at_risk_plus_persistence": at_risk_persistence,
        "failed_persistence": failed_persistence,
    }


def peak_layer(peak_return: float) -> str:
    if peak_return < 8.0:
        return "3-8%"
    if peak_return < 15.0:
        return "8-15%"
    if peak_return < 25.0:
        return "15-25%"
    return ">=25%"


def main() -> None:
    cohort = load_cohort()
    print(f"cohort size (617 dedup): {len(cohort)}")

    db = SessionLocal()
    try:
        all_days = get_recent_trade_dates(db, ANCHOR_END, CALENDAR_DAYS)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")
        taiex_ret = load_taiex_returns(db, all_days)

        events = []
        n_processed = 0
        n_qualified_event = 0
        for sid, rec in cohort.items():
            day0 = date.fromisoformat(rec["catch_date"])
            traj = build_daily_trajectory(db, sid, day0, all_days, day_index, taiex_ret)
            n_processed += 1
            if not traj:
                continue
            event_idx = find_first_meaningful_pullback_event(traj)
            if event_idx is None:
                continue
            n_qualified_event += 1
            event_row = traj[event_idx]
            peak_return_at_event = event_row["max_return_so_far"]
            current_return_at_event = event_row["current_return"]
            drawdown_at_event = event_row["drawdown_from_max"]
            profit_retention_ratio = (
                round(current_return_at_event / peak_return_at_event, 4)
                if peak_return_at_event > 0 else None
            )

            outcome5, has5 = determine_outcome(traj, event_idx, 5)
            outcome10, has10 = determine_outcome(traj, event_idx, 10)
            persistence = compute_persistence_after_event(traj, event_idx)

            events.append({
                "stock_id": sid,
                "first_seen_date": rec["catch_date"],
                "event_date": event_row["date"].isoformat(),
                "days_since_first_seen": event_row["day_offset"],
                "peak_return_at_event": round(peak_return_at_event, 2),
                "current_return_at_event": round(current_return_at_event, 2),
                "drawdown_from_peak_at_event": round(drawdown_at_event, 2),
                "profit_retention_ratio": profit_retention_ratio,
                "momentum_score_day0": rec.get("momentum_score"),
                "rs_market_percentile_day0": rec.get("rs_market_percentile_20d"),
                "market_regime": rec.get("regime"),
                "continuation_quality_state_at_event": event_row["continuation_quality_state"],
                "tracking_state_at_event": event_row["tracking_state"],
                "momentum_freshness_at_event": event_row["momentum_freshness"],
                "excess_return_vs_market_1d_at_event": event_row["excess_return_vs_market_1d"],
                "excess_return_vs_market_3d_at_event": event_row["excess_return_vs_market_3d"],
                "peak_layer": peak_layer(peak_return_at_event),
                "outcome_5d": outcome5,
                "outcome_5d_has_data": has5,
                "outcome_10d": outcome10,
                "outcome_10d_has_data": has10,
                "at_risk_plus_persistence_after_event": persistence["at_risk_plus_persistence"],
                "failed_persistence_after_event": persistence["failed_persistence"],
            })
            if n_processed % 50 == 0:
                print(f"  processed {n_processed}/{len(cohort)}, events found so far: {n_qualified_event}", flush=True)
    finally:
        db.close()

    print(f"\ntotal stocks processed: {n_processed}")
    print(f"total FIRST_MEANINGFUL_PULLBACK_EVENT found: {len(events)}")

    columns = list(events[0].keys()) if events else []
    with open(OUT_EVENTS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for e in events:
            w.writerow(e)
    print(f"wrote {len(events)} rows -> {OUT_EVENTS_CSV}")

    with open("/tmp/phase29_events_raw.json", "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2, default=str)

    # ================= 分析區 =================
    print("\n" + "=" * 70)
    print("=== §32 必要揭露 ===")
    print("=" * 70)

    layer_counts: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        layer_counts.setdefault(e["peak_layer"], []).append(e)
    print(f"\nPeak Layer 樣本數分布：")
    for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
        rows = layer_counts.get(layer, [])
        print(f"  {layer}: n={len(rows)}")

    for horizon_key, horizon_label in (("outcome_5d", "Event+5"), ("outcome_10d", "Event+10")):
        has_key = horizon_key + "_has_data"
        n_has_data = sum(1 for e in events if e[has_key])
        print(f"\n{horizon_label} 資料完整率: {n_has_data}/{len(events)} = {100*n_has_data/len(events):.1f}%")
        dist: Dict[str, int] = {}
        for e in events:
            dist[e[horizon_key]] = dist.get(e[horizon_key], 0) + 1
        print(f"{horizon_label} outcome 分布: {dist}")

    print("\n各 Peak Layer 的 outcome 分布（Event+10）：")
    for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
        rows = layer_counts.get(layer, [])
        if not rows:
            continue
        dist: Dict[str, int] = {}
        for e in rows:
            dist[e["outcome_10d"]] = dist.get(e["outcome_10d"], 0) + 1
        print(f"  {layer} (n={len(rows)}): {dist}")

    def med(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.median(vals), 3) if vals else None

    def pctile(vals, p):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        k = (len(vals) - 1) * p
        f = int(k)
        c = min(f + 1, len(vals) - 1)
        if f == c:
            return round(vals[f], 3)
        return round(vals[f] + (vals[c] - vals[f]) * (k - f), 3)

    # ---- 核心輸出表 1 ----
    print("\n" + "=" * 70)
    print("=== 核心輸出表 1：Event Universe by Peak Layer ===")
    print("=" * 70)
    print(f"{'layer':10s} {'n':>4s} {'RECOVERED':>10s} {'FAILED':>8s} {'UNRESOLVED':>11s} "
          f"{'med_peak':>9s} {'med_dd':>8s}")
    with open(OUT_STRAT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        strat_cols = ["peak_layer", "n", "recovered_10d", "failed_10d", "unresolved_10d",
                      "median_peak_return", "median_drawdown_at_event"]
        w = csv.DictWriter(f, fieldnames=strat_cols)
        w.writeheader()
        for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
            rows = layer_counts.get(layer, [])
            if not rows:
                continue
            n_rec = sum(1 for e in rows if e["outcome_10d"] == "RECOVERED")
            n_fail = sum(1 for e in rows if e["outcome_10d"] == "FAILED")
            n_unres = sum(1 for e in rows if e["outcome_10d"] == "UNRESOLVED")
            mp = med([e["peak_return_at_event"] for e in rows])
            md = med([e["drawdown_from_peak_at_event"] for e in rows])
            print(f"{layer:10s} {len(rows):>4d} {n_rec:>10d} {n_fail:>8d} {n_unres:>11d} "
                  f"{mp:>9.2f} {md:>8.2f}")
            w.writerow({"peak_layer": layer, "n": len(rows), "recovered_10d": n_rec,
                        "failed_10d": n_fail, "unresolved_10d": n_unres,
                        "median_peak_return": mp, "median_drawdown_at_event": md})
    print(f"wrote -> {OUT_STRAT_CSV}")

    # ---- 核心輸出表 2：RECOVERED vs FAILED profit_retention_ratio by layer ----
    print("\n" + "=" * 70)
    print("=== 核心輸出表 2：各 Peak Layer 內 RECOVERED vs FAILED 的 profit_retention_ratio ===")
    print("=" * 70)
    for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
        rows = layer_counts.get(layer, [])
        rec = [e for e in rows if e["outcome_10d"] == "RECOVERED"]
        fail = [e for e in rows if e["outcome_10d"] == "FAILED"]
        print(f"\n-- {layer} -- RECOVERED n={len(rec)}, FAILED n={len(fail)}")
        if len(rec) < 5 or len(fail) < 5:
            print("   樣本數 <5，僅作 descriptive，不下強結論")
        for label, group in (("RECOVERED", rec), ("FAILED", fail)):
            if not group:
                continue
            vals = [e["profit_retention_ratio"] for e in group]
            dd_vals = [e["drawdown_from_peak_at_event"] for e in group]
            print(f"   {label:10s} retention median={med(vals)} p25={pctile(vals,0.25)} p75={pctile(vals,0.75)}  "
                  f"| drawdown median={med(dd_vals)} p25={pctile(dd_vals,0.25)} p75={pctile(dd_vals,0.75)}")

    # ---- 核心輸出表 3：tertile ----
    print("\n" + "=" * 70)
    print("=== 核心輸出表 3：各 Peak Layer 內 Profit Retention Tertile vs Outcome ===")
    print("=" * 70)
    for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
        rows = [e for e in layer_counts.get(layer, []) if e["profit_retention_ratio"] is not None]
        if len(rows) < 9:
            print(f"\n-- {layer} -- n={len(rows)}，樣本太少無法拆 tertile，跳過")
            continue
        rows_sorted = sorted(rows, key=lambda e: e["profit_retention_ratio"])
        n = len(rows_sorted)
        t1 = n // 3
        t2 = 2 * n // 3
        tertiles = {"LOW": rows_sorted[:t1], "MID": rows_sorted[t1:t2], "HIGH": rows_sorted[t2:]}
        print(f"\n-- {layer} -- n={n}")
        for name in ("HIGH", "MID", "LOW"):
            grp = tertiles[name]
            for horizon_key, label in (("outcome_5d", "+5"), ("outcome_10d", "+10")):
                rec_rate = 100 * sum(1 for e in grp if e[horizon_key] == "RECOVERED") / len(grp)
                fail_rate = 100 * sum(1 for e in grp if e[horizon_key] == "FAILED") / len(grp)
                print(f"   {name:5s} (n={len(grp)}) Event{label}: recovery={rec_rate:.1f}% failure={fail_rate:.1f}%")

    # ---- 核心輸出表 4：raw drawdown vs retention ratio outcome gradient ----
    print("\n" + "=" * 70)
    print("=== 核心輸出表 4：Raw Drawdown vs Profit Retention Ratio outcome gradient ===")
    print("=" * 70)
    for layer in ("3-8%", "8-15%", "15-25%", ">=25%"):
        rows = [e for e in layer_counts.get(layer, []) if e["profit_retention_ratio"] is not None]
        if len(rows) < 9:
            continue
        print(f"\n-- {layer} -- n={len(rows)}")
        for metric_key, metric_label in (("profit_retention_ratio", "retention"), ("drawdown_from_peak_at_event", "raw_drawdown")):
            rows_sorted = sorted(rows, key=lambda e: e[metric_key])
            n = len(rows_sorted)
            t1, t2 = n // 3, 2 * n // 3
            tertiles = {"LOW": rows_sorted[:t1], "MID": rows_sorted[t1:t2], "HIGH": rows_sorted[t2:]}
            rec_rates = []
            for name in ("LOW", "MID", "HIGH"):
                grp = tertiles[name]
                rec_rate = 100 * sum(1 for e in grp if e["outcome_10d"] == "RECOVERED") / len(grp) if grp else None
                rec_rates.append(rec_rate)
            print(f"   {metric_label:14s} recovery rate LOW->MID->HIGH tertile: "
                  f"{rec_rates[0]:.1f}% -> {rec_rates[1]:.1f}% -> {rec_rates[2]:.1f}%"
                  if all(r is not None for r in rec_rates) else f"   {metric_label}: 資料不足")

    # ---- 核心輸出表 5：retention x persistence 四象限 ----
    print("\n" + "=" * 70)
    print("=== 核心輸出表 5：Profit Retention x AT_RISK persistence>=3 四象限（全體 events）===")
    print("=" * 70)
    valid_events = [e for e in events if e["profit_retention_ratio"] is not None]
    median_retention = statistics.median([e["profit_retention_ratio"] for e in valid_events])
    print(f"(全體 median profit_retention_ratio = {median_retention:.3f}，用來切 HIGH/LOW)")
    quadrants = {"HIGH_no_persist": [], "LOW_no_persist": [], "HIGH_persist": [], "LOW_persist": []}
    for e in valid_events:
        high = e["profit_retention_ratio"] >= median_retention
        persist = e["at_risk_plus_persistence_after_event"] >= 3
        key = f"{'HIGH' if high else 'LOW'}_{'persist' if persist else 'no_persist'}"
        quadrants[key].append(e)

    with open(OUT_MATRIX_CSV, "w", newline="", encoding="utf-8-sig") as f:
        mcols = ["quadrant", "n", "recovered_rate_10d", "failed_rate_10d",
                 "median_event10d_return", "rebreak_rate_10d"]
        w = csv.DictWriter(f, fieldnames=mcols)
        w.writeheader()
        for key, label in (
            ("HIGH_no_persist", "HIGH retention / no persistent risk"),
            ("LOW_no_persist", "LOW retention / no persistent risk"),
            ("HIGH_persist", "HIGH retention / persistent risk"),
            ("LOW_persist", "LOW retention / persistent risk"),
        ):
            grp = quadrants[key]
            if not grp:
                print(f"{label}: n=0")
                continue
            rec_rate = 100 * sum(1 for e in grp if e["outcome_10d"] == "RECOVERED") / len(grp)
            fail_rate = 100 * sum(1 for e in grp if e["outcome_10d"] == "FAILED") / len(grp)
            print(f"{label} (n={len(grp)}): RECOVERED={rec_rate:.1f}% FAILED={fail_rate:.1f}%")
            w.writerow({"quadrant": label, "n": len(grp), "recovered_rate_10d": round(rec_rate, 1),
                        "failed_rate_10d": round(fail_rate, 1), "median_event10d_return": None,
                        "rebreak_rate_10d": round(rec_rate, 1)})
    print(f"wrote -> {OUT_MATRIX_CSV}")

    # ---- FAILED 訊號時機 / 殘餘空間分析 ----
    print("\n" + "=" * 70)
    print("=== 時間價值：FAILED (Event+10) 案例的 Event Day 狀態 ===")
    print("=" * 70)
    failed_events = [e for e in events if e["outcome_10d"] == "FAILED"]
    print(f"FAILED (Event+10) 總數: {len(failed_events)}")
    if failed_events:
        print(f"median current_return_at_event: {med([e['current_return_at_event'] for e in failed_events])}")
        print(f"median peak_return_at_event: {med([e['peak_return_at_event'] for e in failed_events])}")
        print(f"median drawdown_from_peak_at_event: {med([e['drawdown_from_peak_at_event'] for e in failed_events])}")
        print(f"median profit_retention_ratio: {med([e['profit_retention_ratio'] for e in failed_events])}")
        n_already_below_10 = sum(1 for e in failed_events if e["current_return_at_event"] <= -10.0)
        print(f"Event Day 當下已經 <=-10% 的案例: {n_already_below_10}/{len(failed_events)} "
              f"({100*n_already_below_10/len(failed_events):.1f}%)")

    print(f"\n報告將寫入 -> {OUT_REPORT}")


if __name__ == "__main__":
    main()
