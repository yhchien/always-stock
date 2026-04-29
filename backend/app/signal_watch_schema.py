import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def ensure_signal_watch_hit_return_columns(engine: Engine) -> None:
    """Backfill M23 return-tracking columns for older DBs."""
    inspector = inspect(engine)
    if "signal_watch_hits" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("signal_watch_hits")}
    wanted = {
        "baseline_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN baseline_trade_date DATE",
        "baseline_price": "ALTER TABLE signal_watch_hits ADD COLUMN baseline_price FLOAT",
        "latest_eval_trade_date": "ALTER TABLE signal_watch_hits ADD COLUMN latest_eval_trade_date DATE",
        "latest_eval_price": "ALTER TABLE signal_watch_hits ADD COLUMN latest_eval_price FLOAT",
        "return_pct": "ALTER TABLE signal_watch_hits ADD COLUMN return_pct FLOAT",
    }

    missing = [name for name in wanted if name not in columns]
    if not missing:
        return

    with engine.begin() as conn:
        for name in missing:
            conn.execute(text(wanted[name]))
    logger.info(
        "Added signal_watch_hits return columns: %s",
        ", ".join(missing),
    )
