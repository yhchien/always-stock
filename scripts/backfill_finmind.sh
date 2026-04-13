#!/bin/bash
# FinMind 全量 Backfill 腳本
# 按年分段執行，斷線後只需重跑失敗的年份
#
# 使用方式：
#   bash scripts/backfill_finmind.sh
#
# 從特定年份開始（例如斷線後從 2022 繼續）：
#   START_YEAR=2022 bash scripts/backfill_finmind.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
LOG_DIR="$BACKEND_DIR/logs"
CHECKPOINT_FILE="$LOG_DIR/backfill_checkpoint.txt"

mkdir -p "$LOG_DIR"

# 載入環境變數
cd "$BACKEND_DIR"
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)

START_YEAR="${START_YEAR:-2019}"
END_YEAR=2026

echo "========================================"
echo "FinMind Backfill: $START_YEAR ~ $END_YEAR"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

for YEAR in $(seq $START_YEAR $END_YEAR); do
  if [ "$YEAR" -eq 2019 ]; then
    START_DATE="2019-01-02"
  else
    START_DATE="${YEAR}-01-01"
  fi

  if [ "$YEAR" -eq 2026 ]; then
    END_DATE="2026-04-08"
  else
    END_DATE="${YEAR}-12-31"
  fi

  YEAR_LOG="$LOG_DIR/backfill_${YEAR}.log"

  echo ""
  echo "--- [$YEAR] $START_DATE → $END_DATE ---"
  echo "Log: $YEAR_LOG"

  python3 run_finmind_etl_sdk.py \
    --start-date "$START_DATE" \
    --end-date "$END_DATE" \
    2>&1 | tee "$YEAR_LOG"

  EXIT_CODE=${PIPESTATUS[0]}

  if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ $YEAR 失敗（exit code $EXIT_CODE）"
    echo "重跑指令："
    echo "  START_YEAR=$YEAR bash scripts/backfill_finmind.sh"
    echo "$YEAR FAILED $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
    exit 1
  fi

  echo "✓ $YEAR 完成 $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$CHECKPOINT_FILE"
done

echo ""
echo "========================================"
echo "✅ 全量 Backfill 完成！"
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
