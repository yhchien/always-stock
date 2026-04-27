import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def ensure_industry_daily_flow_streak_column(engine: Engine) -> None:
    """
    create_all() does not ALTER existing tables, so we add streak manually when
    upgrading an older DB that already has industry_daily_flow.
    """
    inspector = inspect(engine)
    if "industry_daily_flow" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("industry_daily_flow")}
    if "streak" in columns:
        return

    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE industry_daily_flow ADD COLUMN streak INTEGER DEFAULT 0")
        )
    logger.info("Added industry_daily_flow.streak column")
