# Data Migration Checklist

這份文件把 `always-stock` 從 SQLite 搬到 Postgres 的檢查項目整理成可執行清單，目的不是描述方向，而是避免切流時遺漏驗證。

## 使用時機

- staging 第一次匯入資料前
- production 正式切流前
- production 切流完成後 24 小時內
- 任一大規模 backfill 或 schema 調整後

## 搬移前

### 1. 盤點來源資料

- 確認 SQLite 檔案版本與大小
- 確認來源 DB 為一致狀態，不在進行 ETL / backfill
- 記錄各主表 row count
- 記錄各主表 min/max `trade_date`

主要表：

- `stocks_master`
- `daily_price`
- `inst_stock_flow`
- `industry_daily_flow`
- `broker_trade`

### 2. 記錄已知資料缺口

以下缺口要明確列入搬移紀錄，避免驗證時誤判為遷移失敗：

- `inst_stock_flow` / `industry_daily_flow` 缺 3 天：
  - `2019-04-04`
  - `2023-04-03`
  - `2026-02-18`
- `daily_price` OHLC 缺 5 天：
  - `2023-05-05`
  - `2023-09-19`
  - `2024-01-17`
  - `2024-02-29`
  - `2024-07-11`

### 3. 準備目標 Postgres

- 建立 staging / production DB
- 建立 schema 與 index
- 建立最小權限的應用程式帳號
- 確認 `DATABASE_URL` 可連線

## 匯入中

### 4. 分表搬移

建議固定順序：

1. `stocks_master`
2. `daily_price`
3. `inst_stock_flow`
4. `industry_daily_flow`
5. `broker_trade`

每張表都要記錄：

- 開始時間
- 結束時間
- 匯入筆數
- 失敗筆數
- 是否可 resume

### 5. 大表處理原則

- 以批次讀取 SQLite
- 批次寫入 Postgres
- 每批保留進度 checkpoint
- 寫入方式要具 idempotency

## 匯入後驗證

### 6. 結構驗證

- 所有必要表存在
- 主鍵 / unique constraint 正常
- 查詢熱點 index 已建立
- app 使用的欄位型別與 nullable 行為符合預期

### 7. 筆數驗證

每張表確認：

- source row count
- target row count
- 差異是否為 0

若差異非 0，要有明確原因紀錄。

### 8. 日期範圍驗證

每張表確認：

- 最小日期
- 最大日期
- 與 SQLite 相同

### 9. 抽樣資料驗證

至少抽查以下場景：

- L0 某日產業排行榜前 10 名
- L1 某產業股票列表
- L2 某股票歷史價格與法人累積曲線
- `brokers` API 任選 3 檔股票
- Telegram Bot 任選 3 個常用查詢

代表性股票建議涵蓋：

- 大型權值股
- 中型股
- 有 broker_trade 的熱門股

## 切流前

### 10. staging 驗證完成

- staging frontend 可正常讀 staging API
- staging API 可正常讀 Postgres
- staging cron job 可寫入 Postgres
- staging bot 可查詢資料

### 11. production 切流窗口

- 暫停 production ETL 寫入
- 記錄切流開始時間
- 完成最後一次來源 DB snapshot / backup
- 確認 rollback 路徑可用

## 切流後

### 12. production 驗證

- `/health` 正常
- 首頁 / L1 / L2 可正常載入
- Telegram Bot 正常
- 當日 ETL 成功
- 切流後新增資料有寫進 Postgres

### 13. 觀察期

- 保留舊環境只讀
- 至少觀察 1 到 2 週
- 監看 error rate、ETL 成功率、API latency、資料最新日期

## 完成標準

- 所有主表資料完成搬移
- staging 與 production 驗證通過
- 新增資料不再寫回 SQLite production
- SQLite 僅保留為 local 開發 / 歷史封存用途
