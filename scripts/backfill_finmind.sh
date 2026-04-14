#!/bin/bash
# FinMind 全量 Backfill 腳本
# 每年一個批次，配額耗盡後自動等待 60 分鐘再重試
#
# 每次跑包含：
#   1. 股價（daily_price）
#   2. 三大法人（inst_stock_flow）
#   3. 估值 P/E P/B（daily_valuation）
#   4. 月營收（monthly_revenue）
#   5. 財報（financial_statement）
#   6. 券商分點聚合（broker_trade_agg，2019-2020 自動跳過，2021-06-30 起才有資料）
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

MAX_RETRIES=3
QUOTA_WAIT_SECONDS=3600  # 1 小時

mkdir -p "$LOG_DIR"

# 載入環境變數
cd "$BACKEND_DIR"
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)

START_YEAR="${START_YEAR:-2019}"
END_YEAR=2026

echo "========================================"
echo "FinMind Backfill: $START_YEAR ~ $END_YEAR"
echo "資料項目：股價、三大法人、估值、月營收、財報、券商分點(2021-06-30起)"
echo "配額耗盡時自動等待 60 分鐘重試（最多 $MAX_RETRIES 次）"
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

  RETRY=0
  SUCCESS=false

  while [ $RETRY -le $MAX_RETRIES ]; do
    if [ $RETRY -gt 0 ]; then
      echo ""
      echo "⏳ 配額等待中，$QUOTA_WAIT_SECONDS 秒後重試（第 $RETRY/$MAX_RETRIES 次）..."
      echo "   預計恢復時間：$(date -v+${QUOTA_WAIT_SECONDS}S '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "+${QUOTA_WAIT_SECONDS} seconds" '+%Y-%m-%d %H:%M:%S')"
      sleep $QUOTA_WAIT_SECONDS
      echo "⏰ 重試開始 $(date '+%Y-%m-%d %H:%M:%S')"
    fi

    python3 run_finmind_etl_sdk.py \
      --start-date "$START_DATE" \
      --end-date "$END_DATE" \
      2>&1 | tee "$YEAR_LOG"

    EXIT_CODE=${PIPESTATUS[0]}

    if [ $EXIT_CODE -eq 0 ]; then
      SUCCESS=true
      break
    fi

    # exit code: 0=ok, 1=partial, 2=insufficient_quota, 3=error
    if [ $EXIT_CODE -eq 2 ]; then
      echo ""
      echo "⚠️  $YEAR 配額不足（exit code 2）"
      RETRY=$((RETRY + 1))
    elif [ $EXIT_CODE -eq 1 ]; then
      # partial 視為成功（部分資料已寫入，繼續下一年）
      echo ""
      echo "⚠️  $YEAR 部分完成（exit code 1），繼續下一年"
      SUCCESS=true
      break
    else
      # exit code 3 或其他錯誤直接中止
      echo ""
      echo "❌ $YEAR 發生錯誤（exit code $EXIT_CODE），中止執行"
      echo "   重跑指令："
      echo "   START_YEAR=$YEAR bash scripts/backfill_finmind.sh"
      echo "$YEAR FAILED exit=$EXIT_CODE $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
      exit 1
    fi
  done

  if [ "$SUCCESS" = false ]; then
    echo ""
    echo "❌ $YEAR 重試 $MAX_RETRIES 次後仍失敗，中止執行"
    echo "   重跑指令："
    echo "   START_YEAR=$YEAR bash scripts/backfill_finmind.sh"
    echo "$YEAR FAILED after $MAX_RETRIES retries $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
    exit 1
  fi

  echo "✓ $YEAR 完成 $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$CHECKPOINT_FILE"
done

echo ""
echo "========================================"
echo "✅ 全量 Backfill 完成！"
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
