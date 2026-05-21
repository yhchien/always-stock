from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    DailyPrice,
    SignalGenerationJob,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
)
from app.signals import archive


def _seed_job_and_snapshot(db, snapshot_date: date) -> str:
    job_id = str(uuid.uuid4())
    db.add(
        SignalGenerationJob(
            job_id=job_id,
            snapshot_date=snapshot_date,
            triggered_by="cron",
            status="done",
            progress_pct=100,
        )
    )
    db.add(
        SignalSnapshot(
            snapshot_date=snapshot_date,
            market_context={"market_state": "RANGE"},
            watchlist=[],
            removed=[],
            summary={"leader_count": 0, "follower_count": 0, "laggard_count": 0},
            generated_at=None,
            job_id=job_id,
        )
    )
    db.commit()
    return job_id


def _seed_price(db, stock_id: str, trade_date: date, open_price: float, close_price: float) -> None:
    db.add(
        DailyPrice(
            stock_id=stock_id,
            trade_date=trade_date,
            open_price=open_price,
            close_price=close_price,
        )
    )
    db.commit()


def test_persist_signal_watch_hits_carries_prior_return_state():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add(
            SignalWatchHit(
                snapshot_date=date(2026, 4, 28),
                stock_id="2330",
                stock_name="台積電",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="晶圓代工",
                business_summary="old",
                reason="old",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 29),
                baseline_price=105.0,
                latest_eval_trade_date=date(2026, 4, 29),
                latest_eval_price=105.0,
                return_pct=0.0,
            )
        )
        db.commit()
        job_id = _seed_job_and_snapshot(db, date(2026, 4, 30))

        archive.persist_signal_watch_hits(
            db,
            date(2026, 4, 30),
            {
                "watchlist": [
                    {
                        "stock": "2330",
                        "name": "台積電",
                        "type": "FOLLOWER",
                        "industry": "半導體業",
                        "sub_industry": "晶圓代工",
                        "business_summary": "new",
                        "reason": "new",
                    }
                ]
            },
            job_id,
        )

        latest = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == "2330", SignalWatchHit.snapshot_date == date(2026, 4, 30))
            .one()
        )
        assert latest.baseline_trade_date == date(2026, 4, 29)
        assert latest.baseline_price == 105.0
        assert latest.latest_eval_trade_date == date(2026, 4, 29)
        assert latest.latest_eval_price == 105.0
        assert latest.return_pct == 0.0


def test_update_signal_watch_returns_applies_requested_rules():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 28),
                    stock_id="2330",
                    stock_name="台積電",
                    signal_type="LEADER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="a",
                    reason="a",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                    baseline_trade_date=date(2026, 4, 29),
                    baseline_price=105.0,
                    latest_eval_trade_date=date(2026, 4, 29),
                    latest_eval_price=105.0,
                    return_pct=0.0,
                ),
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 29),
                    stock_id="2454",
                    stock_name="聯發科",
                    signal_type="FOLLOWER",
                    industry_name="半導體業",
                    sub_industry="IC 設計",
                    business_summary="b",
                    reason="b",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 30),
                    stock_id="2454",
                    stock_name="聯發科",
                    signal_type="FOLLOWER",
                    industry_name="半導體業",
                    sub_industry="IC 設計",
                    business_summary="b2",
                    reason="b2",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 30),
                    stock_id="2317",
                    stock_name="鴻海",
                    signal_type="LEADER",
                    industry_name="其他電子業",
                    sub_industry="EMS",
                    business_summary="c",
                    reason="c",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
            ]
        )
        db.commit()
        _seed_price(db, "2330", date(2026, 4, 30), 110.0, 120.0)
        _seed_price(db, "2454", date(2026, 4, 30), 200.0, 220.0)
        _seed_price(db, "2317", date(2026, 4, 30), 150.0, 160.0)

        updated = archive.update_signal_watch_returns(
            db,
            as_of_trade_date=date(2026, 4, 30),
        )
        assert updated == 2

        tsmc = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "2330").one()
        assert tsmc.latest_eval_trade_date == date(2026, 4, 30)
        assert tsmc.latest_eval_price == 120.0
        assert tsmc.return_pct == ((120.0 - 105.0) / 105.0) * 100.0
        assert tsmc.max_positive_return_pct == ((120.0 - 105.0) / 105.0) * 100.0
        assert tsmc.max_positive_return_trade_date == date(2026, 4, 30)
        assert tsmc.max_negative_return_pct is None
        assert tsmc.max_negative_return_trade_date is None

        mtk_rows = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == "2454")
            .order_by(SignalWatchHit.snapshot_date.asc())
            .all()
        )
        assert len(mtk_rows) == 2
        for mtk in mtk_rows:
            assert mtk.baseline_trade_date == date(2026, 4, 30)
            assert mtk.baseline_price == 210.0
            assert mtk.latest_eval_trade_date == date(2026, 4, 30)
            assert mtk.latest_eval_price == 210.0
            assert mtk.return_pct == 0.0
            assert mtk.max_positive_return_pct is None
            assert mtk.max_negative_return_pct is None

        honhai = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "2317").one()
        assert honhai.baseline_trade_date is None
        assert honhai.baseline_price is None
        assert honhai.latest_eval_trade_date is None
        assert honhai.latest_eval_price is None
        assert honhai.return_pct is None


