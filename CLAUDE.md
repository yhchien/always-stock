# always-stock 專案記憶

## 部署相關文件

- `docs/architecture/architecture_overview.md` — 技術選擇、通訊方式、部署架構總覽
- `docs/operations/deployment_guide.md` — 日常部署操作手冊
- `infra/render/render.yaml.template` — Render blueprint 範本

## 專案概述

**always-stock**：台股產業別三大法人資金流向分析儀表板（原名 tw-stock-dashboard，2026-04 更名）。

## 技術堆疊
- **Backend**: FastAPI + SQLAlchemy, Python 3.9+
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **DB**: PostgreSQL（Render Managed）；本地開發可用 SQLite（`backend/db/tw_stock.db`）
- **ETL 資料來源**: 目前為 TWSE 公開資料 + FinMind + Fugle；目標方向已確定為「全面切 FinMind 為主資料源」，TWSE/TPEX 僅保留備援或校驗
- **Bot**: Telegram Bot（long-polling）+ OpenAI GPT 籌碼分析
- **排程**: macOS launchd（本地）/ Render Cron Job（雲端，週一至五）
- **部署**: Render（後端 API + Bot + ETL + Postgres）+ Vercel（前端）

## FinMind 決策記憶

- 2026-04-11 起，專案方向已確認為「全面改用 FinMind 提供的資料重做」
- FinMind API 以 `https://api.finmindtrade.com/api/v4` 為主，使用 `Authorization: Bearer {token}`
- 依使用者提供的最新規格，token rate limit 為 `600 req/hour`，未帶 token 為 `300 req/hour`
- 若要維持目前這種「每日全市場 ETL」模式，實務上應規劃 `Backer` 或 `Sponsor`
- 若要取代目前 `broker_trade` 的 TWSE BSR parser，應優先使用：
  - `TaiwanStockTradingDailyReportSecIdAgg`：適合現有 BrokerPanel 聚合場景
  - `TaiwanStockTradingDailyReport`：適合未來逐價分點分析
- `TaiwanStockTradingDailyReport` / `TaiwanStockTradingDailyReportSecIdAgg` 為 `Sponsor` 資料，且歷史起點為 `2021-06-30`
- 切換 FinMind 後，資料庫主幹表大多可保留，但需要 migration：
  - `stocks_master` 保留並加 `market/source`
  - `daily_price` 保留並加 `spread/trading_turnover/source`
  - `inst_stock_flow` 保留，改以 FinMind `name` 映射 `foreign/trust/dealer`
  - `industry_daily_flow` 可沿用
  - `broker_trade` 可沿用，但建議未來拆 raw / agg
- 切換 FinMind 後應新增：
  - `daily_valuation` <- `TaiwanStockPER`
  - `monthly_revenue` <- `TaiwanStockMonthRevenue`
  - `financial_statement_*` <- FinMind 基本面資料集
- 工程策略是「先 migration + backfill + 驗證，再淘汰 TWSE parser / ETL」，不要一開始就直接刪舊程式
- `broker_trade` 是用來存「某檔股票、某一天、各券商分點買賣超」的表，主要支撐 L2 個股頁的關鍵券商 / 分點面板
- 現行 `broker_trade` schema 為聚合後結果：`trade_date / stock_id / broker_id / broker_name / buy_shares / sell_shares / net_shares`
- 切到 FinMind 後，`broker_trade` 的資料來源應優先改為：
  - `TaiwanStockTradingDailyReportSecIdAgg`：最符合現有 BrokerPanel 聚合需求
  - `TaiwanStockTradingDailyReport`：若未來要做逐價分點分析再補
- `broker_trade` 這個表的概念可以保留，不一定要砍掉重建；但長期建議拆成：
  - `broker_trade_raw`
  - `broker_trade_daily_agg`
- schema 命名不應混用 TWSE / FinMind 原始欄位名；應採「內部 canonical naming」
- 原則：
  - ETL 層負責把 FinMind / TWSE 原始欄位映射成內部命名
  - DB schema、API schema、前端、回測引擎只使用專案自己的欄位名
- 例如：
  - FinMind `Trading_Volume` -> DB `volume`
  - FinMind `Trading_money` -> DB `turnover`
  - FinMind `max` -> DB `high_price`
  - FinMind `min` -> DB `low_price`
- 不要把外部資料源 naming 直接散落到全系統，避免未來 ETL 切換時 schema 混亂
- FinMind 已提供細產業分類資料集：
  - `TaiwanStockIndustryChain`
  - 欄位：`stock_id / industry / sub_industry / date`
  - 權限：`Backer/Sponsor`
- 但 FinMind 的細產業分類目前不應直接無條件取代既有 Fugle mapping
- 正確做法：
  - 先把 `TaiwanStockIndustryChain` 拉下來
  - 和現有 Fugle mapping 對照
  - 再決定最終採用 FinMind、Fugle，或保留 `industry_source` 雙軌策略

