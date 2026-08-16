"""一次性腳本（2026-08-16）：修復 `_settle_pending_p4_fishtail_stops()` 的
stale-episode mismatch bug（見 observation_lifecycle.py 該函式 2026-08-16 修正註解）
造成的資料損害。

背景：這個函式 2026-08-14 上線第一天執行，就把 9 檔股票「目前正在追蹤、根本沒被
P4 判定失效」的魚尾週期誤判成該結算，強制關閉並寫進 `signal_watch_stopped_observations`
（closure_reason='p4_stopped', completed_trade_date=2026-08-14）。這 9 檔的 P4 觀察
狀態實際上都還是 OBSERVING/CAUTION（3605/6213/9921/2603/2357/2615/2454/2330/6669）。

本腳本只動這 9 檔股票，**不呼叫** `persist_signal_watch_hits()` /
`update_signal_watch_returns()`（兩者都是遍歷全市場目前活躍股票的 whole-market
函式，對「只復原這幾檔、其餘完全不能動」的場景不安全——會連帶重建/覆寫其他無關
股票當天的 hits，甚至可能誤把已經合法結算的其他股票的舊 cycle 復活）。改用
`SignalSnapshot.watchlist`（永久保留所有日期）逐股票直接重建 `SignalWatchHit`，
baseline/報酬率/極值計算完全比照 `update_signal_watch_returns()` 的既有規則
（baseline = first_seen 後第一個交易日的 (open+close)/2；as_of 日期用最新可用的
`signal_snapshots.snapshot_date`），但只對這 9 檔股票的資料做運算與寫入。

用法：
    python3 restore_stale_episode_mismatch_fishtail_cycles.py           # dry-run，只印計畫
    python3 restore_stale_episode_mismatch_fishtail_cycles.py --execute # 真的寫入
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Optional

from app.database import SessionLocal
from app.models import (
    DailyPrice,
    SignalSnapshot,
    SignalWatchHit,
    SignalWatchStoppedObservation,
)
from app.signals.archive import _carry_initial_selection_metrics, _resolve_return_extrema

# 8/14 誤殺的 9 檔股票；(stock_id, first_seen_date) 取自事故當下查證到的
# SignalWatchStoppedObservation 原始資料。
TARGETS: list[tuple[str, date]] = [
    ("3605", date(2026, 8, 7)),
    ("6213", date(2026, 8, 7)),
    ("9921", date(2026, 8, 7)),
    ("2603", date(2026, 8, 11)),
    ("2357", date(2026, 8, 11)),
    ("2615", date(2026, 8, 10)),
    ("2454", date(2026, 8, 11)),
    ("2330", date(2026, 8, 11)),
    ("6669", date(2026, 8, 7)),
]

WRONG_CLOSURE_REASON = "p4_stopped"
WRONG_COMPLETED_DATE = date(2026, 8, 14)


def _latest_snapshot_date(db) -> date:
    from sqlalchemy import func

    latest = db.query(func.max(SignalSnapshot.snapshot_date)).scalar()
    if latest is None:
        raise RuntimeError("no signal_snapshots rows found")
    return latest


def _load_hit_entries(
    db, stock_id: str, first_seen_date: date, as_of_date: date
) -> list[tuple[date, Optional[Any], dict]]:
    """回傳 [(snapshot_date, generated_at, watchlist_item_dict), ...] 依日期升序，
    只取這檔股票真的有出現在當天 watchlist 的日子（不是每天都有）。
    """
    snapshots = (
        db.query(SignalSnapshot)
        .filter(
            SignalSnapshot.snapshot_date >= first_seen_date,
            SignalSnapshot.snapshot_date <= as_of_date,
        )
        .order_by(SignalSnapshot.snapshot_date.asc())
        .all()
    )
    entries = []
    for snap in snapshots:
        for item in snap.watchlist or []:
            if str(item.get("stock")) == stock_id:
                entries.append((snap.snapshot_date, snap.generated_at, item))
                break
    return entries


def _resolve_baseline(
    db, stock_id: str, first_seen_date: date
) -> tuple[Optional[date], Optional[float]]:
    """比照 update_signal_watch_returns 的規則：baseline = first_seen 之後第一個有
    收盤價的交易日，用該日 (open+close)/2。
    """
    row = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date > first_seen_date,
            DailyPrice.open_price.isnot(None),
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.trade_date.asc())
        .first()
    )
    if row is None:
        return None, None
    return row.trade_date, (float(row.open_price) + float(row.close_price)) / 2.0


def plan_restore(db, stock_id: str, first_seen_date: date, as_of_date: date) -> dict:
    entries = _load_hit_entries(db, stock_id, first_seen_date, as_of_date)
    baseline_trade_date, baseline_price = _resolve_baseline(db, stock_id, first_seen_date)

    latest_eval_trade_date: Optional[date] = None
    latest_eval_price: Optional[float] = None
    return_pct: Optional[float] = None
    max_pos_pct = max_pos_date = max_neg_pct = max_neg_date = None

    if baseline_trade_date is not None and baseline_price not in (None, 0):
        price_row = (
            db.query(DailyPrice)
            .filter(
                DailyPrice.stock_id == stock_id,
                DailyPrice.trade_date == as_of_date,
                DailyPrice.close_price.isnot(None),
            )
            .first()
        )
        close_price = float(price_row.close_price) if price_row is not None else None
        if baseline_trade_date == as_of_date:
            latest_eval_trade_date = as_of_date
            latest_eval_price = baseline_price
            return_pct = 0.0
        elif close_price is not None:
            latest_eval_trade_date = as_of_date
            latest_eval_price = close_price
            return_pct = (close_price - baseline_price) / baseline_price * 100.0

        max_pos_pct, max_pos_date, max_neg_pct, max_neg_date = _resolve_return_extrema(
            db,
            stock_id=stock_id,
            baseline_trade_date=baseline_trade_date,
            baseline_price=baseline_price,
            through_trade_date=as_of_date,
        )

    return {
        "stock_id": stock_id,
        "first_seen_date": first_seen_date,
        "entries": entries,
        "baseline_trade_date": baseline_trade_date,
        "baseline_price": baseline_price,
        "latest_eval_trade_date": latest_eval_trade_date,
        "latest_eval_price": latest_eval_price,
        "return_pct": return_pct,
        "max_positive_return_pct": max_pos_pct,
        "max_positive_return_trade_date": max_pos_date,
        "max_negative_return_pct": max_neg_pct,
        "max_negative_return_trade_date": max_neg_date,
    }


def execute_restore(db, plan: dict, *, job_id: Optional[str]) -> None:
    stock_id = plan["stock_id"]

    # 1. 刪掉這檔股票目前所有 active hits（含 6213/6669 bug 之後產生的孤立新 row）。
    db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == stock_id).delete(
        synchronize_session=False
    )

    # 2. 刪掉錯誤的「已停止觀察」封存紀錄。
    db.query(SignalWatchStoppedObservation).filter(
        SignalWatchStoppedObservation.stock_id == stock_id,
        SignalWatchStoppedObservation.closure_reason == WRONG_CLOSURE_REASON,
        SignalWatchStoppedObservation.completed_trade_date == WRONG_COMPLETED_DATE,
    ).delete(synchronize_session=False)

    # 3. 依序重建每個真實命中日的 hit row，signal_metrics 逐日 carry initial_* 欄位。
    prior_metrics: Optional[dict] = None
    for snapshot_date, generated_at, item in plan["entries"]:
        carried = _carry_initial_selection_metrics(item.get("signal_metrics"), prior_metrics)
        prior_metrics = carried
        db.add(
            SignalWatchHit(
                snapshot_date=snapshot_date,
                stock_id=stock_id,
                stock_name=str(item.get("name") or ""),
                signal_type=str(item.get("type") or "LEADER").upper(),
                industry_name=item.get("industry"),
                sub_industry=item.get("sub_industry"),
                business_summary=item.get("business_summary"),
                reason=str(item.get("reason") or ""),
                recommendation_thesis=item.get("recommendation_thesis"),
                relative_advantage=item.get("relative_advantage"),
                margin_analysis=item.get("margin_analysis"),
                theme=item.get("theme") or {},
                group_info=item.get("group_info") or {},
                leader_check=item.get("leader_check") or {},
                signals=item.get("signals") or {},
                signal_metrics=carried,
                prompt_version=str(item.get("prompt_version") or "v1"),
                baseline_trade_date=plan["baseline_trade_date"],
                baseline_price=plan["baseline_price"],
                latest_eval_trade_date=plan["latest_eval_trade_date"],
                latest_eval_price=plan["latest_eval_price"],
                return_pct=plan["return_pct"],
                max_positive_return_pct=plan["max_positive_return_pct"],
                max_positive_return_trade_date=plan["max_positive_return_trade_date"],
                max_negative_return_pct=plan["max_negative_return_pct"],
                max_negative_return_trade_date=plan["max_negative_return_trade_date"],
                snapshot_generated_at=generated_at,
                job_id=job_id,
            )
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="真的寫入（預設 dry-run）")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        as_of_date = _latest_snapshot_date(db)
        print(f"as_of_date（最新可用 snapshot）：{as_of_date}\n")

        for stock_id, first_seen_date in TARGETS:
            plan = plan_restore(db, stock_id, first_seen_date, as_of_date)
            hit_dates = [e[0].isoformat() for e in plan["entries"]]
            print(f"[{stock_id}] first_seen={first_seen_date} hit_dates={hit_dates}")
            print(
                f"  baseline={plan['baseline_trade_date']}@{plan['baseline_price']}"
                f"  latest_eval={plan['latest_eval_trade_date']}@{plan['latest_eval_price']}"
                f"  return_pct={plan['return_pct']}"
            )
            print(
                f"  max_pos={plan['max_positive_return_pct']}"
                f"({plan['max_positive_return_trade_date']})"
                f"  max_neg={plan['max_negative_return_pct']}"
                f"({plan['max_negative_return_trade_date']})"
            )
            if args.execute:
                execute_restore(db, plan, job_id=None)
                db.commit()
                print("  -> 已寫入")
            print()

        if not args.execute:
            print("（dry-run，未寫入。加 --execute 才會真的寫進 DB。）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