def test_archive_summary_uses_persisted_return_fields():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add(
            SignalWatchHit(
                snapshot_date=date(2026, 4, 30),
                stock_id="2330",
                stock_name="台積電",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="晶圓代工",
                business_summary="a",
                reason="a",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 29),
                baseline_price=105.0,
                latest_eval_trade_date=date(2026, 4, 30),
                latest_eval_price=120.0,
                return_pct=((120.0 - 105.0) / 105.0) * 100.0,
                max_positive_return_pct=((120.0 - 105.0) / 105.0) * 100.0,
                max_positive_return_trade_date=date(2026, 4, 30),
                max_negative_return_pct=None,
                max_negative_return_trade_date=None,
            )
        )
        db.commit()

        payload = archive.list_archive_summary(
            db,
            now=None,
        )
        item = payload["items"][0]
        assert item["baseline_trade_date"] == date(2026, 4, 29)
        assert item["baseline_price"] == 105.0
        assert item["latest_eval_trade_date"] == date(2026, 4, 30)
        assert item["latest_eval_price"] == 120.0
        assert item["return_pct"] == ((120.0 - 105.0) / 105.0) * 100.0
        assert item["max_positive_return_pct"] == ((120.0 - 105.0) / 105.0) * 100.0
        assert item["max_positive_return_trade_date"] == date(2026, 4, 30)
        assert item["max_negative_return_pct"] is None
        assert item["max_negative_return_trade_date"] is None


def test_update_signal_watch_returns_keeps_baseline_day_at_zero_on_rerun():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 29),
                    stock_id="2421",
                    stock_name="建準",
                    signal_type="FOLLOWER",
                    industry_name="電機機械",
                    sub_industry="車用機械傳動設備及零配件",
                    business_summary="a",
                    reason="a",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                    baseline_trade_date=date(2026, 4, 30),
                    baseline_price=73.5,
                    latest_eval_trade_date=date(2026, 4, 30),
                    latest_eval_price=73.5,
                    return_pct=0.0,
                ),
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 30),
                    stock_id="2421",
                    stock_name="建準",
                    signal_type="FOLLOWER",
                    industry_name="電機機械",
                    sub_industry="車用機械傳動設備及零配件",
                    business_summary="b",
                    reason="b",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                    baseline_trade_date=date(2026, 4, 30),
                    baseline_price=73.5,
                    latest_eval_trade_date=date(2026, 4, 30),
                    latest_eval_price=73.5,
                    return_pct=0.0,
                ),
            ]
        )
        db.commit()
        _seed_price(db, "2421", date(2026, 4, 30), 75.0, 72.0)

        updated = archive.update_signal_watch_returns(
            db,
            as_of_trade_date=date(2026, 4, 30),
        )
        assert updated == 1

        rows = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == "2421")
            .order_by(SignalWatchHit.snapshot_date.asc())
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.baseline_trade_date == date(2026, 4, 30)
            assert row.baseline_price == 73.5
            assert row.latest_eval_trade_date == date(2026, 4, 30)
            assert row.latest_eval_price == 73.5
            assert row.return_pct == 0.0


