"""
一次性整理：結算「P4 已停止觀察但魚尾追蹤週期從未清掉」的落差股票（2026-08-14）。

背景：`stop_legacy_incomplete_observations.py`（2026-08-10 執行）直接把
`SignalObservation.status` 設成 `STOPPED`，但那時候「P4 確認停止觀察同步結算魚尾」
這個機制（`archive.settle_stock_for_p4_stop`）還不存在，所以這批股票的魚尾追蹤週期
（`signal_watch_hits`）從未被清掉，一直卡在「追蹤中」名單裡，即使 P3 早就不再選中
它們也不會自然消失（只有價格觸發的提前結算規則或滿 30 天才會自然清掉）。

篩選條件：目前在 `signal_watch_hits` 有進行中週期，且該股票「最新一筆」
`SignalObservation`（依 `started_signal_date` 排序）狀態為 `STOPPED`。

用法：
    python settle_stale_stopped_observations.py            # dry-run，只印出會影響的清單
    python settle_stale_stopped_observations.py --execute  # 真的寫入 DB
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal
from app.models import SignalObservation, SignalWatchHit
from app.signals import archive


def find_targets(db) -> list[tuple[str, SignalObservation]]:
    active_ids = [r[0] for r in db.query(SignalWatchHit.stock_id).distinct().all()]
    results = []
    for sid in active_ids:
        obs = (
            db.query(SignalObservation)
            .filter(SignalObservation.stock_id == sid)
            .order_by(SignalObservation.started_signal_date.desc(), SignalObservation.id.desc())
            .first()
        )
        if obs and obs.status == "STOPPED":
            results.append((sid, obs))
    return results


def main(argv: list) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="結算用的 as_of_trade_date（YYYY-MM-DD）；預設用今天。",
    )
    args = parser.parse_args(argv)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    db = SessionLocal()
    try:
        targets = find_targets(db)
        if not targets:
            print("No stale stopped observations found.")
            return 0

        print(f"{'[EXECUTE]' if args.execute else '[DRY-RUN]'} {len(targets)} 檔待結算（as_of={as_of}）")
        for sid, obs in targets:
            print(f"  {sid}  observation_id={obs.id}  stopped_at={obs.stopped_at}")
            if args.execute:
                settled = archive.settle_stock_for_p4_stop(db, stock_id=sid, as_of_trade_date=as_of)
                print(f"    -> settled={settled}")
        if args.execute:
            db.commit()
        else:
            print("（dry-run，未寫入。加 --execute 才會真的寫進 DB。）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
