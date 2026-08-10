"""
一次性整理：停止「真正舊」的 P4 觀察（2026-08-10）。

背景：P4（每日觀察生命週期）上線前，`bootstrap_legacy_observations()` 會從舊的
`signal_watch_hits`（M23 archive 系統）回填一批 `SignalObservation`，這些觀察的
`baseline_quality="LEGACY_INCOMPLETE"` 且 `selection_version` 永遠是 null（舊資料
的 `signal_metrics` 沒有這個欄位，跟「今天才被 v7 pipeline 推薦、只是敘事文字剛好
缺一項」的正常觀察不同——後者的 `selection_version` 一定有值）。

`decide_observation_action()` 的「持續警戒 → STOP」判斷有 `not baseline_incomplete`
前置條件，導致這批觀察即使連續警戒再多次也永遠不會自然 STOP（實測 2618／6533 連續
13 次警戒仍卡在 CAUTION）。這支腳本一次性把這批「真正舊」的觀察轉成 STOPPED，並補建
對應的 SignalObservationArchive 紀錄；`observation_lifecycle.py` 的
`run_daily_observation_reviews` 已經拿掉 `bootstrap_legacy_observations()` 呼叫，
不會再產生新的一批。

篩選條件（同時符合才會被停止，刻意嚴格避免誤停）：
    status IN (OBSERVING, CAUTION)
    AND baseline_quality == "LEGACY_INCOMPLETE"
    AND selection_version IS NULL

用法：
    python stop_legacy_incomplete_observations.py            # dry-run，只印出會影響的清單
    python stop_legacy_incomplete_observations.py --execute  # 真的寫入 DB
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal
from app.models import SignalObservation
from app.signals.observation_lifecycle import (
    STATUS_CAUTION,
    STATUS_OBSERVING,
    STATUS_STOPPED,
    STOP_CONFIRM_THRESHOLD,
    _finalize_observation_archive,
)

STOP_REASON_CODE = "LEGACY_BASELINE_RETIRED"
STOP_REASON = (
    "此觀察源自 P4 系統上線前的舊資料回填，缺乏完整推薦論點基礎；"
    "隨魚尾（archive）與每日觀察合併為單一入口，統一停止觀察。"
)


def find_targets(db) -> list[SignalObservation]:
    return (
        db.query(SignalObservation)
        .filter(
            SignalObservation.status.in_([STATUS_OBSERVING, STATUS_CAUTION]),
            SignalObservation.baseline_quality == "LEGACY_INCOMPLETE",
            SignalObservation.selection_version.is_(None),
        )
        .order_by(SignalObservation.stock_id.asc())
        .all()
    )


def main(argv: list) -> int:
    execute = "--execute" in argv
    today = date.today()

    with SessionLocal() as db:
        targets = find_targets(db)
        if not targets:
            print("沒有符合條件的 legacy 觀察，不需要處理。")
            return 0

        print(f"符合條件（真正舊的 legacy 觀察）共 {len(targets)} 筆：")
        for obs in targets:
            print(
                f"  {obs.stock_id} {obs.stock_name}\t"
                f"status={obs.status}\t"
                f"consecutive_caution_count={obs.consecutive_caution_count}\t"
                f"started_signal_date={obs.started_signal_date}"
            )

        if not execute:
            print(
                "\n這是 dry-run，沒有寫入任何資料。確認清單無誤後加 --execute 才會真的停止觀察。"
            )
            return 0

        now = datetime.utcnow()
        for obs in targets:
            obs.status = STATUS_STOPPED
            obs.stopped_at = now
            obs.stop_reason_code = STOP_REASON_CODE
            obs.stop_reason = STOP_REASON
            obs.stop_confirm_count = STOP_CONFIRM_THRESHOLD
            obs.updated_at = now
            _finalize_observation_archive(db, observation=obs, archived_date=today)
        db.commit()
        print(f"\n已停止 {len(targets)} 筆 legacy 觀察並補建 archive 紀錄。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
