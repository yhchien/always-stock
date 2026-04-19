# always-stock

台股產業別三大法人資金流向分析儀表板。

首頁目前提供兩個 AI 輔助模組：
- `Daily Brief`：依指定交易日產出盤前市場摘要
- `Trade Quality Analysis`：輸入股票與買進日，還原當時可觀察資訊後給出 5 階交易質量評級

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

## Claude Code 帳號切換

專案支援用 `direnv` 在本地自動切換 Claude Code 帳號。無需手動管理環境變數，指令一下自動切換。

### 前置設定（只需做一次）

1. **安裝 direnv**（已包含在開發環境中）

2. **將 direnv hook 加入 shell 配置**

   在 `~/.zshrc` 或 `~/.bashrc` 最後加入：
   ```bash
   eval "$(direnv hook bash)"    # if using bash
   eval "$(direnv hook zsh)"     # if using zsh
   ```

   重開 terminal 或 `source ~/.zshrc` 使設定生效。

3. **第一次進入專案時，允許 direnv**

   ```bash
   cd /Users/brian.yh.chien/.gstack/projects/always-stock
   direnv allow
   ```

### 使用方式

切換到帳號 **a** (brian780223@gmail.com，預設帳號，不加載環境變數)：
```bash
./scripts/switch-claude-account.sh a
```

切換到帳號 **b** (hsuan4store@gmail.com，自動加载環境變數)：
```bash
./scripts/switch-claude-account.sh b
```

也支援別名：
```bash
./scripts/switch-claude-account.sh brian         # 切換 a
./scripts/switch-claude-account.sh hsuan         # 切換 b
./scripts/switch-claude-account.sh brian780223@gmail.com
./scripts/switch-claude-account.sh hsuan4store@gmail.com
```

#### 切換後會發生什麼

- 指定帳號 **a** → direnv 卸載所有 Claude 環境變數 → Claude Code 自動使用預設帳號
- 指定帳號 **b** → direnv 加載 hsuan 的環境變數 → Claude Code 自動切換到 hsuan4store@gmail.com

### 設定檔說明

- `.envrc` — direnv 主配置檔，根據 `.direnv_account` 加載 / 卸載環境變數
- `.direnv_account` — 狀態檔（記錄當前帳號），不被版本控制
- `.envdir/` — 帳號環境變數目錄（不被版本控制）

---

## 資料表

### 現有資料（2026-04-17 現況）

| 資料表 | 說明 | 資料狀態 | 日期範圍 | 資料源 |
|--------|------|----------|----------|--------|
| `stocks_master` | 股票基本資料（含 industry / market） | ✅ 1,588 檔 | — | Fugle / FinMind |
| `daily_price` | 每日 OHLC、成交量、成交金額 | ✅ ~195 萬筆 | 2019-01-02 ~ 2026-04-08 | TWSE（待切 FinMind） |
| `inst_stock_flow` | 個股三大法人買賣超（foreign / trust / dealer） | ✅ ~6,194 萬筆 | 2019-01-02 ~ 2026-04-08 | TWSE（待切 FinMind） |
| `industry_daily_flow` | 產業別每日法人資金流向彙整 | ✅ ~8.5 萬筆 | 2019-01-02 ~ 2026-04-08 | 從 inst_stock_flow 聚合 |
| `broker_trade` | 分點買賣明細（TWSE BSR 舊版快取） | ⚠️ 679 筆 | 2026-04-09 only | TWSE BSR |

### 新增資料（FinMind 全面切換中）

| 資料表 | 說明 | 資料狀態 | 說明 |
|--------|------|----------|------|
| `daily_valuation` | 每日 P/E、P/B、殖利率 | ✅ ~173 萬筆 | FinMind `TaiwanStockPER`，2019-01 ~ 2026-04 |
| `monthly_revenue` | 每月營收 + YoY/MoM | ✅ ~7.4 萬筆 | FinMind `TaiwanStockMonthRevenue`，2019-01 ~ 2026-03 |
| `financial_statement` | 季財報各科目（EPS、營益率等） | ✅ ~45 萬筆 | FinMind 財報資料集，2019-03 ~ 2026-03 |
| `broker_trade_agg` | 分點買賣超聚合（取代舊 broker_trade） | 🔄 backfill 進行中 | FinMind `TaiwanStockTradingDailyReport`，2024-01 補到中（GitHub Actions 每小時自動跑） |
| `broker_trade_raw` | 分點逐筆原始資料（未來用） | ⬜ 待實作 | — |
| `industry_mapping` | 產業分類對照（Fugle ↔ FinMind） | ⬜ 待實作 | — |

