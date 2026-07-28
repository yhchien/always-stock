"""Phase 3C Step 1: Day-level Dimension Extraction（2026-07-27）。

**純研究**，不修改任何 production 程式碼、Candidate Selection、A/B/C/D、Phase 2、
Phase 2.5、Hard Exclusion、LLM、momentum_score。只重建 Day0 Cross-sectional
Comparison 需要的 5 個既有維度，不新增 feature、不做加權分數。

對 `/tmp/phase25_replay_60d.json` 的 **raw flat_records（未 dedup，每個
(stock_id, catch_date) 都是獨立一筆，代表當天實際候選）**，逐日重建：

    Dimension 1: rs_market_percentile_20d（momentum frame，既有欄位）
    Dimension 2: rs_industry_percentile_20d（momentum frame，既有欄位，同產業
                 peer 相對強度——注意跟 industry_rs_percentile_20d 不同，後者是
                 產業整體排名，前者才是個股在產業內的排名）
    Dimension 3: rs_rank_improvement_5d（momentum frame，既有欄位）
    Dimension 4: institution_buy_to_turnover_2d（momentum frame，既有欄位，原始
                 比率；轉 within-group percentile 留給分析階段做）
    Dimension 5: EXTENDED_3D（flat_records 的 risk_warnings 欄位，既有）

    Comparison Group 用欄位：SecurityClassification（sub_sector/primary_sector/
    classification_confidence/asset_type/is_financial/is_etf），Phase 1 既有表，
    不重算。

沿用 candidate_pool.ingest_data + momentum.compute_market_momentum_frame（既有
deterministic 函式，逐日一次全市場計算，非全 production pipeline，不含 LLM/
Hard Exclusion/Regime Gate）。

用法：
    python analyze_phase3c_dominance_extract.py
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
from app.models import SecurityClassification
from app.signals import candidate_pool, momentum

REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
CHECKPOINT_PATH = "/tmp/phase3c_extract_checkpoint.json"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=120,
    connect_args={"connect_timeout": 15, "options": "-c statement_timeout=100000"},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def load_raw_flat_records() -> List[Dict[str, Any]]:
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["flat_records"]


def main() -> None:
    flat = load_raw_flat_records()
    print(f"raw flat_records (未 dedup): {len(flat)}")

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for r in flat:
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
        print(f"resume from checkpoint: {len(done_dates)} dates done, {len(all_rows)} rows loaded")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    db = SessionLocal()
    try:
        for di, catch_date_str in enumerate(unique_dates, start=1):
            if catch_date_str in done_dates:
                continue
            target_date = date.fromisoformat(catch_date_str)
            recs = by_date[catch_date_str]
            stock_ids_today = [r["stock_id"] for r in recs]

            for attempt in range(3):
                try:
                    ingestion = candidate_pool.ingest_data(db, target_date)
                    masters = ingestion.get("stocks_master") or {}
                    if not masters:
                        for rec in recs:
                            all_rows.append(_empty_row(rec, "no_masters_data"))
                        break

                    frame = momentum.compute_market_momentum_frame(db, target_date, masters)

                    class_rows = (
                        db.query(SecurityClassification)
                        .filter(SecurityClassification.stock_id.in_(stock_ids_today))
                        .all()
                    )
                    class_by_id = {c.stock_id: c for c in class_rows}

                    for rec in recs:
                        sid = rec["stock_id"]
                        feats = frame.get(sid) or {}
                        cls = class_by_id.get(sid)
                        risk_warnings = rec.get("risk_warnings") or []
                        all_rows.append({
                            "stock_id": sid,
                            "catch_date": catch_date_str,
                            "regime": rec.get("regime"),
                            "forward_return_10d": rec.get("forward_return_pct"),
                            "role": rec.get("role"),
                            "in_price_momentum_pool": bool(rec.get("in_price_momentum_pool")),
                            "in_acceleration_pool": bool(rec.get("in_acceleration_pool")),
                            "in_fundamental_pool": bool(rec.get("in_fundamental_pool")),
                            "in_top_stocks_3d": bool(rec.get("in_top_stocks_3d")),
                            "extended_3d": "EXTENDED_3D" in risk_warnings,
                            "dim1_rs_market_percentile_20d": feats.get("rs_market_percentile_20d"),
                            "dim2_rs_industry_percentile_20d": feats.get("rs_industry_percentile_20d"),
                            "dim3_rs_rank_improvement_5d": feats.get("rs_rank_improvement_5d"),
                            "dim4_institution_buy_to_turnover_2d": feats.get("institution_buy_to_turnover_2d"),
                            "sub_sector": cls.sub_sector if cls else None,
                            "primary_sector": cls.primary_sector if cls else None,
                            "classification_confidence": cls.classification_confidence if cls else None,
                            "is_financial": bool(cls.is_financial) if cls else False,
                            "is_etf": bool(cls.is_etf) if cls else False,
                            "asset_type": cls.asset_type if cls else None,
                            "note": "",
                        })
                    break
                except OperationalError as e:
                    print(f"  [retry {attempt+1}/3] DB error on {catch_date_str}: {type(e).__name__}: {str(e)[:150]}", flush=True)
                    try:
                        db.close()
                    except Exception:
                        pass
                    engine.dispose()
                    time.sleep(10 * (attempt + 1))  # 遞增等待，讓 DB 有時間恢復
                    db = SessionLocal()
            else:
                print(f"  [SKIP] {catch_date_str} failed after 3 retries — 不標記完成，下次重跑會重試", flush=True)
                continue

            done_dates.add(catch_date_str)
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump({"rows": all_rows, "done_dates": sorted(done_dates)}, f, default=str)

            time.sleep(1.5)  # 讓 DB 稍微喘息，降低連續高頻查詢造成的效能衰退

            if di % 5 == 0:
                print(f"  processed {di}/{len(unique_dates)} unique catch dates "
                      f"({len(all_rows)} rows so far)", flush=True)
    finally:
        db.close()

    print(f"\ntotal rows: {len(all_rows)}")
    with open("/tmp/phase3c_extracted_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2, default=str)
    print("wrote -> /tmp/phase3c_extracted_raw.json")


def _empty_row(rec: Dict[str, Any], note: str) -> Dict[str, Any]:
    return {
        "stock_id": rec["stock_id"], "catch_date": rec["catch_date"], "regime": rec.get("regime"),
        "forward_return_10d": rec.get("forward_return_pct"), "role": rec.get("role"),
        "in_price_momentum_pool": False, "in_acceleration_pool": False, "in_fundamental_pool": False,
        "in_top_stocks_3d": False, "extended_3d": False,
        "dim1_rs_market_percentile_20d": None, "dim2_rs_industry_percentile_20d": None,
        "dim3_rs_rank_improvement_5d": None, "dim4_institution_buy_to_turnover_2d": None,
        "sub_sector": None, "primary_sector": None, "classification_confidence": None,
        "is_financial": False, "is_etf": False, "asset_type": None, "note": note,
    }


if __name__ == "__main__":
    main()
