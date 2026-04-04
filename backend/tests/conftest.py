"""
共用 pytest fixture：in-memory SQLite DB，每個 test 獨立隔離。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base


@pytest.fixture
def db():
    """每個測試都使用乾淨的 in-memory SQLite，測試結束後自動丟棄。"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
