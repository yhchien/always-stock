"""Phase 3B: Candidate Admission Path Audit（2026-07-24）。

**純研究**，不修改任何 production 程式碼、Candidate Selection threshold、A/B/C/D
定義、Phase 2、Phase 2.5、Hard Exclusion、LLM、momentum_score。不新增 Admission
Score / 技術指標 / 法人模型 / Exit Rule。不做 Portfolio Backtest / threshold search。

核心問題：今天魚尾抓進來的股票，從「它為什麼會被抓進來」這件事本身，能不能
找到一群「大量增加候選數量，卻很少增加真正 Winner，反而帶進較多 Big Loser」
的結構性雜訊來源？

只重建 Candidate Admission Source（A=法人資金/熱錢+產業成分股+集團擴散、
B=in_price_momentum_pool、C=in_acceleration_pool、D=in_fundamental_pool），
不重跑 Phase 2 Role / Hard Exclusion / Phase 2.5 / LLM / Continuation。

B/C/D 直接沿用既有 replay JSON 已存的 pool flag（本來就是 Day0 deterministic
產物，無需重算）；A 需要對每個唯一 catch_date 重跑 candidate_pool.py 的
ingest_data + compute_rankings（純 ranking 重建，非全 pipeline），取得
top_industries_3d / top_stocks_3d，再比對每檔股票的產業別 + 集團成員關係。

用法：
    python analyze_phase3b_admission_path.py
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

import time

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import DATABASE_URL
from app.signals import candidate_pool
from app.signals.exclusions import find_group_for_stock, get_group_members

CHECKPOINT_PATH = "/tmp/phase3b_checkpoint.json"

# 本研究腳本專用的獨立 engine（不共用 app.database 的共用 pool，不修改 production
# 程式碼）：加 pool_pre_ping（checkout 前先探測連線是否存活，死掉自動換新的，
# 取代原本手動 dispose+retry 的機制）+ statement_timeout（避免連線卡死不返回、
# 之前遇到的「無錯誤訊息但整個 process 掛住」正是缺這個保護）。
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=180,
    connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"

OUT_ALL_CSV = "/tmp/phase3b_candidate_admission_all.csv"
OUT_COMBO_CSV = "/tmp/phase3b_source_combination_summary.csv"
OUT_SINGLE_MULTI_CSV = "/tmp/phase3b_single_vs_multi_source.csv"
OUT_DAILY_CSV = "/tmp/phase3b_daily_cohort_summary.csv"
OUT_REMOVAL_CSV = "/tmp/phase3b_removal_simulation.csv"
OUT_REPORT = "/Users/brian.yh.chien/.gstack/projects/always-stock/docs/plans/phase3b_candidate_admission_report.md"

MIN_SAMPLE_FOR_STRONG_CONCLUSION = 15


def load_cohort_all_catches() -> List[Dict[str, Any]]:
    """本輪需要「這檔股票這一天被抓到」的每一筆紀錄，而非 dedup 到最早一次
    ——但 outcome/labels 仍用同一批 617 dedup（避免同檔股票在分析中被算多次、
    製造 incumbency bias）。因此：先 dedup 到每檔股票最早 catch_date（與其餘
    Phase 2.6~3A 一致的研究母體定義），再對這批唯一 (stock_id, catch_date) 重建
    admission source。"""
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return list(first_seen.values())


def outcome_group(forward_return_pct: float) -> str:
    if forward_return_pct >= 10.0:
        return "WINNER"
    if forward_return_pct <= -10.0:
        return "BIG_LOSER"
    return "NEUTRAL"


def main() -> None:
    cohort_records = load_cohort_all_catches()
    print(f"cohort size (617 dedup, 這是母體): {len(cohort_records)}")

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for r in cohort_records:
        by_date.setdefault(r["catch_date"], []).append(r)
    unique_dates = sorted(by_date.keys())
    print(f"unique catch_date 數量: {len(unique_dates)}")

    all_rows: List[Dict[str, Any]] = []
    done_dates: Set[str] = set()
    try:
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            ckpt = json.load(f)
        all_rows = ckpt["rows"]
        done_dates = set(ckpt["done_dates"])
        print(f"resume from checkpoint: {len(done_dates)} dates already done, {len(all_rows)} rows loaded")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    db = SessionLocal()
    try:
        for di, catch_date_str in enumerate(unique_dates, start=1):
            if catch_date_str in done_dates:
                continue
            target_date = date.fromisoformat(catch_date_str)

            for attempt in range(3):
                try:
                    ingestion = candidate_pool.ingest_data(db, target_date)
                    masters = ingestion.get("stocks_master") or {}
                    if not masters:
                        for rec in by_date[catch_date_str]:
                            all_rows.append(_build_row(rec, source_a=None, note="no_masters_data"))
                        break
                    rankings = candidate_pool.compute_rankings(db, target_date, ingestion)
                    top_industries_names: Set[str] = {ind["industry_name"] for ind in rankings.get("top_industries_3d") or []}
                    top_stocks = rankings.get("top_stocks_3d") or []
                    top_stock_ids: Set[str] = {s["stock_id"] for s in top_stocks}

                    group_expansion_ids: Set[str] = set()
                    for s in top_stocks[: candidate_pool.TOP_STOCKS_INNER]:
                        group_name = find_group_for_stock(s["stock_id"])
                        if not group_name:
                            continue
                        group_expansion_ids |= set(get_group_members(group_name))

                    for rec in by_date[catch_date_str]:
                        sid = rec["stock_id"]
                        master = masters.get(sid)
                        industry_name = master.industry_name if master else None
                        source_a = bool(
                            sid in top_stock_ids
                            or (industry_name is not None and industry_name in top_industries_names)
                            or sid in group_expansion_ids
                        )
                        all_rows.append(_build_row(rec, source_a=source_a, note=""))
                    break
                except OperationalError as e:
                    print(f"  [retry {attempt+1}/3] DB error on {catch_date_str}: {type(e).__name__}", flush=True)
                    try:
                        db.close()
                    except Exception:
                        pass
                    # root cause: app.database engine 沒有 pool_pre_ping，Render Postgres
                    # 關閉閒置連線後，pool 會一直吐出同一條壞掉的連線；engine.dispose()
                    # 強制丟棄整個 pool，下次 checkout 保證是全新實體連線（只在本研究腳本
                    # 內處理，不改動 production app/database.py）
                    engine.dispose()
                    time.sleep(3)
                    db = SessionLocal()
            else:
                print(f"  [SKIP] {catch_date_str} failed after 3 retries — 不標記為完成，下次重跑會重試", flush=True)
                continue  # 不加入 done_dates，才能在 DB 恢復正常後被重跑重試

            done_dates.add(catch_date_str)
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump({"rows": all_rows, "done_dates": sorted(done_dates)}, f, default=str)

            if di % 10 == 0:
                print(f"  processed {di}/{len(unique_dates)} unique catch dates", flush=True)
    finally:
        db.close()

    print(f"\ntotal admission rows: {len(all_rows)}")
    n_no_a_data = sum(1 for r in all_rows if r["source_A"] is None)
    print(f"source_A 無法判斷（無 stocks_master 資料）: {n_no_a_data}")

    # 無法判斷 source_A 的股票排除於主分析（誠實揭露，不猜測）
    usable_rows = [r for r in all_rows if r["source_A"] is not None]
    print(f"主分析可用樣本: {len(usable_rows)}")

    columns = list(all_rows[0].keys())
    with open(OUT_ALL_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"wrote {len(all_rows)} rows -> {OUT_ALL_CSV}")
    with open("/tmp/phase3b_all_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2, default=str)

    run_analysis(usable_rows)


def _build_row(rec: Dict[str, Any], source_a: Optional[bool], note: str) -> Dict[str, Any]:
    forward_return = rec["forward_return_pct"]
    source_b = bool(rec.get("in_price_momentum_pool"))
    source_c = bool(rec.get("in_acceleration_pool"))
    source_d = bool(rec.get("in_fundamental_pool"))
    combo = ""
    count = 0
    if source_a:
        combo += "A"
        count += 1
    if source_b:
        combo += "B"
        count += 1
    if source_c:
        combo += "C"
        count += 1
    if source_d:
        combo += "D"
        count += 1
    risk_warnings = rec.get("risk_warnings") or []
    return {
        "stock_id": rec["stock_id"],
        "first_seen_date": rec["catch_date"],
        "source_A": source_a,
        "source_B": source_b,
        "source_C": source_c,
        "source_D": source_d,
        "source_count": count,
        "source_combination": combo if combo else "NONE",
        "candidate_primary_reason": rec.get("role"),
        "forward_return_10d": round(forward_return, 2),
        "outcome_group": outcome_group(forward_return),
        "extended_3d": "EXTENDED_3D" in risk_warnings,
        "note": note,
    }


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    n_winner = sum(1 for r in rows if r["outcome_group"] == "WINNER")
    n_neutral = sum(1 for r in rows if r["outcome_group"] == "NEUTRAL")
    n_loser = sum(1 for r in rows if r["outcome_group"] == "BIG_LOSER")
    rets = [r["forward_return_10d"] for r in rows]
    return {
        "n": n,
        "winner_n": n_winner,
        "winner_rate": round(100 * n_winner / n, 1),
        "neutral_n": n_neutral,
        "neutral_rate": round(100 * n_neutral / n, 1),
        "big_loser_n": n_loser,
        "big_loser_rate": round(100 * n_loser / n, 1),
        "mean_return": round(statistics.mean(rets), 2),
        "median_return": round(statistics.median(rets), 2),
    }


def run_analysis(rows: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 78)
    print("=== §1: A/B/C/D 各自帶進多少股票 ===")
    print("=" * 78)
    for src in ("A", "B", "C", "D"):
        n = sum(1 for r in rows if r[f"source_{src}"])
        print(f"source_{src}: {n}/{len(rows)} ({100*n/len(rows):.1f}%)")

    print("\n" + "=" * 78)
    print("=== §7: Source Combination Outcome Table ===")
    print("=" * 78)
    by_combo: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_combo.setdefault(r["source_combination"], []).append(r)

    combo_summary = []
    baseline = _stats(rows)
    print(f"COHORT BASELINE: n={baseline['n']} winner={baseline['winner_rate']}% "
          f"neutral={baseline['neutral_rate']}% big_loser={baseline['big_loser_rate']}% "
          f"mean_ret={baseline['mean_return']}%")
    print(f"\n{'combo':8s} {'n':>4s} {'Winner%':>8s} {'Neutral%':>9s} {'BigLoser%':>10s} {'mean10d':>8s} {'median10d':>9s} {'note':>6s}")
    for combo in sorted(by_combo.keys(), key=lambda c: (len(c), c)):
        s = _stats(by_combo[combo])
        note = "n<15" if s["n"] < MIN_SAMPLE_FOR_STRONG_CONCLUSION else ""
        print(f"{combo:8s} {s['n']:>4d} {s['winner_rate']:>8.1f} {s['neutral_rate']:>9.1f} "
              f"{s['big_loser_rate']:>10.1f} {s['mean_return']:>8.2f} {s['median_return']:>9.2f} {note:>6s}")
        combo_summary.append({"source_combination": combo, **s, "sample_note": note})

    with open(OUT_COMBO_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cols = list(combo_summary[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in combo_summary:
            w.writerow(r)
    print(f"wrote -> {OUT_COMBO_CSV}")

    print("\n" + "=" * 78)
    print("=== §8: Single-source vs Multi-source ===")
    print("=" * 78)
    sm_summary = []
    for n_source in (1, 2, 3, 4):
        grp = [r for r in rows if r["source_count"] == n_source]
        if not grp:
            continue
        s = _stats(grp)
        print(f"{n_source} SOURCE(S): n={s['n']} winner={s['winner_rate']}% neutral={s['neutral_rate']}% "
              f"big_loser={s['big_loser_rate']}% mean={s['mean_return']}% median={s['median_return']}%")
        sm_summary.append({"source_count": n_source, **s})
    with open(OUT_SINGLE_MULTI_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cols = list(sm_summary[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in sm_summary:
            w.writerow(r)
    print(f"wrote -> {OUT_SINGLE_MULTI_CSV}")

    print("\n" + "=" * 78)
    print("=== §10: Single-source Noise (A-only/B-only/C-only/D-only) ===")
    print("=" * 78)
    for src in ("A", "B", "C", "D"):
        grp = by_combo.get(src, [])
        s = _stats(grp)
        if s["n"] == 0:
            print(f"{src}-only: n=0")
            continue
        pct_of_all = 100 * s["n"] / len(rows)
        note = "n<15" if s["n"] < MIN_SAMPLE_FOR_STRONG_CONCLUSION else ""
        print(f"{src}-only: n={s['n']} ({pct_of_all:.1f}% of cohort) winner={s['winner_rate']}% "
              f"neutral={s['neutral_rate']}% big_loser={s['big_loser_rate']}% mean={s['mean_return']}% {note}")

    print("\n" + "=" * 78)
    print("=== §11: Source Addition Value（descriptive，非因果）===")
    print("=" * 78)
    for base_src in ("A", "B", "C", "D"):
        base_only = by_combo.get(base_src, [])
        base_stat = _stats(base_only)
        print(f"\n-- {base_src}-only baseline: n={base_stat.get('n',0)} winner={base_stat.get('winner_rate','-')}% "
              f"big_loser={base_stat.get('big_loser_rate','-')}% --")
        for combo, grp in sorted(by_combo.items()):
            if combo == base_src or base_src not in combo or len(combo) <= 1:
                continue
            s = _stats(grp)
            note = "n<15" if s["n"] < MIN_SAMPLE_FOR_STRONG_CONCLUSION else ""
            print(f"  +{combo}: n={s['n']} winner={s['winner_rate']}% big_loser={s['big_loser_rate']}% "
                  f"mean={s['mean_return']}% {note}")

    print("\n" + "=" * 78)
    print("=== §12: Source Combination x EXTENDED_3D ===")
    print("=" * 78)
    for combo in sorted(by_combo.keys(), key=lambda c: (len(c), c)):
        grp = by_combo[combo]
        ext = [r for r in grp if r["extended_3d"]]
        non_ext = [r for r in grp if not r["extended_3d"]]
        s_ext, s_non = _stats(ext), _stats(non_ext)
        if s_ext.get("n", 0) < 5 and s_non.get("n", 0) < 5:
            continue
        print(f"{combo:8s} EXTENDED    n={s_ext.get('n',0):>3d} winner={s_ext.get('winner_rate','-')!s:>6s} "
              f"big_loser={s_ext.get('big_loser_rate','-')!s:>6s}")
        print(f"{combo:8s} non-EXTEND  n={s_non.get('n',0):>3d} winner={s_non.get('winner_rate','-')!s:>6s} "
              f"big_loser={s_non.get('big_loser_rate','-')!s:>6s}")

    print("\n" + "=" * 78)
    print("=== §15: Noise Efficiency Metric ===")
    print("=" * 78)
    print(f"{'combo':8s} {'n':>4s} {'winners':>7s} {'big_losers':>10s} {'cand/winner':>11s} {'bigloser/winner':>15s}")
    for combo in sorted(by_combo.keys(), key=lambda c: (len(c), c)):
        grp = by_combo[combo]
        s = _stats(grp)
        cand_per_winner = round(s["n"] / s["winner_n"], 2) if s["winner_n"] else None
        loser_per_winner = round(s["big_loser_n"] / s["winner_n"], 2) if s["winner_n"] else None
        note = " (n<15)" if s["n"] < MIN_SAMPLE_FOR_STRONG_CONCLUSION else ""
        print(f"{combo:8s} {s['n']:>4d} {s['winner_n']:>7d} {s['big_loser_n']:>10d} "
              f"{str(cand_per_winner):>11s} {str(loser_per_winner):>15s}{note}")

    # ---- §19 daily cohort ----
    print("\n" + "=" * 78)
    print("=== §19: Daily Cohort Summary（節錄，非全部列出）===")
    print("=" * 78)
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["first_seen_date"], []).append(r)
    daily_summary = []
    for day in sorted(by_day.keys()):
        grp = by_day[day]
        s = _stats(grp)
        combo_counts: Dict[str, int] = {}
        for r in grp:
            combo_counts[r["source_combination"]] = combo_counts.get(r["source_combination"], 0) + 1
        daily_summary.append({"first_seen_date": day, **s, "combo_counts": json.dumps(combo_counts)})
    with open(OUT_DAILY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cols = list(daily_summary[0].keys())
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in daily_summary:
            w.writerow(r)
    print(f"wrote {len(daily_summary)} daily rows -> {OUT_DAILY_CSV}")
    # 找 C-only 集中度最高的幾天（示範揪出「是否集中在少數日期」）
    for combo_check in ("C", "A"):
        day_counts = [(d["first_seen_date"], json.loads(d["combo_counts"]).get(combo_check, 0)) for d in daily_summary]
        day_counts.sort(key=lambda x: -x[1])
        print(f"\n{combo_check}-only 每日數量最高的 5 天: {day_counts[:5]}")

    # ---- §20 time robustness ----
    print("\n" + "=" * 78)
    print("=== §20: Time Robustness (前半 vs 後半) ===")
    print("=" * 78)
    sorted_rows = sorted(rows, key=lambda r: r["first_seen_date"])
    half = len(sorted_rows) // 2
    first_half, second_half = sorted_rows[:half], sorted_rows[half:]
    for combo in sorted(by_combo.keys(), key=lambda c: (len(c), c)):
        fh = [r for r in first_half if r["source_combination"] == combo]
        sh = [r for r in second_half if r["source_combination"] == combo]
        if len(fh) < 8 and len(sh) < 8:
            continue
        sfh, ssh = _stats(fh), _stats(sh)
        print(f"{combo:8s} 前半 n={sfh.get('n',0):>3d} winner={sfh.get('winner_rate','-')!s:>6s} "
              f"big_loser={sfh.get('big_loser_rate','-')!s:>6s}  |  "
              f"後半 n={ssh.get('n',0):>3d} winner={ssh.get('winner_rate','-')!s:>6s} "
              f"big_loser={ssh.get('big_loser_rate','-')!s:>6s}")

    # ---- §16/17 removal simulation：只對明顯 noise candidate 做（C-only 候選數量大、Winner 低、BigLoser 高）----
    print("\n" + "=" * 78)
    print("=== §16/17: Removal Simulation（僅對疑似 Noise Group 執行）===")
    print("=" * 78)
    removal_summary = []
    total_winner = sum(1 for r in rows if r["outcome_group"] == "WINNER")
    total_loser = sum(1 for r in rows if r["outcome_group"] == "BIG_LOSER")
    total_n = len(rows)
    candidate_noise_groups = [c for c in by_combo if len(by_combo[c]) >= MIN_SAMPLE_FOR_STRONG_CONCLUSION]
    for combo in candidate_noise_groups:
        grp = by_combo[combo]
        s = _stats(grp)
        if s["winner_rate"] >= baseline["winner_rate"] * 0.7:
            continue  # 沒有明顯低於 baseline，不算 noise candidate
        remaining_n = total_n - s["n"]
        remaining_winner = total_winner - s["winner_n"]
        remaining_loser = total_loser - s["big_loser_n"]
        winner_retention = round(100 * remaining_winner / total_winner, 1) if total_winner else None
        loser_removal = round(100 * s["big_loser_n"] / total_loser, 1) if total_loser else None
        print(f"若移除 {combo}（n={s['n']}, winner_rate={s['winner_rate']}%, big_loser_rate={s['big_loser_rate']}%）：")
        print(f"  candidate: {total_n} -> {remaining_n} ({100*remaining_n/total_n:.1f}%)")
        print(f"  Winner: {total_winner} -> {remaining_winner}  Winner Retention = {winner_retention}%")
        print(f"  Big Loser: {total_loser} -> {remaining_loser}  Big Loser Removal = {loser_removal}%")
        removal_summary.append({
            "removed_combo": combo, "removed_n": s["n"], "removed_winner_rate": s["winner_rate"],
            "removed_big_loser_rate": s["big_loser_rate"], "remaining_candidates": remaining_n,
            "winner_retention_pct": winner_retention, "big_loser_removal_pct": loser_removal,
        })
    if not removal_summary:
        print("沒有找到 winner_rate 明顯低於 baseline 70% 的 source combination（n>=15），不做 removal simulation。")
    else:
        with open(OUT_REMOVAL_CSV, "w", newline="", encoding="utf-8-sig") as f:
            cols = list(removal_summary[0].keys())
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in removal_summary:
                w.writerow(r)
        print(f"wrote -> {OUT_REMOVAL_CSV}")

    print(f"\n報告將寫入 -> {OUT_REPORT}")


if __name__ == "__main__":
    main()
