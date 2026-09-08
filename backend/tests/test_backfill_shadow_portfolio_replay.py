"""迴歸測試：`_reset_shadow_portfolio_state` 必須清空「每一張」`strategy_version`-scoped
的 shadow 表——這支腳本的核心安全假設是「每次 --execute 都從乾淨狀態重跑」，任何新增的
表只要忘記加進這個函式，就會在重跑時撞到殘留資料的 unique constraint（真實發生過兩次：
`ShadowCompletedTrade` 一次、`ShadowMissedCandidate`/`ShadowWinnerTracking` 一次）。

這裡不逐一測試「清空後重跑結果正確」（那是 `test_shadow_portfolio.py` 的責任），只鎖定
「這個函式真的把每一張已知的 strategy_version-scoped 表清乾淨」這個單一不變量，未來
新增任何 Shadow* 表時，這個測試會強迫開發者也想到要不要加進這裡。
"""
from __future__ import annotations

from datetime import date

from app.models import (
    ShadowCompletedTrade,
    ShadowMissedCandidate,
    ShadowPortfolioDailySnapshot,
    ShadowPositionLot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    ShadowWinnerTracking,
)
from backfill_shadow_portfolio_replay import _reset_shadow_portfolio_state

STRATEGY_VERSION = "TEST_RESET_VERSION"
OTHER_VERSION = "OTHER_VERSION_SHOULD_SURVIVE"


def _seed_all_tables(db, strategy_version: str) -> None:
    db.add(ShadowVirtualPortfolio(strategy_version=strategy_version, cash=100000.0))
    db.commit()

    position = ShadowVirtualPosition(
        strategy_version=strategy_version, stock_id="1101", stock_name="台泥", first_seen_date=date(2026, 8, 1),
    )
    db.add(position)
    db.commit()
    db.add(
        ShadowPositionLot(
            position_id=position.id, entry_type="EARLY_HEALTHY_PULLBACK",
            entry_signal_date=date(2026, 8, 1), entry_execution_date=date(2026, 8, 4),
            entry_price=100.0, shares=1000.0, allocation=100000.0,
        )
    )
    db.add(
        ShadowStrategyOrder(
            strategy_version=strategy_version, stock_id="1101", stock_name="台泥",
            action="BUY", signal_date=date(2026, 8, 1), scheduled_execution_date=date(2026, 8, 4),
            status="EXECUTED", units=1,
        )
    )
    db.add(
        ShadowStrategyDailyDecision(
            strategy_version=strategy_version, trade_date=date(2026, 8, 4),
            stock_id="1101", stock_name="台泥", action="BUY",
        )
    )
    db.add(
        ShadowPortfolioDailySnapshot(
            strategy_version=strategy_version, trade_date=date(2026, 8, 4),
            cash=0.0, invested_cost=100000.0, total_equity=100000.0, total_return_pct=0.0,
            realized_pnl=0.0, position_count=1, total_units=1,
        )
    )
    db.add(
        ShadowCompletedTrade(
            strategy_version=strategy_version, cycle_number=1, stock_id="1101", stock_name="台泥",
            entry_type="EARLY_HEALTHY_PULLBACK", entry_signal_date=date(2026, 8, 1),
            entry_execution_date=date(2026, 8, 4), entry_price=100.0,
            exit_reason="TAKE_PROFIT", exit_signal_date=date(2026, 8, 10),
            exit_execution_date=date(2026, 8, 11), exit_price=110.0,
            shares=1000.0, allocation=100000.0, realized_pnl=10000.0, realized_return_pct=10.0,
            holding_days=7,
        )
    )
    db.add(
        ShadowMissedCandidate(
            strategy_version=strategy_version, trade_date=date(2026, 8, 4),
            stock_id="2330", stock_name="台積電", skip_reason="SKIP_PORTFOLIO_FULL",
        )
    )
    db.add(
        ShadowWinnerTracking(
            strategy_version=strategy_version, stock_id="1101", first_seen_date=date(2026, 8, 1),
            trade_date=date(2026, 8, 10), winner_10_first_date=date(2026, 8, 10),
            current_actual_return=12.0, highest_actual_return=12.0, drawdown_from_peak_pct=0.0,
        )
    )
    db.commit()


# `ShadowPositionLot` 不直接有 strategy_version 欄位（透過 position_id 關聯到
# ShadowVirtualPosition），驗證時另外處理，不放進這個 directly-filterable 清單。
DIRECTLY_SCOPED_TABLES = [
    ShadowVirtualPortfolio, ShadowVirtualPosition, ShadowStrategyOrder,
    ShadowStrategyDailyDecision, ShadowPortfolioDailySnapshot, ShadowCompletedTrade,
    ShadowMissedCandidate, ShadowWinnerTracking,
]


def test_reset_clears_every_strategy_scoped_table_but_leaves_other_versions_alone(db, monkeypatch):
    session_factory = lambda: db  # noqa: E731 — 測試用 in-memory session，reset 直接重用同一個
    monkeypatch.setattr(
        "backfill_shadow_portfolio_replay.SessionLocal", session_factory, raising=False
    )

    _seed_all_tables(db, STRATEGY_VERSION)
    _seed_all_tables(db, OTHER_VERSION)

    class _NullContextSession:
        """`_reset_shadow_portfolio_state` 用 `with session_factory() as db:`——包一層
        讓測試的 in-memory session 可以被當 context manager 使用，且不會被提早 close
        掉（測試結束後還要用同一個 session 驗證結果）。"""

        def __init__(self, real_db):
            self._db = real_db

        def __enter__(self):
            return self._db

        def __exit__(self, *exc):
            return False

    import logging

    _reset_shadow_portfolio_state(lambda: _NullContextSession(db), STRATEGY_VERSION, logging.getLogger("test"))

    for model in DIRECTLY_SCOPED_TABLES:
        remaining = db.query(model).filter(model.strategy_version == STRATEGY_VERSION).count()
        assert remaining == 0, f"{model.__tablename__} 沒有被 _reset_shadow_portfolio_state 清空"
    assert db.query(ShadowPositionLot).count() == db.query(ShadowPositionLot).join(ShadowVirtualPosition).filter(
        ShadowVirtualPosition.strategy_version == OTHER_VERSION
    ).count(), "ShadowPositionLot 沒有跟著它所屬的 ShadowVirtualPosition 一起被清空"

    # 另一個 strategy_version 完全不受影響（reset 必須嚴格 scoped）
    for model in DIRECTLY_SCOPED_TABLES:
        remaining = db.query(model).filter(model.strategy_version == OTHER_VERSION).count()
        assert remaining >= 1, f"{model.__tablename__} 的其他 strategy_version 資料被誤刪"
