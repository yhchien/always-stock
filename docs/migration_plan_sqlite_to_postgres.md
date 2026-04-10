# SQLite to Postgres Migration Plan

本文件說明如何將 `always-stock` 從目前的 SQLite 架構遷移到 Postgres。

## 遷移目標

- 正式環境改用 Postgres
- 本地開發仍可保留 SQLite
- SQLAlchemy model 儘量保持共用
- ETL / API / Bot 共用同一份 Postgres schema

## 範圍

需要遷移的主要表：

- `stocks_master`
- `daily_price`
- `inst_stock_flow`
- `industry_daily_flow`
- `broker_trade`

## 遷移策略

### 階段 1：程式碼先支援雙資料庫

先改 `backend/app/database.py`：

- 支援 `DATABASE_URL`
- 若未提供 `DATABASE_URL`，fallback 到本地 SQLite
- production 使用 Postgres URL

建議順序：

1. 抽離目前 SQLite 專用設定
2. 新增 Postgres driver 依賴
3. 確認本地測試仍可跑 SQLite
4. 確認 staging 可連 Postgres

### 階段 2：建立 Postgres schema

- 在 staging 先建立空 schema
- 用 SQLAlchemy metadata 或 migration 建表
- 為查詢熱點補 index

建議 index：

- `daily_price(stock_id, trade_date)`
- `inst_stock_flow(stock_id, trade_date, inst_type)`
- `industry_daily_flow(industry_name, trade_date)`
- `broker_trade(stock_id, trade_date, broker_id)`

### 階段 3：資料搬移

建議用「分表匯入」而不是一次整顆 DB dump：

1. `stocks_master`
2. `daily_price`
3. `inst_stock_flow`
4. `industry_daily_flow`
5. `broker_trade`

搬移工具建議：

- Python batch import script
- 分頁讀 SQLite
- 批次 insert / upsert 到 Postgres
- 每表都做 row count 驗證

目前 repo 已有第一版匯入腳本骨架：

- `backend/migrate_sqlite_to_postgres.py`

定位：

- 先作為 staging migration tool
- 支援依表分批搬移
- 支援 `--verify-counts`
- 已支援 table-level checkpoint / resume
- 已支援 JSON report 輸出
- 已支援 PostgreSQL sequence reset
- 後續再補更細的 row-level resume 與正式切流前驗證腳本

### 階段 4：資料驗證

每張表至少做：

- row count 比對
- min/max `trade_date`
- 主鍵 / unique constraint 驗證
- 重要聚合抽樣比對

尤其要比對：

- `industry_daily_flow` 某幾個交易日的產業排行
- `stocks/{stock_id}/history` 折線 / K 線資料
- Telegram Bot 某幾檔代表性股票查詢

### 階段 5：切流

建議先做 staging 驗證，再 production 切流：

1. staging API 改連 Postgres
2. staging frontend 指向 staging API
3. staging bot 驗證
4. production API 切到 Postgres
5. production cron job 切到 Postgres
6. production bot 切到 Postgres

## 風險

### 風險 1：SQLite 與 Postgres 行為差異

- transaction / locking 行為不同
- 型別 coercion 不同
- SQLAlchemy query 在不同 dialect 可能有細節差異

### 風險 2：大表搬移時間長

- `inst_stock_flow` 與 `daily_price` 量大
- 必須批次匯入
- 需要中途可 resume

### 風險 3：資料品質缺口帶進新 DB

目前已知缺口：

- `inst_stock_flow` / `industry_daily_flow` 仍缺 3 天：
  - `2019-04-04`
  - `2023-04-03`
  - `2026-02-18`
- `daily_price` 仍有 5 天 OHLC 缺漏：
  - `2023-05-05`
  - `2023-09-19`
  - `2024-01-17`
  - `2024-02-29`
  - `2024-07-11`

這些要在 migration 文件中明確標示，避免誤判為搬移錯誤。

## 回滾策略

- production 切換前保留舊環境只讀一段觀察期
- 新 API 若驗證失敗，可立即切回舊 API
- Postgres 匯入腳本必須可重跑且具 idempotency

## 交付物

- `database.py` 支援 SQLite / Postgres
- Postgres schema 建立方式
- SQLite -> Postgres import script
- data validation script
- 切流 checklist
