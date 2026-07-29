"""Phase 3E Step 1: Consecutive 30-Day Reconstruction（2026-07-27）。

**純研究**，不修改任何 production 程式碼；`top120` 僅重建 P1 前歷史 policy、A/B/C/D
threshold、momentum_score、Hard Exclusion、Phase 2、Phase 2.5、Role、LLM、
confidence、Market Regime。

對 2026-05-28~2026-07-09（**連續 30 個交易日**，非稀疏抽樣——連續性是
First-Seen Episode 判定正確性的必要條件）逐日重建：
    - 完整 momentum_frame（全市場，用於 first-seen episode 的 Day0 feature
      與 lookback path）
    - raw candidate union + momentum_score rank
    - P1 前歷史 Top120 comparator
    - A/B/C/D 通道命中狀態

用法：
    python analyze_phase3e_daily_reconstruct.py
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any, Dict, List, Set

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import DATABASE_URL
from app.signals import candidate_pool, market_regime, momentum
from app.signals.exclusions import find_group_for_stock, get_group_members

CHECKPOINT_PATH = "/tmp/phase3e_daily_checkpoint.json"
FRAME_DIR = "/tmp/phase3e_frames"

ALL_DATES = [
    "2026-05-28", "2026-05-29", "2026-06-01", "2026-06-02", "2026-06-03",
    "2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10",
    "2026-06-11", "2026-06-12", "2026-06-15", "2026-06-16", "2026-06-17",
    "2026-06-18", "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25",
    "2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02",
    "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09",
    # Phase 3E-v2（Downtrend Stress Audit）新增：07-10~07-24 Pending Forward window
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24",
]

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    connect_args={"connect_timeout": 15, "options": "-c statement_timeout=100000"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def main() -> None:
    import os
    os.makedirs(FRAME_DIR, exist_ok=True)

    daily_summary: List[Dict[str, Any]] = []
    done_dates: Set[str] = set()
    try:
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            ckpt = json.load(f)
        daily_summary = ckpt["daily_summary"]
        done_dates = set(ckpt["done_dates"])
        print(f"resume: {len(done_dates)} dates done")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    db = SessionLocal()
    try:
        for di, ds in enumerate(ALL_DATES, start=1):
            if ds in done_dates:
                continue
            target_date = date.fromisoformat(ds)

            for attempt in range(3):
                try:
                    ingestion = candidate_pool.ingest_data(db, target_date)
                    masters = ingestion.get("stocks_master") or {}
                    if not masters:
                        break
                    rankings = candidate_pool.compute_rankings(db, target_date, ingestion)
                    regime_info = market_regime.compute_market_regime(db, target_date)
                    frame = momentum.compute_market_momentum_frame(db, target_date, masters)
                    frame_clean = {sid: {k: v for k, v in feats.items() if not k.startswith("_")} for sid, feats in frame.items()}

                    full_pool = candidate_pool.build_candidate_pool(db, target_date, ingestion, rankings, momentum_frame=frame)

                    # Historical comparator only: production P1 no longer truncates to 120.
                    top120_pool = full_pool[:120]
                    top120_ids = {c["stock_id"] for c in top120_pool}

                    top_industries_names = {ind["industry_name"] for ind in rankings.get("top_industries_3d") or []}
                    top_stocks = rankings.get("top_stocks_3d") or []
                    top_stock_ids = {s["stock_id"] for s in top_stocks}
                    group_expansion_ids: Set[str] = set()
                    for s in top_stocks[: candidate_pool.TOP_STOCKS_INNER]:
                        gn = find_group_for_stock(s["stock_id"])
                        if gn:
                            group_expansion_ids |= set(get_group_members(gn))
                    channels = momentum.select_momentum_candidates(frame)
                    b_ids = set(channels.get("price_momentum") or [])
                    c_ids = set(channels.get("acceleration") or [])
                    d_ids = set(channels.get("fundamental") or [])

                    day_rows = []
                    for rank, c in enumerate(full_pool, start=1):
                        sid = c["stock_id"]
                        day_rows.append({
                            "stock_id": sid, "momentum_score": c.get("momentum_score"),
                            "raw_union_rank": rank, "raw_union_size": len(full_pool),
                            "top120": sid in top120_ids,
                            "source_A": bool(sid in top_stock_ids or (masters.get(sid) and masters[sid].industry_name in top_industries_names) or sid in group_expansion_ids),
                            "source_B": sid in b_ids, "source_C": sid in c_ids, "source_D": sid in d_ids,
                        })

                    with open(f"{FRAME_DIR}/{ds}_frame.json", "w", encoding="utf-8") as f:
                        json.dump(frame_clean, f, default=str)
                    with open(f"{FRAME_DIR}/{ds}_pool.json", "w", encoding="utf-8") as f:
                        json.dump(day_rows, f, default=str)

                    daily_summary.append({
                        "date": ds, "regime": regime_info.get("regime"),
                        "raw_union_size": len(full_pool), "top120_size": len(top120_ids),
                    })
                    break
                except OperationalError as e:
                    print(f"  [retry {attempt+1}/3] DB error on {ds}: {type(e).__name__}", flush=True)
                    try:
                        db.close()
                    except Exception:
                        pass
                    engine.dispose()
                    time.sleep(10 * (attempt + 1))
                    db = SessionLocal()
            else:
                print(f"  [SKIP] {ds} failed after 3 retries", flush=True)
                continue

            done_dates.add(ds)
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump({"daily_summary": daily_summary, "done_dates": sorted(done_dates)}, f, default=str)
            print(f"  [{di}/{len(ALL_DATES)}] {ds} done regime={daily_summary[-1]['regime']} "
                  f"raw_union={daily_summary[-1]['raw_union_size']} top120={daily_summary[-1]['top120_size']}", flush=True)
            time.sleep(1.0)
    finally:
        db.close()

    print(f"\nall {len(daily_summary)} days done. Frames saved in {FRAME_DIR}/")


if __name__ == "__main__":
    main()