> **注意**：`daily_valuation`、`monthly_revenue`、`financial_statement` 資料在 Render PostgreSQL，本地 SQLite 尚無資料。

## API Endpoints

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 健康檢查 |
| GET | `/api/industries?date=YYYY-MM-DD` | L0：產業排行榜 |
| GET | `/api/industries/{name}/summary?date=YYYY-MM-DD` | L1：子產業彙總 |
| GET | `/api/industries/{name}/stocks?date=YYYY-MM-DD` | L1：個股明細 |
| GET | `/api/stocks/{id}/history?days=90` | L2：個股走勢 + 法人累積 |
| GET | `/api/stocks/{id}/valuation?start_date=&end_date=` | L2：PER / PBR / 殖利率走勢 |
| GET | `/api/stocks/{id}/revenue?months=24` | L2：月營收 + YoY / MoM |
| GET | `/api/stocks/{id}/financials?quarters=8&item_names=` | L2：季財報項目（EPS 等） |
| GET | `/api/realtime/quotes?stock_ids=2330,2317` | 即時盤中報價（最多 50 檔） |
| GET | `/api/stocks/{id}/brokers?category=day_trade` | L2：關鍵券商分點 |
| GET | `/api/stocks/{id}/brokers/ranked?date=&days=` | L2：券商買進/賣出 Top10 |
| GET | `/api/stocks/{id}/brokers/{broker_id}/history?start=&end=` | L2：券商逐日買賣超走勢 |
| GET | `/api/stocks/search?q=...` | 股票 autocomplete 搜尋 |
| GET | `/api/market/daily-brief` | 首頁 AI 盤前摘要 |
| GET | `/api/market/latest-trade-date` | 取得 DB 最新交易日 |
| POST | `/api/analysis/trade-quality` | 首頁 AI 交易質量分析 |
| POST | `/api/backtest/run` | L3：回測執行 |
| POST | `/api/backtest/interpret` | L3：策略文字解析 |
| POST | `/api/backtest/advice` | L3：策略建議 |
| GET | `/api/backtest/templates` | L3：策略模板清單 |

---

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1~M4 | ETL + API + 前端三層儀表板 | ✅ |
| M5 | Telegram Bot 個股籌碼查詢 | ✅ |
| M6 | 8 年歷史資料 backfill | 🔄 進行中 |
| M7 | K 線圖（OHLC candlestick） | ✅ |
| M8 | 財報資料庫 + 前端面板 | ✅ |
| M9 | AI 籌碼分析（OpenAI GPT） | ✅ |
| M10 | 雲端部署（Render + Vercel） | ✅ |
| M11 | 回測程式（含 DSL + AI mapping） | ✅ |
| M12 | 自然語言策略優化 | ⬜ |
| M13 | 關鍵券商分點（FinMind 資料切換） | 🔄 進行中 |
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

| 資料 | 來源 | 所需權限 |
|------|------|----------|
| 股票基本資料 + 產業別 | FinMind `TaiwanStockInfo` / `TaiwanStockIndustryChain` | Free / Sponsor |
| 每日收盤價 OHLC | FinMind `TaiwanStockPrice`（目標）/ TWSE（現行） | Backer+ / 公開 |
| 三大法人買賣超 | FinMind `TaiwanStockInstitutionalInvestors`（目標）/ TWSE（現行） | Backer+ / 公開 |
| 每日 P/E、P/B、殖利率 | FinMind `TaiwanStockPER` | Backer+ |
| 月營收 | FinMind `TaiwanStockMonthRevenue` | Backer+ |
| 季財報 | FinMind 財報資料集 | Backer+ |
| 分點買賣超 | FinMind `TaiwanStockTradingDailyReport` | **Sponsor** |
| 子產業分類（舊） | Fugle（自定義爬取） | 本地 CSV |
