"""Phase 2.8: Normal Winner Pullback vs Round-Trip Failure Validation（2026-07-24）。

**純離線分析**，讀既有 `/tmp/continuation_quality_132_raw.json`（Phase 2.6 132-stock
7-observation trajectory）+ `/tmp/phase25_replay_60d.json`（Day0 momentum_score /
rs_market_percentile_20d / regime / risk_warnings，只用來做次要 matching）+
`/tmp/phase27_roundtrip_matched.json`（18 檔 ROUND_TRIP_FAILURE stock_id 清單）。
不查資料庫、不重跑 pipeline、不修改任何既有規則或門檻。

核心問題：當兩檔股票都曾經上漲、且都開始從局部高點回檔時，「最後重新轉強的
大贏家」和「最後把獲利吐光的大輸家」，能不能在回檔早期被區分？

用法：
    python analyze_phase28_normal_pullback.py
"""
from __future__ import annotations

import csv
import json
import statistics
from typing import Any, Dict, List, Optional, Tuple

RAW_PATH = "/tmp/continuation_quality_132_raw.json"
REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
ROUNDTRIP_MATCHED_PATH = "/tmp/phase27_roundtrip_matched.json"

OUT_NPW_CSV = "/tmp/phase28_normal_pullback_winners.csv"
OUT_MATCHED_CSV = "/tmp/phase28_roundtrip_vs_pullback_matched.csv"
OUT_REPORT = "/Users/brian.yh.chien/.gstack/projects/always-stock/docs/plans/phase28_roundtrip_vs_pullback_report.md"

RISK_STATES = ("AT_RISK", "FAILED")

# ---- 研究用取樣門檻（僅用來建立 research control group，不是 production rule）----
_MIN_PULLBACK_DRAWDOWN_PCT = -5.0  # 至少真的有回檔過


def load_trajectories() -> Dict[str, List[Dict[str, Any]]]:
    rows = json.load(open(RAW_PATH, encoding="utf-8"))
    by_stock: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_stock.setdefault(r["stock_id"], []).append(r)
    for sid in by_stock:
        by_stock[sid].sort(key=lambda x: x["day_offset"])
    return by_stock


def load_day0_meta() -> Dict[str, Dict[str, Any]]:
    """回傳 stock_id -> {momentum_score, rs_market_percentile_20d, regime, extended_3d}（首次被抓當天）。"""
    data = json.load(open(REPLAY_617_PATH, encoding="utf-8"))
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    out = {}
    for sid, r in first_seen.items():
        out[sid] = {
            "momentum_score": r.get("momentum_score"),
            "rs_market_percentile_20d": r.get("rs_market_percentile_20d"),
            "regime": r.get("regime"),
            "extended_3d": "EXTENDED_3D" in (r.get("risk_warnings") or []),
        }
    return out


def find_peak_index(traj: List[Dict[str, Any]]) -> int:
    returns = [r["current_return"] for r in traj]
    return max(range(len(returns)), key=lambda i: returns[i])


# ---------------------------------------------------------------------------
# Step 1: 從 66 WINNER 篩出 NORMAL_PULLBACK_WINNER 候選
# ---------------------------------------------------------------------------

