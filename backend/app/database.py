import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "db" / "tw_stock.db"
SQLITE_LOCK_TIMEOUT_SECONDS = 2
load_dotenv(BACKEND_DIR / ".env")


def resolve_sqlite_path(db_path: str) -> Path:
    path = Path(db_path).expanduser()
    if path.is_absolute():
        return path

    for base_dir in (PROJECT_ROOT, BACKEND_DIR, Path.cwd()):
        candidate = (base_dir / path).resolve()
        if candidate.exists():
            return candidate

    return (PROJECT_ROOT / path).resolve()


def normalize_sqlite_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme != "sqlite":
        return database_url

    # Keep in-memory or special sqlite URLs unchanged.
    if parsed.path in {":memory:", "/:memory:"} or parsed.netloc:
        return database_url

    resolved_path = resolve_sqlite_path(parsed.path.lstrip("/"))
    normalized_url = f"sqlite:///{resolved_path}"
    if parsed.query:
        normalized_url = f"{normalized_url}?{parsed.query}"
    if parsed.fragment:
        normalized_url = f"{normalized_url}#{parsed.fragment}"
    return normalized_url


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return normalize_sqlite_url(database_url)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return normalize_database_url(database_url)

    db_path = resolve_sqlite_path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH)))
    return f"sqlite:///{db_path}"


def get_engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite:///"):
        return {
            "connect_args": {
                "check_same_thread": False,
                "timeout": SQLITE_LOCK_TIMEOUT_SECONDS,
            }
        }

    return {}


DATABASE_URL = build_database_url()

logger.debug("Database URL: %s", DATABASE_URL)

engine = create_engine(DATABASE_URL, **get_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


if DATABASE_URL.startswith("sqlite:///"):
    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return

        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {SQLITE_LOCK_TIMEOUT_SECONDS * 1000}")
        try:
            cursor.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            logger.debug("Skipping WAL journal mode for this SQLite connection")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