def test_update_signal_watch_returns_tracks_cycle_extrema_from_first_seen():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add_all(
            [
                SignalWatchHit(
                    snapshot_date=date(2026, 4, 28),
                    stock_id="2330",
                    stock_name="台積電",
                    signal_type="LEADER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="a",
                    reason="a",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                    baseline_trade_date=date(2026, 4, 29),
                    baseline_price=100.0,
                    latest_eval_trade_date=date(2026, 4, 29),
                    latest_eval_price=100.0,
                    return_pct=0.0,
                ),
                SignalWatchHit(
                    snapshot_date=date(2026, 5, 1),
                    stock_id="2330",
                    stock_name="台積電",
                    signal_type="FOLLOWER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="b",
                    reason="b",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
            ]
        )
        db.commit()
        _seed_price(db, "2330", date(2026, 4, 30), 90.0, 95.0)
        _seed_price(db, "2330", date(2026, 5, 2), 140.0, 145.0)

        updated = archive.update_signal_watch_returns(
            db,
            as_of_trade_date=date(2026, 5, 2),
        )
        assert updated == 1

        rows = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == "2330")
            .order_by(SignalWatchHit.snapshot_date.asc())
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.baseline_trade_date == date(2026, 4, 29)
            assert row.max_positive_return_pct == 45.0
            assert row.max_positive_return_trade_date == date(2026, 5, 2)
            assert row.max_negative_return_pct == -5.0
            assert row.max_negative_return_trade_date == date(2026, 4, 30)


