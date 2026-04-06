"""
Centralized logging configuration.

Usage (call once at the entry point):
    from logging_config import setup_logging
    setup_logging()

Default outputs:
  - console: INFO and above, human-readable format
  - logs/etl.log: DEBUG and above, full timestamp, rotated daily, kept for 7 days

The log directory is created automatically on first run.
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
    Initialize logging. Safe to call multiple times (clears existing handlers first).

    Args:
        level: minimum level for the root logger, defaults to INFO
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root is set to lowest; each handler filters independently

    # Avoid duplicate handlers on re-initialization
    if root.handlers:
        root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(ch)

    # File handler (daily rotation, keep 7 days)
    fh = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=7, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(fh)
