"""Phase 2.6 composite signal hypothesis validation（2026-07-23）。

驗證兩個從 7-stock case study 歸納出的 composite signal，在 617 檔 dedup replay
candidates（Day0~Day3）上的辨識力。**純 hypothesis validation，不調整任何門檻、
不新增第三個 feature、不修改 production code。**

沿用 `analyze_phase26_shadow_signals.py` 的底層每日訊號公式（門檻完全不變）：
    - price_flow_divergence（單日）：cum_return_since_day0 >= 5% 且
      negative_flow_ratio(累積至今) >= 0.6
    - extreme_run_state（單日）：consecutive_near_limit_days >= 2 或
      cum_return_since_day0 >= 25%
    - volume_exhaustion（單日）：prior peak volume ratio >= 1.5 且
      decay_ratio(今日/prior peak) <= 0.5

本輪定義兩個 composite（純邏輯組合，不是新門檻）：
    - PERSISTENT_PRICE_FLOW_DIVERGENCE：price_flow_divergence 在 Day0~D 累積
      至少出現 2 天 True（呼應 7-stock case study 發現：單日判斷會誤標 1810，
      「持續出現」才是真正的區分特徵）
    - EXTREME_RUN_EXHAUSTION：過去（含當天）曾經 extreme_run_state=True，
      且當天 volume_exhaustion=True（極端急漲之後才可能有「衰竭」，兩者必須
      有時間先後關係，不是同一天各自獨立判斷）

用法：
    python analyze_phase26_composite_validation.py
"""
from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional

from analyze_phase26_shadow_signals import (
    DIVERGENCE_MIN_CUM_RETURN_PCT,
    DIVERGENCE_MIN_NEGATIVE_FLOW_RATIO,
    EXHAUSTION_MAX_DECAY_RATIO,
    EXHAUSTION_MIN_PEAK_VOLUME_RATIO,
    NEAR_LIMIT_UP_PCT,
    RUN_MIN_CONSECUTIVE_NEAR_LIMIT_DAYS,
    RUN_MIN_CUM_RETURN_PCT,
    compute_shadow_signals_for_stock,
)

SNAPSHOT_PATH = "/tmp/phase26_full617_day0to3.json"
REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"

NAMED_WINNERS = ("8039", "6505", "6414", "1810")
NAMED_LOSERS_CASE_STUDY = ("7610", "6226")
NAMED_LOSER_DETERMINISTIC = ("8033",)


def load_forward_returns() -> Dict[str, float]:
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return {sid: r.get("forward_return_pct") for sid, r in first_seen.items() if r.get("forward_return_pct") is not None}


def outcome_group(ret: float) -> str:
    if ret >= 10:
        return "WINNER"
    if ret <= -10:
        return "BIG_LOSER"
    return "OTHER"


