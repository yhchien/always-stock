#!/bin/bash
# FinMind 全量 Backfill 腳本（月粒度）
# 每月一個批次，配額耗盡後自動等待 60 分鐘再重試同一個月
#
# 每次跑包含：
#   1. 股價（daily_price）
#   2. 三大法人（inst_stock_flow）
#   3. 估值 P/E P/B（daily_valuation）
#   4. 月營收（monthly_revenue）
#   5. 財報（financial_statement）
#   6. 券商分點聚合（broker_trade_agg，2021-06-30 起才有資料）
#
# 使用方式：
#   bash scripts/backfill_finmind.sh
#
# 從特定月份開始（斷線後繼續，或強制覆寫）：
#   START_MONTH=2022-06 bash scripts/backfill_finmind.sh
#
# 若不指定 START_MONTH，自動從 checkpoint 最後完成的月份續跑

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
LOG_DIR="$BACKEND_DIR/logs"
CHECKPOINT_FILE="$LOG_DIR/backfill_checkpoint.txt"

MAX_RETRIES=3
QUOTA_WAIT_SECONDS=4500  # 75 分鐘

mkdir -p "$LOG_DIR"

# 載入環境變數
cd "$BACKEND_DIR"
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)

FIRST_MONTH="2019-01"
LAST_MONTH="2026-04"   # 最後一個月（2026-04-08 止）

# 決定起始月份：
#   1. 環境變數 START_MONTH 優先
#   2. 否則從 checkpoint 最後完成的下一個月續跑
#   3. 否則從頭開始
if [ -n "$START_MONTH" ]; then
  RESUME_FROM="$START_MONTH"
else
  LAST_DONE=$(grep "^DONE " "$CHECKPOINT_FILE" 2>/dev/null | tail -1 | awk '{print $2}')
  if [ -n "$LAST_DONE" ]; then
    # 計算下一個月
    LAST_YEAR=$(echo "$LAST_DONE" | cut -d'-' -f1)
    LAST_MON=$(echo "$LAST_DONE" | cut -d'-' -f2 | sed 's/^0//')
    if [ "$LAST_MON" -eq 12 ]; then
      NEXT_YEAR=$((LAST_YEAR + 1))
      NEXT_MON="01"
    else
      NEXT_YEAR=$LAST_YEAR
      NEXT_MON=$(printf "%02d" $((LAST_MON + 1)))
    fi
    RESUME_FROM="${NEXT_YEAR}-${NEXT_MON}"
  else
    RESUME_FROM="$FIRST_MONTH"
  fi
fi

echo "========================================"
echo "FinMind Backfill（月粒度）: $RESUME_FROM ~ $LAST_MONTH"
echo "資料項目：股價、三大法人、估值、月營收、財報、券商分點(2021-06-30起)"
echo "配額耗盡時自動等待 60 分鐘重試同一個月（最多 $MAX_RETRIES 次）"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 產生所有月份清單（YYYY-MM），從 RESUME_FROM 到 LAST_MONTH
MONTHS=()
CUR_YEAR=$(echo "$FIRST_MONTH" | cut -d'-' -f1)
CUR_MON=$(echo "$FIRST_MONTH" | cut -d'-' -f2 | sed 's/^0//')
END_YEAR=$(echo "$LAST_MONTH" | cut -d'-' -f1)
END_MON=$(echo "$LAST_MONTH" | cut -d'-' -f2 | sed 's/^0//')

while true; do
  YM=$(printf "%d-%02d" $CUR_YEAR $CUR_MON)
  MONTHS+=("$YM")
  if [ "$CUR_YEAR" -eq "$END_YEAR" ] && [ "$CUR_MON" -eq "$END_MON" ]; then
    break
  fi
  if [ "$CUR_MON" -eq 12 ]; then
    CUR_YEAR=$((CUR_YEAR + 1))
    CUR_MON=1
  else
    CUR_MON=$((CUR_MON + 1))
  fi
done

