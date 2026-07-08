"""
一次性修復：魚尾 30 日追蹤跨 cycle carry bug（2026-07-08）。

背景：完成 30 個交易日封存進歷史區後，若同一檔股票又被抓到，因為
persist_signal_watch_hits 舊順序的 bug，新一輪 cycle 的第一筆 hit 會帶入
上一輪的 baseline / max_positive_return_pct / max_negative_return_pct。
程式碼已於 backend/app/signals/archive.py 修復（refresh 移到載入 carry 之前），
但 production DB 內既有的 active cycle 若已受汙染，不會自我修正——這支腳本
負責一次性把它們的 baseline / 極值 重新算對，並讓後續每日排程 (
update_signal_watch_returns) 接手正常運作。

偵測規則：
  一個 active cycle 的 first_seen_date = 該 stock_id 所有 active hits 中
  最早的 snapshot_date。若 baseline_trade_date < first_seen_date，代表
  baseline 是從「更早的一輪」帶過來的 —— 這是不可能發生在正常情況下的
  （baseline 理論上一定 >= first_seen_date + 1 個交易日），視為受汙染。

修復方式：
  用既有的 archive._resolve_nth_trade_date(day_index=2) / _resolve_baseline_price /
  _resolve_return_extrema 這幾個純函式，重新計算「這一輪真正的」baseline
  （cycle 第 2 個交易日）與從那天起算到目前為止的最大正/負報酬、目前報酬率，
  寫回該 stock 所有 active hits row（維持既有慣例：同 cycle 的所有 row 共用
  一份 baseline / 極值狀態）。

用法：
  python backend/scripts/fix_polluted_signal_watch_cycles.py            # dry-run，只印報告
  python backend/scripts/fix_polluted_signal_watch_cycles.py --apply    # 實際寫入
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.models import DailyPrice
from app.signals import archive


def _detect_polluted_groups(db):
    """回傳 [(stock_id, rows, first_seen_date)]，rows 依 snapshot_date 升序。"""
    grouped = archive._load_grouped_hits(db)
    polluted = []
    for stock_id, rows in grouped.items():
        first_seen_date = rows[0].snapshot_date
        latest_row = rows[-1]
        if (
            latest_row.baseline_trade_date is not None
            and latest_row.baseline_trade_date < first_seen_date
        ):
            polluted.append((stock_id, rows, first_seen_date))
    return polluted


def _resolve_eval_price_row(db, *, stock_id: str, as_of_trade_date):
    """優先取 as_of_trade_date 當天收盤；沒有則退回該股 <= as_of 最新一筆。"""
    row = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date == as_of_trade_date,
            DailyPrice.close_price.isnot(None),
        )
        .first()
    )
    if row is not None:
        return row
    return (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= as_of_trade_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )


def _compute_corrected_state(db, *, stock_id: str, first_seen_date, as_of_trade_date):
    """回傳這一輪 cycle 真正該有的狀態 dict；若尚未到 baseline 日則回傳「未 baseline」狀態。"""
    trade_date_cache: dict = {}
    price_cache: dict = {}

    baseline_trade_date = archive._resolve_nth_trade_date(
        db,
        first_seen_date=first_seen_date,
        day_index=2,
        cache=trade_date_cache,
    )

    if baseline_trade_date is None or baseline_trade_date > as_of_trade_date:
        # cycle 還沒走到第 2 個交易日（理論上不該發生在已有多筆 hits 的受汙染 cycle，
        # 但保守處理）：重設為「尚未 baseline」的乾淨初始狀態。
        return {
            "baseline_trade_date": None,
            "baseline_price": None,
            "latest_eval_trade_date": None,
            "latest_eval_price": None,
            "return_pct": None,
            "max_positive_return_pct": None,
            "max_positive_return_trade_date": None,
            "max_negative_return_pct": None,
            "max_negative_return_trade_date": None,
        }

    baseline_price = archive._resolve_baseline_price(
        db,
        stock_id=stock_id,
        baseline_trade_date=baseline_trade_date,
        cache=price_cache,
    )

    eval_row = _resolve_eval_price_row(db, stock_id=stock_id, as_of_trade_date=as_of_trade_date)
    if baseline_price is None or eval_row is None:
        return {
            "baseline_trade_date": baseline_trade_date,
            "baseline_price": baseline_price,
            "latest_eval_trade_date": None,
            "latest_eval_price": None,
            "return_pct": None,
            "max_positive_return_pct": None,
            "max_positive_return_trade_date": None,
            "max_negative_return_pct": None,
            "max_negative_return_trade_date": None,
        }

    eval_trade_date = eval_row.trade_date
    close_price = float(eval_row.close_price)

    if baseline_trade_date == eval_trade_date:
        # 同 archive.update_signal_watch_returns 的既有 guard：baseline 建立當天
        # 強制 latest_eval_price=baseline_price、return_pct=0.0。
        latest_eval_price = baseline_price
        return_pct = 0.0
    else:
        latest_eval_price = close_price
        return_pct = (close_price - baseline_price) / baseline_price * 100.0

    (
        max_positive_return_pct,
        max_positive_return_trade_date,
        max_negative_return_pct,
        max_negative_return_trade_date,
    ) = archive._resolve_return_extrema(
        db,
        stock_id=stock_id,
        baseline_trade_date=baseline_trade_date,
        baseline_price=baseline_price,
        through_trade_date=eval_trade_date,
    )

    return {
        "baseline_trade_date": baseline_trade_date,
        "baseline_price": baseline_price,
        "latest_eval_trade_date": eval_trade_date,
        "latest_eval_price": latest_eval_price,
        "return_pct": return_pct,
        "max_positive_return_pct": max_positive_return_pct,
        "max_positive_return_trade_date": max_positive_return_trade_date,
        "max_negative_return_pct": max_negative_return_pct,
        "max_negative_return_trade_date": max_negative_return_trade_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際寫入修正結果；不帶此參數則只印出偵測到的受汙染股票（dry-run）。",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        polluted = _detect_polluted_groups(db)
        if not polluted:
            print("沒有偵測到受汙染的 active cycle。")
            return 0

        as_of_trade_date = archive.resolve_archive_as_of_trade_date(db)
        if as_of_trade_date is None:
            print("無法解析 as_of_trade_date（daily_price 無資料），中止。")
            return 1

        print(f"as_of_trade_date = {as_of_trade_date}")
        print(f"偵測到 {len(polluted)} 檔受汙染的 active cycle：")
        print()

        for stock_id, rows, first_seen_date in polluted:
            latest_row = rows[-1]
            old_baseline_date = latest_row.baseline_trade_date
            old_baseline_price = latest_row.baseline_price
            old_max_pos = latest_row.max_positive_return_pct
            old_max_neg = latest_row.max_negative_return_pct

            corrected = _compute_corrected_state(
                db,
                stock_id=stock_id,
                first_seen_date=first_seen_date,
                as_of_trade_date=as_of_trade_date,
            )

            print(f"[{stock_id}] {latest_row.stock_name}")
            print(f"  first_seen_date = {first_seen_date}  (hit_count={len(rows)})")
            print(
                f"  舊: baseline_trade_date={old_baseline_date} baseline_price={old_baseline_price} "
                f"max_pos={old_max_pos} max_neg={old_max_neg}"
            )
            print(
                f"  新: baseline_trade_date={corrected['baseline_trade_date']} "
                f"baseline_price={corrected['baseline_price']} "
                f"max_pos={corrected['max_positive_return_pct']} "
                f"max_neg={corrected['max_negative_return_pct']} "
                f"return_pct={corrected['return_pct']}"
            )
            print()

            if args.apply:
                for row in rows:
                    row.baseline_trade_date = corrected["baseline_trade_date"]
                    row.baseline_price = corrected["baseline_price"]
                    row.latest_eval_trade_date = corrected["latest_eval_trade_date"]
                    row.latest_eval_price = corrected["latest_eval_price"]
                    row.return_pct = corrected["return_pct"]
                    row.max_positive_return_pct = corrected["max_positive_return_pct"]
                    row.max_positive_return_trade_date = corrected["max_positive_return_trade_date"]
                    row.max_negative_return_pct = corrected["max_negative_return_pct"]
                    row.max_negative_return_trade_date = corrected["max_negative_return_trade_date"]

        if args.apply:
            db.commit()
            print(f"已寫入修正，共 {len(polluted)} 檔。")
            print("接續執行 update_signal_watch_returns 讓早退 / 封存規則以修正後狀態接手判斷…")
            updated = archive.update_signal_watch_returns(db, as_of_trade_date=as_of_trade_date)
            print(f"update_signal_watch_returns 完成，updated={updated}")
        else:
            print("此為 dry-run，未寫入。加 --apply 才會實際修正。")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
