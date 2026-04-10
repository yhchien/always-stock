# always-stock

台股產業別三大法人資金流向分析儀表板。

## 專案目的

追蹤 TWSE 上市股票的三大法人（外資、投信、自營商）每日買賣超，
以 Fugle 自定義子產業分類為基礎，呈現產業層級的資金流向排行榜，
並支援 drill-down 到個股法人明細與走勢圖，協助制定交易策略。

---

## 專案架構

```
always-stock/
├── backend/
│   ├── app/
│   │   ├── database.py          # SQLAlchemy engine / session
│   │   ├── models.py            # ORM 資料表定義（5 張表）
│   │   ├── broker_config.py     # 關鍵券商分類設定（當沖/隔日沖/短線/波段）
│   │   └── routers/             # FastAPI routers（industries / stocks / brokers）
│   ├── etl/
│   │   ├── fetch_stock_master.py    # FinMind 股票基本資料 + Fugle 子產業 mapping
│   │   ├── fetch_daily_price.py     # TWSE STOCK_DAY_ALL 收盤價
│   │   ├── fetch_inst_flow.py       # TWSE T86 三大法人買賣超
│   │   ├── fetch_broker_trade.py    # TWSE BSR 分點買賣明細（on-demand + 背景回補）
│   │   └── aggregate_industry_flow.py  # 彙整到產業日流向表
│   ├── tests/                   # 每個 ETL 模組的單元測試
│   ├── db/
│   │   └── tw_stock.db          # SQLite 資料庫
│   ├── logs/                    # ETL 執行 log（滾動保留 7 天）
│   ├── logging_config.py        # 統一 logging 設定
│   ├── init_db.py               # 初始化資料表
│   ├── migrate_add_broker_trade.py  # 一次性 migration：建立 broker_trade 表
│   ├── run_daily_etl.py         # 每日 ETL 主程式（CLI）
│   ├── run_backfill.py          # 歷史 backfill（可斷點續傳）
│   ├── scripts/
│   │   ├── daily_update.sh      # 每日自動更新 shell script
│   │   └── com.always-stock.daily-etl.plist  # macOS launchd 排程
│   ├── run_telegram_bot.py      # Telegram Bot 啟動腳本（long-polling）
│   └── requirements.txt
├── frontend/                    # Next.js 前端
│   ├── src/
│   │   ├── app/                 # Next.js App Router
│   │   │   ├── page.tsx                        # 首頁（L0 入口）
│   │   │   ├── industries/[industryName]/page.tsx  # L1 個股列表頁
│   │   │   └── stocks/[stockId]/page.tsx           # L2 個股走勢頁
│   │   ├── components/
│   │   │   ├── ui/              # shadcn/ui 元件
│   │   │   ├── IndustryDashboard.tsx  # L0 產業排行榜
│   │   │   ├── StockList.tsx          # L1 個股列表（可排序）
│   │   │   ├── StockChart.tsx         # L2 雙軸走勢圖（ECharts）
│   │   │   └── BrokerPanel.tsx        # L2 關鍵券商買賣長條表（分類 tab + 即時抓取）
│   │   ├── lib/
│   │   │   └── api.ts           # API fetch helpers + 格式化工具
│   │   └── __tests__/           # Jest 單元測試
│   ├── jest.config.ts
│   └── package.json
└── tools/
    ├── output/
    │   ├── fugle_industry_mapping.csv   # Fugle 子產業分類（stock_id → sub_industry）
    │   └── fugle_industry_mapping.json
    └── scrape_fugle_industry.py
```

### 資料流