def test_refresh_completed_signal_cycles_upserts_full_cycle_archive_rows():
    """2026-05-21 起 retention=30 個交易日；測試 seed 仍給 40 天資料以涵蓋 day_n=40 edge case。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 1, 2)
        for day in range(1, 41):
            trade_date = first_seen + timedelta(days=day - 1)
            _seed_price(
                db,
                "2330",
                trade_date,
                open_price=100.0 + day,
                close_price=101.0 + day,
            )
        db.add_all(
            [
                SignalWatchHit(
                    snapshot_date=first_seen,
                    stock_id="2330",
                    stock_name="台積電",
                    signal_type="LEADER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="a",
                    reason="第一次",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
                SignalWatchHit(
                    snapshot_date=first_seen + timedelta(days=18),
                    stock_id="2330",
                    stock_name="台積電",
                    signal_type="FOLLOWER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="b",
                    reason="第二次",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                ),
            ]
        )
        db.commit()

        upserted = archive.refresh_completed_signal_cycles(
            db,
            as_of_trade_date=first_seen + timedelta(days=39),
        )
        assert upserted == 1
        db.commit()

        # retention=30 → completed_trade_date 是第 30 個交易日（first_seen + 29 calendar days，
        # 因 seed 為連續每日資料 trade_date 與 calendar 一一對應）。
        expected_completed = first_seen + timedelta(days=archive.ARCHIVE_RETENTION_TRADE_DAYS - 1)
        row = db.query(SignalWatchCompletedArchive).one()
        assert row.stock_id == "2330"
        assert row.first_seen_date == first_seen
        assert row.latest_hit_date == first_seen + timedelta(days=18)
        assert row.hit_count == 2
        assert row.latest_signal_type == "FOLLOWER"
        assert row.baseline_trade_date == first_seen + timedelta(days=1)
        assert row.baseline_price == ((102.0 + 103.0) / 2.0)
        assert row.completed_trade_date == expected_completed
        assert row.return_day_10_pct is not None
        assert row.return_day_20_pct is not None
        assert row.return_day_30_pct is not None
        assert row.max_positive_return_pct is not None
        assert row.max_positive_return_trade_date is not None
        assert row.max_negative_return_pct is None
        assert row.max_negative_return_trade_date is None

        payload = archive.list_completed_archive_summary(db)
        assert payload["items"][0]["stock_id"] == "2330"
        assert payload["items"][0]["completed_trade_date"] == expected_completed
        assert payload["items"][0]["max_positive_return_pct"] is not None
        assert payload["items"][0]["closure_reason"] == archive.CLOSURE_REASON_COMPLETED_30_DAYS


def _seed_consecutive_prices(
    db,
    stock_id: str,
    start_date: date,
    daily_close: list[float],
) -> list[date]:
    """連續交易日 seed close_price（用 day_offset 等於日曆日簡化測試）."""
    dates: list[date] = []
    for offset, close_price in enumerate(daily_close):
        trade_date = start_date + timedelta(days=offset)
        _seed_price(db, stock_id, trade_date, open_price=close_price, close_price=close_price)
        dates.append(trade_date)
    return dates


def test_resolve_early_exit_settle_date_pure_logic():
    # 一路都 >= -30% → 不結算
    assert archive._resolve_early_exit_settle_date(
        [(date(2026, 5, 1), -10.0), (date(2026, 5, 2), -25.0), (date(2026, 5, 3), -28.0)]
    ) is None

    # 跌破當天且 grace 還沒過完（只有 1 天）→ 不結算
    assert archive._resolve_early_exit_settle_date(
        [(date(2026, 5, 1), -32.0)]
    ) is None

    # 跌破 + grace 2 天都 < -30% → grace 還差 1 天 → 不結算
    assert archive._resolve_early_exit_settle_date(
        [
            (date(2026, 5, 1), -32.0),
            (date(2026, 5, 2), -33.0),
            (date(2026, 5, 3), -35.0),
        ]
    ) is None

    # 跌破 + grace 3 天都 < -30% → 在 D+3 結算
    assert archive._resolve_early_exit_settle_date(
        [
            (date(2026, 5, 1), -32.0),
            (date(2026, 5, 2), -33.0),
            (date(2026, 5, 3), -35.0),
            (date(2026, 5, 4), -31.0),
        ]
    ) == date(2026, 5, 4)

    # grace 期間任一天漲回 -30% 以上 → 警示重置；之後若再跌破要重新等
    assert archive._resolve_early_exit_settle_date(
        [
            (date(2026, 5, 1), -32.0),
            (date(2026, 5, 2), -28.0),  # 漲回 → 重置
            (date(2026, 5, 3), -33.0),  # 再次跌破，這是新的 D
            (date(2026, 5, 4), -34.0),
        ]
    ) is None  # 新 D 之後只有 1 天 grace，還沒過完


def test_update_signal_watch_returns_early_exits_after_three_day_grace():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 4, 1)
        # baseline_trade_date = 4/2, baseline_price = 100；之後 4 個交易日全都 < -30%
        # 4/3 -32%, 4/4 -33%, 4/5 -35%, 4/6 -36% → 4/6 應結算
        baseline_close = 100.0
        baseline_open = 100.0
        _seed_price(db, "9999", date(2026, 4, 2), baseline_open, baseline_close)
        _seed_price(db, "9999", date(2026, 4, 3), 68.0, 68.0)
        _seed_price(db, "9999", date(2026, 4, 4), 67.0, 67.0)
        _seed_price(db, "9999", date(2026, 4, 5), 65.0, 65.0)
        _seed_price(db, "9999", date(2026, 4, 6), 64.0, 64.0)

        db.add(
            SignalWatchHit(
                snapshot_date=first_seen,
                stock_id="9999",
                stock_name="測試股",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="x",
                business_summary="a",
                reason="a",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 2),
                baseline_price=100.0,
                latest_eval_trade_date=date(2026, 4, 2),
                latest_eval_price=100.0,
                return_pct=0.0,
            )
        )
        db.commit()

        archive.update_signal_watch_returns(db, as_of_trade_date=date(2026, 4, 6))

        # active table 中該股 row 已全部清掉
        active = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "9999").all()
        assert active == []

        # completed archive 有一筆 early_exit_stop_loss，completed_trade_date = 4/6
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "9999")
            .one()
        )
        assert completed.closure_reason == archive.CLOSURE_REASON_EARLY_EXIT_STOP_LOSS
        assert completed.completed_trade_date == date(2026, 4, 6)
        assert completed.first_seen_date == first_seen
        assert completed.baseline_trade_date == date(2026, 4, 2)
        assert completed.baseline_price == 100.0


def test_update_signal_watch_returns_does_not_early_exit_when_grace_recovers():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 4, 1)
        # baseline 4/2 = 100；4/3 -32%, 4/4 -28%（漲回 → 警示解除），4/5 -33%（重新觸發但 grace 還沒過）
        _seed_price(db, "8888", date(2026, 4, 2), 100.0, 100.0)
        _seed_price(db, "8888", date(2026, 4, 3), 68.0, 68.0)
        _seed_price(db, "8888", date(2026, 4, 4), 72.0, 72.0)
        _seed_price(db, "8888", date(2026, 4, 5), 67.0, 67.0)

        db.add(
            SignalWatchHit(
                snapshot_date=first_seen,
                stock_id="8888",
                stock_name="反彈股",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="x",
                business_summary="a",
                reason="a",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 2),
                baseline_price=100.0,
                latest_eval_trade_date=date(2026, 4, 2),
                latest_eval_price=100.0,
                return_pct=0.0,
            )
        )
        db.commit()

        archive.update_signal_watch_returns(db, as_of_trade_date=date(2026, 4, 5))

        # active table 中該股 row 仍存在（未提前結算）
        active = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "8888").all()
        assert len(active) == 1
        # completed archive 中沒有該股
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "8888")
            .all()
        )
        assert completed == []


# ──────────────────────────────────────────────────────────────────────────────
# Drawdown-from-peak 提前結算規則（2026-05-18 新增）
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_drawdown_exit_settle_date_pure_logic():
    """規則 2：max_positive 必須 > 0 才觸發；漲過再跌下來、差距 >= 30% 連續 3 天結算。"""

    # 從未漲過正報酬 → max_positive 永遠 = 0 → 永遠不觸發
    assert archive._resolve_drawdown_exit_settle_date(
        [(date(2026, 5, 1), -10.0), (date(2026, 5, 2), -25.0), (date(2026, 5, 3), -40.0)]
    ) is None

    # 漲過 +10% 但回落只到 -10%（drawdown 20%）→ 沒到 30% threshold → 不觸發
    assert archive._resolve_drawdown_exit_settle_date(
        [(date(2026, 5, 1), 10.0), (date(2026, 5, 2), -10.0)]
    ) is None

    # 漲過 +15% 回落到 -15%（drawdown 30%）：觸發日 D=5/2，需 D+1, D+2, D+3 寬限都仍 >= 30%
    # 5/3 -16%（drawdown 31）/ 5/4 -17%（drawdown 32）/ 5/5 -18%（drawdown 33）→ 5/5 結算
    assert archive._resolve_drawdown_exit_settle_date(
        [
            (date(2026, 5, 1), 15.0),
            (date(2026, 5, 2), -15.0),  # D
            (date(2026, 5, 3), -16.0),  # D+1
            (date(2026, 5, 4), -17.0),  # D+2
            (date(2026, 5, 5), -18.0),  # D+3 結算
        ]
    ) == date(2026, 5, 5)

    # 寬限期未跑完（只到 D+2）→ 不結算
    assert archive._resolve_drawdown_exit_settle_date(
        [
            (date(2026, 5, 1), 15.0),
            (date(2026, 5, 2), -15.0),
            (date(2026, 5, 3), -16.0),
            (date(2026, 5, 4), -17.0),
        ]
    ) is None

    # 寬限期任一天回到 -30% 以內 → 警示解除
    assert archive._resolve_drawdown_exit_settle_date(
        [
            (date(2026, 5, 1), 15.0),
            (date(2026, 5, 2), -15.0),   # D：drawdown 30
            (date(2026, 5, 3), -10.0),   # 回落差距 = 25，警示解除
            (date(2026, 5, 4), -12.0),   # 仍 < threshold
            (date(2026, 5, 5), -14.0),
        ]
    ) is None

    # max_positive 必須 > 0：漲到 +5% 後回落到 -25%（drawdown 30）也要觸發
    settle = archive._resolve_drawdown_exit_settle_date(
        [
            (date(2026, 5, 1), 5.0),
            (date(2026, 5, 2), -25.0),  # D
            (date(2026, 5, 3), -26.0),  # D+1
            (date(2026, 5, 4), -27.0),  # D+2
            (date(2026, 5, 5), -28.0),  # D+3
        ]
    )
    assert settle == date(2026, 5, 5)


def test_update_signal_watch_returns_drawdown_rule_triggers_earlier_than_stop_loss():
    """規則 1 vs 規則 2 並存：取較早觸發者，並寫對應 closure_reason。

    場景：先漲後跌的股票，drawdown 規則先觸發（return 還在 -25% 但 drawdown 已 30%）。
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 4, 1)
        # baseline = 4/2 close = 100
        # 4/3 +15% → 115 (max_positive=15)
        # 4/4 -15% → 85 (drawdown=30) D
        # 4/5 -20% → 80 (drawdown=35) D+1
        # 4/6 -22% → 78 (drawdown=37) D+2
        # 4/7 -25% → 75 (drawdown=40) D+3 → drawdown 結算
        # 此時 return=-25% 還沒到 stop_loss 規則的 -30%
        for d, price in [
            (date(2026, 4, 2), 100.0),
            (date(2026, 4, 3), 115.0),
            (date(2026, 4, 4), 85.0),
            (date(2026, 4, 5), 80.0),
            (date(2026, 4, 6), 78.0),
            (date(2026, 4, 7), 75.0),
        ]:
            _seed_price(db, "7777", d, price, price)

        db.add(
            SignalWatchHit(
                snapshot_date=first_seen,
                stock_id="7777",
                stock_name="先漲後跌股",
                signal_type="LEADER",
                industry_name="半導體業",
                sub_industry="x",
                business_summary="",
                reason="",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=date(2026, 4, 2),
                baseline_price=100.0,
                latest_eval_trade_date=date(2026, 4, 2),
                latest_eval_price=100.0,
                return_pct=0.0,
            )
        )
        db.commit()

        archive.update_signal_watch_returns(db, as_of_trade_date=date(2026, 4, 7))

        # active 已清空、completed 有 drawdown 結算 row
        active = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "7777").all()
        assert active == []

        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "7777")
            .one()
        )
        assert completed.closure_reason == archive.CLOSURE_REASON_EARLY_EXIT_DRAWDOWN
        assert completed.completed_trade_date == date(2026, 4, 7)
        assert completed.max_positive_return_pct is not None
        assert completed.max_positive_return_pct >= 14.9  # +15% 左右


