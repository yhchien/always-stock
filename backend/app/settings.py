import os
from pathlib import Path
from typing import Optional, Set

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

# Load backend-local overrides first, then project-root env for local dev.
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def get_openai_api_key() -> str:
    # strip trailing whitespace / newline — GitHub Secrets web UI 貼值時容易帶到 \n，
    # httpx 對 header 含 CR/LF 會 silently drop 整個 Authorization → OpenAI 回
    # "Missing bearer or basic authentication in header"，難以診斷。
    return os.getenv("OPENAI_API_KEY", "").strip()


def get_openai_model() -> str:
    value = os.getenv("OPENAI_MODEL", "").strip()
    return value or DEFAULT_OPENAI_MODEL


def get_admin_email() -> str:
    # 必須是 email-validator 接受的真實 TLD；.local / .test / .localhost 等 RFC 2606 保留名會被拒。
    return os.getenv("ADMIN_EMAIL", "admin@always-stock.dev").strip().lower()


def get_admin_password() -> str:
    value = os.getenv("ADMIN_PASSWORD", "").strip()
    if not value:
        raise RuntimeError(
            "ADMIN_PASSWORD env var is not set. "
            "Set it in Render dashboard (or local .env) before starting the server."
        )
    return value


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


def is_auth_disabled() -> bool:
    """全站永遠免註冊/登入：require_user / get_optional_user 一律回傳全站共用 demo user，
    所有 user-bound 資料（watchlist、trade-quality cache 等）都綁在這個 user 上。
    入站訪問改由 SITE_GATE_PASSWORD 單一密碼閘門控管。
    """
    return True


def get_demo_user_email() -> str:
    return os.getenv("DEMO_USER_EMAIL", "demo@always-stock.dev").strip().lower()


def get_site_gate_password() -> str:
    """單一密碼閘門：使用者進入主頁面前須輸入此密碼。
    未設時回空字串；router 層會在空字串時回 503，避免無密碼狀態下整站洞開。
    """
    return os.getenv("SITE_GATE_PASSWORD", "").strip()


def get_site_gate_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("SITE_GATE_MAX_ATTEMPTS", "3")))
    except ValueError:
        return 3


def get_site_gate_lockout_seconds() -> int:
    try:
        return max(1, int(os.getenv("SITE_GATE_LOCKOUT_SECONDS", "300")))
    except ValueError:
        return 300


def get_admin_telegram_chat_ids() -> Set[int]:
    """Telegram admin chat_id 白名單（管理員專屬指令 list admin chats / list admin show）。

    格式：comma-separated int（負號允許，給 supergroup 用）；忽略空白與無效 token。
    未設或全 invalid → 空集合 → admin 指令對所有 chat 都拒絕（裝作 unknown）。
    例：`ADMIN_TELEGRAM_CHAT_IDS=12345,-1001234567890`
    """
    raw = os.getenv("ADMIN_TELEGRAM_CHAT_IDS", "").strip()
    if not raw:
        return set()
    out: Set[int] = set()
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        # 允許負號（supergroup chat_id 為負值）
        if t.lstrip("-").isdigit():
            out.add(int(t))
    return out