def find_normal_pullback_winners(by_stock: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    candidates = []
    for sid, traj in by_stock.items():
        if traj[0]["outcome_group"] != "WINNER":
            continue
        peak_idx = find_peak_index(traj)
        if peak_idx >= len(traj) - 1:
            continue  # PEAK 落在最後一個 offset，沒有 P1 可看，剔除（同 Phase 2.7 限制）
        post_peak_dd = [r["drawdown_from_max"] for r in traj[peak_idx + 1:]]
        min_dd = min(post_peak_dd)
        if min_dd > _MIN_PULLBACK_DRAWDOWN_PCT:
            continue  # 沒有真正回檔過（用 -5% 當 research-only 篩選線，非 production rule）
        candidates.append({
            "stock_id": sid,
            "peak_day_offset": traj[peak_idx]["day_offset"],
            "peak_return": traj[peak_idx]["current_return"],
            "min_post_peak_drawdown": min_dd,
            "p1_drawdown": traj[peak_idx + 1]["drawdown_from_max"],
            "future_return_10d": traj[0]["future_return_10d"],
            "n_post_peak_obs": len(traj) - peak_idx - 1,
        })
    return candidates


# ---------------------------------------------------------------------------
# Step 2: Matched sampling — 18 ROUND_TRIP_FAILURE vs NPW pool
# ---------------------------------------------------------------------------

def match_distance(rt: Dict[str, Any], npw: Dict[str, Any], meta: Dict[str, Dict[str, Any]]) -> float:
    rt_sid, npw_sid = rt["stock_id"], npw["stock_id"]
    d = abs(rt["peak_return"] - npw["peak_return"]) * 2.0
    d += abs(rt["p1_drawdown"] - npw["p1_drawdown"]) * 2.0
    rt_meta, npw_meta = meta.get(rt_sid, {}), meta.get(npw_sid, {})
    ms_rt, ms_npw = rt_meta.get("momentum_score"), npw_meta.get("momentum_score")
    if ms_rt is not None and ms_npw is not None:
        d += abs(ms_rt - ms_npw) * 0.1
    rs_rt, rs_npw = rt_meta.get("rs_market_percentile_20d"), npw_meta.get("rs_market_percentile_20d")
    if rs_rt is not None and rs_npw is not None:
        d += abs(rs_rt - rs_npw) * 0.05
    if rt_meta.get("extended_3d") != npw_meta.get("extended_3d"):
        d += 5.0
    if rt_meta.get("regime") != npw_meta.get("regime"):
        d += 3.0
    return d


def greedy_match(rt_list: List[Dict[str, Any]], npw_pool: List[Dict[str, Any]],
                  meta: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, float]]:
    """每個 loser 依「自己能找到的最佳可行 distance」升冪處理，找不到就試下一個候選
    （fix：不是只試單一 best-choice 就放棄，要整張候選清單找到第一個未被取走的）。"""
    all_pairs = []
    for rt in rt_list:
        ranked = sorted(npw_pool, key=lambda w: match_distance(rt, w, meta))
        all_pairs.append((rt, ranked))
    all_pairs.sort(key=lambda item: match_distance(item[0], item[1][0], meta) if item[1] else float("inf"))

    taken = set()
    result = []
    for rt, ranked in all_pairs:
        for w in ranked:
            if w["stock_id"] not in taken:
                taken.add(w["stock_id"])
                result.append((rt["stock_id"], w["stock_id"], match_distance(rt, w, meta)))
                break
    return result


# ---------------------------------------------------------------------------
# Step 3: peak-aligned rows (P0..P3, P4 if available)
# ---------------------------------------------------------------------------

