"""
Phase 2 shadow replay 報酬率分析（2026-07-21）。

對 `signal_shadow_snapshots` 裡每一天的 legacy / Phase 2 存活名單，用「命中當天
收盤價 → 基準日收盤價」算簡單報酬率，並對照 production 真實魚尾（
`signal_watch_hits` / `signal_watch_completed_archives`）當天是否也真的抓到、
抓到的話 production 自己追蹤的報酬率是多少。

**注意**：這裡的「legacy/phase2 存活」是 deterministic filter 之後、**LLM WATCH/
REMOVE 決策之前**的名單（replay 不呼叫 LLM，見 run_phase2_replay.py docstring）。
production 的 `signal_watch_hits` 則是 LLM 決策 WATCH 之後才會有的紀錄，兩者不是
同一個集合——這裡的比較重點是：
    1. Phase 2 存活但 legacy 沒有的股票（Phase 2 新增的候選）之後表現如何
    2. 這些股票裡有多少從來沒被 production 的真實魚尾抓過（完全的新面孔）

用法：
    python analyze_phase2_replay_returns.py --baseline-date 2026-07-20
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text

from app.database import SessionLocal
from app.models import SignalShadowSnapshot, SignalWatchCompletedArchive, SignalWatchHit


def _fetch_shadow_records(
    db,
    pipeline_version: str = "phase2-v1",
    date_from: Optional[date] = None,
) -> List[Dict[str, Any]]:
    query = db.query(SignalShadowSnapshot).filter(SignalShadowSnapshot.pipeline_version == pipeline_version)
    if date_from is not None:
        query = query.filter(SignalShadowSnapshot.snapshot_date >= date_from)
    rows = query.order_by(SignalShadowSnapshot.snapshot_date).all()
    records = []
    for r in rows:
        cs = r.comparison_summary or {}
        legacy_ids = set(cs.get("legacy_survivor_ids") or [])
        phase2_ids = set(cs.get("phase2_survivor_ids") or [])
        for sid in legacy_ids | phase2_ids:
            records.append({
                "snapshot_date": r.snapshot_date,
                "stock_id": sid,
                "legacy_pick": sid in legacy_ids,
                "phase2_pick": sid in phase2_ids,
            })
    return records


def _fetch_close_prices(db, stock_ids: List[str], trade_dates: List[date]) -> Dict[tuple, float]:
    if not stock_ids or not trade_dates:
        return {}
    rows = db.execute(
        text(
            """
            SELECT stock_id, trade_date, close_price
            FROM daily_price
            WHERE stock_id IN :ids AND trade_date IN :dates
            """
        ).bindparams(bindparam("ids", expanding=True), bindparam("dates", expanding=True)),
        {"ids": stock_ids, "dates": trade_dates},
    ).fetchall()
    out = {}
    for r in rows:
        td = r.trade_date if isinstance(r.trade_date, date) else date.fromisoformat(str(r.trade_date)[:10])
        out[(r.stock_id, td)] = r.close_price
    return out


def _fetch_stock_names(db, stock_ids: List[str]) -> Dict[str, str]:
    if not stock_ids:
        return {}
    rows = db.execute(
        text("SELECT stock_id, stock_name FROM stocks_master WHERE stock_id IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": stock_ids},
    ).fetchall()
    return {r.stock_id: r.stock_name for r in rows}


def _fetch_production_hits(db, stock_ids: List[str]) -> Dict[tuple, SignalWatchHit]:
    """production 真實魚尾的 (stock_id, snapshot_date) -> hit row（目前仍在追蹤中的）。"""
    if not stock_ids:
        return {}
    rows = (
        db.query(SignalWatchHit)
        .filter(SignalWatchHit.stock_id.in_(stock_ids))
        .all()
    )
    return {(r.stock_id, r.snapshot_date): r for r in rows}


def _fetch_production_ever_caught(db, stock_ids: List[str]) -> set:
    """production 是否「曾經」抓過這檔股票（含已完成封存的 cycle），用來判斷
    Phase 2 新增的候選是不是 production 完全沒見過的新面孔。"""
    if not stock_ids:
        return set()
    active = {r[0] for r in db.query(SignalWatchHit.stock_id).filter(SignalWatchHit.stock_id.in_(stock_ids)).distinct().all()}
    archived = {
        r[0] for r in db.query(SignalWatchCompletedArchive.stock_id)
        .filter(SignalWatchCompletedArchive.stock_id.in_(stock_ids)).distinct().all()
    }
    return active | archived


def run_analysis(baseline_date: date, output_csv: str, date_from: Optional[date] = None) -> None:
    db = SessionLocal()
    try:
        records = _fetch_shadow_records(db, date_from=date_from)
        if not records:
            print("沒有找到任何 signal_shadow_snapshots 資料，請先跑 run_phase2_replay.py --persist")
            return

        stock_ids = sorted({r["stock_id"] for r in records})
        trade_dates = sorted({r["snapshot_date"] for r in records} | {baseline_date})
        price_map = _fetch_close_prices(db, stock_ids, trade_dates)
        name_map = _fetch_stock_names(db, stock_ids)
        production_hits = _fetch_production_hits(db, stock_ids)
        production_ever_caught = _fetch_production_ever_caught(db, stock_ids)

        for rec in records:
            sid = rec["stock_id"]
            snap_date = rec["snapshot_date"]
            catch_price = price_map.get((sid, snap_date))
            baseline_price = price_map.get((sid, baseline_date))
            rec["stock_name"] = name_map.get(sid, "")
            rec["catch_price"] = catch_price
            rec["baseline_price"] = baseline_price
            if catch_price and baseline_price and catch_price > 0:
                rec["return_pct"] = round((baseline_price / catch_price - 1.0) * 100.0, 2)
            else:
                rec["return_pct"] = None

            prod_hit = production_hits.get((sid, snap_date))
            rec["production_caught_same_day"] = prod_hit is not None
            rec["production_signal_type"] = prod_hit.signal_type if prod_hit else None
            rec["production_return_pct"] = prod_hit.return_pct if prod_hit else None
            rec["production_ever_caught"] = sid in production_ever_caught

        # ---- 輸出 CSV（逐筆） ----
        with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "snapshot_date", "stock_id", "stock_name", "legacy_pick", "phase2_pick",
                "phase2_only", "catch_price", f"baseline_price({baseline_date})", "return_pct",
                "production_caught_same_day", "production_signal_type", "production_return_pct",
                "production_ever_caught_this_stock",
            ])
            for rec in records:
                w.writerow([
                    rec["snapshot_date"], rec["stock_id"], rec["stock_name"],
                    rec["legacy_pick"], rec["phase2_pick"],
                    rec["phase2_pick"] and not rec["legacy_pick"],
                    rec["catch_price"], rec["baseline_price"], rec["return_pct"],
                    rec["production_caught_same_day"], rec["production_signal_type"],
                    rec["production_return_pct"], rec["production_ever_caught"],
                ])
        print(f"逐筆明細已寫入 {output_csv}（{len(records)} 筆）")

        # ---- 彙總統計 ----
        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        legacy_returns = [r["return_pct"] for r in records if r["legacy_pick"]]
        phase2_returns = [r["return_pct"] for r in records if r["phase2_pick"]]
        phase2_only_returns = [r["return_pct"] for r in records if r["phase2_pick"] and not r["legacy_pick"]]
        phase2_only_new_face_returns = [
            r["return_pct"] for r in records
            if r["phase2_pick"] and not r["legacy_pick"] and not r["production_ever_caught"]
        ]

        print("\n=== 彙總統計（基準日：%s） ===" % baseline_date)
        print(f"Legacy 存活總筆數：{len(legacy_returns)}，平均報酬率：{_avg(legacy_returns)}%")
        print(f"Phase2 存活總筆數：{len(phase2_returns)}，平均報酬率：{_avg(phase2_returns)}%")
        print(f"Phase2 新增（legacy 沒有）總筆數：{len(phase2_only_returns)}，平均報酬率：{_avg(phase2_only_returns)}%")
        print(
            f"Phase2 新增且 production 從未抓過的「全新面孔」總筆數："
            f"{len(phase2_only_new_face_returns)}，平均報酬率：{_avg(phase2_only_new_face_returns)}%"
        )

        n_no_price = sum(1 for r in records if r["return_pct"] is None)
        if n_no_price:
            print(f"\n（{n_no_price} 筆缺收盤價資料，無法計算報酬率，通常是 ETF/特別股/當日停牌）")

        # ---- 對照 production 現在真實魚尾的追蹤結果 ----
        prod_caught = [r for r in records if r["production_caught_same_day"]]
        prod_returns = [r["production_return_pct"] for r in prod_caught]
        replay_returns_for_same_rows = [r["return_pct"] for r in prod_caught]
        print("\n=== 對照 production 真實魚尾（同一天真的有抓到的那些股票） ===")
        print(f"同時被 replay 標記、且 production 當天也真的抓到的筆數：{len(prod_caught)}")
        print(f"  這些股票 production 自己追蹤的平均報酬率：{_avg(prod_returns)}%")
        print(f"  這些股票用『命中日收盤→{baseline_date}收盤』簡單算法的平均報酬率：{_avg(replay_returns_for_same_rows)}%")
        print("  （兩者算法不同：production 用 baseline_trade_date 逐日追蹤；這裡是單純頭尾收盤價比較，數字有落差是正常的）")

        # ---- 逐日明細 ----
        by_date: Dict[Any, Dict[str, list]] = {}
        for r in records:
            d = r["snapshot_date"]
            by_date.setdefault(d, {"legacy": [], "phase2": []})
            if r["legacy_pick"]:
                by_date[d]["legacy"].append(r["return_pct"])
            if r["phase2_pick"]:
                by_date[d]["phase2"].append(r["return_pct"])

        print("\n=== 逐日明細 ===")
        print(f"{'日期':<12} {'Legacy筆數':>10} {'Legacy平均':>12} {'Phase2筆數':>10} {'Phase2平均':>12}")
        for d in sorted(by_date.keys()):
            leg = by_date[d]["legacy"]
            ph2 = by_date[d]["phase2"]
            print(f"{str(d):<12} {len(leg):>10} {str(_avg(leg)) + '%':>12} {len(ph2):>10} {str(_avg(ph2)) + '%':>12}")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-date", type=str, required=True, help="YYYY-MM-DD，比較報酬率的基準日")
    parser.add_argument("--output", type=str, default="/tmp/phase2_replay_returns.csv")
    parser.add_argument("--date-from", type=str, default=None, help="YYYY-MM-DD，只分析這天（含）之後的 snapshot")
    args = parser.parse_args()
    baseline_date = datetime.strptime(args.baseline_date, "%Y-%m-%d").date()
    date_from = datetime.strptime(args.date_from, "%Y-%m-%d").date() if args.date_from else None
    run_analysis(baseline_date, args.output, date_from=date_from)


if __name__ == "__main__":
    main()
