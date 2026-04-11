# Observability

這份文件定義 `always-stock` 在新架構下的監控與告警策略，目標是讓 API、ETL、Bot、DB 的問題能被快速發現，而不是等到使用者手動回報。

## 目標

- 快速知道服務是否活著
- 快速知道資料是否有更新
- 快速知道 ETL 是否失敗
- 快速知道 Bot 或 AI 功能是否異常
- 保留足夠 log 追查問題

## 監控面向

### 1. Availability

需要監控：

- frontend 是否可開啟
- backend `/health` 是否回 200
- bot worker 是否持續運作

建議工具：

- UptimeRobot 或 Better Stack
- Render / Vercel 平台內建 service status

### 2. Error Tracking

需要監控：

- FastAPI 例外
- ETL 執行錯誤
- Telegram Bot handler 錯誤
- OpenAI / 第三方 API 錯誤

建議工具：

- Sentry

### 3. Data Freshness

這是 `always-stock` 很重要的一層。

至少要監控：

- `daily_price` 最新日期
- `inst_stock_flow` 最新日期
- `industry_daily_flow` 最新日期
- 當日 ETL 是否成功

若最新日期落後預期交易日，要能告警。

### 4. Data Quality

固定檢查：

- 已知 3 個缺 flow 日期是否仍一致
- 已知 5 個 OHLC 缺漏日期是否仍一致
- 主要表 row count 是否異常突變
- 某日產業排行榜是否可正常生成

### 5. Performance

需要觀察：

- `/api/industries` latency
- `/api/stocks/{stock_id}/history` latency
- `/api/stocks/{stock_id}/brokers` latency
- DB query time

## 最低可行組合

若先求簡潔，建議先做到：

- UptimeRobot：監控 frontend 與 `/health`
- Sentry：監控 backend、bot、etl
- 每日 ETL 完成後寫一條成功 log
- 每日跑一個 data freshness check

## 告警建議

### 立即告警

- backend `/health` 失敗
- cron job 執行失敗
- bot process crash
- DB 連線失敗

### 當日內處理

- 最新交易日資料未更新
- 某個核心 API latency 顯著上升
- broker on-demand 抓取錯誤率異常

### 文件化但不立即告警

- 已知資料源特殊缺口仍存在
- 非核心頁面偶發性查詢偏慢

## log 原則

- 統一結構化 log
- 每次 ETL 記錄日期、步驟、筆數、耗時
- 每次 backfill 記錄 checkpoint
- 每次 bot query 只記必要資訊，避免存敏感內容

## 儀表板建議

之後可逐步整理成簡單 dashboard，至少有：

- API availability
- ETL success / failure history
- latest trade date per table
- error count by service
- DB size growth

## 成功標準

- 不需要等使用者回報才知道資料沒更新
- ETL、Bot、API 任一故障都能在短時間內察覺
- 有 log 可追出是哪個步驟失敗
