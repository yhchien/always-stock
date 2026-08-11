"""2026-08-11：一次性強制結算所有進行中魚尾追蹤週期的腳本測試。"""

from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, SignalWatchCompletedArchive, SignalWatchHit


@pytest.fixture
def script_module():
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    if "stop_all_active_signal_watch_cycles" in sys.modules:
        return importlib.reload(sys.modules["stop_all_active_signal_watch_cycles"])
    return importlib.import_module("stop_all_active_signal_watch_cycles")


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        yield db


def _seed_active_hit(db, stock_id: str, snapshot_date: date) -> None:
    db.add(
        SignalWatchHit(
            snapshot_date=snapshot_date,
            stock_id=stock_id,
            stock_name=f"Stock-{stock_id}",
            signal_type="LEADER",
            reason="test",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            prompt_version="v7_global_selector",
        )
    )
    db.commit()


def test_dry_run_lists_active_cycles_without_writing(script_module, db_session, capsys, monkeypatch):
    _seed_active_hit(db_session, "3231", date(2026, 8, 10))
    _seed_active_hit(db_session, "2454", date(2026, 8, 11))

    monkeypatch.setattr(script_module, "SessionLocal", lambda: db_session)
    exit_code = script_module.main(["stop_all_active_signal_watch_cycles.py"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "3231" in out
    assert "2454" in out
    assert "dry-run" in out
    # 沒有寫入任何 completed archive、active hits 也還在
    assert db_session.query(SignalWatchCompletedArchive).count() == 0
    assert db_session.query(SignalWatchHit).count() == 2


def test_execute_settles_all_active_cycles(script_module, db_session, monkeypatch):
    _seed_active_hit(db_session, "3231", date(2026, 8, 10))
    _seed_active_hit(db_session, "2454", date(2026, 8, 11))

    monkeypatch.setattr(script_module, "SessionLocal", lambda: db_session)
    exit_code = script_module.main(["stop_all_active_signal_watch_cycles.py", "--execute"])

    assert exit_code == 0
    # active hits 全部清空
    assert db_session.query(SignalWatchHit).count() == 0
    archived = {
        row.stock_id: row
        for row in db_session.query(SignalWatchCompletedArchive).all()
    }
    assert set(archived) == {"3231", "2454"}
    assert archived["3231"].closure_reason == "manual_reset"
    assert archived["2454"].closure_reason == "manual_reset"


def test_dry_run_with_no_active_cycles_reports_nothing_to_do(script_module, db_session, capsys, monkeypatch):
    monkeypatch.setattr(script_module, "SessionLocal", lambda: db_session)
    exit_code = script_module.main(["stop_all_active_signal_watch_cycles.py"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "沒有任何進行中的追蹤週期" in out
