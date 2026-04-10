import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "tw_stock.db")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return normalize_database_url(database_url)

    db_path = os.getenv("DB_PATH", DEFAULT_DB_PATH)
    return f"sqlite:///{os.path.abspath(db_path)}"


def get_engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite:///"):
        return {"connect_args": {"check_same_thread": False}}

    return {}


DATABASE_URL = build_database_url()

logger.debug("Database URL: %s", DATABASE_URL)

engine = create_engine(DATABASE_URL, **get_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
