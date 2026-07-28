"""Phase 2.6 shadow signal 研究（2026-07-23）：純離線分析，讀既有
`/tmp/phase26_named7_snapshots.json`（已有 Day0~Day3 raw features，不需要重新查
資料庫）。**只做研究，不修改任何 production 程式碼、不動 watch_quality_state、
不新增 Hard Exclusion、不調 momentum_score/RS threshold。**

三個新 shadow signal（純函式，逐日計算，需要「從 Day0 累積到當天」的序列）：

1. PRICE_FLOW_DIVERGENCE：價格持續走強，但法人資金從一開始就沒有同步支持
   （不要求「由正轉負」，只要求「持續淨賣 + 價格持續走強」同時成立）
2. EXTREME_RUN_STATE：連續逼近漲停（近似 ±10% 日內漲跌限制）或短期累積漲幅極端
3. VOLUME_EXHAUSTION：近期出現過量能爆發，但目前已明顯衰退（衰退比 = 當日量比 /
   目前為止的量比峰值）

用法：
    python analyze_phase26_shadow_signals.py
"""
from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional

SNAPSHOT_PATH = "/tmp/phase26_named7_snapshots.json"
OUT_CSV = "/tmp/phase26_shadow_signals_named7.csv"

WINNERS = ("1810", "6414", "6505", "8039")
LOSERS = ("7610", "8033", "6226")

# ---- 門檻（shadow research 用；不影響任何 production 判斷）----
NEAR_LIMIT_UP_PCT = 9.5           # 近似漲停（台股日漲跌限制 ±10%）
EXTREME_UP_DAY_PCT = 7.0          # 較寬鬆的「極端上漲日」
DIVERGENCE_MIN_CUM_RETURN_PCT = 5.0
DIVERGENCE_MIN_NEGATIVE_FLOW_RATIO = 0.6
RUN_MIN_CONSECUTIVE_NEAR_LIMIT_DAYS = 2
RUN_MIN_CUM_RETURN_PCT = 25.0
EXHAUSTION_MIN_PEAK_VOLUME_RATIO = 1.5
EXHAUSTION_MAX_DECAY_RATIO = 0.5


def _cum_return_pct(daily_returns: List[Optional[float]]) -> Optional[float]:
    """複利累積報酬（%），任一天缺值就整段回 None（避免用不完整資料算出誤導數字）。"""
    if any(r is None for r in daily_returns):
        return None
    factor = 1.0
    for r in daily_returns:
        factor *= (1.0 + r / 100.0)
    return (factor - 1.0) * 100.0


