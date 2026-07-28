"""Phase 2.6 快速 20 檔 hypothesis validation（2026-07-24）。

**不重跑 pipeline_v2 / momentum frame / sector taxonomy / role / candidate
selection。** 只對 20 檔已 matched-sample 的股票，直接查 `daily_price` /
`inst_stock_flow`（單檔查詢，非全市場），算出 PERSISTENT_PRICE_FLOW_DIVERGENCE
與 EXTREME_RUN_EXHAUSTION 兩個 shadow signal 在 Day0~Day3 的表現，門檻完全沿用
`analyze_phase26_shadow_signals.py` 已驗證過的公式，不調整。

20 檔股票（10 大虧 + 10 大贏，matched sampling：rs_market_percentile_20d /
momentum_score 距離最小 + EXTENDED_3D 狀態相同 + regime 相同）已存在
`/tmp/phase26_matched_20.json`。

用法：
    python analyze_phase26_quick20_validation.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice, InstStockFlow
from analyze_phase26_shadow_signals import compute_shadow_signals_for_stock

REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
MATCHED_20_PATH = "/tmp/phase26_matched_20.json"
_INST_TYPES = ("foreign", "trust", "dealer")


def load_cohort() -> Dict[str, Dict[str, Any]]:
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return first_seen


def build_day_features(
    db,
    stock_id: str,
    catch_date: date,
    all_days: List[date],
    day_index: Dict[date, int],
) -> Dict[str, Dict[str, Any]]:
    """對單一股票，直接查 daily_price / inst_stock_flow 算出 Day0~Day3 的
    return_1d / institution_flow_1d(3d) / volume_1d_to_5d_ratio。

    不經過 momentum frame（不需要全市場排名），純粹該股自己的歷史序列。
    `all_days` / `day_index`：整個腳本共用同一份交易日曆（在 `main()` 只查一次，
    不要每檔股票各自重查一次交易日清單——這是先前版本跑很慢的根因之一）。
    """
    if catch_date not in day_index:
        return {}
    i0 = day_index[catch_date]

    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price, DailyPrice.volume)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date.in_(all_days))
        .order_by(DailyPrice.trade_date)
        .all()
    )
    price_by_date = {r[0]: (float(r[1]) if r[1] is not None else None, float(r[2]) if r[2] is not None else None) for r in rows}

    flow_rows = (
        db.query(InstStockFlow.trade_date, InstStockFlow.net_amount_est)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date.in_(all_days),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )
    flow_by_date: Dict[date, float] = {}
    for d, amt in flow_rows:
        flow_by_date[d] = flow_by_date.get(d, 0.0) + float(amt or 0.0)

    days_out: Dict[str, Dict[str, Any]] = {}
    for off in (0, 1, 2, 3):
        j = i0 + off
        if j >= len(all_days):
            continue
        d = all_days[j]
        close, vol = price_by_date.get(d, (None, None))
        prev_close = price_by_date.get(all_days[j - 1], (None, None))[0] if j >= 1 else None
        return_1d = (close / prev_close - 1.0) * 100.0 if close is not None and prev_close else None

        # volume_1d_to_5d_ratio：近 5 日（含當日）均量
        vol_window = [price_by_date.get(all_days[k], (None, None))[1] for k in range(max(0, j - 4), j + 1)]
        vol_window = [v for v in vol_window if v is not None and v > 0]
        vol_ratio = None
        if vol is not None and vol_window:
            avg5 = sum(vol_window) / len(vol_window)
            if avg5 > 0:
                vol_ratio = vol / avg5

        flow_1d = flow_by_date.get(d)
        flow_3d_window = [flow_by_date.get(all_days[k]) for k in range(max(0, j - 2), j + 1)]
        flow_3d = sum(f for f in flow_3d_window if f is not None) if any(f is not None for f in flow_3d_window) else None

        days_out[f"day{off}"] = {
            "date": d.isoformat(),
            "price_change_1d": return_1d,
            "total_institution_flow_1d": flow_1d,
            "total_institution_flow_3d": flow_3d,
            "volume_1d_to_5d_ratio": vol_ratio,
            "close": close,
        }
    return days_out


def main() -> None:
    cohort = load_cohort()
    with open(MATCHED_20_PATH, encoding="utf-8") as f:
        matched = json.load(f)
    all_stock_ids = matched["losers"] + matched["winners"]
    outcome = {sid: "BIG_LOSER" for sid in matched["losers"]}
    outcome.update({sid: "WINNER" for sid in matched["winners"]})

    db = SessionLocal()
    try:
        # 整個腳本只查一次交易日曆（涵蓋最早 catch_date 前 10 天 ~ 最晚 catch_date 後
        # 5 天），20 檔股票共用，不要每檔各自重查一次（這是先前版本變慢的根因之一）。
        t_cal = time.time()
        anchor_end = date(2026, 7, 22)
        all_days = get_recent_trade_dates(db, anchor_end, 90)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar loaded in {time.time() - t_cal:.1f}s, {len(all_days)} days "
              f"({all_days[0]} ~ {all_days[-1]})", flush=True)

        results = {}
        for i, sid in enumerate(all_stock_ids, start=1):
            t0 = time.time()
            rec = cohort[sid]
            catch_date = date.fromisoformat(rec["catch_date"])
            days = build_day_features(db, sid, catch_date, all_days, day_index)
            entry = {"days": days}
            trajectory = compute_shadow_signals_for_stock(entry)

            divergence_true_count = 0
            ever_extreme_run = False
            traj_out = []
            for row in trajectory:
                if row["price_flow_divergence"]:
                    divergence_true_count += 1
                if row["extreme_run_state"]:
                    ever_extreme_run = True
                traj_out.append({
                    "day_offset": row["day_offset"],
                    "persistent_price_flow_divergence": divergence_true_count >= 2,
                    "extreme_run_exhaustion": ever_extreme_run and row["volume_exhaustion"],
                })

            results[sid] = {
                "outcome": outcome[sid],
                "forward_return_pct_10d": rec["forward_return_pct"],
                "catch_date": rec["catch_date"],
                "trajectory": traj_out,
                "raw_days": days,
            }
            print(
                f"[{i}/{len(all_stock_ids)}] {sid} ({outcome[sid]}, ret={rec['forward_return_pct']:.1f}%) "
                f"done in {time.time() - t0:.1f}s, days_captured={len(days)}",
                flush=True,
            )
            with open("/tmp/phase26_quick20_partial.json", "w", encoding="utf-8") as pf:
                json.dump(results, pf, ensure_ascii=False, indent=2, default=str)
    finally:
        db.close()

    with open("/tmp/phase26_quick20_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # ---- 彙整輸出 ----
    def first_trigger(sid: str, key: str) -> Optional[int]:
        for row in results[sid]["trajectory"]:
            if row[key]:
                return row["day_offset"]
        return None

    losers = matched["losers"]
    winners = matched["winners"]

    print(f"{'signal':30s} {'BIG_LOSER':>12s} {'WINNER':>12s}")
    for key, label in (
        ("persistent_price_flow_divergence", "persistent divergence"),
        ("extreme_run_exhaustion", "extreme run exhaustion"),
    ):
        loser_hits = [sid for sid in losers if first_trigger(sid, key) is not None]
        winner_hits = [sid for sid in winners if first_trigger(sid, key) is not None]
        print(f"{label:30s} {len(loser_hits)}/10{'':6s} {len(winner_hits)}/10")

    either_losers = [sid for sid in losers if first_trigger(sid, "persistent_price_flow_divergence") is not None or first_trigger(sid, "extreme_run_exhaustion") is not None]
    either_winners = [sid for sid in winners if first_trigger(sid, "persistent_price_flow_divergence") is not None or first_trigger(sid, "extreme_run_exhaustion") is not None]
    print(f"{'either signal':30s} {len(either_losers)}/10{'':6s} {len(either_winners)}/10")
    print()

    print("=== 每檔觸發明細 ===")
    for sid in losers + winners:
        r = results[sid]
        d1 = first_trigger(sid, "persistent_price_flow_divergence")
        d2 = first_trigger(sid, "extreme_run_exhaustion")
        print(f"{sid:8s} {r['outcome']:10s} ret={r['forward_return_pct_10d']:7.1f}%  "
              f"divergence_first_day={d1}  exhaustion_first_day={d2}")


if __name__ == "__main__":
    main()
