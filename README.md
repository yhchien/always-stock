# always-stock

台股產業別三大法人資金流向分析儀表板。

首頁目前提供以下模組：
- `Trade Quality Analysis`：輸入股票與買進日，還原當時可觀察資訊後給出 5 階交易質量評級（不需登入即可使用，分層 rate limit）
- `Industry Dashboard`：產業別三大法人資金流向排行
- `Hot Money List`：首頁底部與 L1 產業頁頂部呈現「近 N 日三大法人累計淨買超」個股排行（L0 Top 20 / L1 Top 10）
- `Watchlist`：登入後可建立關注買進清單（單一清單上限 30 檔，含未實現損益、交易質量快照卡片與個股頁報告入口）
- `Watchlist Trade Quality Snapshot`（M25）：登入後 `/watchlist` 直接顯示自選清單每檔的 5 階動作建議（強烈推薦/推薦/中立/再看看/快跑），由每日 ETL 後 cron 跑 trade quality 寫快照表並前端讀取；Trade Quality 分析新增 `key_factors` 條列指標（產業/熱度/報酬/籌碼/技術/基本面 + A/B/C 燈號）+ delta 比對（上次 → 這次評級變化）

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
| `industry_daily_flow` | 產業別每日法人資金流向彙整（FinMind 細分類 ~53 產業，~8.8 萬筆，含持久化 `streak`） | 2019-01 ~ today | 從 `inst_stock_flow` 聚合（`rebuild_industry_flow.py`） |
| `daily_valuation` | 每日 P/E、P/B、殖利率 | 2019-01 ~ today | FinMind `TaiwanStockPER` |
| `monthly_revenue` | 每月營收 + YoY/MoM | 2019-01 ~ 上個月 | FinMind `TaiwanStockMonthRevenue` |
| `financial_statement` | 季財報各科目（EPS、營益率等） | 2019-Q1 ~ 最新一季 | FinMind 財報資料集 |
| `broker_trade_agg` | 分點買賣超聚合 | 2024-01 ~ today（GitHub Actions 每小時推進） | FinMind `TaiwanStockTradingDailyReportSecIdAgg` |
| `stock_shares_outstanding` | 發行股數 + 外資持股比每日快照（市值 = shares_issued × close；魚尾 `institution_buy_to_market_cap` 分母） | 2026-07 ~ today | FinMind `TaiwanStockShareholding`（dataset-level 只回 start_date 當日，逐交易日抓） |
| `signal_watch_completed_archives` | M23：完成 30 個交易日追蹤後的封存摘要（first_seen / hit_count / day10/20/30 return） | 2026-04 ~ today | 從 `signal_watch_hits` + `daily_price` 計算 |
| `signal_expectation_prices` | M26：個股「未來 1 個月資金行情可期待價格區間」預測（保守 / 夢想價 + valuation_mode + 追高風險 + 信心 + scorecard + 達標旗標） | 2026-05 ~ today | OpenAI 依 prompt 推估，cron 跑「今日新進股」+ 使用者手動重產 |

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

> 共 5 個 workflow：3 個有排程 + 2 個僅手動。每日 cron 串成一條資料流水線：**18:00 ETL（抓資料）→ 19:00 LLM 訊號（產出今日異常訊號）→ 20:00 更新追蹤報酬**。GitHub Actions cron 設定為 UTC 但對齊台北時間；遇國定假日由 ETL 自動短路（exit 5）。

