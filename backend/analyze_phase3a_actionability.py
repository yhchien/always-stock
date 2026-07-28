"""Phase 3A: Persistence Actionability Test（2026-07-24）。

**純研究 / Shadow Validation**，不修改任何 production 程式碼、Candidate Selection、
Phase 2、Phase 2.5、`continuation_quality_state`、Persistence 定義、既有 threshold、
Hard Exclusion、LLM。不做 re-entry / 換股 / 加碼 / 資金配置 / 完整 Portfolio Backtest。

核心問題：Persistence（AT_RISK/FAILED 持續性）已知在統計上能辨認一部分持續失敗
股票（Phase 2.7），但「統計辨識力」不等於「交易上的 Actionability」——本輪只驗證：
假設歷史上真的依照既有 Persistence 規則退出單一股票，「避免的後續虧損（Saved
Loss）」是否大於「因此錯失的大牛股後續漲幅（Foregone Upside）」？

沿用 Phase 2.7 既有 Persistence 定義：以既定 7 個 observation（Day0/1/2/3/5/7/10）
為單位，「連續 N 個 observation」指這 7 個 observation 陣列中的連續索引，不是連續
交易日。只測 3 條既存 Rule（Rule A: AT_RISK+ persistence>=3、Rule B: FAILED
persistence>=2、Rule C: FAILED persistence>=3），不新增 Rule、不調 threshold。

用法：
    python analyze_phase3a_actionability.py
"""
from __future__ import annotations

import csv
import json
import random
import statistics
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
OFFSETS = (0, 1, 2, 3, 5, 7, 10)
_EXECUTION_BUFFER = 3  # 讓 offset=10 觸發時，仍有下一交易日可執行
_INST_TYPES = ("foreign", "trust", "dealer")

RISK_STATES = ("AT_RISK", "FAILED")

RULES = {
    "RULE_A_ATRISK_PLUS_3": {"match_states": RISK_STATES, "threshold": 3},
    "RULE_B_FAILED_2": {"match_states": ("FAILED",), "threshold": 2},
    "RULE_C_FAILED_3": {"match_states": ("FAILED",), "threshold": 3},
}

OUT_ALL_CSV = "/tmp/phase3a_persistence_actionability_all.csv"
OUT_SUMMARY_CSV = "/tmp/phase3a_persistence_actionability_summary.csv"
OUT_LOSER_CSV = "/tmp/phase3a_big_loser_saved_cases.csv"
OUT_WINNER_CSV = "/tmp/phase3a_winner_foregone_cases.csv"
OUT_REPORT = "/Users/brian.yh.chien/.gstack/projects/always-stock/docs/plans/phase3a_persistence_actionability_report.md"

_NEVER_WORKED_MFE_PCT = 3.0  # 沿用 Phase 2.8 既有分類概念，非新 threshold


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


def build_offset_trajectory(
    db,
    stock_id: str,
    day0: date,
    all_days: List[date],
    day_index: Dict[date, int],
    taiex_ret: Dict[date, Dict[str, Optional[float]]],
) -> Tuple[List[Dict[str, Any]], Dict[date, Dict[str, Optional[float]]], Optional[float]]:
    """沿用 Phase 2.6 verified 邏輯（7 offset emission），額外多留
    _EXECUTION_BUFFER 天的價格資料供「訊號後下一交易日執行」使用。回傳
    (7-offset rows, price_by_date, day0_close)。"""
    if day0 not in day_index:
        return [], {}, None
    i0 = day_index[day0]
    min_j = i0 - 24
    max_j = i0 + max(OFFSETS) + _EXECUTION_BUFFER
    if min_j < 0:
        return [], {}, None
    max_j = min(max_j, len(all_days) - 1)
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
        return [], price_by_date, None

    daily_returns_since_day0: List[float] = []
    rows_out = []

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
            "continuation_quality_state": continuation_state,
        })

    return rows_out, price_by_date, day0_close


