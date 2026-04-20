import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.routers import analysis, backtest, brokers, financials, industries, market, realtime, stocks

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Boot the Telegram bot in webhook mode alongside the FastAPI app so the API
    web service also hosts the bot (no separate worker needed). Skips silently
    when TELEGRAM_BOT_TOKEN is absent so local dev without a token still runs.
    """
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
)

app.include_router(industries.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(brokers.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(financials.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")

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
