"""
Launch the Telegram bot in long-polling mode. Local development only.

Production runs the bot in webhook mode inside the FastAPI web service
(see app/main.py lifespan + /telegram/webhook endpoint), so there is no
separate Render worker. Use this script when you want to iterate on bot
handlers locally without exposing a public webhook.

Usage:
    python run_telegram_bot.py

Reads TELEGRAM_BOT_TOKEN from .env file (project root) or environment variable.
"""
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env from project root (one level above backend/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from logging_config import setup_logging
from app.telegram_bot import create_bot_app

logger = logging.getLogger(__name__)


def main():
    setup_logging()
    logger.info("Starting Telegram bot (long-polling mode)...")

    try:
        app = create_bot_app()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
