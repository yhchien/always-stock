"""一次性建立 Dual-Engine v1_frozen 回測（2026-08-01~2026-09-07）的本機唯讀資料快取。

**動機**：這段窗口已經重跑過好幾次（每次都要抓 bug → 修 → 重跑），每次對 production
Postgres（Render，新加坡）做 26 個交易日 × 4~5 個 orchestrator 呼叫的完整回測，光是
網路來回延遲就要 20~25 分鐘，而其中真正會被讀取的資料（`daily_price`／`signal_watch_
hits`／P4 觀察歷史等）在這段**已經是過去**的窗口內早就不會再變動——沒有必要每次都
重新從遠端抓一樣的東西。

這支腳本把「這次回測實際會查詢到的資料」原封不動複製一份到本機 SQLite 檔案，之後只要
把 `DATABASE_URL`（或 `DB_PATH`）指到這個檔案，`backfill_shadow_portfolio_replay.py`／
`shadow_portfolio.py` 的策略邏輯**完全不用改一行程式碼**（`app/database.py` 本來就是
從環境變數決定要連哪個資料庫，SQLAlchemy session 介面對呼叫端是透明的）。

**範圍如何決定**：不是憑感覺抓一個「夠寬」的日期範圍，是先對 production 實際跑一次
`resolve_fishtail_universe()` 掃過 2026-08-01~2026-09-08 每一天，得到這次回測真正會
碰到的 185 檔股票、以及最早的 `first_seen_date`（2026-07-20）——`daily_price` 複製
範圍以此為準（往前留 5 天緩衝到 2026-07-15，避免任何邊界計算不小心多看一天）。

複製的表（讀取用，不含 Shadow* 寫入表——那些表本來就會被 `backfill_shadow_portfolio_
replay.py --execute` 的 `_reset_shadow_portfolio_state()` 清空重建，不需要預先塞資料，
只需要 schema 存在）：
    - daily_price（限定這 185 檔股票 + 2026-07-15~2026-09-08）
    - signal_watch_hits（限定這 185 檔股票；這張表代表「目前活躍」的追蹤週期，本次
      複製的是「現在（跑這支腳本的當下）」的即時狀態——跟過去三次重跑用的是同一份
      即時狀態，凍結起來正好保證之後的重跑逐位元組可比對）
    - signal_watch_completed_archives／signal_watch_stopped_observations（限定這
      185 檔股票）
    - signal_snapshots（限定 2026-07-15~2026-09-08，全市場單一 blob，不分股票）
    - signal_observations／signal_observation_reviews（限定這 185 檔股票；reviews
      再依複製過去的 observation id 做二次篩選，確保外鍵對得上）
    - etf_classifications（全表複製，本身就很小，不需要篩選）

用法：
    python3 build_dual_engine_backtest_cache.py
        # 對目前 DATABASE_URL 指到的資料庫（production）讀取，寫到
        # backend/db/dual_engine_backtest_cache.db（每次執行都會先刪除舊檔重建）

    DATABASE_URL=sqlite:///$(pwd)/db/dual_engine_backtest_cache.db \\
        python3 backfill_shadow_portfolio_replay.py --execute \\
        --start=2026-08-01 --end=2026-09-07
        # 之後所有重跑都指到這個本機檔案，不再打 production
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

CACHE_DB_PATH = BACKEND_DIR / "db" / "dual_engine_backtest_cache.db"

# 這次回測實際涵蓋的窗口——如果之後要驗證別的日期區間，改這兩個常數重新跑一次即可。
REPLAY_WINDOW_START = date(2026, 8, 1)
REPLAY_WINDOW_END = date(2026, 9, 8)  # 含 T+1 結算需要的最後一天
# daily_price 複製範圍：往前留到比 `_min_first_seen_date_in_window()` 查出來的
# 2026-07-20 更早 5 天，純粹是保守緩衝，不影響正確性（多複製幾天資料不會有壞處）。
DAILY_PRICE_BUFFER_DAYS = 5


def _min_first_seen_date_and_universe(db) -> Tuple[date, Set[str]]:
    """對 production 實際掃一次 `resolve_fishtail_universe()`，找出這次回測窗口
    真正會碰到的股票清單與最早的 first_seen_date——不是猜測，是精確查出來的。"""
    from datetime import timedelta

    from app.signals import shadow_portfolio as sp

    min_fsd: Optional[date] = None
    stock_ids: Set[str] = set()
    d = REPLAY_WINDOW_START
    while d <= REPLAY_WINDOW_END:
        universe = sp.resolve_fishtail_universe(db, target_date=d)
        for sid, _name, fsd in universe.values():
            stock_ids.add(sid)
            if min_fsd is None or fsd < min_fsd:
                min_fsd = fsd
        d += timedelta(days=1)
    assert min_fsd is not None, "沒有掃到任何股票，日期範圍或資料源可能有問題"
    return min_fsd, stock_ids


def _copy_table(dest_engine, source_db, model, *, filter_fn: Optional[Callable] = None,
                label: Optional[str] = None) -> List:
    """把 `model` 在 production 符合 `filter_fn` 的所有列，原封不動（含 PK）搬進
    `dest_engine`。回傳複製過去的列（給呼叫端需要用 PK 做後續篩選時使用，例如
    `SignalObservationReview` 要依 `SignalObservation.id` 篩選）。"""
    query = source_db.query(model)
    if filter_fn is not None:
        query = filter_fn(query)
    rows = query.all()
    label = label or model.__tablename__
    if not rows:
        print(f"  {label}: 0 筆")
        return []
    columns = [c.name for c in model.__table__.columns]
    payload = [{col: getattr(row, col) for col in columns} for row in rows]
    with dest_engine.begin() as conn:
        conn.execute(model.__table__.insert(), payload)
    print(f"  {label}: {len(payload)} 筆")
    return rows


def main() -> int:
    from datetime import timedelta

    from sqlalchemy import create_engine

    from app.database import SessionLocal
    from app.models import (
        Base,
        DailyPrice,
        EtfClassification,
        SignalObservation,
        SignalObservationReview,
        SignalSnapshot,
        SignalWatchCompletedArchive,
        SignalWatchHit,
        SignalWatchStoppedObservation,
    )

    if CACHE_DB_PATH.exists():
        CACHE_DB_PATH.unlink()
        print(f"已刪除舊快取檔：{CACHE_DB_PATH}")

    with SessionLocal() as source_db:
        print(f"對 production 掃描 {REPLAY_WINDOW_START} ~ {REPLAY_WINDOW_END} 的魚尾 universe…")
        min_fsd, stock_ids = _min_first_seen_date_and_universe(source_db)
        price_start = min_fsd - timedelta(days=DAILY_PRICE_BUFFER_DAYS)
        print(f"  最早 first_seen_date={min_fsd}，涵蓋 {len(stock_ids)} 檔股票")
        print(f"  daily_price 複製範圍：{price_start} ~ {REPLAY_WINDOW_END}")

        dest_engine = create_engine(f"sqlite:///{CACHE_DB_PATH}")
        Base.metadata.create_all(bind=dest_engine)
        print(f"\n已在 {CACHE_DB_PATH} 建立完整 schema，開始複製資料：")

        _copy_table(
            dest_engine, source_db, DailyPrice,
            filter_fn=lambda q: q.filter(
                DailyPrice.stock_id.in_(stock_ids),
                DailyPrice.trade_date >= price_start,
                DailyPrice.trade_date <= REPLAY_WINDOW_END,
            ),
        )
        _copy_table(dest_engine, source_db, EtfClassification)
        _copy_table(
            dest_engine, source_db, SignalWatchHit,
            filter_fn=lambda q: q.filter(SignalWatchHit.stock_id.in_(stock_ids)),
        )
        _copy_table(
            dest_engine, source_db, SignalWatchCompletedArchive,
            filter_fn=lambda q: q.filter(SignalWatchCompletedArchive.stock_id.in_(stock_ids)),
        )
        _copy_table(
            dest_engine, source_db, SignalWatchStoppedObservation,
            filter_fn=lambda q: q.filter(SignalWatchStoppedObservation.stock_id.in_(stock_ids)),
        )
        _copy_table(
            dest_engine, source_db, SignalSnapshot,
            filter_fn=lambda q: q.filter(
                SignalSnapshot.snapshot_date >= price_start,
                SignalSnapshot.snapshot_date <= REPLAY_WINDOW_END,
            ),
        )
        copied_observations = _copy_table(
            dest_engine, source_db, SignalObservation,
            filter_fn=lambda q: q.filter(SignalObservation.stock_id.in_(stock_ids)),
        )
        observation_ids = [obs.id for obs in copied_observations]
        _copy_table(
            dest_engine, source_db, SignalObservationReview,
            filter_fn=lambda q: q.filter(SignalObservationReview.observation_id.in_(observation_ids)),
        )

    size_mb = CACHE_DB_PATH.stat().st_size / (1024 * 1024)
    print(f"\n完成。快取檔案大小：{size_mb:.1f} MB")
    print(
        "\n之後要用這份快取跑回測，設定 DATABASE_URL 指到這個檔案即可，程式碼完全不用改：\n"
        f"    DATABASE_URL=sqlite:///{CACHE_DB_PATH} python3 backfill_shadow_portfolio_replay.py "
        "--execute --start=2026-08-01 --end=2026-09-07"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
