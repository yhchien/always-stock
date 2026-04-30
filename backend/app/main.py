import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.rate_limit import limiter
from app.routers import (
    analysis,
    auth as auth_router,
    backtest,
    brokers,
    financials,
    industries,
    market,
    realtime,
    signals,
    stocks,
    watchlist,
)

logger = logging.getLogger(__name__)


def _ensure_industry_flow_schema() -> None:
    from app.database import engine
    from app.industry_flow_schema import ensure_industry_daily_flow_streak_column

    try:
        ensure_industry_daily_flow_streak_column(engine)
    except Exception:
        logger.exception("Failed to ensure industry_daily_flow schema at startup")


def _ensure_signal_watch_schema() -> None:
    from app.database import engine
    from app.signal_watch_schema import ensure_signal_watch_hit_return_columns

    try:
        ensure_signal_watch_hit_return_columns(engine)
    except Exception:
        logger.exception("Failed to ensure signal_watch_hits schema at startup")


def _seed_admin_user() -> None:
    """啟動時確保 M18 auth 表存在 + admin 帳號存在。失敗不阻擋 app 啟動。"""
    from app.auth import ensure_admin_user
    from app.database import Base, SessionLocal, engine
    from app.models import User, UserSession, UserWatchlist  # noqa: F401 — 觸發 metadata 註冊

    # create_all 是 CREATE TABLE IF NOT EXISTS，對既有資料 no-op。
    # 原本靠手動跑 backend/migrate_add_users.py 建表，改成 lifespan 自動 idempotent 建。
    try:
        Base.metadata.create_all(
            bind=engine,
            tables=[User.__table__, UserSession.__table__, UserWatchlist.__table__],
        )
    except Exception:
        logger.exception("Failed to create M18/M19 auth tables at startup")
        return

    db = SessionLocal()
    try:
        ensure_admin_user(db)
    except Exception:
        logger.exception("Failed to ensure admin user at startup")
    finally:
        db.close()


def _ensure_m23_tables() -> None:
    """啟動時確保 M23 訊號管線新表存在（margin_trade / signal_snapshots / signal_generation_jobs）。

    與 _seed_admin_user 拆開：
    - 沒有 seed 動作（純建表）
    - 失敗不阻擋 app 啟動，僅 warn（M23 為新功能，舊 endpoint 不依賴）
    """
    from app.database import Base, engine
    from app.models import (  # noqa: F401 — 觸發 metadata 註冊
        MarginTrade,
        SignalGenerationJob,
        SignalSnapshot,
        SignalWatchCompletedArchive,
        SignalWatchHit,
    )

    try:
        Base.metadata.create_all(
            bind=engine,
            tables=[
                MarginTrade.__table__,
                SignalGenerationJob.__table__,
                SignalSnapshot.__table__,
                SignalWatchCompletedArchive.__table__,
                SignalWatchHit.__table__,
            ],
        )
    except Exception:
        logger.exception("Failed to create M23 signal tables at startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Boot the Telegram bot in webhook mode alongside the FastAPI app so the API
    web service also hosts the bot (no separate worker needed). Skips silently
    when TELEGRAM_BOT_TOKEN is absent so local dev without a token still runs.
    """
    _seed_admin_user()
    _ensure_m23_tables()
    _ensure_industry_flow_schema()
    _ensure_signal_watch_schema()

    app.state.bot_app = None

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip() or None

    if token:
        from app.telegram_bot import create_bot_app

        bot_app = create_bot_app(token=token)
        await bot_app.initialize()
        await bot_app.start()
        app.state.bot_app = bot_app
        logger.info("Telegram bot application started (webhook mode)")

        if webhook_url:
            await bot_app.bot.set_webhook(
                url=webhook_url,
                secret_token=webhook_secret,
                drop_pending_updates=True,
            )
            logger.info("Telegram webhook registered: %s", webhook_url)
        else:
            logger.warning(
                "TELEGRAM_WEBHOOK_URL not set — bot initialized but Telegram "
                "does not know where to POST updates. Set TELEGRAM_WEBHOOK_URL "
                "to the public /telegram/webhook URL."
            )
    else:
        logger.info("TELEGRAM_BOT_TOKEN not set — skipping bot initialization")

    try:
        yield
    finally:
        bot_app = app.state.bot_app
        if bot_app is not None:
            try:
                await bot_app.stop()
                await bot_app.shutdown()
                logger.info("Telegram bot application stopped")
            except Exception:
                logger.exception("Error while shutting down Telegram bot")


app = FastAPI(
    title="always-stock API",
    description="Taiwan stock institutional flow API by industry",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_allowed_origins = [
    "http://localhost:3000",
]
# Allow custom origins via env var (comma-separated)
# Production: set CORS_ORIGINS to your Vercel/Render frontend URL
_extra = os.getenv("CORS_ORIGINS", "")
if _extra:
    _allowed_origins.extend(o.strip() for o in _extra.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(auth_router.router, prefix="/api")
app.include_router(industries.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(brokers.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(financials.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(signals.router, prefix="/api")

logger.info("always-stock API initialized")


@app.get("/health")
def health():
    logger.debug("Health check called")
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    """
    Receive Telegram updates in webhook mode. Telegram POSTs here whenever a
    user sends a message. We hand the payload to the bot Application which
    dispatches to the registered command/message handlers.
    """
    bot_app = request.app.state.bot_app
    if bot_app is None:
        raise HTTPException(status_code=503, detail="Telegram bot not initialized")

    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip() or None
    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        logger.warning("Telegram webhook rejected: bad secret token")
        raise HTTPException(status_code=401, detail="invalid secret token")

    from telegram import Update

    payload = await request.json()
    update = Update.de_json(payload, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}
