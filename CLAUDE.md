# always-stock 專案記憶

## 工作流程規範

- 每次完成一輪修改後，**自動更新 README、CLAUDE.md、memory 並直接 commit & push**，不需要使用者每次重新提醒

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

### 已完成（截至 2026-04-17）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M8: 財報面板（估值 PER/PBR/殖利率、月營收+YoY、季財報 EPS 等）— API + 前端完成
- M9: AI 籌碼分析（`/ai` 指令，接 OpenAI GPT）
- M10: 雲端部署（Render + Vercel）
- M11: 回測程式（含 DSL + AI mapping + equity curve + 策略建議）

### 進行中
- M6: 8 年歷史資料 backfill（2019~2026），OHLC 欄位已加入 daily_price
- L2 頁面進階功能（2026-04-09 起）：
  - 均線疊加（MA10/MA20/MA60 + 自定義，可切換）
  - K 線圖響應式放大
  - 回測策略框架（下半部左，UI skeleton 先行）
  - 關注券商買賣長條圖（下半部右，UI skeleton 先行）

### 待開始
- M12 自然語言策略
- M13 券商分點（ETL 模組已完成，`broker_trade_agg` 表已支援；L2 券商長條圖 UI 已搭好；GitHub Actions 每小時自動 backfill）
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

## 最近重要修正（2026-04-17）

- **環境定位確認（重要）**
  - 本機 `localhost:8000` 當下執行中的 backend 進程環境變數 `DATABASE_URL` 指向 Render PostgreSQL（非本地 SQLite）。
  - 本地 `backend/db/tw_stock.db` 的 `daily_valuation` 目前是 0 筆；本地與雲端資料差異需先確認連線目標。

- **L1 產業名稱 fallback（TWSE ↔ stocks_master）**
  - 修正 API：`/api/industries/{industry}/stocks`、`/api/industries/{industry}/summary`。
  - 新增 fallback 對照（例）：`水泥工業→水泥`、`鋼鐵工業→鋼鐵`、`食品工業→食品`、`金融科技→金融`、`數位雲端→雲端運算`。
  - 另外補 generic fallback：`工業` 後綴與 `業` 後綴剝離。
  - Render 對帳（2026-04-08）實際不匹配為 5 個：`太空衛星科技`、`數位雲端`、`金融科技`、`鋼鐵工業`、`食品工業`（`水泥工業`不在該日 L0 清單）。
  - `太空衛星科技` 目前仍無安全對應，不做硬映射避免誤導。

- **L3 回測頁視覺修正**
  - 修正回測頁右側 `BacktestPanel` 高度策略：`h-full` 改為 `min-h-full`，避免內容展開時底色不延伸造成「破圖感」。
  - 回測頁容器補 `min-h-0` 與 pane 背景，確保雙欄滾動與背景覆蓋一致。

- **L2 財報顯示修正**
  - 估值圖：`PER <= 0` 視為 N/A（顯示 `null` 不畫線），避免誤讀為有效 0 值。
  - 月營收圖：當 `yoy_pct` 無資料時，不顯示 YoY 線與圖例，並提示「目前僅顯示月營收」。
  - 原因確認：Render `monthly_revenue` 目前 `COUNT(yoy_pct)=0`、`COUNT(mom_pct)=0`。

- **monthly_revenue ETL 根因與修補**
  - 根因：`etl/finmind_monthly_revenue_sdk.py` 先前僅讀特定欄位名（`revenue_year_difference_per` / `revenue_month_difference_per`），遇到 SDK 欄位名差異時全部寫成 `NULL`。
  - 已修：支援多欄位名 fallback，並在資料源未提供 YoY/MoM 時以營收序列回算（同股月序列計算 YoY/MoM）。
  - 另修：`revenue_month` 可能是整數月份（1~12），需搭配 `revenue_year` 轉月末日期。
  - 已新增測試：`backend/tests/test_finmind_monthly_revenue_sdk.py`。
  - 當日回補嘗試結果：FinMind 配額超限（`6352/6000`），ETL 回傳 `INSUFFICIENT_QUOTA`，DB 尚未補回 YoY/MoM（仍為 0 筆非空）。

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
- **Bulk upsert**：所有 ETL 模組改用 `INSERT ON CONFLICT DO UPDATE`，batch size 1000，速度提升 450x（row-by-row ~4 rec/sec → bulk ~1800 rec/sec）
- **防斷線機制**：HTTP 402（Payment Required）檢測 + 配額預警 + SDK 自動重試
- **雙軌並行**：所有 DB 寫入加上 `source` 欄位（twse | finmind），支持驗證期間並行跑舊新系統

