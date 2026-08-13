from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    DailyPrice,
    SignalGenerationJob,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
    SignalWatchStoppedObservation,
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


def test_persist_signal_watch_hits_stores_prompt_version():
    """watchlist item 的 prompt_version 要寫進 SignalWatchHit；缺值 fallback v1。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        job_id = _seed_job_and_snapshot(db, date(2026, 4, 30))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 4, 30),
            {
                "watchlist": [
                    {"stock": "2330", "name": "台積電", "type": "LEADER", "prompt_version": "v2"},
                    {"stock": "2454", "name": "聯發科", "type": "FOLLOWER"},  # 缺 → v1
                ]
            },
            job_id,
        )
        by_id = {
            row.stock_id: row
            for row in db.query(SignalWatchHit)
            .filter(SignalWatchHit.snapshot_date == date(2026, 4, 30))
            .all()
        }
        assert by_id["2330"].prompt_version == "v2"
        assert by_id["2454"].prompt_version == "v1"


def test_persist_signal_watch_hits_stores_recommendation_detail_fields():
    """2026-08-11：正式推薦頁併入魚尾——watchlist item 的 recommendation_thesis／
    relative_advantage／margin_analysis 要寫進 SignalWatchHit；缺值時維持 None。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        job_id = _seed_job_and_snapshot(db, date(2026, 8, 11))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 11),
            {
                "watchlist": [
                    {
                        "stock": "3231",
                        "name": "緯創",
                        "type": "LEADER",
                        "recommendation_thesis": "受惠 AI 伺服器需求延伸",
                        "relative_advantage": "今日候選中排序靠前",
                        "margin_analysis": {"weight_ratio": "個股 70% / 大盤 30%"},
                    },
                    {"stock": "2454", "name": "聯發科", "type": "FOLLOWER"},  # 缺值
                ]
            },
            job_id,
        )
        by_id = {
            row.stock_id: row
            for row in db.query(SignalWatchHit)
            .filter(SignalWatchHit.snapshot_date == date(2026, 8, 11))
            .all()
        }
        assert by_id["3231"].recommendation_thesis == "受惠 AI 伺服器需求延伸"
        assert by_id["3231"].relative_advantage == "今日候選中排序靠前"
        assert by_id["3231"].margin_analysis == {"weight_ratio": "個股 70% / 大盤 30%"}
        assert by_id["2454"].recommendation_thesis is None
        assert by_id["2454"].relative_advantage is None
        assert by_id["2454"].margin_analysis is None


def test_get_archive_detail_returns_latest_recommendation_detail_fields():
    """get_archive_detail 取最新一筆命中的三個補充欄位；跨天以最新一筆為準。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        job_id_1 = _seed_job_and_snapshot(db, date(2026, 8, 10))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 10),
            {
                "watchlist": [
                    {
                        "stock": "3231",
                        "name": "緯創",
                        "type": "LEADER",
                        "recommendation_thesis": "第一天的論點",
                        "relative_advantage": "第一天的優勢",
                    },
                ]
            },
            job_id_1,
        )
        job_id_2 = _seed_job_and_snapshot(db, date(2026, 8, 11))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 11),
            {
                "watchlist": [
                    {
                        "stock": "3231",
                        "name": "緯創",
                        "type": "LEADER",
                        "recommendation_thesis": "第二天的論點",
                        "relative_advantage": "第二天的優勢",
                        "margin_analysis": {"weight_ratio": "個股 70% / 大盤 30%"},
                    },
                ]
            },
            job_id_2,
        )
        detail = archive.get_archive_detail(db, "3231", now=datetime(2026, 8, 11, 20, 0))
        assert detail is not None
        assert detail["recommendation_thesis"] == "第二天的論點"
        assert detail["relative_advantage"] == "第二天的優勢"
        assert detail["margin_analysis"] == {"weight_ratio": "個股 70% / 大盤 30%"}


def test_get_archive_detail_exposes_momentum_score_per_report():
    """2026-08-13：動能分數歷史折線圖用——每筆 report 帶出當天 signal_metrics.momentum_score。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        job_id_1 = _seed_job_and_snapshot(db, date(2026, 8, 10))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 10),
            {
                "watchlist": [
                    {
                        "stock": "3231",
                        "name": "緯創",
                        "type": "LEADER",
                        "signal_metrics": {"momentum_score": 62.5},
                    },
                ]
            },
            job_id_1,
        )
        job_id_2 = _seed_job_and_snapshot(db, date(2026, 8, 11))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 11),
            {
                "watchlist": [
                    {
                        "stock": "3231",
                        "name": "緯創",
                        "type": "LEADER",
                        "signal_metrics": {"momentum_score": 70.1},
                    },
                ]
            },
            job_id_2,
        )
        detail = archive.get_archive_detail(db, "3231", now=datetime(2026, 8, 11, 20, 0))
        assert detail is not None
        by_date = {r["snapshot_date"]: r["momentum_score"] for r in detail["reports"]}
        assert by_date[date(2026, 8, 10)] == 62.5
        assert by_date[date(2026, 8, 11)] == 70.1

    with Session() as db:
        # 舊快照沒有 signal_metrics（None）時，momentum_score 應優雅回 None，不噴例外
        job_id = _seed_job_and_snapshot(db, date(2026, 8, 12))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 8, 12),
            {"watchlist": [{"stock": "6505", "name": "台塑化", "type": "LEADER"}]},
            job_id,
        )
        detail = archive.get_archive_detail(db, "6505", now=datetime(2026, 8, 12, 20, 0))
        assert detail is not None
        assert detail["reports"][0]["momentum_score"] is None


