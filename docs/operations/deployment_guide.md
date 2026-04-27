# Deployment Guide

日常開發與部署操作手冊。

## 環境分層

| 環境 | Frontend | Backend | DB | 用途 |
|------|----------|---------|-----|------|
| **Local** | `npm run dev` | `uvicorn` | SQLite | 日常開發、UI 調整、ETL 除錯 |
| **Staging** | Vercel preview | Render staging | Staging Postgres | 驗證 migration / schema |
| **Production** | Vercel | Render Web Service | Render Postgres | 正式上線 |

## 架構總覽

```
GitHub repo (yhchien/always-stock)
       │
       ├── push to main ──→ Render 自動 build + deploy 後端
       │                     (Web Service, Docker, backend/)
       │
       └── push to main ──→ Vercel 自動 build + deploy 前端
                             (Next.js, frontend/)
```

| 元件 | 平台 | URL |
|------|------|-----|
| 前端 | Vercel | https://always-stock.vercel.app |
| 後端 API | Render Web Service | https://always-stock.onrender.com |
| 資料庫 | Render PostgreSQL | Singapore region |

---

## 修改前端

前端程式碼在 `frontend/` 目錄。

### 流程

1. 本地修改 `frontend/` 下的檔案
2. 本地測試：
   ```bash
   cd frontend
   npm run dev
   # 打開 http://localhost:3000 確認
   ```
3. 測試通過後 commit + push：
   ```bash
   git add frontend/...
   git commit -m "描述你改了什麼"
   git push origin main
   ```
4. **Vercel 會自動偵測 push，自動 build + deploy**
5. 約 1~2 分鐘後，https://always-stock.vercel.app 就會更新

### 注意

- Vercel 只會在 `frontend/` 目錄有變更時觸發 rebuild
- 如果 build 失敗，Vercel 不會更新線上版本，舊版會繼續運行
- 可以在 Vercel Dashboard → Deployments 頁面看 build log

---

## 修改後端

後端程式碼在 `backend/` 目錄。

### 流程

1. 本地修改 `backend/` 下的檔案
2. 本地測試：
   ```bash
   cd backend
   python3 -m uvicorn app.main:app --reload
   # Swagger UI: http://localhost:8000/docs
   ```
3. 跑測試：
   ```bash
   cd backend
   python3 -m pytest
   ```
4. 測試通過後 commit + push：
   ```bash
   git add backend/...
   git commit -m "描述你改了什麼"
   git push origin main
   ```
5. **Render 會自動偵測 push，自動 build Docker image + deploy**
6. 約 3~5 分鐘後，https://always-stock.onrender.com 就會更新

### 注意

- Render Free 方案 build 較慢（約 3~5 分鐘）
- build 期間舊版會繼續服務，新版 ready 後才切換（zero downtime）
- 可以在 Render Dashboard → Events 頁面看 build log

---

## 修改資料庫（schema 變更）

如果需要新增欄位、新增表等 schema 變更：

### 流程

1. 修改 `backend/app/models.py`（SQLAlchemy model）
2. 本地先用 SQLite 測試：
   ```bash
   cd backend
   python3 init_db.py
   python3 -m uvicorn app.main:app --reload
   ```
3. 確認 OK 後，**在 Render Postgres 上執行 schema 變更**：
   ```bash
   cd backend
   DATABASE_URL="postgresql://<USER>:<PASSWORD>@<HOST>/<DBNAME>" python3 init_db.py
   ```
   注意：`init_db.py` 只會新增表/欄位，不會刪除或修改既有的欄位。
   如果需要修改既有欄位（改名、改型別、刪欄位），需要手動寫 migration script。
4. Commit + push 後端程式碼，Render 會自動重新部署

### 注意

- 先改 schema，再部署程式碼，避免新程式碼讀到舊 schema 報錯
- `init_db.py` 是冪等的（跑多次不會壞），安全使用

---

## 新增資料（ETL）

每日 ETL 目前仍在本地跑（macOS launchd），或可手動執行：

```bash
cd backend
python3 run_daily_etl.py --skip-master
```

如果你的 `DATABASE_URL` 指向 Render Postgres，ETL 會直接寫入雲端 DB。

### 未來可選：在 Render 開 Cron Job

在 Render Dashboard 建一個 Cron Job：

| 欄位 | 值 |
|------|-----|
| Name | `always-stock-etl` |
| Region | Singapore |
| Branch | `main` |
| Root Directory | `backend` |
| Runtime | Docker |
| Schedule | `0 12 * * 1-5`（UTC 12:00 = 台灣 20:00，週一至五） |
| Command | `python run_daily_etl.py --skip-master` |

環境變數同後端 API（`DATABASE_URL`、`TZ`）。

---

## 環境變數管理

### Render（後端）

在 Render Dashboard → `always-stock-api` → **Environment**：

| 變數 | 用途 | 目前值 |
|------|------|--------|
| `DATABASE_URL` | Postgres 連線字串 | postgresql://... |
| `CORS_ORIGINS` | 允許的前端 origin | `https://always-stock.vercel.app,http://localhost:3000` |
| `TZ` | 時區 | `Asia/Taipei` |

修改環境變數後 Render 會自動重新部署。

### Vercel（前端）

在 Vercel Dashboard → `always-stock` → **Settings** → **Environment Variables**：

| 變數 | 用途 | 目前值 |
|------|------|--------|
| `NEXT_PUBLIC_API_URL` | 後端 API URL | `https://always-stock.onrender.com` |

修改後需要手動 Redeploy（Vercel Dashboard → Deployments → 最新一筆 → Redeploy）。

---

## 免費方案限制

| 平台 | 限制 | 影響 |
|------|------|------|
| Vercel Hobby | 無時間限制，100GB bandwidth/月 | 個人專案不太可能超過 |
| Render Free Web Service | 750 小時/月，閒置 15 分鐘後休眠 | 冷啟動慢 30~60 秒，之後正常 |
| Render Free Postgres | **90 天後過期刪除**，1GB storage | 到期前須升級（Starter $7/月）或遷移 |

### Postgres 90 天到期怎麼辦

選項：
1. **升級 Render Postgres**：Starter 方案 $7/月，無過期限制
2. **遷移到 Neon/Supabase**：都有免費 Postgres，無 90 天限制
3. **重新建一個 Free Postgres**：再跑一次 migration，但資料要重新匯入

建議在到期前 2 週處理。

---

## 常用指令速查

```bash
# === 本地開發 ===
cd backend && python3 -m uvicorn app.main:app --reload    # 後端
cd frontend && npm run dev                                  # 前端

# === 測試 ===
cd backend && python3 -m pytest                             # 後端測試
cd frontend && npm test                                     # 前端測試

# === 部署（自動） ===
git push origin main                                        # push 後兩邊自動部署

# === 手動觸發 ETL（寫入雲端 DB） ===
cd backend
DATABASE_URL="postgresql://..." python3 run_daily_etl.py --skip-master

# === 測試雲端 API ===
curl https://always-stock.onrender.com/health
curl "https://always-stock.onrender.com/api/industries?date=2026-04-08"
```
