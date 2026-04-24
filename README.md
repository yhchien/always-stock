# always-stock

台股產業別三大法人資金流向分析儀表板。

首頁目前提供以下模組：
- `Trade Quality Analysis`：輸入股票與買進日，還原當時可觀察資訊後給出 5 階交易質量評級（不需登入即可使用，分層 rate limit）
- `Industry Dashboard`：產業別三大法人資金流向排行
- `Hot Money List`：首頁底部與 L1 產業頁頂部呈現「近 N 日三大法人累計淨買超」個股排行（L0 Top 20 / L1 Top 10）
- `Watchlist`：登入後可建立關注買進清單（單一清單上限 20 檔，含未實現損益與一鍵跳 M17 交易分析）

> Daily Brief（盤前摘要）已從首頁移到 Telegram Bot `/brief` 指令。

## 專案目的

追蹤 TWSE 上市股票的三大法人（外資、投信、自營商）每日買賣超，
以 FinMind `TaiwanStockIndustryChain` 細分類（約 53 個產業）為基礎，呈現產業層級的資金流向排行榜，
並支援 drill-down 到個股法人明細、K 線圖、財報面板與 AI 交易質量分析，協助制定交易策略。

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

### 主要資料表（2026-04-23 現況；FinMind 切換已完成）

| 資料表 | 說明 | 日期範圍 | 資料源 |
|--------|------|----------|--------|
| `stocks_master` | 股票基本資料（industry / sub_industry） | — | FinMind `TaiwanStockInfo` + `TaiwanStockIndustryChain` |
| `daily_price` | 每日 OHLC、成交量、成交金額 | 2019-01 ~ today | FinMind `TaiwanStockPrice` |
| `inst_stock_flow` | 個股三大法人買賣超（foreign / trust / dealer，含 amount_est） | 2019-01 ~ today | FinMind `TaiwanStockInstitutionalInvestorsBuySell` |
| `industry_daily_flow` | 產業別每日法人資金流向彙整（FinMind 細分類 ~53 產業，~8.8 萬筆） | 2019-01 ~ today | 從 `inst_stock_flow` 聚合（`rebuild_industry_flow.py`） |
| `daily_valuation` | 每日 P/E、P/B、殖利率 | 2019-01 ~ today | FinMind `TaiwanStockPER` |
| `monthly_revenue` | 每月營收 + YoY/MoM | 2019-01 ~ 上個月 | FinMind `TaiwanStockMonthRevenue` |
| `financial_statement` | 季財報各科目（EPS、營益率等） | 2019-Q1 ~ 最新一季 | FinMind 財報資料集 |
| `broker_trade_agg` | 分點買賣超聚合 | 2024-01 ~ today（GitHub Actions 每小時推進） | FinMind `TaiwanStockTradingDailyReportSecIdAgg` |

### M18 / M19 資料表（使用者系統）

| 資料表 | 說明 |
|--------|------|
| `users` | 使用者帳號（email / password_hash / is_admin） |
| `user_sessions` | Server-side session（UUID token、httpOnly cookie、30 天過期） |
| `user_watchlist` | 關注買進清單（user_id / stock_id / buy_date / avg_price，UNIQUE (user_id, stock_id)，單一清單上限 20 檔） |

### Deprecated

| 資料表 | 說明 |
|--------|------|
| `broker_trade` | TWSE BSR 舊版快取（L2 券商面板已隱藏，後續若復活改吃 `broker_trade_agg`） |
| `chain` 欄位 | Fugle 上中下游分類；2026-04-21 隨 FinMind 切換永久捨棄（`stocks_master.chain` 保留欄位但永遠 NULL） |

### 自動化排程（GitHub Actions）

| Workflow | 排程 | 說明 |
|----------|------|------|
| `.github/workflows/daily_etl_update.yml` | 週一~五 23:00（台北） | 每個交易日收盤後全量刷新 Render PostgreSQL（6 個 FinMind 模組：daily_price / inst_flow / daily_valuation / monthly_revenue / financial_statement / broker_trade_agg）。配額耗盡時 1.5h 後自動 retry 一次；假日由 daily_price 空資料 + 配額健康判定自動短路 |
| `.github/workflows/broker_trade_backfill.yml` | 每小時第 5 分 | 分點買賣超歷史 backfill，以交易日為單位逐批推進 |

### 待實作資料表

| 資料表 | 說明 | 狀態 |
|--------|------|------|
| `broker_trade_raw` | 分點逐筆原始資料（未來用） | ⬜ 待實作 |