def test_persist_recatch_after_cycle_completion_starts_fresh_cycle():
    """完成 30 日 → 進歷史區 → 同一天又被抓到，要視為全新獨立事件。

    Regression：production session 為 autoflush=False。舊版 persist 先建立帶著
    上一輪 carried state 的新 hit（pending insert），才在後面用
    synchronize_session=False 的 bulk delete 封存刪除舊 cycle —— 刪不到 pending
    insert，導致新 cycle 的第一筆 hit 殘留上一輪的 baseline / max 正負報酬。
    修法：把 refresh_completed_signal_cycles 移到載入 carry 之前。

    這裡刻意用 autoflush=False（對齊 app/database.py），autoflush=True 會意外
    先 flush 掉 pending insert 而遮住這個 bug。
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    first_seen = date(2026, 5, 1)
    completion_day = first_seen + timedelta(days=archive.ARCHIVE_RETENTION_TRADE_DAYS - 1)

    with Session() as db:
        # 30 個交易日價格；baseline（第 2 個交易日）= 100，第 3 天 +50%、第 5 天 -40%
        for i in range(archive.ARCHIVE_RETENTION_TRADE_DAYS):
            d = first_seen + timedelta(days=i)
            price = 100.0
            if d == first_seen + timedelta(days=2):
                price = 150.0
            elif d == first_seen + timedelta(days=4):
                price = 60.0
            db.add(
                DailyPrice(
                    stock_id="9999",
                    trade_date=d,
                    open_price=price,
                    close_price=price,
                )
            )
        # cycle 1 的 active hits（day1..day29），已帶上一輪 baseline / 極值
        for i in range(archive.ARCHIVE_RETENTION_TRADE_DAYS - 1):
            db.add(
                SignalWatchHit(
                    snapshot_date=first_seen + timedelta(days=i),
                    stock_id="9999",
                    stock_name="測試",
                    signal_type="LEADER",
                    industry_name="半導體業",
                    sub_industry="晶圓代工",
                    business_summary="c1",
                    reason="c1",
                    theme={},
                    group_info={},
                    leader_check={},
                    signals={},
                    baseline_trade_date=first_seen + timedelta(days=1),
                    baseline_price=100.0,
                    latest_eval_trade_date=first_seen + timedelta(days=28),
                    latest_eval_price=100.0,
                    return_pct=0.0,
                    max_positive_return_pct=50.0,
                    max_positive_return_trade_date=first_seen + timedelta(days=2),
                    max_negative_return_pct=-40.0,
                    max_negative_return_trade_date=first_seen + timedelta(days=4),
                )
            )
        db.commit()

        job_id = _seed_job_and_snapshot(db, completion_day)
        archive.persist_signal_watch_hits(
            db,
            completion_day,
            {"watchlist": [{"stock": "9999", "name": "測試", "type": "LEADER"}]},
            job_id,
        )

        # 舊 cycle 已封存到歷史區（first_seen = day1，帶自己這輪的極值）
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "9999")
            .one()
        )
        assert completed.first_seen_date == first_seen
        assert completed.max_positive_return_pct == 50.0
        assert completed.max_negative_return_pct == -40.0

        # 新 cycle：完成日的 re-catch 應是全新獨立事件，first_seen = 完成日，
        # 且不帶上一輪的 baseline / max 正負報酬
        active = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == "9999")
            .all()
        )
        assert len(active) == 1
        new_hit = active[0]
        assert new_hit.snapshot_date == completion_day
        assert new_hit.baseline_trade_date is None
        assert new_hit.baseline_price is None
        assert new_hit.max_positive_return_pct is None
        assert new_hit.max_negative_return_pct is None


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


def test_archive_summary_includes_latest_close_and_daily_change():
    """卡片極簡化 UI：summary / detail 每檔要帶 as_of 收盤價 + 當日漲跌幅（相對前一交易日收盤）。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        db.add(
            SignalWatchHit(
                snapshot_date=date(2026, 4, 29),
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
            )
        )
        db.commit()
        _seed_price(db, "2330", date(2026, 4, 29), 99.0, 100.0)
        _seed_price(db, "2330", date(2026, 4, 30), 101.0, 105.0)

        payload = archive.list_archive_summary(db, now=datetime(2026, 4, 30, 23, 0))
        item = payload["items"][0]
        assert item["latest_close_price"] == 105.0
        assert item["daily_change_pct"] == 5.0

        detail = archive.get_archive_detail(db, "2330", now=datetime(2026, 4, 30, 23, 0))
        assert detail is not None
        assert detail["latest_close_price"] == 105.0
        assert detail["daily_change_pct"] == 5.0


