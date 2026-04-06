import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "tw_stock.db"))
DATABASE_URL = f"sqlite:///{os.path.abspath(DB_PATH)}"

logger.debug("Database URL: %s", DATABASE_URL)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
