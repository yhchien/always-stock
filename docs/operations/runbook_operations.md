# Operations Runbook

這份文件整理 `always-stock` 在新架構下的日常維運方式。

## 日常檢查

每天建議確認：

- API 健康檢查 `/health`
- 當日 ETL 是否成功
- Frontend 首頁是否可正常載入
- Telegram Bot 是否能回應股票代號

## ETL 失敗處理

### 症狀

- 當日資料未更新
- cron job log 出錯
- 產業排行缺最新交易日

### 檢查順序

1. 看 cron job logs
2. 確認資料源是否可用
3. 確認 Postgres 連線正常
4. 手動重跑 `run_daily_etl.py --date YYYY-MM-DD`

## Backfill / 補跑策略

- 不再透過上傳整顆 SQLite 到正式環境完成 backfill
- 正式環境以 ETL / batch job 直接寫入 Postgres
- 若要歷史回補，優先在 staging 驗證後再 production 執行

## 資料品質檢查

建議固定檢查：

- `daily_price` 最新日期
- `inst_stock_flow` 最新日期
- `industry_daily_flow` 最新日期
- 已知缺口日是否仍維持一致

目前已知特殊資料缺口：

- `inst_stock_flow` / `industry_daily_flow` 缺：
  - `2019-04-04`
  - `2023-04-03`
  - `2026-02-18`
- `daily_price` OHLC 缺：
  - `2023-05-05`
  - `2023-09-19`
  - `2024-01-17`
  - `2024-02-29`
  - `2024-07-11`

## Bot 問題排查

### 症狀

- Telegram 無回應
- `/ai` 失敗

### 檢查順序

1. worker 是否運行
2. `TELEGRAM_BOT_TOKEN` 是否正確
3. `OPENAI_API_KEY` 是否過期 / 配額異常
4. API / DB 是否可連線

## 發版順序

建議順序：

1. 後端 staging
2. 前端 staging / preview
3. cron / ETL staging
4. production backend
5. production frontend
6. production bot

## 事故處理原則

- 先保服務可用，再追根因
- 切換或回滾要有明確時間點
- 保留最近一次成功 ETL 日期
- 若資料源異常，標記為 external dependency issue，不直接覆蓋舊資料