def test_archive_summary_close_and_change_handle_missing_price_rows():
    """個股 as_of 當日停牌（無 daily_price row）→ 收盤價與漲跌幅皆 None；
    只有一天資料（無前一交易日收盤）→ 收盤價有值、漲跌幅 None。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def _hit(stock_id: str) -> SignalWatchHit:
        return SignalWatchHit(
            snapshot_date=date(2026, 4, 29),
            stock_id=stock_id,
            stock_name=f"股票{stock_id}",
            signal_type="LEADER",
            industry_name="半導體業",
            sub_industry=None,
            business_summary="a",
            reason="a",
            theme={},
            group_info={},
            leader_check={},
            signals={},
        )

    with Session() as db:
        db.add_all([_hit("2330"), _hit("2317")])
        db.commit()
        # 2330 只有 4/29 有價（4/30 停牌）；2317 只有 4/30 有價（前一日無收盤可比）
        _seed_price(db, "2330", date(2026, 4, 29), 99.0, 100.0)
        _seed_price(db, "2317", date(2026, 4, 30), 200.0, 210.0)

        payload = archive.list_archive_summary(db, now=datetime(2026, 4, 30, 23, 0))
        by_id = {item["stock_id"]: item for item in payload["items"]}
        assert by_id["2330"]["latest_close_price"] is None
        assert by_id["2330"]["daily_change_pct"] is None
        assert by_id["2317"]["latest_close_price"] == 210.0
        assert by_id["2317"]["daily_change_pct"] is None


def test_archive_summary_aggregates_distinct_prompt_versions():
    """同一檔在追蹤 cycle 內跨 v1 / v2 命中 → 摘要顯示 "v1,v2"（集合，非只取最新）。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def _hit(snapshot_date, version):
        return SignalWatchHit(
            snapshot_date=snapshot_date,
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
            prompt_version=version,
        )

    with Session() as db:
        db.add_all([_hit(date(2026, 4, 28), "v1"), _hit(date(2026, 4, 30), "v2")])
        db.commit()
        payload = archive.list_archive_summary(db, now=None)
        item = payload["items"][0]
        assert item["stock_id"] == "2330"
        assert item["prompt_version"] == "v1,v2"


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

        # 2026-08-13：「停止觀察的股票」新表——與 SignalWatchCompletedArchive 平行寫入，
        # 內容完全一致
        stopped = db.query(SignalWatchStoppedObservation).one()
        assert stopped.stock_id == "2330"
        assert stopped.first_seen_date == first_seen
        assert stopped.completed_trade_date == expected_completed
        assert stopped.closure_reason == row.closure_reason
        assert row.return_day_10_pct is not None
        assert row.return_day_20_pct is not None
        assert row.return_day_30_pct is not None
        assert row.max_positive_return_pct is not None
        assert row.max_positive_return_trade_date is not None
        assert row.max_negative_return_pct is None
        assert row.max_negative_return_trade_date is None
        assert row.prompt_version == "v1"  # 缺值 → server_default v1

        payload = archive.list_completed_archive_summary(db)
        assert payload["items"][0]["stock_id"] == "2330"
        assert payload["items"][0]["completed_trade_date"] == expected_completed
        assert payload["items"][0]["max_positive_return_pct"] is not None
        assert payload["items"][0]["closure_reason"] == archive.CLOSURE_REASON_COMPLETED_30_DAYS
        assert payload["items"][0]["prompt_version"] == "v1"


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

        # 2026-08-13：早退結算也要同步寫進「停止觀察的股票」新表
        stopped = (
            db.query(SignalWatchStoppedObservation)
            .filter(SignalWatchStoppedObservation.stock_id == "9999")
            .one()
        )
        assert stopped.closure_reason == archive.CLOSURE_REASON_EARLY_EXIT_STOP_LOSS
        assert stopped.completed_trade_date == date(2026, 4, 6)


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