```text
+--------------------------+      +--------------------------+
| FinMind TaiwanStockInfo  | ---> | fetch_stock_master.py    |
+--------------------------+      +--------------------------+
                                      |
+--------------------------+          v
| Fugle mapping CSV / JSON | ------> +--------------------------+
                                      | stocks_master            |
                                      +--------------------------+

+--------------------------+      +--------------------------+      +--------------------------+
| TWSE MI_INDEX            | ---> | fetch_daily_price.py     | ---> | daily_price              |
| 每日收盤 / OHLC / 成交量 |      +--------------------------+      +--------------------------+
+--------------------------+

+--------------------------+      +--------------------------+      +--------------------------+
| TWSE T86                 | ---> | fetch_inst_flow.py       | ---> | inst_stock_flow          |
| 三大法人買賣超           |      +--------------------------+      +--------------------------+
+--------------------------+

+--------------------------+      +--------------------------+      +--------------------------+
| TWSE BSR                 | ---> | fetch_broker_trade.py    | ---> | broker_trade             |
| 券商分點明細             |      +--------------------------+      +--------------------------+
+--------------------------+

                  +--------------------------+
stocks_master --->|                          |
daily_price   --->| aggregate_industry_flow  | ---> industry_daily_flow
inst_stock_flow ->|                          |
                  +--------------------------+

industry_daily_flow + stocks_master + inst_stock_flow + daily_price
  ---> FastAPI /api/industries ---> Next.js L0 / L1

stocks_master + daily_price + inst_stock_flow
  ---> FastAPI /api/stocks ---> Next.js L2 / Telegram Bot

broker_trade
  ---> FastAPI /api/stocks/{stock_id}/brokers ---> Next.js L2 BrokerPanel
  ---> cache miss 時 on-demand 抓 TWSE BSR
```

**重點說明：**
- `run_daily_etl.py` 每日固定跑 `stock_master -> daily_price -> inst_flow -> industry_daily_flow`
- `industry_daily_flow` 是 L0 排行榜的主查詢表，避免前端或 API 即時計算產業聚合
- `broker_trade` 不在每日 ETL 主流程內，而是 L2 需要時才由 `/api/stocks/{stock_id}/brokers` 觸發抓取與快取
- 前端 L1 / L2 顯示需要的個股與歷史細節，直接查 `stocks_master`、`daily_price`、`inst_stock_flow`

### 頁面 Flow

```text
+----------------------------------+
| L0 首頁 /                        |
| IndustryDashboard                |
| - 日期切換                       |
| - 產業排行 / 搜尋 / streak       |
+----------------------------------+
                |
                | 點擊產業
                v
+----------------------------------+
| L1 產業頁 /industries/{name}     |
| StockList                        |
| - 子產業 summary table           |
| - chain 分組個股                 |
| - 盤中即時報價補充               |
+----------------------------------+
                |
                | 點擊子產業
                v
       套用子產業 filter
                |
                | 點擊個股
                v
+----------------------------------+
| L2 個股頁 /stocks/{stockId}      |
| StockChart                       |
| - K 線 / 收盤價                  |
| - 法人累積買超                   |
| - MA 與區間切換                  |
| BrokerPanel                      |
| BacktestPanel skeleton           |
+----------------------------------+
```

**使用者視角：**
- L0 看「今天哪些產業被買、被賣，且是否連買 / 連賣」
- L1 看「某個產業裡，資金集中在哪些子產業、供應鏈哪一段、哪些股票」
- L2 看「單一股票的價格走勢、法人累積部位、關鍵券商分點，以及後續回測擴充入口」

### 目前資料狀態（2026-04-10）

- 已重新補跑本地 backfill 缺口與 `2026-04-09` 前的歷史缺日
- `inst_stock_flow` / `industry_daily_flow` 目前仍缺 3 天：
  - `2019-04-04`
  - `2023-04-03`
  - `2026-02-18`
- 這 3 天重抓時，TWSE `MI_INDEX` 目前回傳「沒有符合條件的資料」，因此暫時視為資料源特殊日
- `daily_price` 仍有 5 天的 `OHLC` 缺漏：
  - `2023-05-05`
  - `2023-09-19`
  - `2024-01-17`
  - `2024-02-29`
  - `2024-07-11`
- 上述 5 天已重抓一次，但 `open_price / high_price / low_price` 仍為空，推測是現行 `MI_INDEX` 回傳本身即缺少這些欄位

### 資料表

| 資料表 | 說明 |
|--------|------|
| `stocks_master` | 股票基本資料，含 industry / chain / sub_industry |
| `daily_price` | 每日收盤價、成交量、成交金額 |
| `inst_stock_flow` | 個股三大法人買賣超（每股 3 筆：foreign / trust / dealer） |
| `industry_daily_flow` | 產業別每日法人資金流向（以 Fugle 大類彙整） |
| `broker_trade` | 分點買賣明細（stock_id + date + broker_id，on-demand 從 TWSE BSR 抓取快取） |