## Milestones 進度

### 已完成（截至 2026-04-08）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M9: AI 籌碼分析（`/ai` 指令，接 OpenAI GPT）
- M10: 雲端部署（Render + Vercel）

### 進行中
- M6: 8 年歷史資料 backfill（2019~2026），OHLC 欄位已加入 daily_price
- L2 頁面進階功能（2026-04-09 起）：
  - 均線疊加（MA10/MA20/MA60 + 自定義，可切換）
  - K 線圖響應式放大
  - 回測策略框架（下半部左，UI skeleton 先行）
  - 關注券商買賣長條圖（下半部右，UI skeleton 先行）

### 待開始
- M8 財報
- M11 回測（L2 回測框架 UI 已先搭好）
- M12 自然語言策略
- M13 券商分點（L2 券商長條圖 UI 已先搭好）
- M14 輿情分析
- M15 Telegram 電子報

## 開發注意事項
- 優先考慮資料正確性與 TWSE API rate limiting
- 前端以深色主題為主
- Brian 的個人專案，目標是從法人籌碼面輔助台股交易決策

## 最近重要修正（2026-04-09）

- L2 頁面 `StockChart` 與 `BrokerPanel` 必須共用同一個 `date` query param，避免同頁不同日期資料混用
- L0 / L1 前端預設日期必須用 `Asia/Taipei`，不可用 `toISOString().slice(0, 10)`，否則台灣凌晨會落到前一天
- 即時報價 API `/api/realtime/quotes` 單次上限 50 檔；前端若要查整個產業，必須自動分 batch，不能假設所有股票可一次取回
- `industry_daily_flow` 仍是 L0 主查詢來源；不要把產業聚合搬回 API 臨時計算或前端計算
- L2 個股頁的「回測程式」與「關鍵券商」已拆成兩個獨立 toggle，且會記住使用者上次的顯示偏好；被隱藏的 panel 不應 render，也不應觸發後續 API

## 資料狀態（2026-04-10）

- 本地 backfill 已重新補跑大部分歷史缺口
- `inst_stock_flow` / `industry_daily_flow` 仍缺 3 天：`2019-04-04`、`2023-04-03`、`2026-02-18`
- 上述 3 天重抓時，TWSE `MI_INDEX` 回傳「沒有符合條件的資料」，暫列為資料源特殊日
- `daily_price` 仍有 5 天 `OHLC` 缺漏：`2023-05-05`、`2023-09-19`、`2024-01-17`、`2024-02-29`、`2024-07-11`
- 這 5 天已重抓一次，`close_price` 與後續 flow 可更新，但 `open/high/low` 仍為空，推測是資料源回傳本身缺欄位
- 已從 Fly.io 遷移至 Render（Postgres）+ Vercel（前端）
- Fly.io 資源已停用，可待驗證完成後刪除

## 最近重要修正（2026-04-12）

- L3 回測 MVP 第一批已落地：
  - 後端新增 `/api/backtest/templates`
  - 後端新增 `/api/backtest/interpret`
  - 後端新增 `/api/backtest/run`
  - 後端新增 `/api/backtest/advice`
- 回測引擎目前範圍固定為：
  - 單一股票 / ETF
  - 日線資料
  - long-only
  - 訊號以當日收盤判斷、次日開盤成交
  - 同時間單一部位
  - 成本模型固定為 `0`
- 第一批 parser / DSL 僅保證支援：
  - `收盤價站上 N 日均線`
  - `收盤價跌破 N 日均線`
  - `成交量高於 N 日均量`
  - `外資 / 投信 / 自營商 連買 N 天`
  - `外資 / 投信 / 自營商 轉賣 / 賣超`
- 回測標準輸出目前已包含：
  - `total_return_pct`
  - `annual_return_pct`
  - `win_rate_pct`
  - `max_drawdown_pct`
  - `sharpe_ratio`
  - `trade_count`
  - `ending_equity`
  - `benchmark_return_pct`
  - `excess_return_pct`
  - `avg_trade_return_pct`
  - `avg_holding_days`
  - `profit_factor`
  - `avg_gain_pct`
  - `avg_loss_pct`
- 前端 `BacktestPanel` 已從假資料改成真 API 串接，並支援：
  - 策略模板載入
  - 策略文字手動編輯
  - `interpret -> preview -> run -> advice` 流程
  - 顯示 quick metrics
  - 顯示正式 equity curve chart
  - 顯示最近交易紀錄
  - 顯示最新交易日建議
  - 顯示策略建議卡片
  - 顯示 warnings / validation error
  - 顯示 `unsupported_conditions`
  - 顯示更細的 422 中文錯誤訊息
  - 從交易紀錄 / 最新訊號跳回 L2 研究頁