def test_list_stopped_observations_summary_is_independent_of_completed_archive():
    """2026-08-13：「停止觀察的股票」是獨立的表，不會把 SignalWatchCompletedArchive
    既有（策略大改版前）的舊資料一起列出來——只回這張新表自己的 rows。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        # 舊策略時代留下的歷史紀錄，只在 completed archive
        db.add(
            SignalWatchCompletedArchive(
                stock_id="OLD1",
                stock_name="舊策略股",
                first_seen_date=date(2026, 1, 1),
                latest_hit_date=date(2026, 1, 20),
                hit_count=3,
                latest_signal_type="LEADER",
                completed_trade_date=date(2026, 2, 1),
                closure_reason="completed_30_days",
            )
        )
        # 新策略上線後才被停止觀察的股票，只在新表
        db.add(
            SignalWatchStoppedObservation(
                stock_id="NEW1",
                stock_name="新策略股",
                first_seen_date=date(2026, 8, 10),
                latest_hit_date=date(2026, 8, 12),
                hit_count=3,
                latest_signal_type="LEADER",
                completed_trade_date=date(2026, 8, 13),
                closure_reason="p4_stopped",
            )
        )
        db.commit()

        stopped_data = archive.list_stopped_observations_summary(db)
        assert [item["stock_id"] for item in stopped_data["items"]] == ["NEW1"]

        completed_data = archive.list_completed_archive_summary(db)
        assert [item["stock_id"] for item in completed_data["items"]] == ["OLD1"]


def test_update_signal_watch_returns_early_exit_commits_with_autoflush_false():
    """Regression：production session 為 autoflush=False。

    提前結算股的 hit rows 會先被改寫成 dirty（待 UPDATE），隨後又被 bulk delete。
    若 delete 用 synchronize_session=False，autoflush=False 下這些 dirty row 不會被移出
    session，commit flush 時會對已刪除的 row 發 UPDATE → StaleDataError
    （expected to update N row(s); M were matched）。本測試鏡像 production 設定，
    同時放一檔正常追蹤股一起 dirty，確認整批 commit 成功、各自結果正確。
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    with Session() as db:
        first_seen = date(2026, 4, 1)

        # 提前結算股 9999：baseline 4/2=100，之後連 4 日 < -30% → 4/6 結算
        _seed_price(db, "9999", date(2026, 4, 2), 100.0, 100.0)
        _seed_price(db, "9999", date(2026, 4, 3), 68.0, 68.0)
        _seed_price(db, "9999", date(2026, 4, 4), 67.0, 67.0)
        _seed_price(db, "9999", date(2026, 4, 5), 65.0, 65.0)
        _seed_price(db, "9999", date(2026, 4, 6), 64.0, 64.0)

        # 正常追蹤股 8888：baseline 4/2=50，4/6 收 55（+10%），不觸發提前結算
        _seed_price(db, "8888", date(2026, 4, 2), 50.0, 50.0)
        _seed_price(db, "8888", date(2026, 4, 6), 55.0, 55.0)

        def _hit(stock_id: str, baseline_price: float) -> SignalWatchHit:
            return SignalWatchHit(
                snapshot_date=first_seen,
                stock_id=stock_id,
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
                baseline_price=baseline_price,
                latest_eval_trade_date=date(2026, 4, 2),
                latest_eval_price=baseline_price,
                return_pct=0.0,
            )

        # 9999 給兩列（同 cycle 多 hit），確保 delete 移除多列
        db.add(_hit("9999", 100.0))
        db.add(
            SignalWatchHit(
                snapshot_date=date(2026, 4, 3),
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
                latest_eval_trade_date=date(2026, 4, 3),
                latest_eval_price=68.0,
                return_pct=-32.0,
            )
        )
        db.add(_hit("8888", 50.0))
        db.commit()

        # 修法前：此呼叫的 db.commit() 會丟 StaleDataError；修法後應正常完成
        archive.update_signal_watch_returns(db, as_of_trade_date=date(2026, 4, 6))

        # 提前結算股：active 全清 + 寫入 completed archive
        assert db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "9999").all() == []
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "9999")
            .one()
        )
        assert completed.closure_reason == archive.CLOSURE_REASON_EARLY_EXIT_STOP_LOSS
        assert completed.completed_trade_date == date(2026, 4, 6)

        # 正常追蹤股：未被清掉，報酬率有更新（+10%）
        normal_rows = db.query(SignalWatchHit).filter(SignalWatchHit.stock_id == "8888").all()
        assert len(normal_rows) == 1
        assert normal_rows[0].return_pct == 10.0
        assert (
            db.query(SignalWatchCompletedArchive)
            .filter(SignalWatchCompletedArchive.stock_id == "8888")
            .first()
            is None
        )


