import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

# Load backend-local overrides first, then project-root env for local dev.
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env")


def get_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")


def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def get_admin_email() -> str:
    # 必須是 email-validator 接受的真實 TLD；.local / .test / .localhost 等 RFC 2606 保留名會被拒。
    return os.getenv("ADMIN_EMAIL", "admin@always-stock.dev").strip().lower()


def get_admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "forwork")


def get_session_cookie_name() -> str:
    return os.getenv("SESSION_COOKIE_NAME", "always_stock_session")


def is_cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}


def get_cookie_domain() -> Optional[str]:
    value = os.getenv("COOKIE_DOMAIN", "").strip()
    return value or None


def get_session_ttl_days() -> int:
    try:
        return int(os.getenv("SESSION_TTL_DAYS", "30"))
    except ValueError:
        return 30