| Workflow | 何時做（台北時間） | 做什麼 |
|----------|------------------|--------|
| [`daily_etl_update.yml`](.github/workflows/daily_etl_update.yml) | 週一~五 **18:00**（cron）+ 手動 | 每個交易日傍晚全量刷新 Render PostgreSQL（FinMind ETL：stocks_master / daily_price / inst_flow / daily_valuation / monthly_revenue / financial_statement / margin_trade / shareholding / industry_flow 聚合；broker_trade_agg 已拆到獨立 workflow）。配額耗盡（exit 2）時 sleep 1.5h 後自動 retry 一次；假日由 daily_price 空資料 + 配額健康判定自動短路（exit 5）。**ETL 結束（exit 0/1）後串跑 M25 watchlist trade quality refresh**，對全使用者 watchlist 跑 trade quality 寫入 `watchlist_trade_quality_snapshots`，給 `/watchlist` 卡片與個股頁報告直接讀 |
| [`daily_signals.yml`](.github/workflows/daily_signals.yml) | 週一~五 **19:00**（cron）+ 手動 | M23 每日異常訊號 pipeline（`run_daily_signals.py`）：deterministic filter 建候選池 → LLM batch 分析公司業務／集團／龍頭比對 → 寫入 `signal_snapshots`，產出 LEADER / FOLLOWER / LAGGARD 三類訊號清單。exit 0/1（ok / no_data）→ workflow pass；exit 2/3（llm_error / db_error）→ workflow fail |
| [`signal_archive_returns.yml`](.github/workflows/signal_archive_returns.yml) | 週一~五 **20:00**（cron）+ 手動 | M23 30 個交易日訊號追蹤報酬率更新（`run_signal_archive_returns.py`）：對 active hits 同步 `latest_eval_price` / `return_pct`；完成 30 個交易日 cycle 後封存到 `signal_watch_completed_archives`（2026-05-21 起 retention 從 40 改 30）。可帶 `target_date` 手動補跑 |
| [`signal_expectation_prices.yml`](.github/workflows/signal_expectation_prices.yml) | `workflow_run` 接在 `daily_signals.yml` 完成（success）後 + 手動 | M26 個股保守 / 夢想價預測（`run_signal_expectation_prices.py`）：對「今日新進」`first_seen_date == target_date` 的股票呼叫 OpenAI 推估「未來 1 個月可期待價格區間」；同時 `update_hit_targets` 用當日收盤價標 `hit_conservative_at` / `hit_dream_at`。exit 0/1/2 視為 pass（no_data / partial 合理），exit 3 才 fail |
| [`broker_trade_backfill.yml`](.github/workflows/broker_trade_backfill.yml) | **手動觸發**（cron 已停用，2026-04-21 起） | 找出 `broker_trade_agg` 在 `[min_backfill_date, end_date]` 範圍內缺漏的週一~五交易日，每批 N 天（預設 3）補資料；FinMind 6000 req/hr 限制下，1 日 ≈ 1588 req |
| [`aggregate_industry_flow.yml`](.github/workflows/aggregate_industry_flow.yml) | **手動觸發** | 純本地 DB 聚合 `industry_daily_flow`（不打 FinMind）；用於 `daily_etl_update` 在 inst_flow 後斷掉（quota / timeout）時補聚合，或歷史資料 backfill 後重算 industry 層 |

> **M25 cron 時機決策**：watchlist trade quality 不獨立排程，串在 daily_etl_update.yml ETL 完成之後跑，避免兩個 workflow 同時打 OpenAI 與 DB。ETL 完全失敗（exit 2/3）時跳過 wtq；ETL holiday（exit 5）時也跳過。Cron 時間（18:00 台北）不動 —— 18:30~19:30 完成 ETL 後接著跑 wtq，使用者隔天早上看資料；`snapshot_trade_date` resolver 用 `ETL_DONE_TIME=20:00` 當截斷點，確保使用者打點時不會吃到不完整的當日 ETL。

### 待實作資料表

| 資料表 | 說明 | 狀態 |
|--------|------|------|
| `broker_trade_raw` | 分點逐筆原始資料（未來用） | ⬜ 待實作 |

> **注意**：`daily_valuation`、`monthly_revenue`、`financial_statement`、`broker_trade_agg` 資料在 Render PostgreSQL，本地 SQLite 尚無資料。

## API Endpoints

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 健康檢查 |
| GET | `/api/industries?date=YYYY-MM-DD` | L0：產業排行榜（同交易日 60 秒 server-side cache） |
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
| GET | `/api/signals/archive` | M23：最近 30 個交易日訊號追蹤總表（2026-05-21 起 retention 從 40 改 30） |
| GET | `/api/signals/archive/{stock_id}` | M23：單一股票 30 個交易日追蹤報告時間軸 |
| GET | `/api/signals/archive/completed` | M23：追蹤期滿移出後的封存表（含 day10/20/30 報酬） |
| GET | `/api/signals/expectation-prices?snapshot_date=` | M26：當日 watchlist 對應的「保守 / 夢想價」批次預測（公開） |
| GET | `/api/signals/expectation-prices/{stock_id}` | M26：單檔最新預測（公開） |
| GET | `/api/signals/expectation-prices/quota` | M26：手動重新預測今日剩餘額度（需登入；30/day per user、100/day 全站） |
| POST | `/api/signals/expectation-prices/regenerate` | M26：手動重新預測指定股票（需登入；背景跑 + UPSERT） |
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
| POST | `/api/watchlist` | M19：新增持股（`stock_id` / `buy_date` / `avg_price`，上限 30 檔） |
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
| M19 | 關注買進清單（單一清單上限 30 檔、加入 popup 填買進日/均價、`/watchlist` 顯示 trade quality 卡片與個股頁報告入口、Navbar「我的清單 N/30」） | ✅ |
| M20 | 交易分析擴充（預期 45% 報酬率加碼建議 + 風報比 1:1.75） | ⬜ 規劃中（M19 完成後） |
| M21 | Trade Quality Context 資料管線（6 section 預聚合 JSON + `GET /api/analysis/context`；deterministic + no-hindsight） | ✅ |
| M22 | 熱錢湧入個股排行（L0 底部 Top 20 / L1 頂部 Top 10，近 N 日三大法人累計買超） | ✅ |
| M23 | 每日異常訊號清單（deterministic filter 建候選池 + LLM 上網查公司業務／集團／龍頭比對；最終只保留 top 3 檔，輸出 LEADER / FOLLOWER / LAGGARD 三類；L0 tab bar + pulse 通知 + 多工背景重新產生 + 進度條；全候選先做短 decision，只有 WATCH 補長理由；另含 30 個交易日訊號追蹤清單（2026-05-21 起 retention 從 40 改 30）、命中次數、報酬率與報告時間軸，並新增追蹤期滿移出的 completed archive 封存表；不預測報酬、不出買賣建議。**2026-07-15 魚尾動能升級 v2.1**：候選池改四通道（法人 / 價格動能 / 動能加速 / 基本面暫緩）、每檔算 deterministic `momentum_score`（0~100）、LEADER/FOLLOWER/ROTATION_LAGGARD 改以相對強度 + 分數驅動、震盪盤 score<60 / 退潮盤 RS<90 直接剔除、動能特徵落 `signal_watch_hits.signal_metrics` 供回測歸因） | 🚧 持續優化中（[core spec](docs/plans/m23_daily_signals_spec.md) / [archive spec](docs/plans/m23_signal_archive_spec.md) / [momentum spec](docs/plans/fishtail_momentum_upgrade_spec.md)） |