def test_persist_signal_watch_hits_stores_signal_metrics():
    """v2.1：watchlist item 的 signal_metrics（動能特徵 JSON）要寫進 SignalWatchHit；缺值為 None。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    metrics = {
        "momentum_score": 76.5,
        "rs_market_percentile_20d": 91.0,
        "rs_industry_percentile_20d": 88.0,
        "return_20d": 12.5,
        "breadth_score": None,
    }
    with Session() as db:
        job_id = _seed_job_and_snapshot(db, date(2026, 7, 15))
        archive.persist_signal_watch_hits(
            db,
            date(2026, 7, 15),
            {
                "watchlist": [
                    {"stock": "2330", "name": "台積電", "type": "LEADER", "signal_metrics": metrics},
                    {"stock": "2454", "name": "聯發科", "type": "FOLLOWER"},  # 缺 → None
                ]
            },
            job_id,
        )
        by_id = {
            row.stock_id: row
            for row in db.query(SignalWatchHit)
            .filter(SignalWatchHit.snapshot_date == date(2026, 7, 15))
            .all()
        }
        assert by_id["2330"].signal_metrics["momentum_score"] == 76.5
        assert by_id["2330"].signal_metrics["rs_market_percentile_20d"] == 91.0
        assert by_id["2454"].signal_metrics is None


def test_carry_initial_selection_metrics_does_not_reset_tracking_origin():
    prior = {
        "initial_recommendation_date": "2026-07-20",
        "initial_recommendation_rank": 3,
        "initial_recommendation_thesis": "首次論點",
        "recommendation_rank": 3,
    }
    current = {
        "initial_recommendation_date": "2026-07-29",
        "initial_recommendation_rank": 1,
        "initial_recommendation_thesis": "今日論點",
        "recommendation_rank": 1,
    }

    merged = archive._carry_initial_selection_metrics(current, prior)

    assert merged["initial_recommendation_date"] == "2026-07-20"
    assert merged["initial_recommendation_rank"] == 3
    assert merged["initial_recommendation_thesis"] == "首次論點"
    assert merged["recommendation_rank"] == 1


def test_not_selected_or_remove_day_does_not_stop_existing_observation():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_date = date(2026, 7, 28)
        first_job = _seed_job_and_snapshot(db, first_date)
        archive.persist_signal_watch_hits(
            db,
            first_date,
            {
                "watchlist": [
                    {
                        "stock": "2330",
                        "name": "台積電",
                        "type": "LEADER",
                        "reason": "首次推薦",
                    }
                ]
            },
            first_job,
        )

        second_date = date(2026, 7, 29)
        second_job = _seed_job_and_snapshot(db, second_date)
        # P3 NOT_SELECTED / REMOVE are snapshot buckets, never passed as watchlist.
        archive.persist_signal_watch_hits(
            db,
            second_date,
            {"watchlist": []},
            second_job,
        )

        rows = db.query(SignalWatchHit).filter_by(stock_id="2330").all()
        assert len(rows) == 1
        assert rows[0].snapshot_date == first_date
        assert archive.clear_signal_watch_hits_for_date(db, second_date) == 0
        assert archive.clear_signal_watch_hits_for_date(db, first_date) == 1


# ──────────────────────────────────────────────────────────────────────────────
# P4 停止觀察 → 魚尾追蹤週期跟著結算（2026-08-11 新增）
# ──────────────────────────────────────────────────────────────────────────────


def test_settle_stock_for_p4_stop_archives_active_cycle():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 7, 20)
        _seed_price(db, "1234", date(2026, 7, 21), 100.0, 100.0)
        _seed_price(db, "1234", date(2026, 7, 22), 108.0, 108.0)

        db.add(
            SignalWatchHit(
                snapshot_date=first_seen,
                stock_id="1234",
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
                baseline_trade_date=date(2026, 7, 21),
                baseline_price=100.0,
                latest_eval_trade_date=date(2026, 7, 22),
                latest_eval_price=108.0,
                return_pct=8.0,
                prompt_version="v7",
            )
        )
        db.commit()

        settled = archive.settle_stock_for_p4_stop(
            db, stock_id="1234", as_of_trade_date=date(2026, 7, 22)
        )
        db.commit()

        assert settled is True
        active = db.query(SignalWatchHit).filter_by(stock_id="1234").all()
        assert active == []

        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter_by(stock_id="1234")
            .one()
        )
        assert completed.closure_reason == archive.CLOSURE_REASON_P4_STOPPED
        assert completed.completed_trade_date == date(2026, 7, 22)
        assert completed.first_seen_date == first_seen
        assert completed.baseline_trade_date == date(2026, 7, 21)
        assert completed.baseline_price == 100.0

        # 2026-08-13：P4 判定停止觀察也要同步寫進「停止觀察的股票」新表——這是這張新表
        # 最主要的資料來源（使用者要求的原始場景）
        stopped = (
            db.query(SignalWatchStoppedObservation)
            .filter_by(stock_id="1234")
            .one()
        )
        assert stopped.closure_reason == archive.CLOSURE_REASON_P4_STOPPED
        assert stopped.completed_trade_date == date(2026, 7, 22)
        assert stopped.first_seen_date == first_seen


def test_settle_stock_for_p4_stop_no_active_cycle_is_noop():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        settled = archive.settle_stock_for_p4_stop(
            db, stock_id="9876", as_of_trade_date=date(2026, 7, 22)
        )

        assert settled is False
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter_by(stock_id="9876")
            .all()
        )
        assert completed == []


def test_settle_stock_for_p4_stop_without_baseline_still_archives():
    """今天才第一次抓到、還沒建立 baseline 就被 P4 判定停止觀察的邊界情境；
    不應該噴例外，報酬相關欄位保持 None（沒有資料可算）。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        first_seen = date(2026, 7, 22)
        db.add(
            SignalWatchHit(
                snapshot_date=first_seen,
                stock_id="5555",
                stock_name="當日新進",
                signal_type="LAGGARD",
                industry_name="航運業",
                sub_industry="x",
                business_summary="a",
                reason="a",
                theme={},
                group_info={},
                leader_check={},
                signals={},
                baseline_trade_date=None,
                baseline_price=None,
                latest_eval_trade_date=None,
                latest_eval_price=None,
                return_pct=None,
            )
        )
        db.commit()

        settled = archive.settle_stock_for_p4_stop(
            db, stock_id="5555", as_of_trade_date=date(2026, 7, 22)
        )
        db.commit()

        assert settled is True
        completed = (
            db.query(SignalWatchCompletedArchive)
            .filter_by(stock_id="5555")
            .one()
        )
        assert completed.closure_reason == archive.CLOSURE_REASON_P4_STOPPED
        assert completed.baseline_trade_date is None
        assert completed.max_positive_return_pct is None
        assert completed.max_negative_return_pct is None
