# always-stock

台股產業別三大法人資金流向分析儀表板。

## 專案目的

追蹤 TWSE 上市股票的三大法人（外資、投信、自營商）每日買賣超，
以 Fugle 自定義子產業分類為基礎，呈現產業層級的資金流向排行榜，
並支援 drill-down 到個股法人明細與走勢圖，協助制定交易策略。

---

## 技術堆疊

| 層級 | 技術 |
|------|------|
| **Frontend** | Next.js + Tailwind CSS + shadcn/ui + ECharts |
| **Backend** | FastAPI + SQLAlchemy（Python 3.9+） |
| **DB** | PostgreSQL（Render Managed）；本地開發可用 SQLite |
| **ETL** | FinMind API + TWSE 公開資料 |
| **Bot** | Telegram Bot（long-polling）+ OpenAI GPT 籌碼分析 |
| **部署** | Render（後端 + Postgres）+ Vercel（前端） |

## 部署架構

```
┌──── Vercel ────────────────┐     ┌──── Render (Singapore) ──────────┐
│                             │     │                                   │
│  Frontend (Next.js)         │────→│  Web Service — FastAPI API        │
│  自動從 GitHub 部署         │ HTTP│  Background Worker — Telegram Bot │
│                             │     │  Cron Job — 每日 ETL              │
└─────────────────────────────┘     │  PostgreSQL — 資料庫              │
                                    └───────────────────────────────────┘
```

| 服務 | 平台 | URL |
|------|------|-----|
| 前端 | Vercel | https://always-stock.vercel.app |
| 後端 API | Render | https://always-stock.onrender.com |

推 code 到 `main` 即自動部署兩邊。詳見 [Deployment Guide](docs/operations/deployment_guide.md)。

---

## 快速開始（本地開發）

```bash
# 後端
cd backend
pip install -r requirements.txt
python init_db.py
python3 -m uvicorn app.main:app --reload
# API: http://localhost:8000  |  Swagger: http://localhost:8000/docs

# 前端
cd frontend
npm install
npm run dev
# http://localhost:3000
```

本地模式直接讀取 `backend/db/tw_stock.db`，不需要任何雲端服務。

---

## 資料表

| 資料表 | 說明 |
|--------|------|
| `stocks_master` | 股票基本資料，含 industry / chain / sub_industry |
| `daily_price` | 每日收盤價、OHLC、成交量、成交金額 |
| `inst_stock_flow` | 個股三大法人買賣超（每股 3 筆：foreign / trust / dealer） |
| `industry_daily_flow` | 產業別每日法人資金流向（以 Fugle 大類彙整） |
| `broker_trade` | 分點買賣明細（on-demand 從 TWSE BSR 抓取快取） |

## API Endpoints

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 健康檢查 |
| GET | `/api/industries?date=YYYY-MM-DD` | L0：產業排行榜 |
| GET | `/api/industries/{name}/summary?date=YYYY-MM-DD` | L1：子產業彙總 |
| GET | `/api/industries/{name}/stocks?date=YYYY-MM-DD` | L1：個股明細 |
| GET | `/api/stocks/{id}/history?days=90` | L2：個股走勢 + 法人累積 |
| GET | `/api/realtime/quotes?stock_ids=2330,2317` | 即時盤中報價（最多 50 檔） |
| GET | `/api/stocks/{id}/brokers?category=day_trade` | L2：關鍵券商分點 |

---

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1~M4 | ETL + API + 前端三層儀表板 | ✅ |
| M5 | Telegram Bot 個股籌碼查詢 | ✅ |
| M6 | 8 年歷史資料 backfill | 🔄 進行中 |
| M7 | K 線圖（OHLC candlestick） | ✅ |
| M8 | 財報資料庫 | ⬜ |
| M9 | AI 籌碼分析（OpenAI GPT） | ✅ |
| M10 | 雲端部署（Render + Vercel） | ✅ |
| M11 | 回測程式 | ⬜ |
| M12 | 自然語言策略輸入 | ⬜ |
| M13 | 關鍵券商分點 | ✅ |
| M14 | LLM 輿情分析 | ⬜ |
| M15 | Telegram 電子報 | ⬜ |

---

## 文件

### 架構
- [Architecture Overview](docs/architecture/architecture_overview.md) — 技術選擇、通訊方式、部署演進
- [Architecture Target](docs/architecture/architecture_target.md) — 長期目標架構
- [SQLite to Postgres Migration](docs/architecture/migration_plan_sqlite_to_postgres.md)

### 維運
- [Deployment Guide](docs/operations/deployment_guide.md) — 日常部署操作手冊
- [Operations Runbook](docs/operations/runbook_operations.md) — 健康檢查、故障排除
- [Security and Secrets](docs/operations/security_and_secrets.md) — 環境變數與 secrets 管理
- [Data Migration Checklist](docs/operations/data_migration_checklist.md)
- [Observability](docs/operations/observability.md) — 監控策略

### 未來規劃
- [FinMind Migration Plan](docs/plans/finmind_migration_plan.md) — 全面切換 FinMind 主資料源
- [Data Source Feasibility](docs/plans/data_source_feasibility_assessment.md) — 資料源評估
- [Natural Language Backtest](docs/plans/natural_language_backtest_design.md) — 自然語言回測設計
- [Repo Restructure](docs/plans/repo_restructure_plan.md) — 目錄重整規劃

---

## 資料來源

| 資料 | 來源 | 限制 |
|------|------|------|
| 股票基本資料 + 產業別 | FinMind `TaiwanStockInfo` | 免費 300 req/hr |
| 每日收盤價 | TWSE `STOCK_DAY_ALL` | 公開免費 |
| 三大法人買賣超 | TWSE `T86` | 公開免費 |
| 子產業分類 | Fugle（自定義爬取） | 本地 CSV |