- 邊界處理已補上：
  - 空白策略文字
  - 開始日大於結束日
  - 部分不支援條件的 `interpret` 回應
  - `run` 對不支援條件的拒絕執行
  - lookback 不足 warnings
  - 開盤價缺失 fallback warnings
- UX 已補上：
  - strategy preview loading state
  - advice loading skeleton
  - partial-support preview
- 已完成（2026-04-12 全部完工，對齊 docs/plans/l3_manual_strategy_backtest_spec.md）：
  - DSL 條件：停損停利、均線交叉、突破高低點、volume_ratio（倍數量能）
  - 三大法人完整支援：net_positive / net_negative、consecutive_buy / consecutive_sell、all_inst_net
  - 完整 summary / period analysis（月/季/年度報酬）
  - AI mapping 流程（backtest_ai_mapping.py 接入 interpret，回傳 ai_mapped_conditions）
  - 前端顯示 AI 補充解析來源標記（天藍色提示區塊）
  - strategy templates 7 個，對齊 spec 15.1.1：4 個核心 + 3 個延伸
  - 使用範例文件：docs/guides/backtest_strategy_examples.md

## 回測引擎設計規範（2026-04-12 整理）

### normalized_text 生成方式
- 必須從解析後的 `entry_rules`/`exit_rules` AST 重建，不可用 naive `str.replace()` 修改原文
- 由 `backtest_parser._rule_to_text(rule)` 負責 rule → 可讀文字的映射
- 停損/停利附加在 exit 段尾端（不可混入 entry 段）

### 語義正確性
- `profit_factor`：無虧損交易時應回傳 `None`（不是 `0.0`）
- `avg_gain_pct`：無獲利交易時應回傳 `None`
- `avg_loss_pct`：無虧損交易時應回傳 `None`
- 前端顯示 `null` 時用 `—` 代替，不可直接 `toFixed()`

### Sharpe Ratio 年化係數
- 使用 `_TRADING_DAYS_PER_YEAR = 252`（美股慣例，與 Zipline/Backtrader 對齊）
- 台股實際約 245 天，但改動會影響可比性，暫不修改

### 前端預設值
- `startDate` 預設為台北時區一年前，使用 `Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" })`
- 不可用 `new Date().toISOString()` 或寫死日期字串
- 策略文字預設值由後端 `/api/backtest/templates` 第一筆決定，前端不另存常數

## FinMind SDK ETL 集成（2026-04-13 完成）

### 核心改進
- **架構升級**：從 REST API per-stock/per-day（400K+ calls）→ FinMind SDK async batch（50-100 calls）
- **防斷線機制**：HTTP 402（Payment Required）檢測 + 配額預警 + SDK 自動重試
- **雙軌並行**：所有 DB 寫入加上 `source` 欄位（twse | finmind），支持驗證期間並行跑舊新系統

### 已完成的程式碼（2026-04-13）

#### 第一層：SDK 客戶端
- **`etl/finmind_sdk_client.py`** (400 行)
  - FinMind Python SDK 封裝層
  - `__init__()` 自動初始化 + token 驗證
  - `_refresh_quota()` 配額管理（HTTP 402 詳細解析）
  - `can_proceed()` gate-keeper（防超額）
  - 5 大 batch 查詢：
    - `fetch_taiwan_stock_price(stock_id_list, start_date, end_date, use_async=True)`
    - `fetch_institutional_investors(...)`
    - `fetch_per(...)` → P/E, P/B, dividend_yield
    - `fetch_month_revenue(...)` → 月營收 + YoY/MoM
    - `fetch_financial_statements(...)` → 財報（placeholder）
  - 所有方法均返回 pandas DataFrame

#### 第二層：ETL 模組（均支持 batch 處理 + upsert）
1. **`etl/finmind_daily_price_sdk.py`** (170 行)
   - 日股價批量 ETL
   - 欄位映射：FinMind `open/high/low/close/volume/money` → DB `open_price/high_price/low_price/close_price/volume/turnover`
   - Upsert 邏輯：merge by (trade_date, stock_id)
   - 批量提交：每 500 筆一次

2. **`etl/finmind_inst_flow_sdk.py`** (220 行)
   - 三大法人買賣超批量 ETL
   - 正規化：FinMind `name` ("外資"/"投信"/"自營商") → DB `inst_type` ("foreign"/"trust"/"dealer")
   - 含預快取機制：批次開始前一次性載入收盤價，用於估算買賣金額
   - 處理：每檔股票、每天、3 筆法人資料的 upsert

3. **`etl/finmind_daily_valuation_sdk.py`** (170 行)
   - P/E、P/B、殖利率批量 ETL
   - 新表：`daily_valuation` (trade_date, stock_id, per, pbr, dividend_yield, source, ingested_at)
   - Upsert merge by (trade_date, stock_id)