### 技術堆疊

- **DB**: SQLite
- **Backend**: FastAPI + SQLAlchemy（Python 3.9+）
- **ETL 資料來源**: FinMind API + TWSE 公開資料
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **產業分類**: Fugle 自定義供應鏈子產業（三層：大類 → chain → sub_industry）
- **AI 分析**: OpenAI GPT（gpt-4o-mini，Telegram `/ai` 指令觸發籌碼分析）
- **Bot**: Telegram Bot（long-polling）
- **部署**: Fly.io（API + Bot + 前端 + cron ETL + persistent volume 12GB）

---

## 快速開始

### 1. 安裝依賴

```bash
cd backend
pip install -r requirements.txt
```

### 2. 初始化資料庫

```bash
python init_db.py
```

### 3. 執行 ETL

```bash
# 跑今天
python run_daily_etl.py

# 指定日期
python run_daily_etl.py --date 2025-04-01

# 歷史 backfill（往前 N 天）
python run_daily_etl.py --backfill-days 30 --skip-master
```

### 3a. 歷史資料批次回補（可斷點續傳）

```bash
# 預設抓 2023-01-01 ~ 2026-04-01
python run_backfill.py

# 自訂區間
python run_backfill.py --start 2024-01-01 --end 2025-12-31

# 斷線後重跑會自動從上次成功的日期繼續
python run_backfill.py

# 強制重頭開始（忽略 checkpoint）
python run_backfill.py --reset
```

**特性：**
- 自動跳過週末（TWSE 無交易）
- 斷點續傳：checkpoint 存在 `db/backfill_checkpoint.txt`
- 連續 5 次錯誤自動停止，重跑即可續傳
- TWSE API rate limiting 防護（預設每筆間隔 3.5 秒）
- 支援 SIGINT/SIGTERM 優雅終止

### 3b. 設定每晚自動更新（macOS launchd）

```bash
# 安裝 launchd plist（週一至週五 20:00 自動執行）
cp backend/scripts/com.always-stock.daily-etl.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.always-stock.daily-etl.plist

# 手動測試
bash backend/scripts/daily_update.sh

# 停用
launchctl unload ~/Library/LaunchAgents/com.always-stock.daily-etl.plist
```

### 4. 啟動 API

```bash
python3 -m uvicorn app.main:app --reload
# Swagger UI: http://localhost:8000/docs
```

**Endpoints：**

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 健康檢查 |
| GET | `/api/industries?date=YYYY-MM-DD` | L0：產業排行榜（以 Fugle 大類彙總，含連續買賣超天數） |
| GET | `/api/industries/{industry_name}/summary?date=YYYY-MM-DD` | L1 彙總：子產業層級法人金額 + 連續買賣超天數 |
| GET | `/api/industries/{industry_name}/stocks?date=YYYY-MM-DD` | L1：指定產業個股明細（含漲跌幅、chain 分組） |
| GET | `/api/stocks/{stock_id}/history?days=90&end_date=YYYY-MM-DD` | L2：個股收盤價 + 法人累積買超（預設 90 天） |
| GET | `/api/realtime/quotes?stock_ids=2330,2317` | 即時盤中報價（TWSE mis API，最多 50 檔） |
| GET | `/api/stocks/{stock_id}/brokers?category=day_trade&date=YYYY-MM-DD&days=1` | L2：關鍵券商分點買賣超（on-demand 抓 TWSE BSR，DB 快取） |
| GET | `/api/stocks/{stock_id}/brokers/status?start=YYYY-MM-DD&end=YYYY-MM-DD` | 查詢分點資料快取狀況（已快取天數 / 總天數） |

### 5. 啟動 Telegram Bot

```bash
# 設定 Bot Token（從 @BotFather 取得）
export TELEGRAM_BOT_TOKEN="your-token-here"

# 啟動 bot（long-polling 模式，不需 webhook）
cd backend
python run_telegram_bot.py
```

