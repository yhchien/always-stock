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

        mtk = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "2454").one()
        assert mtk.baseline_trade_date == date(2026, 4, 30)
        assert mtk.baseline_price == 210.0
        assert mtk.latest_eval_trade_date == date(2026, 4, 30)
        assert mtk.latest_eval_price == 210.0
        assert mtk.return_pct == 0.0

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


def test_refresh_completed_signal_cycles_upserts_40_day_archive_rows():
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

        row = db.query(SignalWatchCompletedArchive).one()
        assert row.stock_id == "2330"
        assert row.first_seen_date == first_seen
        assert row.latest_hit_date == first_seen + timedelta(days=18)
        assert row.hit_count == 2
        assert row.latest_signal_type == "FOLLOWER"
        assert row.baseline_trade_date == first_seen + timedelta(days=1)
        assert row.baseline_price == ((102.0 + 103.0) / 2.0)
        assert row.completed_trade_date == first_seen + timedelta(days=39)
        assert row.return_day_10_pct is not None
        assert row.return_day_20_pct is not None
        assert row.return_day_30_pct is not None
        assert row.return_day_40_pct is not None

        payload = archive.list_completed_archive_summary(db)
        assert payload["items"][0]["stock_id"] == "2330"
        assert payload["items"][0]["completed_trade_date"] == first_seen + timedelta(days=39)
