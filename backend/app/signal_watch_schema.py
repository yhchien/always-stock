import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def ensure_signal_watch_hit_return_columns(engine: Engine) -> None:
    """Backfill M23 archive tracking columns for older DBs."""
    inspector = inspect(engine)
    table_alters = {
        "signal_snapshots": {
            "prompt_version": "ALTER TABLE signal_snapshots ADD COLUMN prompt_version VARCHAR(16) NOT NULL DEFAULT 'v1'",
        },
        "signal_watch_hits": {
            "baseline_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN baseline_trade_date DATE",
            "baseline_price": "ALTER TABLE signal_watch_hits ADD COLUMN baseline_price FLOAT",
            "latest_eval_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN latest_eval_trade_date DATE",
            "latest_eval_price": "ALTER TABLE signal_watch_hits ADD COLUMN latest_eval_price FLOAT",
            "return_pct": "ALTER TABLE signal_watch_hits ADD COLUMN return_pct FLOAT",
            "max_positive_return_pct": "ALTER TABLE signal_watch_hits ADD COLUMN max_positive_return_pct FLOAT",
            "max_positive_return_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN max_positive_return_trade_date DATE",
            "max_negative_return_pct": "ALTER TABLE signal_watch_hits ADD COLUMN max_negative_return_pct FLOAT",
            "max_negative_return_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN max_negative_return_trade_date DATE",
            "prompt_version": "ALTER TABLE signal_watch_hits ADD COLUMN prompt_version VARCHAR(16) NOT NULL DEFAULT 'v1'",
            # v2.1 fishtail momentum upgrade（2026-07-15）：spec §9.2 第一批動能特徵 JSON
            "signal_metrics": "ALTER TABLE signal_watch_hits ADD COLUMN signal_metrics JSON",
            # 2026-08-11：正式推薦頁併入魚尾單一入口，補這三欄讓魚尾詳情 popup 能顯示完整內容
            "recommendation_thesis": "ALTER TABLE signal_watch_hits ADD COLUMN recommendation_thesis TEXT",
            "relative_advantage": "ALTER TABLE signal_watch_hits ADD COLUMN relative_advantage TEXT",
            "margin_analysis": "ALTER TABLE signal_watch_hits ADD COLUMN margin_analysis JSON",
        },
        "signal_watch_completed_archives": {
            "max_positive_return_pct": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_positive_return_pct FLOAT",
            "max_positive_return_trade_date": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_positive_return_trade_date DATE",
            "max_negative_return_pct": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_negative_return_pct FLOAT",
            "max_negative_return_trade_date": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_negative_return_trade_date DATE",
            "closure_reason": "ALTER TABLE signal_watch_completed_archives ADD COLUMN closure_reason VARCHAR(32) NOT NULL DEFAULT 'completed_30_days'",
            "prompt_version": "ALTER TABLE signal_watch_completed_archives ADD COLUMN prompt_version VARCHAR(16) NOT NULL DEFAULT 'v1'",
        },
        "signal_observation_reviews": {
            # 2026-08-13：動能分數歷史折線圖用，見 models.py SignalObservationReview.momentum_score 註解
            "momentum_score": "ALTER TABLE signal_observation_reviews ADD COLUMN momentum_score FLOAT",
        },
    }

    for table_name, wanted in table_alters.items():
        if table_name not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = [name for name in wanted if name not in columns]
        if not missing:
            continue
        with engine.begin() as conn:
            for name in missing:
                conn.execute(text(wanted[name]))
        logger.info(
            "Added %s columns: %s",
            table_name,
            ", ".join(missing),
        )


def migrate_completed_archive_to_30_days(engine: Engine) -> None:
    """2026-05-21：retention 40 → 30 一次性 idempotent migration。

    三步驟（全部 SQL，整段包 try/except，失敗 logger.warning 不阻擋 app 啟動）：
      1. DROP COLUMN return_day_40_pct（PostgreSQL `IF EXISTS` 確保 idempotent）
      2. UPDATE closure_reason WHERE = 'completed_40_days' → 'completed_30_days'
      3. ALTER COLUMN closure_reason SET DEFAULT 'completed_30_days'

    SQLite 沒走這條路（測試以 in-memory + create_all 直接用新 schema），所以只針對
    Postgres 設計。各步驟獨立 try：DROP / ALTER DEFAULT 失敗不致命（既有 schema 已對），
    UPDATE 失敗也只是新舊 row 並存，前端 ClosureReasonChip 已對齊新值。
    """
    inspector = inspect(engine)
    if "signal_watch_completed_archives" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("signal_watch_completed_archives")}
    has_legacy_column = "return_day_40_pct" in columns

    if has_legacy_column:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE signal_watch_completed_archives "
                    "DROP COLUMN IF EXISTS return_day_40_pct"
                ))
            logger.info("Dropped legacy column signal_watch_completed_archives.return_day_40_pct")
        except SQLAlchemyError:
            logger.warning(
                "Failed to drop signal_watch_completed_archives.return_day_40_pct; "
                "may be SQLite or insufficient privilege",
                exc_info=True,
            )

    try:
        with engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE signal_watch_completed_archives "
                "SET closure_reason = 'completed_30_days' "
                "WHERE closure_reason = 'completed_40_days'"
            ))
            if result.rowcount:
                logger.info(
                    "Updated %s completed_archive rows: closure_reason 40d → 30d",
                    result.rowcount,
                )
    except SQLAlchemyError:
        logger.warning(
            "Failed to UPDATE legacy closure_reason='completed_40_days'",
            exc_info=True,
        )

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE signal_watch_completed_archives "
                "ALTER COLUMN closure_reason SET DEFAULT 'completed_30_days'"
            ))
    except SQLAlchemyError:
        logger.warning(
            "Failed to ALTER DEFAULT for closure_reason; new inserts still use Python default",
            exc_info=True,
        )


def widen_completed_archive_prompt_version_column(engine: Engine) -> None:
    """2026-08-11：`signal_watch_completed_archives.prompt_version` 存的是整個追蹤
    cycle 涵蓋的 prompt 版本**集合**（`_distinct_versions()`，逗號相連，如
    "v6,v7_research"），不是單一版本——原本 VARCHAR(16) 是比照單一版本的欄位寬度設的，
    版本 token 變長（`v7_research` 本身就 11 字元）之後，一個 cycle 只要跨過 2~3 個版本
    就可能超過 16 字元，寫入時直接被 Postgres 拒絕（StringDataRightTruncation），不是
    默默截斷。widen 到 VARCHAR(64) 給足夠安全邊界。純粹放寬長度限制（metadata-only
    ALTER，不搬動既有資料），SQLite（測試環境）不強制執行 VARCHAR 長度所以不需要處理。
    """
    inspector = inspect(engine)
    if "signal_watch_completed_archives" not in inspector.get_table_names():
        return
    columns = {
        column["name"]: column
        for column in inspector.get_columns("signal_watch_completed_archives")
    }
    column = columns.get("prompt_version")
    if column is None:
        return
    current_length = getattr(column["type"], "length", None)
    if current_length is not None and current_length >= 64:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE signal_watch_completed_archives "
                "ALTER COLUMN prompt_version TYPE VARCHAR(64)"
            ))
        logger.info(
            "Widened signal_watch_completed_archives.prompt_version to VARCHAR(64)"
        )
    except SQLAlchemyError:
        logger.warning(
            "Failed to widen signal_watch_completed_archives.prompt_version column",
            exc_info=True,
        )