# ──────────────────────────────────────────────────────────────────────────────
# Half-year period filter
# ──────────────────────────────────────────────────────────────────────────────


def test_half_year_period_start_aligns_to_anchor():
    """半年區間切割：2026-05-01 起算，每 6 個月一段。"""
    # anchor 之前 → 對齊 anchor
    assert archive.half_year_period_start(date(2026, 4, 30)) == date(2026, 5, 1)
    # 區間內任一天 → 起始日 = 2026-05-01
    assert archive.half_year_period_start(date(2026, 5, 1)) == date(2026, 5, 1)
    assert archive.half_year_period_start(date(2026, 7, 15)) == date(2026, 5, 1)
    assert archive.half_year_period_start(date(2026, 10, 31)) == date(2026, 5, 1)
    # 第二段 = 2026-11-01 ~ 2027-04-30
    assert archive.half_year_period_start(date(2026, 11, 1)) == date(2026, 11, 1)
    assert archive.half_year_period_start(date(2027, 1, 1)) == date(2026, 11, 1)
    assert archive.half_year_period_start(date(2027, 4, 30)) == date(2026, 11, 1)
    # 第三段 = 2027-05-01 起
    assert archive.half_year_period_start(date(2027, 5, 1)) == date(2027, 5, 1)
    assert archive.half_year_period_start(date(2027, 12, 31)) == date(2027, 11, 1)