**功能：**
- 輸入股票代號（如 `2330`）→ 回報最近一個交易日的三大法人買賣超
- 顯示所屬產業、子產業、供應鏈位置
- `/ai 2330` → AI 籌碼分析（GPT 解讀近期法人動向，需設定 `OPENAI_API_KEY`）
- `/start`、`/help` 查看使用說明

### 6. 啟動前端

```bash
cd frontend
npm install
npm run dev
# http://localhost:3000
```

### 7. 執行前端測試

```bash
cd frontend
npm test
```

### 本地完整啟動（不需雲端）

除了 Fly.io 雲端版本，你也可以完全在本機跑，使用本地 SQLite 資料庫：

```bash
# 終端機 1：啟動後端 API
cd backend
python3 -m uvicorn app.main:app --reload
# API: http://localhost:8000  |  Swagger: http://localhost:8000/docs

# 終端機 2：啟動前端
cd frontend
npm run dev
# 前端: http://localhost:3000
```

本地模式直接讀取 `backend/db/tw_stock.db`，不需要任何雲端服務。適合開發、除錯、以及在 backfill 完成前搶先查看資料。

---

## 已完成項目

- [x] **資料層**：SQLite schema、股票主檔、收盤價、三大法人、產業彙總、券商分點快取
- [x] **ETL 層**：`run_daily_etl.py`、`run_backfill.py`、統一 logging、週末跳過、斷點續傳、macOS/Fly.io 排程
- [x] **API 層**：L0/L1/L2 查詢、即時報價、券商分點、健康檢查
- [x] **前端主流程**：L0 產業排行榜、L1 子產業/個股列表、L2 K 線與法人累積走勢
- [x] **互動體驗**：日期狀態傳遞、搜尋/排序/filter、loading skeleton、RWD、dataZoom、lazy load
- [x] **即時與擴充功能**：TWSE 即時報價、Telegram Bot、OpenAI `/ai` 分析、BrokerPanel、MA 疊加、BacktestPanel skeleton
- [x] **部署與維運**：Fly.io API + Web + Bot、persistent volume、cron ETL
- [x] **測試**：backend / frontend 單元測試已建立並可本地執行

---

## 長期規劃

目前正式環境的主要風險來自：

- production 仍依賴 `Fly.io + SQLite volume`
- API / Bot / ETL 與單一檔案型 DB 綁定
- 大型 `.db` 檔搬移與備份成本高

因此長期方向已定為：

- Frontend：Vercel
- Backend API：Render Web Service
- Telegram Bot：Render Background Worker
- ETL / 排程：Render Cron Job
- Database：Postgres（Render Postgres 或 Neon）

規劃文件：

- [Target Architecture](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/architecture_target.md)
- [SQLite to Postgres Migration Plan](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/migration_plan_sqlite_to_postgres.md)
- [Deployment Strategy](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/deployment_strategy.md)
- [Operations Runbook](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/runbook_operations.md)
- [Data Migration Checklist](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/data_migration_checklist.md)
- [Security and Secrets](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/security_and_secrets.md)
- [Observability](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/observability.md)
- [Repo Restructure Plan](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/repo_restructure_plan.md)

建議閱讀順序：

1. 先看架構方向：
   [architecture_target.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/architecture_target.md)
2. 再看資料與部署遷移：
   [migration_plan_sqlite_to_postgres.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/migration_plan_sqlite_to_postgres.md)、
   [deployment_strategy.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/deployment_strategy.md)
3. 進入執行前 checklist：
   [data_migration_checklist.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/data_migration_checklist.md)
4. 補齊維運治理：
   [runbook_operations.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/runbook_operations.md)、
   [security_and_secrets.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/security_and_secrets.md)、
   [observability.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/observability.md)
