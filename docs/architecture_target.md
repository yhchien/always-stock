# Target Architecture

這份文件定義 `always-stock` 的長期正式架構，目標是從目前的 `Fly.io + SQLite volume` 過渡到較穩定、可維運、可擴充的服務拆分架構。

## 設計原則

- 正式環境不再依賴 SQLite 作為主資料庫
- API、Bot、ETL、Frontend 分離責任
- 讓資料庫成為可備份、可還原、可觀測的獨立服務
- 降低單一 machine / volume 綁死的風險
- 保留本地 SQLite 作為開發與快速測試用途

## 建議正式架構

```text
+-------------------------+
| User Browser / Telegram |
+-------------------------+
           |
           v
+-------------------------+         +---------------------------+
| Vercel                  |         | Render Background Worker  |
| Next.js Frontend        |         | Telegram Bot              |
+-------------------------+         +---------------------------+
           |                                   |
           v                                   v
                     +---------------------------+
                     | Render Web Service        |
                     | FastAPI API               |
                     +---------------------------+
                                   |
                                   v
                     +---------------------------+
                     | Postgres                  |
                     | Render Postgres / Neon    |
                     +---------------------------+
                                   ^
                                   |
                     +---------------------------+
                     | Render Cron Job           |
                     | Daily ETL / Backfill      |
                     +---------------------------+
```

## 服務責任

### Frontend

- 平台：Vercel
- 內容：`frontend/`
- 職責：
  - 提供 L0 / L1 / L2 畫面
  - 呼叫 backend API
  - 不直接聚合產業資料

### Backend API

- 平台：Render Web Service
- 內容：`backend/app/`
- 職責：
  - 提供 `/api/industries`、`/api/stocks`、`/api/realtime`、`/api/brokers`
  - 對外提供統一資料契約
  - 不與 cron / bot 綁在同一個 process

### Telegram Bot

- 平台：Render Background Worker
- 內容：`backend/run_telegram_bot.py`
- 職責：
  - 處理 Telegram 查詢與 `/ai`
  - 查詢同一份 Postgres
  - 與 API service 分離部署

### ETL / Backfill

- 平台：Render Cron Job
- 內容：`backend/run_daily_etl.py`、必要時用 batch 任務跑 backfill
- 職責：
  - 每日更新股票主檔、股價、三大法人、產業彙總
  - 歷史回補
  - 只負責寫 DB，不提供 HTTP

### Database

- 平台：
  - 首選：Render Postgres
  - 次選：Neon Postgres
- 職責：
  - 成為正式環境的唯一主資料庫
  - 支援備份、還原、連線池、監控

## 為什麼不再用 Fly.io + SQLite volume

- SQLite 是檔案型 DB，不適合作為長期正式環境的主資料庫
- API、Bot、ETL 共享同一顆檔案 DB，容易遇到鎖與搬運問題
- 線上資料搬移必須傳輸數 GB 單一檔案，維運脆弱
- volume 與 machine 綁定太強，遷移與回復成本高

## 為什麼這套架構比較適合 always-stock

- 專案已經同時具備：
  - Web frontend
  - API
  - Telegram Bot
  - 每日排程
  - 長期累積型歷史資料
- 這類型專案更適合用 service-based 架構，而不是 single-machine SQLite

## 本地開發策略

- 本地仍保留 SQLite
- staging / production 改用 Postgres
- DB connection 由 `DATABASE_URL` 決定

```text
local      -> sqlite:///backend/db/tw_stock.db
staging    -> postgresql+psycopg://...
production -> postgresql+psycopg://...
```

## 成功標準

- 前端、API、Bot、ETL 可獨立部署與重啟
- 正式 DB 不再透過搬運單一 `.db` 檔更新
- 支援自動備份與 restore
- 新環境部署後，L0 / L1 / L2 與 Telegram 查詢結果和現有系統一致
