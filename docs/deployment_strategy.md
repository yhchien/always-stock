# Deployment Strategy

本文件定義 `always-stock` 的目標部署策略。

## 環境分層

### Local

- Frontend：本機 `npm run dev`
- Backend：本機 `uvicorn`
- DB：本機 SQLite
- 用途：
  - 日常開發
  - UI 調整
  - ETL / API 除錯

### Staging

- Frontend：Vercel preview / staging project
- Backend：Render staging web service
- Bot：可選 staging bot 或暫不部署
- DB：Staging Postgres
- 用途：
  - 驗證 migration
  - 驗證 schema 與查詢
  - 驗證切流前相容性

### Production

- Frontend：Vercel production
- Backend API：Render Web Service
- Bot：Render Background Worker
- ETL：Render Cron Job
- DB：Render Postgres / Neon Postgres

## 最小可用 staging 流程

1. 建立 staging Postgres
2. 在 backend 設定 `DATABASE_URL`
3. 先執行：
   - `python init_db.py`
4. 再執行資料匯入：
   - `python migrate_sqlite_to_postgres.py --target-database-url ... --verify-counts`
5. 再執行資料驗證：
   - `python validate_migrated_data.py --target-database-url ...`
6. 啟動 backend：
   - `uvicorn app.main:app --host 0.0.0.0 --port 8000`
7. 啟動 frontend：
   - `NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev`

若要直接準備 Render blueprint，可參考：

- `infra/render/render.yaml.template`

本地環境變數範本：

- `backend/.env.example`
- `frontend/.env.local.example`

本地若要做 SQLite -> Postgres 匯入，建議建立：

- `backend/.env`

至少放：

```env
DATABASE_URL=sqlite:///backend/db/tw_stock.db
TARGET_DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE
TZ=Asia/Taipei
```

之後可直接在 `backend/` 執行：

```bash
python migrate_sqlite_to_postgres.py --verify-counts
python validate_migrated_data.py
```

前端本地則放：

- `frontend/.env.local`

內容：

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 服務與 repo 對應

### Vercel

- 來源：`frontend/`
- 必要環境變數：
  - `NEXT_PUBLIC_API_URL`

### Render Web Service

- 來源：`backend/`
- 啟動：
  - `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 必要環境變數：
  - `DATABASE_URL`
  - `TZ=Asia/Taipei`
  - `CORS_ORIGINS`

### Render Background Worker

- 來源：`backend/`
- 啟動：
  - `python run_telegram_bot.py`
- 必要環境變數：
  - `DATABASE_URL`
  - `TELEGRAM_BOT_TOKEN`
  - `OPENAI_API_KEY`
  - `TZ=Asia/Taipei`

### Render Cron Job

- 來源：`backend/`
- 指令：
  - `python run_daily_etl.py --skip-master`
- 必要環境變數：
  - `DATABASE_URL`
  - `TZ=Asia/Taipei`
  - `FINMIND_API_TOKEN`（若使用）

## Secrets 規劃

正式環境至少應區分：

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `FINMIND_API_TOKEN`
- `CORS_ORIGINS`

原則：

- 不寫入 repo
- staging / production 分開
- 不讓 frontend 持有 backend-only secret

## 切換策略

### 第 1 階段

- 先把 frontend 搬到 Vercel
- 仍指向現有 API

### 第 2 階段

- 建立 staging Postgres
- backend 支援 Postgres
- staging API 驗證完成

### 第 3 階段

- production API 切到 Postgres
- Telegram Bot 切到新 backend / DB
- ETL / cron 改寫入 Postgres

### 第 4 階段

- 保留舊 Fly 系統短期觀察
- 確認資料一致後，移除舊主流程依賴

## 不再推薦的 production 模式

- `Fly.io + SQLite volume` 作為主要正式資料庫
- 以傳輸單一巨大 `.db` 檔作為日常維運方式