# 計算月份最後一天
last_day_of_month() {
  local yr=$1
  local mo=$2
  # 用 Python 最可靠（跨平台）
  python3 -c "import calendar; print(calendar.monthrange($yr, $mo)[1])"
}

SKIPPING=true
RESUME_YEAR=$(echo "$RESUME_FROM" | cut -d'-' -f1)
RESUME_MON=$(echo "$RESUME_FROM" | cut -d'-' -f2 | sed 's/^0//')

for YM in "${MONTHS[@]}"; do
  Y=$(echo "$YM" | cut -d'-' -f1)
  M=$(echo "$YM" | cut -d'-' -f2 | sed 's/^0//')

  # 跳過 RESUME_FROM 之前的月份
  if [ "$SKIPPING" = true ]; then
    if [ "$Y" -lt "$RESUME_YEAR" ] || { [ "$Y" -eq "$RESUME_YEAR" ] && [ "$M" -lt "$RESUME_MON" ]; }; then
      continue
    fi
    SKIPPING=false
  fi

  MM=$(printf "%02d" $M)
  START_DATE="${Y}-${MM}-01"

  # 最後一個月截止到 2026-04-08
  if [ "$Y" -eq 2026 ] && [ "$M" -eq 4 ]; then
    END_DATE="2026-04-08"
  else
    LAST_DAY=$(last_day_of_month $Y $M)
    END_DATE="${Y}-${MM}-${LAST_DAY}"
  fi

  MONTH_LOG="$LOG_DIR/backfill_${Y}_${MM}.log"

  echo ""
  echo "--- [$YM] $START_DATE → $END_DATE ---"
  echo "Log: $MONTH_LOG"

  RETRY=0
  SUCCESS=false

  while [ $RETRY -le $MAX_RETRIES ]; do
    if [ $RETRY -gt 0 ]; then
      echo ""
      echo "⏳ 配額等待中，$QUOTA_WAIT_SECONDS 秒後重試（第 $RETRY/$MAX_RETRIES 次）..."
      echo "   預計恢復時間：$(python3 -c "from datetime import datetime, timedelta; print((datetime.now()+timedelta(seconds=$QUOTA_WAIT_SECONDS)).strftime('%Y-%m-%d %H:%M:%S'))")"
      sleep $QUOTA_WAIT_SECONDS
      echo "⏰ 重試開始 $(date '+%Y-%m-%d %H:%M:%S')"
    fi

    python3 run_finmind_etl_sdk.py \
      --start-date "$START_DATE" \
      --end-date "$END_DATE" \
      2>&1 | tee "$MONTH_LOG"

    EXIT_CODE=${PIPESTATUS[0]}

    # exit code: 0=ok, 1=partial, 2=insufficient_quota, 3=error
    if [ $EXIT_CODE -eq 0 ]; then
      SUCCESS=true
      break
    elif [ $EXIT_CODE -eq 1 ]; then
      echo ""
      echo "⚠️  $YM 部分完成（exit code 1），繼續下一月"
      SUCCESS=true
      break
    elif [ $EXIT_CODE -eq 2 ]; then
      echo ""
      echo "⚠️  $YM 配額不足（exit code 2），等待後重試..."
      RETRY=$((RETRY + 1))
    else
      echo ""
      echo "❌ $YM 發生錯誤（exit code $EXIT_CODE），中止執行"
      echo "   重跑指令："
      echo "   START_MONTH=$YM bash scripts/backfill_finmind.sh"
      echo "FAILED $YM exit=$EXIT_CODE $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
      exit 1
    fi
  done

  if [ "$SUCCESS" = false ]; then
    echo ""
    echo "❌ $YM 重試 $MAX_RETRIES 次後仍失敗，中止執行"
    echo "   重跑指令："
    echo "   START_MONTH=$YM bash scripts/backfill_finmind.sh"
    echo "FAILED $YM after $MAX_RETRIES retries $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
    exit 1
  fi

  echo "DONE $YM $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$CHECKPOINT_FILE"
done

echo ""
echo "========================================"
echo "✅ 全量 Backfill 完成！"
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
