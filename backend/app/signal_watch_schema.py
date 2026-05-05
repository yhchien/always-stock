import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def ensure_signal_watch_hit_return_columns(engine: Engine) -> None:
    """Backfill M23 archive tracking columns for older DBs."""
    inspector = inspect(engine)
    table_alters = {
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
        },
        "signal_watch_completed_archives": {
            "max_positive_return_pct": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_positive_return_pct FLOAT",
            "max_positive_return_trade_date": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_positive_return_trade_date DATE",
            "max_negative_return_pct": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_negative_return_pct FLOAT",
            "max_negative_return_trade_date": "ALTER TABLE signal_watch_completed_archives ADD COLUMN max_negative_return_trade_date DATE",
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
