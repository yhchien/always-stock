#!/bin/bash
# 補跑 4 個新 schema 的全量 Backfill
#
# 目標表：daily_valuation / monthly_revenue / financial_statement / broker_trade_agg
# 不重跑：daily_price / inst_stock_flow（已有資料到 2026-04-08）
#
# 策略：一次呼叫涵蓋全區間（2019-01-02 ~ 今日）
# 原理：FinMind SDK batch 是「每支股票一個 request，日期範圍不影響 request 數」
#   → 7 年全區間一次呼叫 ≈ 1 個月一次呼叫，消耗 request 數相同
#   → 4 個 schema 合計約 4,000 requests，不到 1 小時
#
# 使用方式：
#   bash scripts/backfill_new_schemas.sh
#
# 指定日期區間（可選）：
#   START_DATE=2021-01-01 END_DATE=2026-04-14 bash scripts/backfill_new_schemas.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../backend" && pwd)"
LOG_DIR="$BACKEND_DIR/logs"

mkdir -p "$LOG_DIR"

cd "$BACKEND_DIR"
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)

START_DATE="${START_DATE:-2019-01-02}"
END_DATE="${END_DATE:-$(python3 -c "
from datetime import date, timedelta
d = date.today() - timedelta(days=1)
while d.isoweekday() > 5:
    d -= timedelta(days=1)
print(d)
")}"

NEW_STEPS="daily_valuation,monthly_revenue,financial_statement,broker_trade_agg"
LOG_FILE="$LOG_DIR/backfill_new_schemas_$(date '+%Y%m%d_%H%M%S').log"

echo "========================================"
echo "FinMind 新 Schema Backfill"
echo "區間：$START_DATE ~ $END_DATE"
echo "步驟：$NEW_STEPS"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# Step 1: 建表（migration）
echo ""
echo ">>> [1/2] 執行 migration（建立缺少的表）..."
python3 migrate_finmind_phase1.py 2>&1 | tee -a "$LOG_FILE"
MIGRATION_EXIT=${PIPESTATUS[0]}
if [ $MIGRATION_EXIT -ne 0 ]; then
  echo "❌ Migration 失敗，中止"
  exit 1
fi
echo "✓ Migration 完成"

# Step 2: 全區間 backfill（只跑 4 個新 schema）
echo ""
echo ">>> [2/2] 開始 backfill $START_DATE ~ $END_DATE..."
echo "    預計消耗 request：約 4,000（4 schema × ~1000 requests）"
echo ""

set +e
python3 run_finmind_etl_sdk.py \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --steps "$NEW_STEPS" \
  2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo ""
echo "========================================"
if [ $EXIT_CODE -eq 0 ]; then
  echo "✅ 全部完成！"
elif [ $EXIT_CODE -eq 1 ]; then
  echo "⚠️  部分完成（次要步驟有失敗，核心資料已寫入）"
elif [ $EXIT_CODE -eq 2 ]; then
  echo "❌ 配額不足，下次從頭重跑即可（upsert 安全）"
else
  echo "❌ 發生錯誤（exit $EXIT_CODE），請查看 log: $LOG_FILE"
fi
echo "Finished: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Log: $LOG_FILE"
echo "========================================"
exit $EXIT_CODE
