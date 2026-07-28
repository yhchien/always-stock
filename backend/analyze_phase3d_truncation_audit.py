"""Phase 3D: Candidate Discovery Recall & Truncation Value Audit（2026-07-27）。

**純研究**，不修改任何 production 程式碼、Candidate Pool 120 上限、A/B/C/D
threshold、momentum_score、Hard Exclusion、Phase 2、Phase 2.5、Role、LLM、
confidence、Market Regime、Outcome threshold。

用途：對 ~20 天（6 天沿用 Phase 3C 已重建的 RISK_OFF momentum frame + 14 天
新選樣的 BULL_TREND/VOLATILE_RANGE）重建「686→120 截斷前後」完整候選聯集，
記錄每檔候選的 momentum_score 排名、A/B/C/D 通道命中狀態與門檻相關原始數值，
並自行計算 Day10 forward return（涵蓋全部原始聯集成員，非僅 production
survivors），供 Part B（截斷排序價值）與 Part C（Admission Near-Miss）分析。

用法：
    python analyze_phase3d_truncation_audit.py
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import DATABASE_URL
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice
from app.signals import candidate_pool, market_regime, momentum

FORWARD_TRADE_DAYS = 10
CHECKPOINT_PATH = "/tmp/phase3d_checkpoint.json"
CACHED_FRAMES_PATH = "/tmp/phase3c_riskoff_momentum_frames.json"  # 沿用 Phase 3C 已重建的 6 天

ADDITIONAL_DATES = [
    "2026-04-13", "2026-04-17", "2026-04-23", "2026-04-29", "2026-05-06",
    "2026-05-12", "2026-05-18", "2026-05-26", "2026-06-01", "2026-06-05",
    "2026-06-15", "2026-06-22", "2026-06-30", "2026-07-06",
]
CACHED_DATES = ["2026-05-19", "2026-05-20", "2026-06-10", "2026-06-11", "2026-06-26", "2026-06-29"]
ALL_DATES = sorted(CACHED_DATES + ADDITIONAL_DATES)

ANCHOR_END = date(2026, 7, 24)
CALENDAR_DAYS = 260

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    connect_args={"connect_timeout": 15, "options": "-c statement_timeout=100000"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def load_cached_frame(ds: str) -> Optional[Dict[str, Dict[str, Any]]]:
    try:
        cached = json.load(open(CACHED_FRAMES_PATH, encoding="utf-8"))
        return cached.get(ds)
    except FileNotFoundError:
        return None


def main() -> None:
    all_rows: List[Dict[str, Any]] = []
    done_dates: Set[str] = set()
    try:
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            ckpt = json.load(f)
        all_rows = ckpt["rows"]
        done_dates = set(ckpt["done_dates"])
        print(f"resume from checkpoint: {len(done_dates)} dates done, {len(all_rows)} rows")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    all_days = None
    db = SessionLocal()
    try:
        all_days = get_recent_trade_dates(db, ANCHOR_END, CALENDAR_DAYS)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")

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

                    cached_frame = load_cached_frame(ds) if ds in CACHED_DATES else None
                    if cached_frame is not None:
                        frame = cached_frame
                    else:
                        frame = momentum.compute_market_momentum_frame(db, target_date, masters)
                        frame = {sid: {k: v for k, v in feats.items() if not k.startswith("_")} for sid, feats in frame.items()}

                    # ---- 重建 raw union（不截斷）----
                    orig_soft = candidate_pool.POOL_SOFT_TRIGGER
                    candidate_pool.POOL_SOFT_TRIGGER = 999999
                    full_pool = candidate_pool.build_candidate_pool(db, target_date, ingestion, rankings, momentum_frame=frame)
                    candidate_pool.POOL_SOFT_TRIGGER = orig_soft
                    full_pool.sort(key=lambda c: (-(c.get("momentum_score") or 0.0), -(c.get("total_institution_flow_3d") or 0.0), str(c.get("stock_id") or "")))

                    # ---- 正式 120 檔（原本 soft trigger）----
                    top120_pool = candidate_pool.build_candidate_pool(db, target_date, ingestion, rankings, momentum_frame=frame)
                    top120_ids = {c["stock_id"] for c in top120_pool}

                    # ---- A/B/C/D 通道命中判定 ----
                    top_industries_names = {ind["industry_name"] for ind in rankings.get("top_industries_3d") or []}
                    top_stocks = rankings.get("top_stocks_3d") or []
                    top_stock_ids = {s["stock_id"] for s in top_stocks}
                    from app.signals.exclusions import find_group_for_stock, get_group_members
                    group_expansion_ids: Set[str] = set()
                    for s in top_stocks[: candidate_pool.TOP_STOCKS_INNER]:
                        gn = find_group_for_stock(s["stock_id"])
                        if gn:
                            group_expansion_ids |= set(get_group_members(gn))
                    channels = momentum.select_momentum_candidates(frame)
                    b_ids = set(channels.get("price_momentum") or [])
                    c_ids = set(channels.get("acceleration") or [])
                    d_ids = set(channels.get("fundamental") or [])

                    # ---- Day10 forward return（涵蓋 raw union 全部成員）----
                    if target_date not in day_index:
                        break
                    i0 = day_index[target_date]
                    i10 = i0 + FORWARD_TRADE_DAYS
                    if i10 >= len(all_days):
                        fwd_date = None
                    else:
                        fwd_date = all_days[i10]
                    all_ids = [c["stock_id"] for c in full_pool]
                    catch_closes = {r.stock_id: float(r.close_price) for r in db.query(DailyPrice).filter(
                        DailyPrice.stock_id.in_(all_ids), DailyPrice.trade_date == target_date).all() if r.close_price}
                    fwd_closes = {}
                    if fwd_date:
                        fwd_closes = {r.stock_id: float(r.close_price) for r in db.query(DailyPrice).filter(
                            DailyPrice.stock_id.in_(all_ids), DailyPrice.trade_date == fwd_date).all() if r.close_price}

                    for rank, c in enumerate(full_pool, start=1):
                        sid = c["stock_id"]
                        f = frame.get(sid, {})
                        cc = catch_closes.get(sid)
                        fc = fwd_closes.get(sid)
                        fwd_ret = (fc / cc - 1.0) * 100.0 if cc and fc else None
                        all_rows.append({
                            "stock_id": sid, "evaluation_date": ds, "regime": regime_info.get("regime"),
                            "momentum_score": c.get("momentum_score"),
                            "raw_union_rank": rank, "raw_union_size": len(full_pool),
                            "selected_top120": sid in top120_ids,
                            "source_A": bool(sid in top_stock_ids or (masters.get(sid) and masters[sid].industry_name in top_industries_names) or sid in group_expansion_ids),
                            "source_B": sid in b_ids, "source_C": sid in c_ids, "source_D": sid in d_ids,
                            "rs_market_percentile_20d": f.get("rs_market_percentile_20d"),
                            "rs_industry_percentile_20d": f.get("rs_industry_percentile_20d"),
                            "distance_to_20d_high": f.get("distance_to_20d_high"),
                            "volume_1d_to_20d_avg": f.get("volume_1d_to_20d_avg"),
                            "return_percentile_60d": f.get("return_percentile_60d"),
                            "return_5d": f.get("return_5d"),
                            "rs_rank_improvement_5d": f.get("rs_rank_improvement_5d"),
                            "revenue_yoy": f.get("revenue_yoy"),
                            "revenue_yoy_industry_percentile": f.get("revenue_yoy_industry_percentile"),
                            "forward_return_10d": fwd_ret,
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
                json.dump({"rows": all_rows, "done_dates": sorted(done_dates)}, f, default=str)
            print(f"  [{di}/{len(ALL_DATES)}] {ds} done, raw_union={all_rows[-1]['raw_union_size'] if all_rows else '?'}, total_rows={len(all_rows)}", flush=True)
            time.sleep(1.0)
    finally:
        db.close()

    print(f"\ntotal rows: {len(all_rows)}")
    with open("/tmp/phase3d_candidate_union_all.json", "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, default=str)
    print("wrote -> /tmp/phase3d_candidate_union_all.json")


if __name__ == "__main__":
    main()