def compute_composite_trajectory(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """對單一股票回傳每天的 composite 判斷（累積至該天為止的狀態）。"""
    daily_signals = compute_shadow_signals_for_stock(entry)
    trajectory = []
    ever_extreme_run = False
    divergence_true_count = 0
    for row in daily_signals:
        if row["price_flow_divergence"]:
            divergence_true_count += 1
        if row["extreme_run_state"]:
            ever_extreme_run = True

        persistent_divergence = divergence_true_count >= 2
        extreme_run_exhaustion = ever_extreme_run and row["volume_exhaustion"]

        trajectory.append({
            "day_offset": row["day_offset"],
            "persistent_price_flow_divergence": persistent_divergence,
            "extreme_run_exhaustion": extreme_run_exhaustion,
            "composite_union": persistent_divergence or extreme_run_exhaustion,
        })
    return trajectory


def first_trigger_day(trajectory: List[Dict[str, Any]], key: str) -> Optional[int]:
    for row in trajectory:
        if row[key]:
            return row["day_offset"]
    return None


def main() -> None:
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshots = json.load(f)
    forward_returns = load_forward_returns()

    print(f"snapshot stocks: {len(snapshots)}")
    print(f"thresholds unchanged: divergence(cum_return>={DIVERGENCE_MIN_CUM_RETURN_PCT}%, "
          f"neg_flow_ratio>={DIVERGENCE_MIN_NEGATIVE_FLOW_RATIO}), "
          f"extreme_run(consec>={RUN_MIN_CONSECUTIVE_NEAR_LIMIT_DAYS} or cum>={RUN_MIN_CUM_RETURN_PCT}%), "
          f"exhaustion(peak>={EXHAUSTION_MIN_PEAK_VOLUME_RATIO}x, decay<={EXHAUSTION_MAX_DECAY_RATIO})")
    print()

    results: Dict[str, Dict[str, Any]] = {}
    for sid, entry in snapshots.items():
        ret = forward_returns.get(sid)
        if ret is None:
            continue
        trajectory = compute_composite_trajectory(entry)
        if not trajectory:
            continue
        final = trajectory[-1]  # 累積到 Day3（或最後可得那天）的狀態
        results[sid] = {
            "forward_return_pct_10d": ret,
            "outcome": outcome_group(ret),
            "persistent_divergence_final": final["persistent_price_flow_divergence"],
            "extreme_exhaustion_final": final["extreme_run_exhaustion"],
            "composite_union_final": final["composite_union"],
            "persistent_divergence_first_day": first_trigger_day(trajectory, "persistent_price_flow_divergence"),
            "extreme_exhaustion_first_day": first_trigger_day(trajectory, "extreme_run_exhaustion"),
            "composite_union_first_day": first_trigger_day(trajectory, "composite_union"),
            "trajectory": trajectory,
        }

    print(f"stocks with forward_return + snapshot: {len(results)}")
    winners = {sid: r for sid, r in results.items() if r["outcome"] == "WINNER"}
    losers = {sid: r for sid, r in results.items() if r["outcome"] == "BIG_LOSER"}
    others = {sid: r for sid, r in results.items() if r["outcome"] == "OTHER"}
    print(f"WINNER (>=+10%): {len(winners)}, BIG_LOSER (<=-10%): {len(losers)}, OTHER: {len(others)}")
    print()

    def _report(signal_key: str, first_day_key: str, label: str) -> None:
        print(f"===== {label} =====")
        loser_hits = [sid for sid, r in losers.items() if r[signal_key]]
        winner_hits = [sid for sid, r in winners.items() if r[signal_key]]
        print(f"BIG_LOSER 命中率: {len(loser_hits)}/{len(losers)} = {100*len(loser_hits)/len(losers):.1f}%" if losers else "no losers")
        print(f"WINNER 誤標率: {len(winner_hits)}/{len(winners)} = {100*len(winner_hits)/len(winners):.1f}%" if winners else "no winners")

        for day in (0, 1, 2, 3):
            cum_loser_hits = [sid for sid, r in losers.items() if r[first_day_key] is not None and r[first_day_key] <= day]
            cum_winner_hits = [sid for sid, r in winners.items() if r[first_day_key] is not None and r[first_day_key] <= day]
            print(f"  Day<={day} 累積命中率: BIG_LOSER {len(cum_loser_hits)}/{len(losers)} = "
                  f"{100*len(cum_loser_hits)/len(losers):.1f}% | WINNER {len(cum_winner_hits)}/{len(winners)} = "
                  f"{100*len(cum_winner_hits)/len(winners):.1f}%")

        hit_returns = [r["forward_return_pct_10d"] for r in results.values() if r[signal_key]]
        miss_returns = [r["forward_return_pct_10d"] for r in results.values() if not r[signal_key]]
        if hit_returns:
            print(f"命中股票平均 future_return_10d (全體，非僅 winner/loser): {statistics.mean(hit_returns):.2f}% (N={len(hit_returns)})")
        if miss_returns:
            print(f"未命中股票平均 future_return_10d (全體): {statistics.mean(miss_returns):.2f}% (N={len(miss_returns)})")
        print()

    _report("persistent_divergence_final", "persistent_divergence_first_day", "PERSISTENT_PRICE_FLOW_DIVERGENCE")
    _report("extreme_exhaustion_final", "extreme_exhaustion_first_day", "EXTREME_RUN_EXHAUSTION")
    _report("composite_union_final", "composite_union_first_day", "UNION (divergence OR exhaustion)")

    print("===== 特別檢查：具名案例 =====")
    for sid in NAMED_WINNERS:
        r = results.get(sid)
        if r is None:
            print(f"{sid}: not in Day0-3 snapshot (missing)")
            continue
        print(f"{sid} (winner, ret={r['forward_return_pct_10d']:.1f}%): "
              f"persistent_divergence={r['persistent_divergence_final']} (first_day={r['persistent_divergence_first_day']}), "
              f"extreme_exhaustion={r['extreme_exhaustion_final']} (first_day={r['extreme_exhaustion_first_day']})")
    for sid in NAMED_LOSERS_CASE_STUDY:
        r = results.get(sid)
        if r is None:
            print(f"{sid}: not in Day0-3 snapshot (missing)")
            continue
        print(f"{sid} (case-study loser, ret={r['forward_return_pct_10d']:.1f}%): "
              f"persistent_divergence={r['persistent_divergence_final']} (first_day={r['persistent_divergence_first_day']}), "
              f"extreme_exhaustion={r['extreme_exhaustion_final']} (first_day={r['extreme_exhaustion_first_day']})")
    for sid in NAMED_LOSER_DETERMINISTIC:
        r = results.get(sid)
        if r is None:
            print(f"{sid}: not in Day0-3 snapshot (missing)")
            continue
        print(f"{sid} (loser, handled by DETERIORATING not composite, ret={r['forward_return_pct_10d']:.1f}%): "
              f"persistent_divergence={r['persistent_divergence_final']} (first_day={r['persistent_divergence_first_day']}), "
              f"extreme_exhaustion={r['extreme_exhaustion_final']} (first_day={r['extreme_exhaustion_first_day']})")

    with open("/tmp/phase26_composite_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("\nfull results written to /tmp/phase26_composite_validation_results.json")


if __name__ == "__main__":
    main()
