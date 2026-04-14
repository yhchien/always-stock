# FinMind 全量 Backfill 操作手冊

## 前置確認

### 1. DB Migration 是否跑過
```bash
cd backend
python3 migrate_finmind_phase1.py
```
全部 ✓ 才繼續。（已跑過則直接跳過）

### 2. 確認配額是否充足
```bash
cd backend
export $(cat .env.finmind | grep -v '^#' | grep -v '^$' | xargs)
python3 -c "
from etl.finmind_sdk_client import FinMindSDKClient
import os
client = FinMindSDKClient(os.environ['FINMIND_TOKEN'])
print(client.get_quota())
"
```
`remaining` 需要 > 0 才能開始。配額每小時整點重置（Sponsor 上限 6,000 req/hr）。

---

## 執行 Backfill

### 開新 tmux session

```bash
tmux new-session -s finmind-backfill
```

### 在 session 內執行

```bash
cd /path/to/always-stock
bash scripts/backfill_finmind.sh
```

### 離開 session（讓它繼續跑）

`Ctrl+B` 然後 `D`

---

## 查看進度

### 重新進入 session
```bash
tmux attach -t finmind-backfill
```

### 不進 session，直接看 checkpoint
```bash
# 已完成哪些日期
tail -30 backend/logs/backfill_checkpoint.txt

# 目前跑到哪一天（最新 log）
ls -t backend/logs/backfill_*.log | head -1 | xargs tail -40
```

---

## 斷線或中途停止後續跑

腳本會自動依 checkpoint 最後事件續跑：

- 最後一筆是 `DONE YYYY-MM-DD ...` 或 `FAILED YYYY-MM-DD ...`：從隔天繼續
- 最後一筆是 `QUOTA_EXHAUSTED YYYY-MM-DD ...`：從該天重新開始

```bash
tmux new-session -s finmind-backfill
bash scripts/backfill_finmind.sh
```

若要強制從特定日期開始：

```bash
START_DATE=2022-06-01 bash scripts/backfill_finmind.sh
```

---

## 配額不足時的行為

- exit code `2`（insufficient_quota）→ 自動等待 **75 分鐘**後重試同一天
- 最多重試 3 次，仍失敗會寫入 `QUOTA_EXHAUSTED YYYY-MM-DD ...` 後停止
- 下次直接重新執行 `bash scripts/backfill_finmind.sh`，會從該天重新開始

---

## 預期完成時間

日粒度後，實際耗時取決於交易日數、空資料日比例與 quota 重試次數。

> 每天約 6 次 batch call；遇到配額不足時，腳本會先對同一天重試，若仍不足則記下該天並結束，下一次從同一天續跑。

---

## 完成後的資料表

| 資料 | 表 | 年份範圍 |
|------|-----|---------|
| 股價 OHLCV | `daily_price` | 2019–2026 |
| 三大法人買賣超 | `inst_stock_flow` | 2019–2026 |
| P/E、P/B、殖利率 | `daily_valuation` | 2019–2026 |
| 月營收 | `monthly_revenue` | 2019–2026 |
| 財報（季報） | `financial_statement` | 2019–2026 |
| 券商分點買賣超 | `broker_trade_agg` | 2021-06-30 起 |

---

## Log 檔位置

```
backend/logs/
├── backfill_checkpoint.txt     # 日粒度 checkpoint（DONE / FAILED / QUOTA_EXHAUSTED）
├── backfill_2019.log           # 每年 ETL 詳細 log
├── backfill_2020.log
├── ...
└── finmind_migration_checkpoint.json
```