def build_peak_aligned_rows(sid: str, role: str, traj: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    peak_idx = find_peak_index(traj)
    peak_return = traj[peak_idx]["current_return"]
    rows_out = []
    for rel_off in (0, 1, 2, 3, 4):
        idx = peak_idx + rel_off
        if idx >= len(traj):
            break
        r = traj[idx]
        profit_retention_ratio = None
        if peak_return and peak_return > 0:
            profit_retention_ratio = round(r["current_return"] / peak_return, 4)
        rows_out.append({
            "stock_id": sid,
            "role": role,
            "future_return_10d": traj[0]["future_return_10d"],
            "peak_day_offset": traj[peak_idx]["day_offset"],
            "peak_return": peak_return,
            "rel_offset": f"P{rel_off}",
            "actual_day_offset": r["day_offset"],
            "current_return": r["current_return"],
            "drawdown_from_max": r["drawdown_from_max"],
            "profit_retention_ratio": profit_retention_ratio,
            "excess_return_vs_market_3d": r["excess_return_vs_market_3d"],
            "momentum_freshness": r["momentum_freshness"],
            "tracking_state": r["tracking_state"],
            "continuation_quality_state": r["continuation_quality_state"],
        })
    return rows_out


def consecutive_risk_streak(states_up_to_here: List[str]) -> int:
    streak = 0
    for s in reversed(states_up_to_here):
        if s in RISK_STATES:
            streak += 1
        else:
            break
    return streak


def consecutive_failed_streak(states_up_to_here: List[str]) -> int:
    streak = 0
    for s in reversed(states_up_to_here):
        if s == "FAILED":
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Step 4: post-peak persistence metrics (從 peak 開始算，非從 Day0)
# ---------------------------------------------------------------------------

def compute_post_peak_persistence(traj: List[Dict[str, Any]]) -> Dict[str, Any]:
    peak_idx = find_peak_index(traj)
    post = traj[peak_idx:]
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

    recovery_speed = "N/A"
    if first_risk_idx is not None:
        recovered_at = next((i for i in range(first_risk_idx + 1, n) if states[i] in ("HEALTHY", "CAUTION")), None)
        if recovered_at is None:
            recovery_speed = "NOT_RECOVERED_BY_LAST_OBS"
        else:
            gap = recovered_at - first_risk_idx
            recovery_speed = "RECOVERED_NEXT" if gap == 1 else ("RECOVERED_WITHIN_2" if gap == 2 else "RECOVERED_LATER")

    ever_reaccelerating = any(r.get("tracking_state") == "REACCELERATING" for r in post)
    drawdowns = [r["drawdown_from_max"] for r in post]
    worst_dd = min(drawdowns)
    worst_dd_idx = drawdowns.index(worst_dd)
    last_dd = drawdowns[-1]
    last_state = states[-1]
    peak_return = traj[peak_idx]["current_return"]
    last_return = post[-1]["current_return"]
    re_break_peak = bool(peak_return and last_return >= peak_return)

    repair_label = (
        "HEALTHY_REPAIR"
        if (last_state in ("HEALTHY", "CAUTION")) and (worst_dd_idx < len(drawdowns) - 1 or last_dd > worst_dd + 1e-9 or len(drawdowns) == 1)
        else "FAILED_REPAIR"
    )

    return {
        "ever_at_risk_plus": first_risk_idx is not None,
        "at_risk_plus_persistence": at_risk_persistence,
        "ever_failed": first_failed_idx is not None,
        "failed_persistence": failed_persistence,
        "recovery_speed": recovery_speed,
        "not_recovered": recovery_speed == "NOT_RECOVERED_BY_LAST_OBS",
        "worst_drawdown_after_peak": worst_dd,
        "re_break_peak": re_break_peak,
        "ever_reaccelerating": ever_reaccelerating,
        "repair_label": repair_label,
        "n_post_peak_obs": n,
    }


# ---------------------------------------------------------------------------
# Step 5: magnitude-aligned comparison（不看 offset，看「第一次回檔到 -5%~-8% 附近」時）
# ---------------------------------------------------------------------------

def first_meaningful_pullback_row(traj: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    peak_idx = find_peak_index(traj)
    states_so_far: List[str] = []
    for i in range(peak_idx, len(traj)):
        states_so_far.append(traj[i]["continuation_quality_state"])
        if i == peak_idx:
            continue
        if traj[i]["drawdown_from_max"] <= _MIN_PULLBACK_DRAWDOWN_PCT:
            r = traj[i]
            peak_return = traj[peak_idx]["current_return"]
            ratio = round(r["current_return"] / peak_return, 4) if peak_return and peak_return > 0 else None
            return {
                "rel_offset_from_peak": i - peak_idx,
                "current_return": r["current_return"],
                "drawdown_from_max": r["drawdown_from_max"],
                "profit_retention_ratio": ratio,
                "excess_return_vs_market_3d": r["excess_return_vs_market_3d"],
                "momentum_freshness": r["momentum_freshness"],
                "tracking_state": r["tracking_state"],
                "continuation_quality_state": r["continuation_quality_state"],
                "at_risk_streak_here": consecutive_risk_streak(states_so_far),
                "failed_streak_here": consecutive_failed_streak(states_so_far),
            }
    return None


def main() -> None:
    by_stock = load_trajectories()
    meta = load_day0_meta()
    rt_matched = json.load(open(ROUNDTRIP_MATCHED_PATH, encoding="utf-8"))
    rt_ids = rt_matched["round_trip_losers"]

    # ---- Step 1: NPW 候選 ----
    npw_candidates = find_normal_pullback_winners(by_stock)
    print(f"=== Step 1: NORMAL_PULLBACK_WINNER 候選掃描 ===")
    print(f"66 WINNER 中符合 NORMAL_PULLBACK_WINNER 條件：{len(npw_candidates)} 檔")

    with open(OUT_NPW_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["stock_id", "peak_day_offset", "peak_return", "min_post_peak_drawdown",
                "p1_drawdown", "future_return_10d", "n_post_peak_obs"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in npw_candidates:
            w.writerow(c)
    print(f"wrote {len(npw_candidates)} rows -> {OUT_NPW_CSV}")

    # ---- Step 2: matched sampling ----
    rt_features = []
    for sid in rt_ids:
        traj = by_stock[sid]
        peak_idx = find_peak_index(traj)
        rt_features.append({
            "stock_id": sid,
            "peak_return": traj[peak_idx]["current_return"],
            "p1_drawdown": traj[peak_idx + 1]["drawdown_from_max"] if peak_idx + 1 < len(traj) else traj[peak_idx]["drawdown_from_max"],
        })

    use_all = len(npw_candidates) < 18
    if use_all:
        print(f"\n誠實揭露：NPW 候選只有 {len(npw_candidates)} 檔，少於 18 檔，將使用全部候選，"
              f"不放寬篩選條件湊數。")
        matched_pairs = greedy_match(rt_features, npw_candidates, meta)
    else:
        matched_pairs = greedy_match(rt_features, npw_candidates, meta)

    print(f"\n=== Step 2: Matched sampling 結果 ===")
    print(f"完成配對數：{len(matched_pairs)} / {len(rt_ids)} ROUND_TRIP_FAILURE")
    distances = [d for _, _, d in matched_pairs]
    if distances:
        print(f"distance median={statistics.median(distances):.2f}, "
              f"min={min(distances):.2f}, max={max(distances):.2f}, "
              f"count distance<10={sum(1 for d in distances if d < 10)}/{len(distances)}")

    npw_ids = [w for _, w, _ in matched_pairs]

    # ---- Step 3: peak-aligned P0-P4 rows for both groups ----
    all_rows = []
    for rt_sid, npw_sid, dist in matched_pairs:
        all_rows.extend(build_peak_aligned_rows(rt_sid, "ROUND_TRIP_FAILURE", by_stock[rt_sid]))
        all_rows.extend(build_peak_aligned_rows(npw_sid, "NORMAL_PULLBACK_WINNER", by_stock[npw_sid]))

    columns = ["stock_id", "role", "future_return_10d", "peak_day_offset", "peak_return",
               "rel_offset", "actual_day_offset", "current_return", "drawdown_from_max",
               "profit_retention_ratio", "excess_return_vs_market_3d", "momentum_freshness",
               "tracking_state", "continuation_quality_state"]
    with open(OUT_MATCHED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"\nwrote {len(all_rows)} rows -> {OUT_MATCHED_CSV}")

    # ---- Step 4: post-peak persistence per stock (both groups, matched set) ----
    rt_persist = {sid: compute_post_peak_persistence(by_stock[sid]) for sid in rt_ids}
    npw_persist = {sid: compute_post_peak_persistence(by_stock[sid]) for sid in npw_ids}
    # also compute for the FULL npw pool (not just matched) — for the robustness test
    npw_all_persist = {c["stock_id"]: compute_post_peak_persistence(by_stock[c["stock_id"]]) for c in npw_candidates}

    def group_pct(persist_map: Dict[str, Dict[str, Any]], cond) -> Tuple[int, int, float]:
        rows = list(persist_map.values())
        c = sum(1 for r in rows if cond(r))
        n = len(rows)
        return c, n, round(100 * c / n, 1) if n else 0.0

    print("\n=== Robustness Test：真正有回檔的 Winner，persistence 是否還乾淨？===")
    print(f"NPW 全部候選 n={len(npw_all_persist)}")
    for label, cond in (
        ("ever AT_RISK+ after peak", lambda r: r["ever_at_risk_plus"]),
        ("AT_RISK+ persistence >=2", lambda r: r["at_risk_plus_persistence"] >= 2),
        ("AT_RISK+ persistence >=3", lambda r: r["at_risk_plus_persistence"] >= 3),
        ("ever FAILED after peak", lambda r: r["ever_failed"]),
        ("FAILED persistence >=2", lambda r: r["failed_persistence"] >= 2),
        ("FAILED persistence >=3", lambda r: r["failed_persistence"] >= 3),
    ):
        c, n, p = group_pct(npw_all_persist, cond)
        c_r, n_r, p_r = group_pct(rt_persist, cond)
        print(f"{label:30s}  NPW(all)={c}/{n} ({p}%)   ROUND_TRIP(18)={c_r}/{n_r} ({p_r}%)")

    # ---- Step 9: magnitude-aligned comparison ----
    print("\n=== Magnitude-aligned comparison（兩組都回檔到 <=-5% 時）===")
    rt_align = {sid: first_meaningful_pullback_row(by_stock[sid]) for sid in rt_ids}
    npw_align = {sid: first_meaningful_pullback_row(by_stock[sid]) for sid in npw_ids}
    rt_align = {k: v for k, v in rt_align.items() if v is not None}
    npw_align = {k: v for k, v in npw_align.items() if v is not None}
    print(f"ROUND_TRIP 有效樣本 n={len(rt_align)}/{len(rt_ids)}, "
          f"NPW(matched) 有效樣本 n={len(npw_align)}/{len(npw_ids)}")

    def med(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.median(vals), 2) if vals else None

    for label, key in (
        ("rel_offset_from_peak", "rel_offset_from_peak"),
        ("current_return", "current_return"),
        ("drawdown_from_max", "drawdown_from_max"),
        ("profit_retention_ratio", "profit_retention_ratio"),
        ("excess_return_vs_market_3d", "excess_return_vs_market_3d"),
        ("at_risk_streak_here", "at_risk_streak_here"),
    ):
        rt_vals = [v[key] for v in rt_align.values()]
        npw_vals = [v[key] for v in npw_align.values()]
        print(f"{label:28s} ROUND_TRIP median={med(rt_vals)}   NPW median={med(npw_vals)}")

    rt_state_dist = {}
    for v in rt_align.values():
        rt_state_dist[v["continuation_quality_state"]] = rt_state_dist.get(v["continuation_quality_state"], 0) + 1
    npw_state_dist = {}
    for v in npw_align.values():
        npw_state_dist[v["continuation_quality_state"]] = npw_state_dist.get(v["continuation_quality_state"], 0) + 1
    print(f"ROUND_TRIP continuation_quality_state 分布: {rt_state_dist}")
    print(f"NPW continuation_quality_state 分布: {npw_state_dist}")

    # ---- Step 13/14: comparison tables ----
    print("\n=== 核心 Comparison Table（post-peak）===")
    print(f"{'metric':35s} {'ROUND_TRIP(n=18)':>20s} {'NPW_matched(n=' + str(len(npw_ids)) + ')':>22s}")

    def show(label, rt_val, npw_val):
        print(f"{label:35s} {str(rt_val):>20s} {str(npw_val):>22s}")

    show("sample count", len(rt_ids), len(npw_ids))
    show("median peak_return", med([f["peak_return"] for f in rt_features]),
         med([c["peak_return"] for c in npw_candidates if c["stock_id"] in npw_ids]))
    show("median P1 drawdown", med([f["p1_drawdown"] for f in rt_features]),
         med([c["p1_drawdown"] for c in npw_candidates if c["stock_id"] in npw_ids]))

    for label, cond in (
        ("ever AT_RISK after peak", lambda r: r["ever_at_risk_plus"]),
        ("AT_RISK persistence >=2", lambda r: r["at_risk_plus_persistence"] >= 2),
        ("AT_RISK persistence >=3", lambda r: r["at_risk_plus_persistence"] >= 3),
        ("ever FAILED after peak", lambda r: r["ever_failed"]),
        ("FAILED persistence >=2", lambda r: r["failed_persistence"] >= 2),
        ("FAILED persistence >=3", lambda r: r["failed_persistence"] >= 3),
        ("not recovered by last obs", lambda r: r["not_recovered"]),
        ("re-break previous peak", lambda r: r["re_break_peak"]),
        ("ever REACCELERATING", lambda r: r["ever_reaccelerating"]),
    ):
        _, _, p_rt = group_pct(rt_persist, cond)
        _, _, p_npw = group_pct(npw_persist, cond)
        show(label, f"{p_rt}%", f"{p_npw}%")

    show("median worst drawdown after peak",
         med([r["worst_drawdown_after_peak"] for r in rt_persist.values()]),
         med([r["worst_drawdown_after_peak"] for r in npw_persist.values()]))

    repair_rt = {}
    for r in rt_persist.values():
        repair_rt[r["repair_label"]] = repair_rt.get(r["repair_label"], 0) + 1
    repair_npw = {}
    for r in npw_persist.values():
        repair_npw[r["repair_label"]] = repair_npw.get(r["repair_label"], 0) + 1
    print(f"\nrepair_label 分布 ROUND_TRIP: {repair_rt}")
    print(f"repair_label 分布 NPW(matched): {repair_npw}")

    # ---- time series P0-P3 ----
    print("\n=== 時間序列 P0/P1/P2/P3 比較 ===")
    by_rel: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in all_rows:
        by_rel.setdefault((r["role"], r["rel_offset"]), []).append(r)

    # ---- 補充：running persistence streak（從 peak 累計到當前 offset）per rel_offset ----
    def running_streak_at(traj: List[Dict[str, Any]], peak_idx: int, rel_off: int) -> Optional[int]:
        idx = peak_idx + rel_off
        if idx >= len(traj):
            return None
        states_so_far = [r["continuation_quality_state"] for r in traj[peak_idx: idx + 1]]
        return consecutive_risk_streak(states_so_far)

    print("\n=== Running AT_RISK+ streak（從 peak 累計到當前 offset，含全部可用位置）===")
    print(f"{'role':25s} {'rel':4s} {'n':>3s} {'streak>=2 %':>12s} {'streak>=3 %':>12s}")
    for role, ids in (("ROUND_TRIP_FAILURE", rt_ids), ("NORMAL_PULLBACK_WINNER", npw_ids)):
        for rel_off in (0, 1, 2, 3, 4, 5, 6):
            vals = []
            for sid in ids:
                traj = by_stock[sid]
                peak_idx = find_peak_index(traj)
                s = running_streak_at(traj, peak_idx, rel_off)
                if s is not None:
                    vals.append(s)
            if not vals:
                continue
            pct2 = 100 * sum(1 for v in vals if v >= 2) / len(vals)
            pct3 = 100 * sum(1 for v in vals if v >= 3) / len(vals)
            print(f"{role:25s} P{rel_off:<3d} {len(vals):>3d} {pct2:>11.1f}% {pct3:>11.1f}%")

    print(f"{'role':25s} {'rel':4s} {'n':>3s} {'med_ret':>8s} {'med_dd':>8s} {'med_retain':>10s} "
          f"{'med_ex3d':>9s} {'AT_RISK+%':>10s} {'FAILED%':>8s} {'HEALTHY/CAUTION%':>17s} {'REACCEL%':>9s}")
    for role in ("ROUND_TRIP_FAILURE", "NORMAL_PULLBACK_WINNER"):
        for rel in ("P0", "P1", "P2", "P3"):
            rows = by_rel.get((role, rel), [])
            if not rows:
                continue
            rets = [r["current_return"] for r in rows]
            dds = [r["drawdown_from_max"] for r in rows]
            retains = [r["profit_retention_ratio"] for r in rows if r["profit_retention_ratio"] is not None]
            ex3d = [r["excess_return_vs_market_3d"] for r in rows if r["excess_return_vs_market_3d"] is not None]
            atrisk_pct = 100 * sum(1 for r in rows if r["continuation_quality_state"] in RISK_STATES) / len(rows)
            failed_pct = 100 * sum(1 for r in rows if r["continuation_quality_state"] == "FAILED") / len(rows)
            healthy_pct = 100 * sum(1 for r in rows if r["continuation_quality_state"] in ("HEALTHY", "CAUTION")) / len(rows)
            reaccel_pct = 100 * sum(1 for r in rows if r["tracking_state"] == "REACCELERATING") / len(rows)
            print(f"{role:25s} {rel:4s} {len(rows):>3d} {statistics.median(rets):>8.2f} "
                  f"{statistics.median(dds):>8.2f} "
                  f"{(statistics.median(retains) if retains else float('nan')):>10.2f} "
                  f"{(statistics.median(ex3d) if ex3d else float('nan')):>9.2f} "
                  f"{atrisk_pct:>10.1f} {failed_pct:>8.1f} {healthy_pct:>17.1f} {reaccel_pct:>9.1f}")

    # ---- False Exit Risk table ----
    print("\n=== False Exit Risk Table（post-peak conditions）===")
    print(f"{'condition':30s} {'RoundTrip Capture':>18s} {'NPW(matched) False-Exit':>25s} {'NPW(all) False-Exit':>22s}")
    for label, cond in (
        ("ever AT_RISK", lambda r: r["ever_at_risk_plus"]),
        ("AT_RISK persistence >=2", lambda r: r["at_risk_plus_persistence"] >= 2),
        ("AT_RISK persistence >=3", lambda r: r["at_risk_plus_persistence"] >= 3),
        ("ever FAILED", lambda r: r["ever_failed"]),
        ("FAILED persistence >=2", lambda r: r["failed_persistence"] >= 2),
        ("FAILED persistence >=3", lambda r: r["failed_persistence"] >= 3),
    ):
        _, _, cap = group_pct(rt_persist, cond)
        _, _, fe_matched = group_pct(npw_persist, cond)
        _, _, fe_all = group_pct(npw_all_persist, cond)
        print(f"{label:30s} {cap:>17.1f}% {fe_matched:>24.1f}% {fe_all:>21.1f}%")

    # ---- residual profit / timing at first FAILED persistence>=2 (RT only) ----
    print("\n=== ROUND_TRIP_FAILURE: 訊號形成時的殘餘獲利 ===")
    for sid in rt_ids:
        traj = by_stock[sid]
        peak_idx = find_peak_index(traj)
        post_states = [r["continuation_quality_state"] for r in traj[peak_idx:]]
        trigger_idx = None
        streak = 0
        for i, s in enumerate(post_states):
            if s in RISK_STATES:
                streak += 1
            else:
                streak = 0
            if streak >= 2:
                trigger_idx = i
                break
        if trigger_idx is not None:
            r = traj[peak_idx + trigger_idx]
            print(f"{sid}: peak_return={traj[peak_idx]['current_return']:.2f}% "
                  f"trigger_at=day{r['day_offset']} return={r['current_return']:.2f}% "
                  f"drawdown_from_peak={r['drawdown_from_max']:.2f}%")
        else:
            print(f"{sid}: AT_RISK+ persistence>=2 從未觸發")

    print(f"\n報告將寫入 -> {OUT_REPORT}")


if __name__ == "__main__":
    main()
