# tw-stock-dashboard

台股產業別三大法人資金流向分析儀表板。

## 專案目的

追蹤 TWSE 上市股票的三大法人（外資、投信、自營商）每日買賣超，
以 Fugle 自定義子產業分類為基礎，呈現產業層級的資金流向排行榜，
並支援 drill-down 到個股法人明細與走勢圖，協助制定交易策略。

---

## 專案架構

```
tw-stock-dashboard/
├── backend/
│   ├── app/
│   │   ├── database.py          # SQLAlchemy engine / session
│   │   ├── models.py            # ORM 資料表定義（4 張表）
│   │   └── routers/             # FastAPI routers（industries / stocks）
│   ├── etl/
│   │   ├── fetch_stock_master.py    # FinMind 股票基本資料 + Fugle 子產業 mapping
│   │   ├── fetch_daily_price.py     # TWSE STOCK_DAY_ALL 收盤價
│   │   ├── fetch_inst_flow.py       # TWSE T86 三大法人買賣超
│   │   └── aggregate_industry_flow.py  # 彙整到產業日流向表
│   ├── tests/                   # 每個 ETL 模組的單元測試
│   ├── db/
│   │   └── tw_stock.db          # SQLite 資料庫
│   ├── logs/                    # ETL 執行 log（滾動保留 7 天）
│   ├── logging_config.py        # 統一 logging 設定
│   ├── init_db.py               # 初始化資料表
│   ├── run_daily_etl.py         # 每日 ETL 主程式（CLI）
│   ├── run_backfill.py          # 歷史 backfill（可斷點續傳）
│   ├── scripts/
│   │   ├── daily_update.sh      # 每日自動更新 shell script
│   │   └── com.tw-stock-dashboard.daily-etl.plist  # macOS launchd 排程
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
│   │   │   └── StockChart.tsx         # L2 雙軸走勢圖（ECharts）
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

### 資料表

| 資料表 | 說明 |
|--------|------|
| `stocks_master` | 股票基本資料，含 industry / chain / sub_industry |
| `daily_price` | 每日收盤價、成交量、成交金額 |
| `inst_stock_flow` | 個股三大法人買賣超（每股 3 筆：foreign / trust / dealer） |
| `industry_daily_flow` | 產業別每日法人資金流向（以 Fugle 大類彙整） |

### 技術堆疊

- **DB**: SQLite
- **Backend**: FastAPI + SQLAlchemy（Python 3.9+）
- **ETL 資料來源**: FinMind API + TWSE 公開資料
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **產業分類**: Fugle 自定義供應鏈子產業（三層：大類 → chain → sub_industry）

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
cp backend/scripts/com.tw-stock-dashboard.daily-etl.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tw-stock-dashboard.daily-etl.plist

# 手動測試
bash backend/scripts/daily_update.sh

# 停用
launchctl unload ~/Library/LaunchAgents/com.tw-stock-dashboard.daily-etl.plist
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

---

## 已完成項目

- [x] DB schema（4 張資料表）
- [x] ETL：股票基本資料（FinMind + Fugle 子產業 mapping）
- [x] ETL：每日收盤價（TWSE STOCK_DAY_ALL）
- [x] ETL：三大法人買賣超（TWSE T86）
- [x] ETL：產業流向彙整
- [x] ETL 主程式（CLI，支援 backfill）
- [x] 統一 logging（console + file，所有模組皆輸出至 `logs/etl.log`）
- [x] 單元測試（70+ tests）
- [x] FastAPI routers（L0 產業排行榜、L1 個股明細、L2 個股走勢）
- [x] 程式碼與 comment 統一使用英文
- [x] Next.js L0 產業排行榜頁面（以 Fugle 大類彙總 + 外資/投信/自營商/合計 tab）
- [x] Next.js L1 個股卡片頁面（依 chain 上中下游分組，卡片顯示股價漲跌 + 三大法人買賣）
- [x] Next.js L2 個股走勢圖（ECharts 雙軸：收盤價 + 三大法人累積淨買超，90 天）
- [x] 三層 drill-down 日期正確傳遞（L0 → L1 → L2）
- [x] L0 欄位排序（外資/投信/自營商/合計）+ 趨勢欄（連續買賣超天數）
- [x] L1 子產業彙總表格（排序 + 趨勢 + 子產業 filter）
- [x] 前端單元測試（51 tests：api helpers + IndustryDashboard + StockList + StockChart）
- [x] 漲跌幅計算改用 per-stock prev close（停牌股也能正確顯示漲跌）
- [x] 深色主題調亮：提升底色亮度、filter/input/tab/badge 對比度改善可見性
- [x] ETL 加入週末偵測：跳過 Saturday/Sunday，防止 TWSE API 回傳重複前日資料
- [x] 可斷點續傳的歷史 backfill 腳本（`run_backfill.py`，支援 2023-01-01 ~ 2026-04-01）
- [x] 每晚自動更新：launchd plist（週一至週五 20:00 觸發 `run_daily_etl.py`）
- [x] 即時盤中報價 API（`/api/realtime/quotes`，串接 TWSE mis API）
- [x] L1 卡片 + L2 走勢圖整合即時報價（15 秒自動刷新，盤中顯示「即時」標記）
- [x] L2 走勢圖支援時間軸拖拉縮放（ECharts dataZoom）
- [x] L2 tooltip 顯示單位（收盤價 元、累積張數 萬股）
- [x] 返回上一頁保留日期 / 子產業篩選（URL search params 狀態同步）
- [x] Telegram Bot：輸入股票代號查詢三大法人買賣超（long-polling 模式）
- [x] AI 籌碼分析：`/ai` 指令接 OpenAI GPT，根據近期法人動向提供投資觀點

---

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1 | ETL 完整跑通，資料入庫 | ✅ 完成 |
| M2 | FastAPI 回傳產業/個股 JSON | ✅ 完成 |
| M3 | Next.js L0 產業排行榜頁面 | ✅ 完成 |
| M4 | L1 個股列表 + L2 走勢圖 | ✅ 完成 |
| M5 | Telegram Bot 個股籌碼查詢 | ✅ 完成 |
| M6 | 10 年歷史股市資料庫 | 🔄 進行中（backfill 2016 ~ 2026） |
| M7 | K 線圖（OHLC candlestick） | ⬜ 待開始 |
| M8 | 財報資料庫（含 PE / 基本面指標） | ⬜ 待開始 |
| M9 | AI Sub-agent（接 LLM，投資策略初版） | ✅ 完成 |
| M10 | 部署上線（Cloud） | ⬜ 待開始 |
| M11 | 回測程式（策略績效驗證） | ⬜ 待開始 |
| M12 | 文字化投資策略輸入 | ⬜ 待開始 |
| M13 | 關鍵券商分點爬蟲 | ⬜ 待開始 |
| M14 | LLM 輿情爬文分析 | ⬜ 待開始 |
| M15 | Telegram 電子報（定期投資推薦推播） | ⬜ 待開始 |

---

## TODO Roadmap

以下為整體開發路線圖，依序推進：

### Phase 1 — 資料基礎建設

- [x] **M1~M4** ETL + API + 前端儀表板（產業排行榜、個股列表、走勢圖）
- [x] **M5 Telegram Bot 個股籌碼查詢**：輸入股號 → 回報三大法人買賣超 + 所屬產業
- [ ] **M6 10 年歷史股市資料庫**：將 backfill 區間從 4 年擴充至 2016 ~ 2026（約 10 年）
  - 擴充 OHLC 欄位（open/high/low）到 `daily_price`
- [ ] **M7 K 線圖**：前端 L2 股價改用 candlestick 呈現（需 M6 OHLC 資料）
- [ ] **M8 財報資料庫**：建立季度財報資料表（營收、EPS、PE ratio、本益比河流圖等）
  - 資料來源：公開資訊觀測站 / FinMind

### Phase 2 — AI 策略引擎

- [x] **M9 AI Sub-agent**：接 OpenAI GPT API，Telegram `/ai` 指令觸發籌碼分析
  - 接收：股號 → 查 DB 取近 5 日法人動向 + 股價
  - 輸出：GPT 生成的籌碼面觀察、法人解讀、短期多空看法
- [ ] **M11 回測程式**：指定策略 + 時間區間，計算績效指標（勝率、最大回撤、夏普比率等）
- [ ] **M12 文字化投資策略輸入**：用自然語言描述策略，LLM 解析為可執行的回測條件

### Phase 3 — 資訊聚合

- [ ] **M13 關鍵券商分點爬蟲**：追蹤特定券商分點的進出（如外資常用券商）
- [ ] **M14 LLM 輿情爬文分析**：自動爬取財經新聞 / PTT Stock / 社群，LLM 摘要與情緒分析

### Phase 4 — 部署 & 推播

- [ ] **M10 部署上線**：從 localhost 轉為 cloud 部署（Fly.io / Render / VPS）
  - 後端 API + Telegram Bot 一起部署
  - launchd 改為 cron job 或平台內建排程
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
