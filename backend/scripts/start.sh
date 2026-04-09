#!/bin/sh
# Start script for Fly.io deployment.
# Runs cron (ETL scheduler) + FastAPI (foreground) + Telegram Bot (background).

set -eu

echo "=== always-stock backend starting ==="
echo "DB_PATH=${DB_PATH:-not set}"
echo "TZ=${TZ:-not set}"

# Ensure log directory exists on persistent volume
mkdir -p /data/logs

# Dump current env vars so cron jobs can inherit Fly secrets
env | grep -E '^(DB_PATH|TZ|TELEGRAM_BOT_TOKEN|OPENAI_API_KEY|FINMIND_API_TOKEN|PATH|PYTHONPATH)=' \
    | sed 's/^/export /' > /app/scripts/env.sh
chmod 600 /app/scripts/env.sh

# Initialize DB tables (idempotent)
python init_db.py

# Run OHLC migration (idempotent)
python migrate_add_ohlc.py

# Start cron daemon (ETL at 19:00 + 21:30 Mon-Fri)
echo "Starting cron daemon..."
cron

# Start Telegram Bot in background (only if token is set)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "Starting Telegram Bot..."
    python run_telegram_bot.py &
else
    echo "TELEGRAM_BOT_TOKEN not set, skipping Telegram Bot"
fi

# Start FastAPI (foreground)
echo "Starting FastAPI on port 8000..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
