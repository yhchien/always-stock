"""
執行一次即可：在 SQLite 建立所有資料表。
Usage: python init_db.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.database import engine
from app.models import Base  # noqa: F401 — import 觸發所有 model 註冊

if __name__ == "__main__":
    print("建立資料表...")
    Base.metadata.create_all(bind=engine)
    print("完成。")
