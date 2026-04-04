"""
統一 logging 設定。

使用方式（在任何入口點最開頭呼叫）:
    from logging_config import setup_logging
    setup_logging()

預設輸出:
  - console: INFO 以上，人類可讀格式
  - logs/etl.log: DEBUG 以上，含完整 timestamp，滾動保留 7 天

log 目錄在 backend/ 同層，首次執行自動建立。
"""
import logging
import logging.handlers
import os

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "etl.log")

CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_FORMAT    = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
DATE_FORMAT    = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """
    初始化 logging。重複呼叫是安全的（會先清除既有 handler）。

    Args:
        level: root logger 的最低等級，預設 INFO
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root 設最低，由各 handler 自己篩

    # 避免重複 attach
    if root.handlers:
        root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(ch)

    # File handler（滾動，每天換檔，保留 7 天）
    fh = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=7, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(fh)