### 已完成的程式碼（2026-04-13）

#### 第一層：SDK 客戶端
- **`etl/finmind_sdk_client.py`**
  - FinMind Python SDK 封裝層
  - `__init__()` 自動初始化 + token 驗證
  - `_refresh_quota()` 配額管理（HTTP 402 詳細解析）
  - `can_proceed()` gate-keeper（防超額）
  - 7 大 batch 查詢（均返回 pandas DataFrame）：
    - `fetch_taiwan_stock_price(...)` → 日股價
    - `fetch_institutional_investors(...)` → 三大法人
    - `fetch_per(...)` → P/E, P/B, dividend_yield
    - `fetch_month_revenue(...)` → 月營收 + YoY/MoM
    - `fetch_financial_statements(...)` → 財報
    - `fetch_broker_trade_agg(...)` → 券商分點聚合（Sponsor）

#### 第二層：ETL 模組（均支持 batch 處理 + bulk upsert）

1. **`etl/finmind_daily_price_sdk.py`**
   - 欄位映射（注意：FinMind 實際回傳欄位名）：
     - `max` → `high_price`、`min` → `low_price`
     - `Trading_Volume` → `volume`、`Trading_money` → `turnover`
   - Upsert：`ON CONFLICT (trade_date, stock_id) DO UPDATE`

2. **`etl/finmind_inst_flow_sdk.py`**
   - FinMind 回傳 5 種法人類型 → 需先 `groupby().agg()` 合併後再 upsert（否則 unique constraint 違反）
   - 映射（在 `finmind_utils.py` 的 `FINMIND_INST_TYPES_MAPPING`）：
     - `Foreign_Investor` + `Foreign_Dealer_Self` → `foreign`
     - `Investment_Trust` → `trust`
     - `Dealer_self` + `Dealer_Hedging` → `dealer`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_flow_date_stock_inst DO UPDATE`

3. **`etl/finmind_daily_valuation_sdk.py`**
   - 新表 `daily_valuation`：`(trade_date, stock_id, per, pbr, dividend_yield, source, ingested_at)`
   - Upsert：`ON CONFLICT (trade_date, stock_id) DO UPDATE`

4. **`etl/finmind_monthly_revenue_sdk.py`**
  - 月營收：`revenue_year`/`revenue_month` 欄位轉換為月末日期（用 `calendar.monthrange()`）
  - YoY/MoM：優先吃 FinMind 回傳欄位（多欄位名 fallback），若資料源無提供則以同股營收序列回算
  - Upsert：`ON CONFLICT ON CONSTRAINT uq_revenue_month_stock DO UPDATE`

5. **`etl/finmind_financial_statement_sdk.py`**
   - 財報：`origin_name` → `item_name`、`type` → `item_code`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_finstatement_date_stock_item DO UPDATE`