def test_half_year_period_end_is_inclusive_last_day():
    assert archive.half_year_period_end(date(2026, 5, 1)) == date(2026, 10, 31)
    assert archive.half_year_period_end(date(2026, 11, 1)) == date(2027, 4, 30)
    assert archive.half_year_period_end(date(2027, 5, 1)) == date(2027, 10, 31)


def test_list_completed_archive_summary_period_filter_returns_only_matching_rows():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def _seed_completed(db, stock_id: str, completed: date) -> None:
        db.add(
            SignalWatchCompletedArchive(
                stock_id=stock_id,
                stock_name=f"股{stock_id}",
                industry_name="半導體業",
                first_seen_date=completed - timedelta(days=30),
                latest_hit_date=completed - timedelta(days=10),
                hit_count=2,
                latest_signal_type="LEADER",
                completed_trade_date=completed,
                closure_reason="completed_30_days",
            )
        )

    with Session() as db:
        _seed_completed(db, "AAAA", date(2026, 6, 15))    # 1st half period
        _seed_completed(db, "BBBB", date(2026, 10, 31))   # 1st half period (boundary)
        _seed_completed(db, "CCCC", date(2026, 11, 1))    # 2nd half period (boundary)
        _seed_completed(db, "DDDD", date(2027, 3, 1))     # 2nd half period
        db.commit()

        # 不帶 period → 回全部 4 個 + periods meta 列出 2 段
        all_data = archive.list_completed_archive_summary(db)
        assert len(all_data["items"]) == 4
        assert len(all_data["periods"]) == 2
        # periods 倒序（最新先）
        assert all_data["periods"][0]["period_start"] == date(2026, 11, 1)
        assert all_data["periods"][1]["period_start"] == date(2026, 5, 1)
        assert all_data["periods"][0]["count"] == 2  # CCCC + DDDD
        assert all_data["periods"][1]["count"] == 2  # AAAA + BBBB

        # 指定 period_start=2026-05-01 → 只回 AAAA + BBBB
        first_half = archive.list_completed_archive_summary(
            db, period_start=date(2026, 5, 1)
        )
        stock_ids = sorted(item["stock_id"] for item in first_half["items"])
        assert stock_ids == ["AAAA", "BBBB"]
        assert first_half["selected_period_start"] == date(2026, 5, 1)
