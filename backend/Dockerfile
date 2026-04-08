FROM python:3.9-slim

WORKDIR /app

# Install cron daemon
RUN apt-get update && apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install crontab
COPY scripts/crontab /etc/cron.d/etl-cron
RUN chmod 0644 /etc/cron.d/etl-cron && crontab /etc/cron.d/etl-cron

# DB will be mounted at /data via Fly volume
ENV DB_PATH=/data/tw_stock.db

EXPOSE 8000

# Start script runs cron + API + Telegram Bot
CMD ["sh", "scripts/start.sh"]