def detect_rule_signal(traj: List[Dict[str, Any]], rule: Dict[str, Any]) -> Optional[int]:
    """回傳觸發時的 traj index（第一次連續 threshold 次符合 match_states）。"""
    streak = 0
    for i, r in enumerate(traj):
        if r["continuation_quality_state"] in rule["match_states"]:
            streak += 1
        else:
            streak = 0
        if streak >= rule["threshold"]:
            return i
    return None


def find_execution(
    signal_date: date, all_days: List[date], day_index: Dict[date, int],
    price_by_date: Dict[date, Dict[str, Optional[float]]],
) -> Optional[Tuple[date, float, str]]:
    """訊號日下一交易日 open（fallback close）。回傳 (execution_date, price, price_type)。"""
    i = day_index.get(signal_date)
    if i is None or i + 1 >= len(all_days):
        return None
    exec_date = all_days[i + 1]
    prices = price_by_date.get(exec_date)
    if not prices:
        return None
    if prices.get("open") is not None:
        return exec_date, prices["open"], "next_day_open"
    if prices.get("close") is not None:
        return exec_date, prices["close"], "next_day_close_fallback"
    return None


def classify_failure_type(baseline_return: float, mfe: float) -> Optional[str]:
    if baseline_return > -10.0:
        return None  # 非 loser，不適用 NEVER_WORKED/ROUND_TRIP_FAILURE 分類
    if mfe < _NEVER_WORKED_MFE_PCT:
        return "NEVER_WORKED"
    return "ROUND_TRIP_FAILURE"


def bootstrap_ci(deltas: List[float], n_iter: int = 2000, seed: int = 42) -> Tuple[Optional[float], Optional[float]]:
    if not deltas:
        return None, None
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(n_iter):
        sample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(sample))
    means.sort()
    lo = means[int(0.025 * n_iter)]
    hi = means[int(0.975 * n_iter)]
    return round(lo, 3), round(hi, 3)


def pctile(vals: List[float], p: float) -> Optional[float]:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return round(vals[f], 2)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 2)


