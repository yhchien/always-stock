#!/bin/bash
# FinMind 全量 Backfill 腳本（日粒度）
# 每日一個批次；配額耗盡後自動等待重試；非配額錯誤記錄後繼續下一天
#
# Checkpoint 格式：
#   DONE YYYY-MM-DD timestamp      — 成功
#   FAILED YYYY-MM-DD exit=N timestamp — 失敗（非配額），已跳過
#
# 使用方式：
#   bash scripts/backfill_finmind.sh
#
# 從特定日期開始（斷線後或強制覆寫）：
#   START_DATE=2022-06-01 bash scripts/backfill_finmind.sh
#
# 若不指定 START_DATE，自動依 checkpoint 最後事件續跑：
#   - DONE / FAILED         → 從隔天開始
#   - QUOTA_EXHAUSTED       → 從同一天開始重跑

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
LOG_DIR="$BACKEND_DIR/logs"
CHECKPOINT_FILE="$LOG_DIR/backfill_checkpoint.txt"

MAX_RETRIES=3
QUOTA_WAIT_SECONDS=4500  # 75 分鐘

mkdir -p "$LOG_DIR"

# 根據 checkpoint 最後事件決定續跑日期
resolve_resume_from_checkpoint() {
  local checkpoint_file="$1"

  if [ ! -f "$checkpoint_file" ]; then
    return 1
  fi

  local last_entry
  last_entry=$(grep -E '^(DONE|FAILED|QUOTA_EXHAUSTED) ' "$checkpoint_file" | tail -1)

  if [ -z "$last_entry" ]; then
    return 1
  fi

  local status checkpoint_date
  status=$(echo "$last_entry" | awk '{print $1}')
  checkpoint_date=$(echo "$last_entry" | awk '{print $2}')

  if [ "$status" = "QUOTA_EXHAUSTED" ]; then
    echo "$checkpoint_date"
    return 0
  fi

  python3 -c "
from datetime import datetime, timedelta
print((datetime.strptime('$checkpoint_date', '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'))
"
}

# 載入環境變數
cd "$BACKEND_DIR"
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)

FIRST_DATE="2019-01-02"
LAST_DATE=$(python3 -c "
from datetime import date, timedelta
# 上一個交易日（跳過週末）
d = date.today() - timedelta(days=1)
while d.isoweekday() > 5:
    d -= timedelta(days=1)
print(d)
")

# 決定起始日期
if [ -n "${START_DATE:-}" ]; then
  RESUME_FROM="$START_DATE"
else
  if RESUME_FROM=$(resolve_resume_from_checkpoint "$CHECKPOINT_FILE"); then
    :
  else
    RESUME_FROM="$FIRST_DATE"
  fi
fi

echo "========================================"
echo "FinMind Backfill（日粒度）: $RESUME_FROM ~ $LAST_DATE"
echo "資料項目：股價、三大法人、估值、月營收、財報、券商分點(2021-06-30起)"
echo "配額耗盡時自動等待 $((QUOTA_WAIT_SECONDS/60)) 分鐘重試；其他錯誤記錄後繼續"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 產生所有平日清單（週一~五；台灣假日不過濾，ETL 會自動跳過空日）
DATES=$(python3 -c "
from datetime import datetime, timedelta
start = datetime.strptime('$RESUME_FROM', '%Y-%m-%d')
end   = datetime.strptime('$LAST_DATE',   '%Y-%m-%d')
d = start
while d <= end:
    if d.isoweekday() <= 5:
        print(d.strftime('%Y-%m-%d'))
    d += timedelta(days=1)
")

TOTAL=$(echo "$DATES" | wc -l | tr -d ' ')
CURRENT=0
FAILED_DAYS=()

if [ -z "$DATES" ]; then
  echo "✅ 無需補跑，$RESUME_FROM 之後已無交易日"
  exit 0
fi

for DATE in $DATES; do
  CURRENT=$((CURRENT + 1))
  YEAR=$(echo "$DATE" | cut -d'-' -f1)
  DAY_LOG="$LOG_DIR/backfill_${YEAR}.log"

  echo ""
  echo "--- [$CURRENT/$TOTAL] $DATE ---"

  RETRY=0
  SUCCESS=false
  EXIT_CODE=0

  while [ $RETRY -le $MAX_RETRIES ]; do
    if [ $RETRY -gt 0 ]; then
      echo ""
      echo "⏳ 配額等待中，$QUOTA_WAIT_SECONDS 秒後重試（第 $RETRY/$MAX_RETRIES 次）..."
      echo "   預計恢復：$(python3 -c "
from datetime import datetime, timedelta
print((datetime.now()+timedelta(seconds=$QUOTA_WAIT_SECONDS)).strftime('%Y-%m-%d %H:%M:%S'))
")"
      sleep $QUOTA_WAIT_SECONDS
      echo "⏰ 重試 $(date '+%Y-%m-%d %H:%M:%S')"
    fi

    set +e
    python3 run_finmind_etl_sdk.py --date "$DATE" --skip-log \
      2>&1 | tee -a "$DAY_LOG"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e

    # exit code: 0=ok, 1=partial, 2=insufficient_quota, 3=error
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 1 ]; then
      SUCCESS=true
      break
    elif [ $EXIT_CODE -eq 2 ]; then
      RETRY=$((RETRY + 1))
    else
      # 非配額錯誤：記錄後繼續（不中止）
      echo "⚠️  $DATE 失敗（exit $EXIT_CODE），記錄後繼續"
      echo "FAILED $DATE exit=$EXIT_CODE $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
      FAILED_DAYS+=("$DATE")
      SUCCESS=true  # 設 true 讓外層不再等待，直接下一天
      break
    fi
  done

  if [ "$SUCCESS" = false ]; then
    echo ""
    echo "❌ $DATE 配額重試 $MAX_RETRIES 次後仍不足，中止"
    echo "   已記錄 checkpoint，下一次會從這一天重新開始"
    echo "   也可手動執行："
    echo "   START_DATE=$DATE bash scripts/backfill_finmind.sh"
    echo "QUOTA_EXHAUSTED $DATE $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
    exit 2
  fi

  # 只有真正成功或 partial 才寫 DONE
  if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 1 ]; then
    echo "DONE $DATE $(date '+%Y-%m-%d %H:%M:%S')" >> "$CHECKPOINT_FILE"
  fi
done

echo ""
echo "========================================"
echo "✅ Backfill 完成！最後日期：$LAST_DATE"
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"

if [ ${#FAILED_DAYS[@]} -gt 0 ]; then
  echo ""
  echo "⚠️  以下 ${#FAILED_DAYS[@]} 天失敗（非配額），可單獨重跑："
  for D in "${FAILED_DAYS[@]}"; do
    echo "   python3 run_finmind_etl_sdk.py --date $D"
  done
fi
echo "========================================"
