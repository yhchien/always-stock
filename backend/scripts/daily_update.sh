#!/bin/bash
# Daily ETL update script for always-stock.
# Intended to be triggered by launchd (macOS) or cron at ~20:00 after market close.
#
# Usage:
#   ./scripts/daily_update.sh              # Run for today
#   ./scripts/daily_update.sh 2025-04-01   # Run for a specific date

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/daily_update_$(date +%Y%m%d).log"

cd "$PROJECT_DIR"

echo "=== always-stock daily update ===" >> "$LOG_FILE"
echo "Started at: $(date)" >> "$LOG_FILE"

DATE_ARG=""
if [ -n "${1:-}" ]; then
    DATE_ARG="--date $1"
fi

/usr/bin/python3 run_daily_etl.py $DATE_ARG --skip-master >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "Finished at: $(date), exit code: $EXIT_CODE" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"

# Clean up logs older than 30 days
find "$PROJECT_DIR/logs" -name "daily_update_*.log" -mtime +30 -delete 2>/dev/null || true

exit $EXIT_CODE