def med(vals: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def mean(vals: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 2) if vals else None


def main() -> None:
    cohort = load_cohort()
    print(f"cohort size (617 dedup): {len(cohort)}")

    db = SessionLocal()
    all_records = []
    try:
        all_days = get_recent_trade_dates(db, ANCHOR_END, CALENDAR_DAYS)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")
        taiex_ret = load_taiex_returns(db, all_days)

        n_processed = 0
        n_complete_day10 = 0
        for sid, rec in cohort.items():
            day0 = date.fromisoformat(rec["catch_date"])
            traj, price_by_date, day0_close = build_offset_trajectory(db, sid, day0, all_days, day_index, taiex_ret)
            n_processed += 1
            if not traj or day0_close is None:
                continue
            day10_rows = [r for r in traj if r["day_offset"] == 10]
            if not day10_rows:
                continue  # 資料不足，不能建立 Day10 baseline，主分析排除
            n_complete_day10 += 1
            baseline_return = day10_rows[0]["current_return"]
            mfe = day10_rows[0]["max_return_so_far"]

            if baseline_return >= 10.0:
                outcome_group = "WINNER"
            elif baseline_return <= -10.0:
                outcome_group = "BIG_LOSER"
            else:
                outcome_group = "OTHER"
            failure_type = classify_failure_type(baseline_return, mfe)

            row_record: Dict[str, Any] = {
                "stock_id": sid,
                "first_seen_date": rec["catch_date"],
                "baseline_day10_return": round(baseline_return, 2),
                "mfe_day10": round(mfe, 2),
                "outcome_group": outcome_group,
                "existing_failure_type": failure_type or "",
            }

            for rule_name, rule in RULES.items():
                sig_idx = detect_rule_signal(traj, rule)
                if sig_idx is None:
                    row_record[f"{rule_name}__triggered"] = False
                    row_record[f"{rule_name}__signal_date"] = ""
                    row_record[f"{rule_name}__signal_day_offset"] = None
                    row_record[f"{rule_name}__signal_return"] = None
                    row_record[f"{rule_name}__execution_date"] = ""
                    row_record[f"{rule_name}__execution_price_type"] = ""
                    row_record[f"{rule_name}__exit_return"] = None
                    row_record[f"{rule_name}__counterfactual_delta"] = None
                    continue
                sig_row = traj[sig_idx]
                execution = find_execution(sig_row["date"], all_days, day_index, price_by_date)
                if execution is None:
                    row_record[f"{rule_name}__triggered"] = True
                    row_record[f"{rule_name}__signal_date"] = sig_row["date"].isoformat()
                    row_record[f"{rule_name}__signal_day_offset"] = sig_row["day_offset"]
                    row_record[f"{rule_name}__signal_return"] = round(sig_row["current_return"], 2)
                    row_record[f"{rule_name}__execution_date"] = ""
                    row_record[f"{rule_name}__execution_price_type"] = "no_data"
                    row_record[f"{rule_name}__exit_return"] = None
                    row_record[f"{rule_name}__counterfactual_delta"] = None
                    continue
                exec_date, exec_price, price_type = execution
                exit_return = (exec_price / day0_close - 1.0) * 100.0
                delta = exit_return - baseline_return
                row_record[f"{rule_name}__triggered"] = True
                row_record[f"{rule_name}__signal_date"] = sig_row["date"].isoformat()
                row_record[f"{rule_name}__signal_day_offset"] = sig_row["day_offset"]
                row_record[f"{rule_name}__signal_return"] = round(sig_row["current_return"], 2)
                row_record[f"{rule_name}__execution_date"] = exec_date.isoformat()
                row_record[f"{rule_name}__execution_price_type"] = price_type
                row_record[f"{rule_name}__exit_return"] = round(exit_return, 2)
                row_record[f"{rule_name}__counterfactual_delta"] = round(delta, 2)

            all_records.append(row_record)
            if n_processed % 50 == 0:
                print(f"  processed {n_processed}/{len(cohort)}, complete Day10 baseline: {n_complete_day10}", flush=True)
    finally:
        db.close()

    print(f"\ntotal stocks processed: {n_processed}")
    print(f"complete Day10 baseline (主分析母體): {len(all_records)}")
    print(f"incomplete/excluded: {n_processed - len(all_records)}")

    columns = list(all_records[0].keys()) if all_records else []
    with open(OUT_ALL_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_records:
            w.writerow(r)
    print(f"wrote {len(all_records)} rows -> {OUT_ALL_CSV}")
    with open("/tmp/phase3a_all_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2, default=str)

    # ============================ 分析區 ============================
    def subset(records, group=None, failure_type=None):
        out = records
        if group is not None:
            out = [r for r in out if r["outcome_group"] == group]
        if failure_type is not None:
            out = [r for r in out if r["existing_failure_type"] == failure_type]
        return out

    summary_rows = []

    print("\n" + "=" * 78)
    print("=== 核心 Trade-off Table ===")
    print("=" * 78)

    for rule_name in RULES:
        print(f"\n--- {rule_name} ---")
        trig_key = f"{rule_name}__triggered"
        delta_key = f"{rule_name}__counterfactual_delta"

        all_baseline = [r["baseline_day10_return"] for r in all_records]
        triggered_all = [r for r in all_records if r[trig_key] and r[delta_key] is not None]
        n_trig = len(triggered_all)
        trig_rate = 100 * n_trig / len(all_records)
        overall_exec_return = [
            (r[delta_key] + r["baseline_day10_return"]) if r[trig_key] and r[delta_key] is not None else r["baseline_day10_return"]
            for r in all_records
        ]
        overall_delta_all = [
            r[delta_key] if (r[trig_key] and r[delta_key] is not None) else 0.0
            for r in all_records
        ]

        print(f"Trigger Rate (全體): {n_trig}/{len(all_records)} = {trig_rate:.1f}%")
        print(f"Baseline Mean Return (全體): {mean(all_baseline)}%")
        print(f"Counterfactual Mean Return (全體，未觸發=baseline): {mean(overall_exec_return)}%")
        print(f"Overall Mean Delta: {mean(overall_delta_all)}pp   Median Delta: {med(overall_delta_all)}pp")
        ci_lo, ci_hi = bootstrap_ci(overall_delta_all)
        print(f"Overall Delta 95% Bootstrap CI: [{ci_lo}, {ci_hi}]")

        pos_delta = sum(d for d in overall_delta_all if d > 0)
        neg_delta = sum(d for d in overall_delta_all if d < 0)
        print(f"Total Positive Delta: {round(pos_delta,1)}pp   Total Negative Delta: {round(neg_delta,1)}pp   "
              f"Net Delta: {round(pos_delta+neg_delta,1)}pp")

        # ---- BIG_LOSER ----
        losers = subset(all_records, group="BIG_LOSER")
        losers_trig = [r for r in losers if r[trig_key] and r[delta_key] is not None]
        loser_trig_rate = 100 * len(losers_trig) / len(losers) if losers else 0
        loser_saved = [r[delta_key] for r in losers_trig]
        print(f"\nBIG_LOSER: total={len(losers)}, triggered={len(losers_trig)} ({loser_trig_rate:.1f}%)")
        print(f"  mean signal_day={mean([r[f'{rule_name}__signal_day_offset'] for r in losers_trig])}")
        print(f"  mean signal_return={mean([r[f'{rule_name}__signal_return'] for r in losers_trig])}%")
        print(f"  mean exit_return={mean([r[f'{rule_name}__exit_return'] for r in losers_trig])}%")
        print(f"  mean baseline_return={mean([r['baseline_day10_return'] for r in losers_trig])}%")
        print(f"  mean saved_loss={mean(loser_saved)}pp   median saved_loss={med(loser_saved)}pp")
        loser_ci = bootstrap_ci(loser_saved)
        print(f"  saved_loss 95% CI: {loser_ci}")
        if losers_trig:
            n_pos = sum(1 for r in losers_trig if r[f"{rule_name}__exit_return"] > 0)
            n_gt_neg5 = sum(1 for r in losers_trig if r[f"{rule_name}__exit_return"] > -5)
            n_gt_neg10 = sum(1 for r in losers_trig if r[f"{rule_name}__exit_return"] > -10)
            n_already_neg10 = sum(1 for r in losers_trig if r[f"{rule_name}__signal_return"] <= -10)
            print(f"  退出時 return>0: {100*n_pos/len(losers_trig):.1f}%  >-5%: {100*n_gt_neg5/len(losers_trig):.1f}%  "
                  f">-10%: {100*n_gt_neg10/len(losers_trig):.1f}%  訊號當下已<=-10%: {100*n_already_neg10/len(losers_trig):.1f}%")

        # ---- NEVER_WORKED ----
        nw = subset(all_records, failure_type="NEVER_WORKED")
        nw_trig = [r for r in nw if r[trig_key] and r[delta_key] is not None]
        nw_trig_rate = 100 * len(nw_trig) / len(nw) if nw else 0
        nw_saved = [r[delta_key] for r in nw_trig]
        print(f"\nNEVER_WORKED: total={len(nw)}, triggered={len(nw_trig)} ({nw_trig_rate:.1f}%)")
        print(f"  mean saved_loss={mean(nw_saved)}pp  median={med(nw_saved)}pp")
        for th in (-10, -15, -20):
            base_hit = sum(1 for r in nw if r["baseline_day10_return"] <= th)
            cf_return = [
                (r[f"{rule_name}__exit_return"] if (r[trig_key] and r[delta_key] is not None) else r["baseline_day10_return"])
                for r in nw
            ]
            cf_hit = sum(1 for v in cf_return if v <= th)
            print(f"  <={th}%: baseline {base_hit}/{len(nw)} -> counterfactual {cf_hit}/{len(nw)}")

        # ---- ROUND_TRIP_FAILURE (觀察，不救) ----
        rtf = subset(all_records, failure_type="ROUND_TRIP_FAILURE")
        rtf_trig = [r for r in rtf if r[trig_key] and r[delta_key] is not None]
        rtf_trig_rate = 100 * len(rtf_trig) / len(rtf) if rtf else 0
        rtf_saved = [r[delta_key] for r in rtf_trig]
        print(f"\nROUND_TRIP_FAILURE（僅觀察）: total={len(rtf)}, triggered={len(rtf_trig)} ({rtf_trig_rate:.1f}%)")
        print(f"  mean saved_loss={mean(rtf_saved)}pp  median={med(rtf_saved)}pp")

        # ---- WINNER ----
        winners = subset(all_records, group="WINNER")
        winners_trig = [r for r in winners if r[trig_key] and r[delta_key] is not None]
        winner_trig_rate = 100 * len(winners_trig) / len(winners) if winners else 0
        winner_forgone = [-r[delta_key] for r in winners_trig]  # foregone = -delta (delta 為負代表少賺)
        print(f"\nWINNER: total={len(winners)}, triggered={len(winners_trig)} ({winner_trig_rate:.1f}%)")
        print(f"  mean exit_return={mean([r[f'{rule_name}__exit_return'] for r in winners_trig])}%")
        print(f"  mean baseline_return={mean([r['baseline_day10_return'] for r in winners_trig])}%")
        print(f"  mean foregone_upside={mean(winner_forgone)}pp  median={med(winner_forgone)}pp")
        if winner_forgone:
            print(f"  max foregone_upside={max(winner_forgone):.2f}pp")
        winner_ci = bootstrap_ci([-x for x in winner_forgone])
        print(f"  (foregone as delta) 95% CI: {winner_ci}")

        # ---- Left tail ----
        print(f"\nLeft Tail Reduction:")
        for th in (-10, -15, -20):
            base_hit = sum(1 for r in all_records if r["baseline_day10_return"] <= th)
            cf_return = [
                (r[f"{rule_name}__exit_return"] if (r[trig_key] and r[delta_key] is not None) else r["baseline_day10_return"])
                for r in all_records
            ]
            cf_hit = sum(1 for v in cf_return if v <= th)
            reduction = base_hit - cf_hit
            rel = 100 * reduction / base_hit if base_hit else 0
            print(f"  <={th}%: baseline {base_hit} -> counterfactual {cf_hit}  (reduction={reduction}, {rel:.1f}%)")

        # ---- percentiles ----
        print(f"\nDelta percentiles (ALL): p10={pctile(overall_delta_all,0.1)} p25={pctile(overall_delta_all,0.25)} "
              f"median={pctile(overall_delta_all,0.5)} p75={pctile(overall_delta_all,0.75)} p90={pctile(overall_delta_all,0.9)}")
        loser_delta_all = [
            r[delta_key] if (r[trig_key] and r[delta_key] is not None) else 0.0 for r in losers
        ]
        print(f"Delta percentiles (BIG_LOSER): p10={pctile(loser_delta_all,0.1)} p25={pctile(loser_delta_all,0.25)} "
              f"median={pctile(loser_delta_all,0.5)} p75={pctile(loser_delta_all,0.75)} p90={pctile(loser_delta_all,0.9)}")
        winner_delta_all = [
            r[delta_key] if (r[trig_key] and r[delta_key] is not None) else 0.0 for r in winners
        ]
        print(f"Delta percentiles (WINNER): p10={pctile(winner_delta_all,0.1)} p25={pctile(winner_delta_all,0.25)} "
              f"median={pctile(winner_delta_all,0.5)} p75={pctile(winner_delta_all,0.75)} p90={pctile(winner_delta_all,0.9)}")
        nw_delta_all = [
            r[delta_key] if (r[trig_key] and r[delta_key] is not None) else 0.0 for r in nw
        ]
        print(f"Delta percentiles (NEVER_WORKED): p10={pctile(nw_delta_all,0.1)} p25={pctile(nw_delta_all,0.25)} "
              f"median={pctile(nw_delta_all,0.5)} p75={pctile(nw_delta_all,0.75)} p90={pctile(nw_delta_all,0.9)}")

        # ---- time split ----
        sorted_by_date = sorted(all_records, key=lambda r: r["first_seen_date"])
        half = len(sorted_by_date) // 2
        first_half, second_half = sorted_by_date[:half], sorted_by_date[half:]
        print(f"\nTime Split (first_seen_date 前半 n={len(first_half)} / 後半 n={len(second_half)}):")
        for label, part in (("前半", first_half), ("後半", second_half)):
            part_delta = [r[delta_key] if (r[trig_key] and r[delta_key] is not None) else 0.0 for r in part]
            part_losers = [r for r in part if r["outcome_group"] == "BIG_LOSER"]
            part_losers_trig = [r for r in part_losers if r[trig_key] and r[delta_key] is not None]
            part_winners = [r for r in part if r["outcome_group"] == "WINNER"]
            part_winners_trig = [r for r in part_winners if r[trig_key] and r[delta_key] is not None]
            print(f"  {label}: overall_mean_delta={mean(part_delta)}pp  "
                  f"loser_saved_loss={mean([r[delta_key] for r in part_losers_trig])}pp  "
                  f"winner_foregone={mean([-r[delta_key] for r in part_winners_trig])}pp")
            for th in (-10,):
                base_hit = sum(1 for r in part if r["baseline_day10_return"] <= th)
                cf_return = [
                    (r[f"{rule_name}__exit_return"] if (r[trig_key] and r[delta_key] is not None) else r["baseline_day10_return"])
                    for r in part
                ]
                cf_hit = sum(1 for v in cf_return if v <= th)
                print(f"    <={th}% left-tail: {base_hit} -> {cf_hit}")

        summary_rows.append({
            "rule": rule_name,
            "trigger_rate_all_pct": round(trig_rate, 1),
            "baseline_mean_return": mean(all_baseline),
            "counterfactual_mean_return": mean(overall_exec_return),
            "overall_mean_delta": mean(overall_delta_all),
            "overall_median_delta": med(overall_delta_all),
            "big_loser_trigger_rate_pct": round(loser_trig_rate, 1),
            "big_loser_mean_saved_loss": mean(loser_saved),
            "never_worked_trigger_rate_pct": round(nw_trig_rate, 1),
            "never_worked_mean_saved_loss": mean(nw_saved),
            "winner_trigger_rate_pct": round(winner_trig_rate, 1),
            "winner_mean_foregone_upside": mean(winner_forgone),
            "winner_max_foregone_upside": round(max(winner_forgone), 2) if winner_forgone else None,
        })

        # ---- CSV: saved cases / foregone cases (只有 Rule A 輸出，避免重複三份) ----
        if rule_name == "RULE_A_ATRISK_PLUS_3":
            with open(OUT_LOSER_CSV, "w", newline="", encoding="utf-8-sig") as f:
                cols = ["stock_id", "first_seen_date", "baseline_day10_return",
                        f"{rule_name}__signal_day_offset", f"{rule_name}__signal_return",
                        f"{rule_name}__exit_return", f"{rule_name}__counterfactual_delta",
                        "existing_failure_type"]
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in losers_trig:
                    w.writerow({c: r.get(c) for c in cols})
            print(f"\nwrote {len(losers_trig)} rows -> {OUT_LOSER_CSV}")

            with open(OUT_WINNER_CSV, "w", newline="", encoding="utf-8-sig") as f:
                cols = ["stock_id", "first_seen_date", "baseline_day10_return",
                        f"{rule_name}__signal_day_offset", f"{rule_name}__signal_return",
                        f"{rule_name}__exit_return", f"{rule_name}__counterfactual_delta"]
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in sorted(winners_trig, key=lambda r: r[delta_key])[:10]:
                    w.writerow({c: r.get(c) for c in cols})
            print(f"wrote top10 foregone cases -> {OUT_WINNER_CSV}")

    with open(OUT_SUMMARY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cols = list(summary_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in summary_rows:
            w.writerow(r)
    print(f"\nwrote summary -> {OUT_SUMMARY_CSV}")
    print(f"\n報告將寫入 -> {OUT_REPORT}")


if __name__ == "__main__":
    main()
