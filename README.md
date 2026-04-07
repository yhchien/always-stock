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

### 5. 啟動前端

```bash
cd frontend
npm install
npm run dev
# http://localhost:3000
```

### 6. 執行前端測試

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

---

## Milestones

| # | 目標 | 狀態 |
|---|------|------|
| M1 | ETL 完整跑通，資料入庫 | ✅ 完成 |
| M2 | FastAPI 回傳產業/個股 JSON | ✅ 完成 |
| M3 | Next.js L0 產業排行榜頁面 | ✅ 完成 |
| M4 | L1 個股列表 + L2 走勢圖 | ✅ 完成 |

---

## TODO

### 核心功能

- [x] **M2** FastAPI routers（產業排行榜 API、個股法人明細 API、個股走勢 API）
- [x] **M3** 前端 L0：產業排行榜（日期選擇 + foreign/trust/dealer tab）
- [x] **M4** 前端 L1：sub_industry 個股列表（可依法人欄排序）
- [x] **M4** 前端 L2：個股雙軸走勢圖（收盤價 + 三大法人累積淨買超，60 天）

### 資料更新

- [ ] **每晚自動更新資料庫**（cron job 或 launchd，收盤後約 20:00 觸發 `run_daily_etl.py`）
- [ ] **即時股價**（串接 TWSE 盤中報價 API 或 WebSocket）

### 擴充功能

- [ ] **Telegram 機器人**：輸入股號 → 回報昨日法人買賣情況 + 所屬產業
  - 找不到股號 → 回「沒有此股」
  - 找到 → 列出 foreign/trust/dealer 淨買超股數與金額估計
- [ ] **交易策略模組**：每個策略寫成獨立 Python 腳本，統一介面，便於回測與組合
- [ ] **回測程式**：指定策略 + 時間區間，計算績效指標（勝率、最大回撤、夏普比率等）
- [ ] **策略追蹤 threads**：整合社群或筆記，記錄各策略的觀察盤點
- [ ] **AI 策略助手**：接 ChatGPT API，根據使用者目前關注的策略，針對特定股票提供分析想法
  - 接收：股號 + 使用者策略偏好
  - 輸出：結合近期法人動向、技術面的 AI 觀點
- [ ] **部署上線**：從 localhost 轉為公開網站（Fly.io / Render / VPS）

---

## 資料來源

| 資料 | 來源 | 限制 |
|------|------|------|
| 股票基本資料 + 產業別 | FinMind `TaiwanStockInfo` | 免費 300 req/hr |
| 每日收盤價 | TWSE `STOCK_DAY_ALL` | 公開免費 |
| 三大法人買賣超 | TWSE `T86` | 公開免費 |
| 子產業分類 | Fugle（自定義爬取） | 本地 CSV |