M23 診斷約定：
- `market_context` 與 research / decision / watch-reason 各階段 fallback 都會附帶 `llm_diagnostic`
- `llm_diagnostic.status` 目前至少區分：`ok`、`api_key_missing`、`openai_exception`、`empty_output`、`invalid_json`
- Step 0 若 fallback，不可再籠統寫成「OpenAI 服務不可用」；需保留更精確的 stage / reason，方便事後判斷是 API key、timeout、空回應還是 JSON 解析失敗
- M23 走的是 Responses API + `web_search` tool；預設不再使用 `gpt-4o-search-preview` 這種舊 search-preview model 名稱作 fallback，避免 `404 Model not found`
- M23 pipeline 現在對 research / decision / watch-reason batch 採有限度並行（concurrency=2），在不把 OpenAI 壓太兇的前提下縮短整體 job 時間
- 首頁改為分段 deferred mount：`TradeQualityAnalysis` 先載，`DailySignalsPanel` / `HotMoneyList` / `IndustryDashboard` 近視窗時再掛載，降低初次載入壓力
- 交易分析 `/api/analysis/trade-quality` 與 `/stream` 目前對同 stock+buy_date 有 5 分鐘短時快取，重複分析可直接回 cache
- 觀察清單上限已由 20 調整為 30
- M23 候選來源已縮窄：`TOP_INDUSTRIES_LIMIT=6`、`TOP_STOCKS_LIMIT=30`、`TOP_STOCKS_INNER=6`
- M23 前端不再顯示 `removed` 候選；最終只保留 top 3 檔，卡片直接顯示保留理由
- M23 laggard 候選維持 `hits >= 2`，但額外要求 `total_institution_flow_1d > 0`
- M23 在 `after_hard` 後新增 `LLM_INPUT_HARD_LIMIT=50`，依 `prelim_type -> top_stock/top_industry -> flow score` 排序後截斷，再送進 LLM
| M24 | 自訂進出場策略回測（M11 擴充；使用者自設規則 + 歷史回測驗證 edge + LLM 在 trigger 當下給「適合執行」現場判斷） | ⬜ 規劃中 |

---

## 訪問控制：免登入 + 單一密碼閘門（2026-05-06）

註冊/登入功能已**永久停用**（程式碼保留可逆，`settings.is_auth_disabled()` 與前端 `feature_flags.isAuthDisabled()` 都 hardcode 回 `True`）。所有 user-bound 資料（watchlist、trade-quality 5 分鐘 cache、signals 每日 10 次配額…）全站共用一個 demo user（lifespan 啟動時 idempotent seed）。

進入儀表板前須通過**單一密碼閘門**（`<SiteGate />`）：

```bash
# Backend env（必填）
SITE_GATE_PASSWORD=<請改成自己的密碼>
SITE_GATE_MAX_ATTEMPTS=3       # optional, default 3
SITE_GATE_LOCKOUT_SECONDS=300  # optional, default 300（5 分鐘）
```

**行為**：
- 第一次造訪 → 顯示密碼輸入畫面，主頁面完全不渲染
- 答對 → localStorage 寫 `unlocked_until = now + 7 天`，期間直接放行
- 答錯 → 計數 +1（存 localStorage），達上限寫 `locked_until = now + 5 分鐘`，期間只顯示鎖定畫面
- 鎖定到期 → 自動回密碼輸入；嘗試次數重置
- 後端 `POST /api/gate/verify` 用 `hmac.compare_digest` 比對；`SITE_GATE_PASSWORD` 未設時回 503（不會洞開）

**Trade-offs**：
- 鎖定計數存 localStorage，使用者主動清掉就能繞過。個人專案信任使用者，這個強度夠用。要更嚴需改成後端按 IP rate limit
- 解鎖維持 7 天；想短/長就改 `SiteGate.tsx` 的 `UNLOCK_DURATION_MS`
- 密碼放 backend env，不會 leak 進 frontend bundle

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