5. 最後再整理 repo 與 infra：
   [repo_restructure_plan.md](/Users/brian.yh.chien/.gstack/projects/always-stock/docs/repo_restructure_plan.md)

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1 | ETL 完整跑通，資料入庫 | ✅ 完成 |
| M2 | FastAPI 回傳產業/個股 JSON | ✅ 完成 |
| M3 | Next.js L0 產業排行榜頁面 | ✅ 完成 |
| M4 | L1 個股列表 + L2 走勢圖 | ✅ 完成 |
| M5 | Telegram Bot 個股籌碼查詢 | ✅ 完成 |
| M6 | 8 年歷史股市資料庫 | 🔄 進行中（backfill 2019 ~ 2026，OHLC 已加入） |
| M7 | K 線圖（OHLC candlestick） | ✅ 完成 |
| M8 | 財報資料庫（含 PE / 基本面指標） | ⬜ 待開始 |
| M9 | AI Sub-agent（接 LLM，投資策略初版） | ✅ 完成 |
| M10 | 部署上線（Cloud） | ✅ 完成（Fly.io） |
| M11 | 回測程式（策略績效驗證） | ⬜ 待開始 |
| M12 | 文字化投資策略輸入 | ⬜ 待開始 |
| M13 | 關鍵券商分點爬蟲 | ✅ 完成 |
| M14 | LLM 輿情爬文分析 | ⬜ 待開始 |
| M15 | Telegram 電子報（定期投資推薦推播） | ⬜ 待開始 |

---

## TODO Roadmap

以下為整體開發路線圖，依序推進：

### Phase 1 — 資料基礎建設

- [x] **M1~M4** ETL + API + 前端儀表板（產業排行榜、個股列表、走勢圖）
- [x] **M5 Telegram Bot 個股籌碼查詢**：輸入股號 → 回報三大法人買賣超 + 所屬產業
- [ ] **M6 8 年歷史股市資料庫**：backfill 2019 ~ 2026（OHLC 欄位已加入 `daily_price`）
- [x] **M7 K 線圖**：前端 L2 股價改用 candlestick 呈現（紅漲綠跌，舊資料 fallback 折線圖）
- [ ] **M8 財報資料庫**：建立季度財報資料表（營收、EPS、PE ratio、本益比河流圖等）
  - 資料來源：公開資訊觀測站 / FinMind

### Phase 2 — AI 策略引擎

- [x] **M9 AI Sub-agent**：接 OpenAI GPT API，Telegram `/ai` 指令觸發籌碼分析
  - 接收：股號 → 查 DB 取近 5 日法人動向 + 股價
  - 輸出：GPT 生成的籌碼面觀察、法人解讀、短期多空看法
- [ ] **M11 回測程式**：指定策略 + 時間區間，計算績效指標（勝率、最大回撤、夏普比率等）
- [ ] **M12 文字化投資策略輸入**：用自然語言描述策略，LLM 解析為可執行的回測條件

### Phase 3 — 資訊聚合

- [x] **M13 關鍵券商分點爬蟲**：TWSE BSR on-demand 抓取，分類為當沖/隔日沖/短線/波段，快取進 `broker_trade` 表，L2 BrokerPanel 顯示 Top 10 分點
- [ ] **M14 LLM 輿情爬文分析**：自動爬取財經新聞 / PTT Stock / 社群，LLM 摘要與情緒分析

### Phase 4 — 部署 & 推播

- [x] **M10 部署上線**：Fly.io 雲端部署
  - 後端 API + Telegram Bot → `always-stock-api.fly.dev`
  - 前端 → `always-stock-web.fly.dev`
  - SQLite → Fly persistent volume（12 GB）
  - ETL 排程 → container 內 cron（19:00 + 21:30 台灣時間）
- [ ] **M15 Telegram 電子報**：定期推播投資推薦到 Telegram
  - 結合法人籌碼 + 財報 + AI 策略 + 輿情分析
  - 每日 / 每週摘要報告

---

## 資料來源

| 資料 | 來源 | 限制 |
|------|------|------|
| 股票基本資料 + 產業別 | FinMind `TaiwanStockInfo` | 免費 300 req/hr |
| 每日收盤價 | TWSE `STOCK_DAY_ALL` | 公開免費 |
| 三大法人買賣超 | TWSE `T86` | 公開免費 |
| 子產業分類 | Fugle（自定義爬取） | 本地 CSV |

## 部署架構（Fly.io）