> **注意**：`daily_valuation`、`monthly_revenue`、`financial_statement`、`broker_trade_agg` 資料在 Render PostgreSQL，本地 SQLite 尚無資料。

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
| GET | `/api/market/daily-brief` | AI 盤前摘要（Telegram `/brief` 共用） |
| GET | `/api/market/latest-trade-date` | 取得 DB 最新交易日 |
| GET | `/api/market/hot-money?date=&days=3&limit=20` | L0：熱錢湧入個股排行 Top N（M22） |
| GET | `/api/industries/{name}/hot-money?date=&days=3&limit=10&sub_industry=` | L1：單產業熱錢排行（M22） |
| POST | `/api/analysis/trade-quality` | 首頁 AI 交易質量分析（公開；未登入 3/day、已登入 30/day） |
| GET | `/api/analysis/context` | M21：Trade Quality Context 6 section 預聚合 JSON（需登入；deterministic + no-hindsight） |
| POST | `/api/backtest/run` | L3：回測執行（需登入） |
| POST | `/api/backtest/interpret` | L3：策略文字解析（需登入） |
| POST | `/api/backtest/advice` | L3：策略建議（需登入） |
| GET | `/api/backtest/templates` | L3：策略模板清單 |
| POST | `/api/auth/register` | M18：Email/password 註冊（自動登入） |
| POST | `/api/auth/login` | M18：Email/password 登入（httpOnly cookie session） |
| POST | `/api/auth/logout` | M18：登出 |
| GET | `/api/auth/me` | M18：取得當前登入使用者 |
| GET | `/api/watchlist` | M19：取回關注清單（含未實現損益） |
| POST | `/api/watchlist` | M19：新增持股（`stock_id` / `buy_date` / `avg_price`，上限 20 檔） |
| DELETE | `/api/watchlist/{entry_id}` | M19：移除單筆持股 |
| DELETE | `/api/watchlist` | M19：清空整份清單 |

---

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1~M4 | ETL + API + 前端三層儀表板 | ✅ |
| M5 | Telegram Bot 個股籌碼查詢 | ✅ |
| M6 | 8 年歷史資料 backfill（2019-01 ~ 2026-04） | ✅（僅 5 天 OHLC 資料源缺漏） |
| M7 | K 線圖（OHLC candlestick） | ✅ |
| M8 | 財報資料庫 + 前端面板 | ✅ |
| M9 | AI 籌碼分析（OpenAI GPT） | ✅ |
| M10 | 雲端部署（Render + Vercel） | ✅ |
| M11 | 回測程式（含 DSL + AI mapping；2026-04 擴充 4 欄位 + 9 K棒 + 6 技術型態 + 報酬率%回撤圖） | ✅ |
| M12 | 自然語言策略優化 | ⬜ |
| M13 | 關鍵券商分點（FinMind 資料切換） | 🔄 ETL / backfill 完成，L2 UI 暫時隱藏 |
| M14 | LLM 輿情分析 | ⬜ |
| M15 | Telegram 電子報 | ⬜ |
| M16 | 首頁 AI 盤前摘要（Daily Brief） | ✅ |
| M17 | 交易質量 AI 分析（Trade Quality Analysis，5 階評級 + 四象限 + 目標價；**2026-04-24** 起吃 M21 deterministic 預聚合訊號） | ✅ |
| M18 | 使用者註冊系統（Email/password + server-side session + RequireAuth；M17 公開但 3/day/30/day 分層 rate limit） | ✅ |
| M19 | 關注買進清單（單一清單上限 20 檔、加入 popup 填買進日/均價、`/watchlist` 卡片含未實現損益 + M17 深連結、Navbar「我的清單 N/20」） | ✅ |
| M20 | 交易分析擴充（預期 45% 報酬率加碼建議 + 風報比 1:1.75） | ⬜ 規劃中（M19 完成後） |
| M21 | Trade Quality Context 資料管線（6 section 預聚合 JSON + `GET /api/analysis/context`；deterministic + no-hindsight） | ✅ |
| M22 | 熱錢湧入個股排行（L0 底部 Top 20 / L1 頂部 Top 10，近 N 日三大法人累計買超） | ✅ |
| M23 | 每日異常訊號清單（07:00 台北排程；deterministic filter 篩股 + LLM 解釋層中文註解；不預測報酬、不排推薦度） | ⬜ 規劃中 |
| M24 | 自訂進出場策略回測（M11 擴充；使用者自設規則 + 歷史回測驗證 edge + LLM 在 trigger 當下給「適合執行」現場判斷） | ⬜ 規劃中 |

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
| 股票基本資料 + 產業別 | FinMind `TaiwanStockInfo` / `TaiwanStockIndustryChain` | Backer+ |
| 每日收盤價 OHLC | FinMind `TaiwanStockPrice` | Backer+ |
| 三大法人買賣超 | FinMind `TaiwanStockInstitutionalInvestorsBuySell` | Backer+ |
| 每日 P/E、P/B、殖利率 | FinMind `TaiwanStockPER` | Backer+ |
| 月營收 | FinMind `TaiwanStockMonthRevenue` | Backer+ |
| 季財報 | FinMind 財報資料集 | Backer+ |
| 分點買賣超（聚合） | FinMind `TaiwanStockTradingDailyReportSecIdAgg` | **Sponsor** |