4. **`etl/finmind_fundamentals_sdk.py`** (150 行)
   - 月營收 + 財報預留位置
   - `fetch_and_upsert_monthly_revenue_finmind_sdk()` 實作約 70%
   - 財報部分標記待補（SDK 可能不直接支援，考慮 REST 補充或延後）

#### 第三層：協調與監控
- **`run_finmind_etl_sdk.py`** (280 行)
  - 統一協調器，管理所有 SDK ETL 模組
  - 支援執行模式：
    - 單日：`python run_finmind_etl_sdk.py --date 2026-04-13`
    - 日期區間：`python run_finmind_etl_sdk.py --start-date 2026-01-01 --end-date 2026-04-13`
    - 預設：前一天
  - 流程：配額檢查 → 日股價 → 三大法人 → 估值 → 狀態彙總
  - 自動保存 JSON 日誌到 `backend/logs/`

- **`test_finmind_sdk_integration.py`** (350 行)
  - 5 階段集成測試：
    1. SDK 初始化 & 認證
    2. 配額查詢驗證
    3. Batch fetch 效能測試（3 種規模：small/medium/large）
    4. 三大法人 batch 查詢驗證
    5. HTTP 402 處理驗證（safeguard）
  - 用法：`export FINMIND_TOKEN=xxx && python test_finmind_sdk_integration.py --config test_small`

- **`.env.finmind.template`**
  - 環境變數範本，包含所有可配置參數說明

#### 支持文件
- **`etl/etl_healthcheck.py`** (400 行，已存在，此版本相容)
- **`migrate_finmind_phase1.py`** (300 行，已存在，此版本相容)
- **`etl/finmind_utils.py`** (300 行，已存在，此版本相容)

### 關鍵設計決策

| 決策項 | 選項 | 最終選擇 | 理由 |
|-------|------|---------|------|
| API 方式 | REST vs SDK | **SDK async batch** | API 呼叫數 -99%、內部自動並行 |
| Batch 大小 | 固定 vs 動態 | **動態（由 SDK 決定）** | SDK 支援自動分塊、無需自己管理 |
| 配額耗盡處理 | 阻止 vs 警告 | **HTTP 402 gate + 預警** | 防突然斷線、給使用者反應時間 |
| 雙軌策略 | 舊新完全隔離 vs 共用源標記 | **source 欄位標記** | 無痛驗證、易於比對、方便 rollback |
| 財報資料 | 立即實作 vs 延後 | **延後（M8 再細化）** | SDK 支援度不確定，先做主流用途 |

### 防斷線機制詳解

```python
# 三層檢測
def run_etl_range():
    # 層 1：執行前預檢
    quota = client.get_quota()
    if quota["status"] == "critical":
        return {"status": "insufficient_quota"}  # 直接撤退
    
    # 層 2：執行中自動重試（SDK 內建）
    df = client.fetch_taiwan_stock_price(..., use_async=True)
    # SDK 內部已處理 HTTP 402、429、timeout
    
    # 層 3：執行後驗證
    for _, row in df.iterrows():
        try:
            upsert_to_db(row)
        except Exception as e:
            failed_stocks.append(row['stock_id'])  # 記錄、續行（fail-safe）
```

### 配額消耗estimate

| 場景 | 舊方法（REST per-stock/day） | 新方法（SDK batch） | 節省 |
|------|---------------------------|------------------|-----|
| 單日全市場 | 1,600 × 1 = 1,600 | ~1 | **-99.9%** |
| 一月 backfill | 1,600 × 22 = 35,200 | ~2-3 | **-99%** |
| 一年 backfill | 1,600 × 245 = 392,000 | ~5-10 | **-99.9%** |

### 待辦事項（優先序）

#### 立即（用前確認）
- ⏳ 提供 FinMind token，執行 `test_finmind_sdk_integration.py --config test_small`
- ⏳ 確認 SDK 架構是否符合預期
- ⏳ 決定財報部分：REST 補充 或 延後到 M8

#### 第一階段（backfill + 驗證）
- ⬜ 執行 `migrate_finmind_phase1.py` 初始化 DB schema
- ⬜ 設定 backfill 計畫：分月執行或一次性？
- ⬜ 跑 72 小時並行驗證（舊 TWSE ETL + 新 FinMind ETL）
- ⬜ 比對關鍵指標（股價、成交量、法人持股）

#### 第二階段（切換）
- ⬜ 配額足夠 → 切換為 FinMind 為主
- ⬜ TWSE ETL 改為 fallback / 校驗用途
- ⬜ 舊資料保留（source='twse'），新資料標記 source='finmind'

#### M8-M13 相依
- M8 財報：須決定 financial_statements 取用方式
- M13 券商分點：需要 TaiwanStockTradingDailyReport（非 Agg），目前 FinMind 限 Sponsor