| 服務 | App 名稱 | URL |
|------|---------|-----|
| 後端 API + Telegram Bot | `always-stock-api` | https://always-stock-api.fly.dev |
| 前端 | `always-stock-web` | https://always-stock-web.fly.dev |

### 部署指令

```bash
# 後端（含 API + Telegram Bot + cron ETL）
cd backend && fly deploy

# 前端
cd frontend && fly deploy
```

### DB 上傳 / 下載

```bash
# 上傳本地 DB 到雲端
fly proxy 10022:22 --app always-stock-api &
scp -P 10022 backend/db/tw_stock.db root@localhost:/data/tw_stock.db

# 下載雲端 DB（備份）
scp -P 10022 root@localhost:/data/tw_stock.db ./backup.db
```

### 日常維護

```bash
# 查看 app 狀態
fly status --app always-stock-api

# 即時 logs
fly logs --app always-stock-api

# 檢查 ETL 排程執行紀錄
fly ssh console --app always-stock-api -C "tail -20 /data/logs/etl_cron.log"

# 手動觸發 ETL（不等排程）
fly ssh console --app always-stock-api -C "cd /app && python run_daily_etl.py --skip-master"

# 管理 secrets
fly secrets list --app always-stock-api
fly secrets set OPENAI_API_KEY="new-key" --app always-stock-api

# SSH 進 container 除錯
fly ssh console --app always-stock-api
```

### 自動化機制

| 項目 | 說明 |
|------|------|
| ETL 排程 | cron：週一至週五 19:00 + 21:30（台灣時間） |
| SSL 憑證 | Fly.io 自動管理 |
| 閒置省錢 | `auto_stop_machines = "suspend"`，無流量時自動暫停 |
| DB 備份 | Fly volume 每日自動 snapshot（保留 5 份） |

### 每月費用估算（不含 AI API）

以 Fly.io 2026 年定價，目前設定（閒置自動暫停）的情況：

| 項目 | 規格 | 月費（USD） |
|------|------|------------|
| API machine | shared-cpu-1x, 512MB, 閒置暫停 | ~$1–2（依使用時數） |
| Web machine | shared-cpu-1x, 512MB, 閒置暫停 | ~$1–2（依使用時數） |
| Persistent volume | 12 GB SSD | ~$1.80 |
| Outbound bandwidth | 含 100 GB 免費 | $0 |
| **合計（暫停模式）** | | **~$3–6/月** |

若改為 24/7 不停機（`min_machines_running = 1`）：

| 項目 | 規格 | 月費（USD） |
|------|------|------------|
| API machine | shared-cpu-1x, 512MB, 24/7 | ~$3.19 |
| Web machine | shared-cpu-1x, 512MB, 24/7 | ~$3.19 |
| Persistent volume | 12 GB SSD | ~$1.80 |
| Outbound bandwidth | 含 100 GB 免費 | $0 |
| **合計（不停機模式）** | | **~$8.18/月** |

> Fly.io Hobby plan 含免費額度（3 shared VMs + 1 GB volume），實際帳單可能更低。

---

## Claude Code 協作

本專案使用 Claude Code 協助開發與維護。Claude 的 memory 系統已記錄完整的部署架構、URL、secrets、排程、維護指令等資訊。

你可以直接用自然語言請 Claude 幫忙，例如：
- 「幫我檢查 ETL 有沒有正常跑」
- 「重新部署後端」
- 「更新 OpenAI key」
- 「下載雲端 DB 備份」
- 「幫我看 Fly.io logs」

Claude 會自動從 memory 取得正確的 app 名稱、指令和上下文來執行。

---

## 參考資源

- [FinLab AI 回測筆記本](https://ai.finlab.tw/notebook/?uid=tBcYFAAsnvMS4Wuhv1NOuKouf5e2&sid=0ef97103-3998-439d-986a-806bfad785b3&name=%E6%9C%AA%E5%91%BD%E5%90%8D1) — M11 回測程式的目標參考，希望做到類似的互動式回測體驗
- ⚠️ **API Key 管理**：所有 API key（Telegram Bot Token、OpenAI API Key、FinMind Token）統一存放在 `.env` 檔案中，切換環境時記得重新設定
