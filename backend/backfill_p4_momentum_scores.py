"""
一次性回補：P4 每日複核歷史缺漏的 momentum_score（2026-08-14）。

背景：`SignalObservationReview.momentum_score` 是這次 session 才新增的欄位——P4
每日複核（`build_current_tracking_evidence`）本來就會用跟 P3 完全相同的
`momentum_frame`／`compute_momentum_score()` 重算一次動能分數，過去只拿來衍生
`momentum_phase` 等欄位，算完即丟，從未持久化。上線前產生的複核紀錄因此全部是
`momentum_score IS NULL`，導致動能分數折線圖在那幾天出現空缺。

這是純 deterministic 的 DB 運算（跟 P3 用同一套公式、同一份歷史市場快照），不需要
呼叫 LLM，可以安全回補：對每個有缺漏的 review_date，重新組出當天的 momentum_frame
（`momentum.compute_market_momentum_frame`，用 `review_date` 當下的歷史資料計算，
不會有「用未來資料回填過去」的問題），再對當天所有 momentum_score 缺漏的複核紀錄
批次呼叫 `build_current_tracking_evidence` 補值。

用法：
    python backfill_p4_momentum_scores.py            # dry-run，只印出會影響的清單
    python backfill_p4_momentum_scores.py --execute  # 真的寫入 DB
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal
from app.models import SignalObservation, SignalObservationReview
from app.signals import candidate_pool, momentum
from app.signals.observation_lifecycle import build_current_tracking_evidence


def find_target_dates(db) -> list:
    rows = (
        db.query(SignalObservationReview.review_date)
        .filter(SignalObservationReview.momentum_score.is_(None))
        .distinct()
        .order_by(SignalObservationReview.review_date)
        .all()
    )
    return [r[0] for r in rows]


def backfill_date(db, review_date, *, execute: bool) -> tuple[int, int]:
    """回傳 (該天缺漏筆數, 成功補上筆數)。"""
    reviews = (
        db.query(SignalObservationReview)
        .join(SignalObservation, SignalObservationReview.observation_id == SignalObservation.id)
        .filter(
            SignalObservationReview.review_date == review_date,
            SignalObservationReview.momentum_score.is_(None),
        )
        .all()
    )
    if not reviews:
        return 0, 0

    observation_ids = {row.observation_id for row in reviews}
    observations = (
        db.query(SignalObservation)
        .filter(SignalObservation.id.in_(observation_ids))
        .all()
    )

    ingestion = candidate_pool.ingest_data(db, review_date)
    masters = ingestion.get("stocks_master") or {}
    momentum_frame = momentum.compute_market_momentum_frame(db, review_date, masters)

    evidence_by_id = build_current_tracking_evidence(
        db,
        observations=observations,
        review_date=review_date,
        market_context={},
        ingestion=ingestion,
        momentum_frame=momentum_frame,
        current_candidates=[],
    )

    filled = 0
    for row in reviews:
        evidence = evidence_by_id.get(row.observation_id)
        if not evidence:
            continue
        score = evidence.get("momentum_score")
        if score is None:
            continue
        filled += 1
        if execute:
            row.momentum_score = score
    return len(reviews), filled


def main(argv: list) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        dates = find_target_dates(db)
        if not dates:
            print("No dates need backfilling.")
            return 0

        print(f"{'[EXECUTE]' if args.execute else '[DRY-RUN]'} {len(dates)} 個交易日需要回補")
        total_missing = 0
        total_filled = 0
        for d in dates:
            missing, filled = backfill_date(db, d, execute=args.execute)
            total_missing += missing
            total_filled += filled
            print(f"  {d}: {missing} 筆缺漏，成功算出 {filled} 筆")
            if args.execute:
                db.commit()

        print(f"合計：{total_missing} 筆缺漏，成功回補 {total_filled} 筆")
        if not args.execute:
            print("（dry-run，未寫入。加 --execute 才會真的寫進 DB。）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