6. **`etl/finmind_broker_trade_sdk.py`**
   - 券商分點聚合（Sponsor，資料起點 2021-06-30）
   - `start_date < 2021-06-30` 時自動調整；`start_date > end_date` 後跳過（回傳 `status: skipped`）
   - 映射：`securities_trader_id` → `broker_id`、`securities_trader` → `broker_name`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_broker_agg_date_stock_broker DO UPDATE`

#### 第三層：協調與監控
- **`run_finmind_etl_sdk.py`**
  - 6 步協調流程：
    - [1/6] daily_price
    - [2/6] inst_flow
    - [3/6] daily_valuation
    - [4/6] monthly_revenue
    - [5/6] financial_statement
    - [6/6] broker_trade_agg（2019/2020 自動跳過）
  - 支援：單日模式 `--date`、區間模式 `--start-date/--end-date`、預設昨天

- **`scripts/backfill_finmind.sh`**
  - 以年為單位逐年 backfill（2019 → 2026）
  - `START_YEAR=YYYY bash scripts/backfill_finmind.sh` 可從斷點繼續
  - checkpoint 記錄至 `backend/logs/backfill_checkpoint.txt`
  - 每年日誌寫入 `backend/logs/backfill_YYYY.log`

- **`test_finmind_sdk_integration.py`**
  - 5 階段集成測試，用法：`python test_finmind_sdk_integration.py --config test_small`
  - 注意：`hasattr(client, "api")` 是正確檢測（SDK 使用 `self.api`，不是 `self.client`）

### FinMind 欄位對照（重要 gotcha）

| FinMind 原始欄位 | DB 欄位 | 說明 |
|----------------|---------|------|
| `max` | `high_price` | 不是 `high` |
| `min` | `low_price` | 不是 `low` |
| `Trading_Volume` | `volume` | 不是 `volume` |
| `Trading_money` | `turnover` | 不是 `money` |
| `open` | `open_price` | 一致 |
| `close` | `close_price` | 一致 |

### 配額消耗 (Sponsor 6000 req/hour)

| 場景 | 舊方法（REST per-stock/day） | 新方法（SDK batch） | 節省 |
|------|---------------------------|------------------|-----|
| 單日全市場 | 1,600 × 1 = 1,600 | ~1 | **-99.9%** |
| 一年 backfill | 1,600 × 245 = 392,000 | ~6 batches × 6 = ~36 | **-99.9%** |
| 一年完整 backfill（6 種資料）| ~2.35M | ~36 | **-99.9%** |

每年約消耗 36 req，遠低於 6000/hour 上限；全量 8 年 backfill 約需 `36 × 8 = 288 req`，一個小時內可完成。

### 待辦事項

#### Backfill（配額充足後執行）
- ⬜ 執行 `bash scripts/backfill_finmind.sh` 全量 backfill 2019-2026
- ⬜ 確認各年 log 均為 `✓ XXXX 完成`
- ⬜ 驗證新舊資料一致性（`source='twse'` vs `source='finmind'`）

#### 第二階段（切換）
- ⬜ 配額足夠 → 切換為 FinMind 為主
- ⬜ TWSE ETL 改為 fallback / 校驗用途

#### M8-M13 相依
- M8 財報：✅ 已完成（API + 前端面板，2026-04-17）
- M13 券商分點：ETL 模組已完成（`finmind_broker_trade_sdk.py`，Agg 版），`broker_trade_agg` 表已支援，GitHub Actions 每小時自動 backfill

## 前端功能更新（2026-04-14）

### 新增功能
1. **首頁今日觀察重點**（AI 盤前摘要）
   - 後端：`backend/app/routers/market.py`，endpoint `GET /api/market/daily-brief`
   - 收集 DB 法人流向資料 + Yahoo Finance（VIX、WTI、USD/TWD）→ OpenAI 生成盤前摘要
   - `_resolve_trade_date()` 確保一定落在有資料的交易日（非假日/非休市日）
   - `_top_industries_3d()` 使用 DB 實際有資料的 3 個交易日，不依曆法推算
   - 前端：`frontend/src/components/DailyBrief.tsx`（手動觸發，不自動載入）
   - 掛在首頁 `page.tsx` IndustryDashboard 上方

2. **BrokerPanel 改版**（買進 / 賣出排行 + 標籤）
   - 後端新增 `GET /api/stocks/{stock_id}/brokers/ranked`：返回 `buy_top` / `sell_top` 各 10 筆
   - `BrokerTradeItem` 新增 `categories: List[str]` 欄位（舊分類以標籤顯示）
   - 前端 `BrokerPanel.tsx` 改為兩 tab：「買進 Top10 / 賣出 Top10」，附舊分類標籤（顏色標示）

3. **點擊券商 → 買賣超走勢圖**
   - 後端新增 `GET /api/stocks/{stock_id}/brokers/{broker_id}/history?start=&end=`
   - 前端新增 `frontend/src/components/BrokerBarChart.tsx`（ECharts 長條圖）
   - L2 個股頁點擊 BrokerPanel 中的券商 → StockChart 下方顯示該券商逐日淨買超長條圖
   - StockChart 新增 `onDaysChange` prop，讓 L2 頁追蹤當前 K 線時間範圍

### UI 調整
4. **背景/卡片調淺**：body `bg-zinc-800` → `bg-zinc-600`，卡片 `bg-zinc-900` → `bg-zinc-700`，border `zinc-700` → `zinc-600`
5. **K 線圖放大**：StockChart `60vh / min 400px` → `70vh / min 500px`；BacktestEquityChart `240px` → `380px`

## M8 財報面板（2026-04-17 完成）

### 後端 API（`backend/app/routers/financials.py`）
- `GET /api/stocks/{stock_id}/valuation` — PER/PBR/殖利率走勢（預設一年）
- `GET /api/stocks/{stock_id}/revenue` — 月營收 + YoY/MoM（預設 24 個月）
- `GET /api/stocks/{stock_id}/financials` — 財報項目，支援 `item_names` 篩選、`quarters` 參數

### 前端（`frontend/src/components/FinancialsPanel.tsx`）
- 三 tab：估值 / 月營收 / 財報
- 估值：PER + PBR 折線（左軸）+ 殖利率折線（右軸），ECharts
- 月營收：柱狀圖（營收，億元）+ YoY% 折線（右軸），ECharts
- 財報：EPS、營業收入、淨利、毛利、營業利益 的季度橫向對照表
- 位置：L2 個股頁，toggle 列下方、券商面板上方
- `chartDays` prop：三個子元件隨 K 線天數連動（估值=天數、營收=天數÷30 月、財報=天數÷90 季）
- PER <= 0 視為 N/A，全期間不適用時顯示提示文字

### Bug 修復
- `finmind_monthly_revenue_sdk.py`：月份解析 bug，`revenue_month` 為單位數（2~9）時 `mo_str[-2:]` 長度判斷錯誤，導致全部被歸到 1 月
- 修正後 monthly_revenue 從 29,349 → 74,354 筆，全 12 個月覆蓋

### GitHub Actions 優化
- `broker_trade_backfill.yml`：batch 從 calendar days 改為 trading days 計算，跳過週末，效率提升 ~30%

## 回測圖表改版（2026-04-17）

### BacktestEquityChart 改善
- Y 軸從絕對金額改為**報酬率 %**（`+10.5%` 取代 `$1,105,000`）
- 新增**回撤副圖**（drawdown % 紅色面積圖），上下圖聯動
- 標記**進出場點**：買入 ▲ 紅色三角、賣出 pin（獲利黃/虧損綠）
- tooltip 整合策略報酬、Buy & Hold、回撤三項數值
- 移除圖下方冗餘的 equity point 數字列表
- Props 新增 `trades?: BacktestTrade[]`，用於繪製進出場標記

## L2 個股頁 UX 改版（2026-04-17）

### 功能 toggle 列
- 原「功能顯示」獨立 section 改為 K 線圖下方的**緊湊 pill 列**（`ToggleChip` 元件）
- 三項目橫排：`回測程式 →`（連結）、`財報`（toggle）、`關鍵券商`（toggle）
- 兩個 toggle 存 `localStorage`（`always-stock:show-financials-panel` / `always-stock:show-broker-panel`）
- 關閉的 panel 不 render、不觸發 API

### 券商面板 retry 上限
- `BrokerPanel` 新增 `emptyRefreshCount` 狀態
- 當 API 回傳 `is_refreshing: true` 但 `buy_top` / `sell_top` 為空時計數 +1
- **超過 3 次**後停止 auto-refresh polling，顯示「此日期無券商交易紀錄」
- 切換股票/日期時自動重設計數器

### FinancialsPanel 日期連動
- 新增 `chartDays` prop，三個子元件隨 K 線天數變化重新載入
- 估值：直接用 `chartDays` 計算 `startDate` / `endDate`
- 月營收：`chartDays ÷ 30`（最少 6、最多 120 月）
- 財報：`chartDays ÷ 90`（最少 4、最多 20 季）

### PER 不適用提示
- 當全期間 PER <= 0（EPS 為負），圖表下方顯示「此期間 EPS 為負值或不適用，本益比無法顯示」
- FinMind 回傳 PER=0 即代表 EPS 為負值，非 ETL 錯誤

## L3 回測 4 欄位改版與 K 棒型態擴充（2026-04-19）

### 策略輸入改為 4 欄位
- 原單一 `strategy_text` textarea → **四欄位分離**：買進條件（entry_text）、賣出條件（exit_text）、停損 %（stop_loss_pct）、停利 %（take_profit_pct）
- 後端 `BacktestRunRequest` / `BacktestInterpretRequest` 皆新增 optional 欄位，保留 `strategy_text` 做向後相容
- `backtest_parser.parse_strategy()` 優先使用 entry/exit 分段；未提供時 fallback 解析 `strategy_text`
- `stop_loss_pct` / `take_profit_pct` 優先序：顯式參數 > entry 文字 > exit 文字 > AI mapping
- 自由文字無格式限制，parser 無法匹配的條件會走 OpenAI AI mapping fallback

### K 棒 / 技術型態擴充（backend/app/backtest_patterns.py）
- K 棒型態（OHLC-based）：
  - 紅三兵 `candle_three_white_soldiers`、三隻烏鴉 `candle_three_black_crows`
  - 錘子線 `candle_hammer`、吊人 `candle_hanging_man`
  - 十字星 `candle_doji`
  - 多頭吞噬 `candle_bullish_engulfing`、空頭吞噬 `candle_bearish_engulfing`
  - 晨星 `candle_morning_star`、夜星 `candle_evening_star`
- 技術型態（peak/trough via `_find_local_peaks` / `_find_local_troughs`, radius=3）：
  - 頭肩頂/底 `pattern_head_shoulders_top` / `pattern_head_shoulders_bottom`
  - 雙頂 M `pattern_double_top`、雙底 W `pattern_double_bottom`
  - V 型反轉 `pattern_v_reversal`、A 型反轉/倒 V `pattern_a_reversal`
- **Gotcha**：`detect_head_shoulders_*` / `detect_double_*` guard 須用 `if i < lookback - 1`，不可寫 `if i < lookback`（V/A 用後者，因為 n 天資料 index 0..n-1，lookback=n 時 i=n-1 合法）

### 可用條件目錄分組（backend/app/backtest_catalog.py）
- `CapabilityCatalog.groups` 新增 high-level 分組：
  - 外資買賣、投信買賣、自營商買賣
  - 均線 / MA（站上/跌破/黃金交叉/死亡交叉）
  - K 棒型態、技術型態
  - 風險控制（停損 / 停利 / 突破高低點 / 量能倍數）
- 前端 `BacktestPanel` 加入可收合的「查看可用條件列表」，以 `CatalogGroups` 元件依 `groups` 渲染；後端未提供 `groups` 時退回 flat 顯示

### L2 關鍵券商面板暫時隱藏
- `frontend/src/app/stocks/[stockId]/page.tsx`：從 `SIDEBAR_ITEMS` 移除 `broker` 項目，不再載入 `BrokerPanel`、不再讀寫 `always-stock:show-broker-panel` localStorage
- 程式碼保留（`components/BrokerPanel.tsx`、`components/BrokerBarChart.tsx`、對應 API）僅隱藏入口
- 理由：使用者希望優先聚焦「策略回測」與「主動推薦」，券商分點面板待產品優先序再決定是否復活

## Phase 2：交易質量 AI 分析（規劃中，2026-04-19 啟動）

### 需求背景
- `docs/交易想法.md` 是使用者沉澱下來的買方分析師 prompt：輸入 `{stock, buy_date}`，輸出 A/B/C 分類 + JSON + 中文分析報告
- 核心規則：no hindsight bias、只用 buy_date 當日及以前的資訊、target price 必須自己推導、資料不足要明講「無法建立有效交易判斷」
- 此 phase 將 prompt 接成首頁的互動功能

### 功能落點
- **位置**：首頁（`frontend/src/app/page.tsx`）`DailyBrief` 下方新增 `TradeQualityAnalysis` section
- **輸入**：
  - 股票代號 / 名稱（autocomplete，user 打字即時 filter 下拉選單）
  - 買進日期（空白時預設為 DB 最近一個交易日）
- **輸出**：
  - 5 階顏色評級：強烈推薦（深綠）/ 推薦（綠）/ 中立（黃）/ 再看看（橘）/ 快跑（紅）
  - Summary（一段話原因）
  - 預估目標價區間
  - 「詳細」按鈕 → 展開 PART 2 完整中文分析報告

### 設計決策（2026-04-19）
- **5 階由 prompt 直接輸出** `rating` 欄位（不在後端做 A/B/C → 5 階映射，避免 JSON 與 PART 2 不一致）
- **第一版不接新聞資料**：prompt 裡註明「本次分析無 10 天內新聞」；依規則 15，缺新聞時分析師應趨向保守判斷（C/快跑或中立），這是刻意的行為 —— 日後接 Google News / 輿情 ETL 再補
- **context 組裝**：後端會把 buy_date 前可觀察資料（近 10 交易日 OHLC、法人、最近一次月營收 YoY/MoM）塞進 user message，再把 `docs/交易想法.md` 作為 system prompt

### API 設計
- `POST /api/analysis/trade-quality`
  - Request: `{ stock_id: str, buy_date?: date }`（buy_date 空白時 fallback 到 latest trade date）
  - Response: `{ rating, rating_label, summary, target_price_low, target_price_high, classification, action, report_markdown, ... }`
- 支援端點（若尚未存在則新增）：
  - `GET /api/stocks/search?q=...` — 股票 autocomplete
  - `GET /api/market/latest-trade-date` — DB 最新交易日
