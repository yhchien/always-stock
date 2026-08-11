"""
一次性整理：強制結算所有進行中的魚尾追蹤週期（2026-08-11）。

背景：正式推薦頁（/signals/recommendations）併入魚尾（archive）成為單一入口後，
使用者要求把「現在所有進行中的追蹤週期」全部強制結算一次，讓之後任何股票被 P3
推薦時，都是全新 cycle（first_seen_date = 那天）——不必等自然的 30 個交易日
retention 或提前結算規則觸發。

用法：
    python stop_all_active_signal_watch_cycles.py            # dry-run，只印出會被結算的清單
    python stop_all_active_signal_watch_cycles.py --execute  # 真的寫入 DB

執行順序（重要）：這支腳本應該在 signal_watch_hits 新增
recommendation_thesis/relative_advantage/margin_analysis 三個欄位、且
persist_signal_watch_hits() 已經開始寫入這幾個欄位的版本部署上線「之後」才執行——
這樣結算後開的全新 cycle 從第一天就能拿到完整欄位，不會有欄位空窗期。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal
from app.models import SignalWatchHit
from app.signals.archive import (
    CLOSURE_REASON_MANUAL_RESET,
    _build_completed_archive_item,
    _load_grouped_hits,
    _upsert_completed_archive,
)


def main(argv: list) -> int:
    execute = "--execute" in argv

    with SessionLocal() as db:
        grouped = _load_grouped_hits(db)
        if not grouped:
            print("目前沒有任何進行中的追蹤週期，不需要處理。")
            return 0

        print(f"目前進行中的追蹤週期共 {len(grouped)} 檔：")
        for stock_id, rows in sorted(grouped.items()):
            latest = rows[-1]
            print(
                f"  {stock_id} {latest.stock_name}\t"
                f"first_seen={rows[0].snapshot_date}\t"
                f"latest_hit={latest.snapshot_date}\t"
                f"hit_count={len(rows)}"
            )

        if not execute:
            print(
                "\n這是 dry-run，沒有寫入任何資料。確認清單無誤後加 --execute 才會真的結算。"
            )
            return 0

        trade_date_cache: dict = {}
        price_cache: dict = {}
        for stock_id, rows in grouped.items():
            completed_item = _build_completed_archive_item(
                db,
                stock_id=stock_id,
                rows=rows,
                trade_date_cache=trade_date_cache,
                price_cache=price_cache,
            )
            completed_item.closure_reason = CLOSURE_REASON_MANUAL_RESET
            _upsert_completed_archive(db, completed_item)
            db.query(SignalWatchHit).filter(
                SignalWatchHit.stock_id == stock_id
            ).delete(synchronize_session="evaluate")
        db.commit()
        print(f"\n已強制結算 {len(grouped)} 檔追蹤週期並補建 archive 紀錄。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