def compute_shadow_signals_for_stock(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    days = entry.get("days", {})
    ordered_offsets = sorted(int(k[3:]) for k in days.keys())

    returns_so_far: List[float] = []
    flows_so_far: List[float] = []
    vol_ratios_so_far: List[float] = []
    rows = []

    for off in ordered_offsets:
        day = days[f"day{off}"]
        ret = day.get("price_change_1d")
        flow = day.get("total_institution_flow_1d")
        vol = day.get("volume_1d_to_5d_ratio")

        returns_so_far.append(ret if ret is not None else 0.0)
        flows_so_far.append(flow if flow is not None else 0.0)
        if vol is not None:
            vol_ratios_so_far.append(vol)

        # ---- 1. PRICE_FLOW_DIVERGENCE ----
        cum_return = _cum_return_pct(returns_so_far)
        n_days = len(flows_so_far)
        negative_flow_days = sum(1 for f in flows_so_far if f < 0)
        negative_flow_ratio = negative_flow_days / n_days if n_days else None
        cum_flow = sum(flows_so_far)
        price_flow_divergence = bool(
            cum_return is not None
            and cum_return >= DIVERGENCE_MIN_CUM_RETURN_PCT
            and negative_flow_ratio is not None
            and negative_flow_ratio >= DIVERGENCE_MIN_NEGATIVE_FLOW_RATIO
        )
        # strength：價格強度（累積報酬正規化到 0~50）+ 背離程度（負賣天數比例 0~50）
        divergence_strength = None
        if cum_return is not None and negative_flow_ratio is not None:
            divergence_strength = round(
                min(max(cum_return, 0.0), 25.0) / 25.0 * 50.0 + negative_flow_ratio * 50.0, 1
            )

        # ---- 2. EXTREME_RUN_STATE ----
        near_limit_flags = [r >= NEAR_LIMIT_UP_PCT for r in returns_so_far]
        consecutive_near_limit = 0
        for flag in reversed(near_limit_flags):
            if flag:
                consecutive_near_limit += 1
            else:
                break
        extreme_up_days = sum(1 for r in returns_so_far if r >= EXTREME_UP_DAY_PCT)
        extreme_run_state = bool(
            consecutive_near_limit >= RUN_MIN_CONSECUTIVE_NEAR_LIMIT_DAYS
            or (cum_return is not None and cum_return >= RUN_MIN_CUM_RETURN_PCT)
        )
        extreme_run_strength = round(
            consecutive_near_limit * 25.0 + min(max(cum_return or 0.0, 0.0), 50.0), 1
        )

        # ---- 3. VOLUME_EXHAUSTION ----
        volume_exhaustion = False
        volume_decay_ratio = None
        peak_volume_ratio_so_far = None
        if len(vol_ratios_so_far) >= 2:
            # peak 必須發生在「今天以前」，今天本身不計入 peak（否則今天剛好是
            # peak 時 decay_ratio 恆為 1.0，語意上不算「已經衰退」）
            prior_peak = max(vol_ratios_so_far[:-1])
            current_vol = vol_ratios_so_far[-1]
            peak_volume_ratio_so_far = prior_peak
            if prior_peak > 0:
                volume_decay_ratio = round(current_vol / prior_peak, 3)
                volume_exhaustion = bool(
                    prior_peak >= EXHAUSTION_MIN_PEAK_VOLUME_RATIO
                    and volume_decay_ratio <= EXHAUSTION_MAX_DECAY_RATIO
                )

        rows.append({
            "day_offset": off,
            "date": day.get("date"),
            "return_1d": ret,
            "institution_flow_1d": flow,
            "volume_ratio": vol,
            "cum_return_since_day0": round(cum_return, 2) if cum_return is not None else None,
            "negative_flow_ratio": round(negative_flow_ratio, 2) if negative_flow_ratio is not None else None,
            "cum_institution_flow": round(cum_flow, 1),
            "price_flow_divergence": price_flow_divergence,
            "price_flow_divergence_strength": divergence_strength,
            "price_flow_divergence_evidence": (
                f"cum_return={cum_return:.1f}% over {n_days}d, "
                f"{negative_flow_days}/{n_days} days net-sell (ratio={negative_flow_ratio:.2f}), "
                f"cum_flow={cum_flow:,.0f}"
                if cum_return is not None else "insufficient data"
            ),
            "consecutive_near_limit_days": consecutive_near_limit,
            "extreme_up_days_so_far": extreme_up_days,
            "extreme_run_state": extreme_run_state,
            "extreme_run_strength": extreme_run_strength,
            "extreme_run_evidence": (
                f"{consecutive_near_limit} consecutive days >= {NEAR_LIMIT_UP_PCT}%, "
                f"cum_return={cum_return:.1f}% over {n_days}d" if cum_return is not None else "insufficient data"
            ),
            "peak_volume_ratio_so_far": peak_volume_ratio_so_far,
            "volume_decay_ratio": volume_decay_ratio,
            "volume_exhaustion": volume_exhaustion,
            "volume_exhaustion_evidence": (
                f"prior_peak={peak_volume_ratio_so_far:.2f}x, today={vol:.2f}x, decay_ratio={volume_decay_ratio:.2f}"
                if peak_volume_ratio_so_far is not None and vol is not None and volume_decay_ratio is not None
                else "insufficient data (need >=2 days)"
            ),
        })
    return rows


def main() -> None:
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshots = json.load(f)

    all_rows = []
    for sid in list(WINNERS) + list(LOSERS):
        entry = snapshots.get(sid)
        if not entry:
            print(f"WARNING: {sid} missing from snapshot file")
            continue
        outcome = "WINNER" if sid in WINNERS else "LOSER"
        for row in compute_shadow_signals_for_stock(entry):
            all_rows.append({"stock_id": sid, "outcome": outcome, **row})

    columns = [
        "stock_id", "outcome", "day_offset", "date",
        "price_flow_divergence", "price_flow_divergence_strength", "price_flow_divergence_evidence",
        "extreme_run_state", "extreme_run_strength", "extreme_run_evidence",
        "volume_exhaustion", "volume_decay_ratio", "volume_exhaustion_evidence",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"wrote {len(all_rows)} rows -> {OUT_CSV}")

    print()
    print(f"{'stock':7s} {'out':6s} {'day':3s} {'p_flow_div':10s} {'div_str':8s} {'extreme':8s} {'ext_str':8s} {'vol_exh':8s} {'decay':6s}")
    for r in all_rows:
        print(
            f"{r['stock_id']:7s} {r['outcome']:6s} {r['day_offset']:3d} "
            f"{str(r['price_flow_divergence']):10s} {str(r['price_flow_divergence_strength']):8s} "
            f"{str(r['extreme_run_state']):8s} {str(r['extreme_run_strength']):8s} "
            f"{str(r['volume_exhaustion']):8s} {str(r['volume_decay_ratio']):6s}"
        )


if __name__ == "__main__":
    main()
