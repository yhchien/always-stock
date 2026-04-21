# always-stock 專案記憶

## 工作流程規範

- 每次完成一輪修改後，**自動更新 README、CLAUDE.md、memory 並直接 commit & push**，不需要使用者每次重新提醒

## Claude Code Skills

- `.claude/skills/always-stock-backend/SKILL.md` — 後端開發規範（FinMind ETL 欄位對照 / bulk upsert / 回測引擎 / prompt 管理 / Daily Brief 模式 / L1 產業 fallback）。修改 `backend/` 下的 Python 檔案時自動觸發
- `.claude/skills/always-stock-frontend/SKILL.md` — 前端開發規範（時區日期 / Panel toggle / 圖表 null 處理 / API 邊界 / L2-L3 頁面結構 / TradeQuality 輸入）。修改 `frontend/` 下的檔案時自動觸發

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
- **ETL 資料來源**: FinMind 為主（價、法人、估值、月營收、財報、券商分點、產業分類），TWSE/TPEX 僅保留備援或校驗；Fugle 已全面下線（2026-04-21）
- **Bot**: Telegram Bot（long-polling）+ OpenAI GPT 籌碼分析
- **排程**:
  - macOS launchd（本地）
  - Render Cron Job（雲端，週一至五）
  - GitHub Actions：`daily_etl_update.yml`（台北週一~五 21:00 全量 ETL）、`broker_trade_backfill.yml`（每小時 broker_trade_agg backfill）
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
- FinMind 為**唯一產業分類來源**（2026-04-21 完成切換）：
  - `TaiwanStockIndustryChain`：`stock_id / industry / sub_industry / date`（`Backer/Sponsor`）
  - Fugle CSV mapping 已全面下線，`chain`（上游/中游/下游）欄位永久捨棄
  - `stocks_master.industry_name` / `sub_industry` 由 FinMind 寫入；`chain` 欄位保留但永遠 NULL
  - `industry_daily_flow` 的 `industry_name` 已重建為 FinMind 細分類（53 個產業/日）

## Milestones 進度

### 已完成（截至 2026-04-20）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M6: 8 年歷史資料 backfill（2019-01 ~ 2026-04），僅 5 天 OHLC 資料源缺漏
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M8: 財報面板（估值 PER/PBR/殖利率、月營收+YoY、季財報 EPS 等）— API + 前端完成
- M9: AI 籌碼分析（`/ai` 指令，接 OpenAI GPT）
- M10: 雲端部署（Render + Vercel）
- M11: 回測程式（DSL + AI mapping + equity curve + 策略建議；2026-04 擴充 4 欄位改版 + 9 K棒型態 + 6 技術型態 + 報酬率%回撤圖）
- M16: AI 盤前摘要（Daily Brief，2026-04-20 起改由 Telegram Bot `/brief` 提供）
- M17: 交易質量 AI 分析（Trade Quality Analysis，5 階評級 + 四象限 + 目標價）
- M18: 使用者註冊系統（Email/password + server-side session + RequireAuth；M17 公開但分層 rate limit；admin@always-stock.dev / forwork）

### 進行中
- M13 關鍵券商分點：ETL 模組與 `broker_trade_agg` backfill 已完成；L2 券商面板在 2026-04-19 主動隱藏（產品優先序下調），未來視需要復活

### 待開始
- M12 自然語言策略
- M14 輿情分析
- M15 Telegram 電子報
- M19 關注買進清單（L0 側邊欄 + 持股卡片 + M17 交易分析整合）
- M20 交易分析擴充（預期 45% 報酬率加碼建議 + 風報比 1:1.75）
- M21 Trade Quality Context 資料管線（industry/chip/peer_rank/fundamental/price_structure 預聚合，餵結論層給 LLM）

> M18 → M19 → M20 依序執行。M19 需 M18 註冊系統落地，M20 擴充建立在 M19 卡片帶入 context 之上。
> M21 與 M20 平行但互補：M20 改 prompt、M21 改 backend context 組裝，兩者合起來才能讓 M17 分析真正精準。

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

- **L1 產業名稱 fallback（已於 2026-04-21 全面移除）**
  - 舊設計（TWSE `industry_daily_flow` ↔ Fugle `stocks_master` 名稱不一致）需要 `INDUSTRY_NAME_FALLBACKS` 硬映射 + 後綴剝離。
  - 2026-04-21 切換後，`industry_daily_flow.industry_name` 與 `stocks_master.industry_name` 皆由 FinMind 寫入，名稱一致，fallback 已全部刪除。

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
1. **今日觀察重點**（AI 盤前摘要）
   - 後端：`backend/app/routers/market.py`
     - 共用函式 `build_daily_brief(db, requested_date)` — HTTP endpoint 與 Telegram bot 共用，確保兩個入口輸出一致
     - HTTP endpoint `GET /api/market/daily-brief` 僅負責 `ValueError → HTTPException` 轉換
   - 收集 DB 法人流向資料 + Yahoo Finance（VIX、WTI、USD/TWD）→ OpenAI 生成盤前摘要
   - `_resolve_trade_date()` 確保一定落在有資料的交易日（非假日/非休市日）
   - `_top_industries_3d()` 使用 DB 實際有資料的 3 個交易日，不依曆法推算
   - 曝光入口（2026-04-20 調整）：
     - Telegram Bot `/brief`（主要入口，handler 在 `backend/app/telegram_bot.py::brief_handler`）
     - 前端 `DailyBrief.tsx` 元件保留但已從首頁移除；若未來要重新掛回再 import 即可

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
- `docs/trade_quality_prompt.md` 是使用者沉澱下來的買方分析師 prompt：輸入 `{stock, buy_date}`，輸出 A/B/C 分類 + JSON + 中文分析報告
- 線上 backend 實際部署的 canonical prompt 應放在 `backend/app/prompts/trade_quality.md`，因為 Render Web Service `rootDir=backend`，不保證 repo 根目錄 `docs/` 會被帶進 production artifact
- 核心規則：no hindsight bias、只用 buy_date 當日及以前的資訊、target price 必須自己推導、資料不足要明講「無法建立有效交易判斷」
- 此 phase 將 prompt 接成首頁的互動功能

### 功能落點
- **位置**：首頁（`frontend/src/app/page.tsx`）`TradeQualityAnalysis` section（2026-04-20 起為首頁頂部，DailyBrief 已移至 Telegram Bot）
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
- **context 組裝**：後端會把 buy_date 前可觀察資料（近 10 交易日 OHLC、法人、最近一次月營收 YoY/MoM）塞進 user message，再把 `backend/app/prompts/trade_quality.md` 作為主要 system prompt；repo `docs/trade_quality_prompt.md` 保留給人讀與編輯

### API 設計
- `POST /api/analysis/trade-quality`
  - Request: `{ stock_id: str, buy_date?: date }`（buy_date 空白時 fallback 到 latest trade date）
  - Response: `{ rating, rating_label, summary, target_price_low, target_price_high, classification, action, report_markdown, ... }`
- 支援端點（若尚未存在則新增）：
  - `GET /api/stocks/search?q=...` — 股票 autocomplete
  - `GET /api/market/latest-trade-date` — DB 最新交易日

## Daily ETL 穩定性修正（2026-04-21）

### 問題
- 2026-04-20 的 scheduled run（台北 21:00 觸發，實際因 Actions cron 延遲在 22:54 才跑）被標 `error`、GitHub Actions fail
- 根因：FinMind `TaiwanStockInstitutionalInvestorsBuySell` 與 `TaiwanStockPER` 同步比 broker_trade_agg 慢；22:54 時 inst_flow / daily_valuation 還拿不到全市場資料
- inst_flow 在空資料時直接回傳 `status: "error"`，因為它屬於 `CRITICAL_STEPS`，整包被拖倒

### 修正
1. **cron 推遲**：`.github/workflows/daily_etl_update.yml`
   - `0 13 * * 1-5`（台北 21:00）→ `0 15 * * 1-5`（台北 23:00）
   - `timeout-minutes: 45 → 75`（預留 30 分鐘給 critical step retry）
2. **no_data 語義拆分**：`backend/etl/finmind_inst_flow_sdk.py`
   - 空資料時 `status: "error" → "no_data"`（與真的 exception 區分）
3. **CRITICAL step retry**：`backend/run_finmind_etl_sdk.py`
   - 新增 `NO_DATA_RETRY_SCHEDULE = [600, 1200]`（10 / 20 分鐘）
   - `_run_critical_step_with_retry()`：CRITICAL step 回 `no_data` 時依排程重試最多 2 次
   - `daily_price` / `inst_flow` 兩個 step 呼叫改用 helper
4. **整體狀態判定**：
   - `RESUMEABLE_STEP_STATUSES` 加入 `no_data`（非 CRITICAL 的空資料視為正常，例如月營收 / 財報）
   - CRITICAL step 最終仍 `no_data`（retry 用盡）→ 整包 `error`（觸發 workflow fail，提醒人工檢查）

### 測試
- `backend/tests/test_run_finmind_etl_sdk.py` 新增兩個測試案例：
  - CRITICAL step `no_data` 最終仍 no_data → error
  - 非 CRITICAL step `no_data` → ok

## 產業分類全面切換至 FinMind（2026-04-21）

### 背景
- 舊架構：`industry_daily_flow` 用 TWSE 名稱（`水泥工業` 等），`stocks_master` 用 Fugle 名稱（`水泥` 等），L1 API 靠 `INDUSTRY_NAME_FALLBACKS` + 後綴剝離硬接
- 舊架構的 `chain`（上游/中游/下游）是 Fugle 特有三層結構，FinMind 沒有
- 決策：**全面切 FinMind `TaiwanStockIndustryChain`**，徹底移除 Fugle 與 `chain` 欄位

### 執行步驟
1. **`backend/etl/fetch_stock_master.py` 重寫**：移除 Fugle CSV，改吃 `TaiwanStockInfo` + `TaiwanStockIndustryChain`；`chain` 寫 NULL、`source="finmind"`
2. **`backend/run_daily_etl.py` / `run_backfill.py`**：移除 `--fugle-mapping` argparse 與 `fugle_mapping_path` 參數
3. **`backend/run_finmind_etl_sdk.py` 新增 step 0**：`stocks_master`（non-CRITICAL），每日 ETL 自動 refresh 產業分類
4. **`backend/app/routers/industries.py`**：移除 `INDUSTRY_NAME_FALLBACKS` + `_candidate_industry_names`；`StockFlowItem` / `SubIndustrySummaryItem` schema 拔掉 `chain`
5. **`backend/app/ai_analyst.py` + `telegram_bot.py`**：移除 `stock.chain` 輸出（`供應鏈位置` / `⛓ 供應鏈`）
6. **`frontend/src/lib/api.ts`**：TS 型別拔掉 `chain`
7. **`frontend/src/components/StockList.tsx`**：移除 `CHAIN_ORDER` / `chainSortKey`；卡片由 `chain` 分組改為 `sub_industry` 分組，SummaryTable 移除「鏈」欄位
8. **`rebuild_industry_flow.py`** 全量重建：清空 `industry_daily_flow` 1672 個交易日 + 逐日 re-aggregate

### DB 欄位保留政策
- `stock_master.chain` 欄位**不做 migration**（避免 schema 震盪），ETL 永遠寫 NULL
- `source` 欄位統一寫 `finmind`
- 前端 / API schema / 測試 **完全不再引用** `chain`

### 重建後資料
- `industry_daily_flow` 從 267 筆（舊 TWSE 粗分類） → ~53 個產業 × 1672 天 ≈ 88,000 筆（FinMind 細分類）
- Render Postgres 端執行，每天 aggregate 約 5 秒，全量耗時約 2 小時
- L0→L1 drill-down 100% 對應（53/53 產業都能在 stocks_master 找到股票）

### 環境變數命名對齊
- 本地 `.env` 跟 Render dashboard 對齊：`TARGET_DATABASE_URL` → `DATABASE_URL`、`FINMIND_API_TOKEN` → `FINMIND_TOKEN`
- 2026-04-21 commit `bc51dc9` 已修完 `.env.example` / `backend/.env.example` / `infra/render/render.yaml.template` / `docs/operations/security_and_secrets.md` / `docs/architecture/architecture_overview.md`
- `backend/migrate_sqlite_to_postgres.py` / `backend/validate_migrated_data.py` 保留 `TARGET_DATABASE_URL`（migration 工具區別來源/目的，有 fallback 到 `DATABASE_URL`）

## Phase 3：使用者註冊 + 關注清單 + 加碼建議（規劃中，2026-04-21 啟動）

三個相依的 milestone，依 **M18 → M19 → M20** 順序執行。

### M18 使用者註冊系統
- **認證方式**：第一階段僅支援 Gmail OAuth（未來可能加其他 provider）
- **Admin local auth**：帳號 `admin` / 密碼 `forwork`（寫死在後端 env 或 seeder，給開發者繞過 Gmail 用）
- **Gating 範圍**：
  - 未登入：全站頁面可 render，但互動 **disable**（灰掉蓋提示「請登入」），**唯一例外**是首頁 M17 AI 交易分析（不需登入即可使用）
  - Telegram Bot 也要 gating：chat_id 需先綁定已註冊的 Gmail 帳號才能使用任何指令（Bot 第一次互動時引導至登入頁）
- **登入頁**：新增 `/login` 前端路由，含 Gmail OAuth 按鈕 + Admin local auth fallback 區塊
- **DB schema**：新增 `users` 表（email / provider / provider_user_id / is_admin / created_at）與 `user_sessions` 或 JWT token 機制；Telegram 綁定另建 `user_telegram_bindings`（user_id / chat_id / verified_at）
- **API**：`POST /api/auth/google/callback`、`POST /api/auth/admin-login`、`POST /api/auth/logout`、`GET /api/auth/me`

### M19 關注買進清單
- **前提**：M18 完成（清單必須綁使用者帳號）
- **資料持久化**：一律存 **Render Postgres**，不走 localStorage（跨裝置同步需求）
- **L0 sidebar 擴展**：把現在 L1 頁面左側的 sidebar 樣式套到 L0 首頁，兩層 UI 導覽一致
- **「關注買進清單」入口**：放在 sidebar 中（具體位置設計階段再定）
- **新增持股 popup**（shadcn/ui Dialog）：
  - 股票代號（autocomplete，沿用 `/api/stocks/search`）
  - 買進日期（date picker，預設最近交易日）
  - 均價（數字輸入，必填）
  - 按「儲存」寫入 `user_watchlist` 表
- **清單展開頁**（新路由，例如 `/watchlist`）：
  - 每檔持股一張卡片：顯示股票代號/名稱、買進日期、均價、今日股價、未實現損益 %（帶顏色）
  - 卡片**右下角「交易分析」按鈕** → 呼叫 M17 AI 交易分析 endpoint，`stock_id` / `buy_date` 從卡片資料帶入（使用者無需重新輸入）
- **DB schema**：`user_watchlist`（user_id / stock_id / buy_date / avg_price / created_at，複合 unique 視需求）

### M20 交易分析擴充：加碼建議
- **前提**：M19 完成（交易分析從卡片觸發，可以帶入 avg_price 作為 context）
- **新增分析段落**：在 M17 的 PART 2 中新增「如何操作以達到 45% 預期報酬率」段落
  - 加碼點位建議：跌到 X 加碼 / 漲到 Y 加碼
  - 停損與停利點位（配合風報比 1:1.75）
- **寫死參數**（不做 UI 調整）：
  - 目標報酬率 = **45%**
  - 風報比 = **1 : 1.75**（即每承擔 1 單位下行風險，追求 1.75 單位上行報酬）
- **實作方式**：修改 `backend/app/prompts/trade_quality.md`（canonical），同步 `docs/trade_quality_prompt.md`（鏡像）。程式碼只需把 avg_price 加進 context，不改 API 契約。
- **JSON schema 調整**：`if_strong` 視需要新增 `add_position_levels: [{price, reason}, ...]` 欄位

### M21 Trade Quality Context 資料管線
- **定位**：與 M20 平行互補。M20 是 prompt 工程；M21 是把 DB raw data 預聚合成「結論層」訊號，避免 AI 自己瞎推
- **輸出**：`build_stock_analysis_input(stock_id, buy_date) -> dict`，回傳 6 區塊結構化 JSON：
  - `industry_summary`（hot_score / hot_level / price_strength / volume_trend / institution_flow / capital_type / is_false_hot）
  - `chip_summary`（foreign/trust/dealer buy_days / volume_trend / price_trend / is_accumulation / chip_strength）
  - `peer_rank`（return_5d_percentile / volume_percentile / institution_rank_percentile / leader_or_follower）
  - `fundamental`（revenue_yoy / revenue_mom / guidance）
  - `price_structure`（trend / is_breakout / is_consolidation / is_accelerating）
  - `news_input_stub`（query_stock / query_industry / date_end，給未來 M14 接入）
- **可行度**：~92%。`industry_news_heat` / `guidance` 兩欄必為 `null`（無 DB 來源，未來 M14 輿情 ETL 完成再補）
- **完整 spec**：`docs/plans/trade_quality_context_spec.md`（含 DB 欄位對照表、SQL 範例、常數門檻、null 政策）
- **實作原則**：
  - 只用 `buy_date` 當日及以前資料（no hindsight bias）
  - 規則 deterministic、可測試、不用 LLM 判斷
  - 所有門檻常數集中 `backend/app/analysis/context_thresholds.py`
  - Raw extraction 與 derived signal 分開寫（未來加欄位容易擴充）
- **技術注意**：
  - `industry_daily_flow` 只有法人淨買超，**沒有** volume → industry_volume_trend 要從 `daily_price` + `stocks_master` 跨股聚合
  - peer_rank 用 `PERCENT_RANK() OVER (PARTITION BY industry_name)` 即時算（同產業小集合速度可接受）
  - 連續買超 N 日建議 Python loop 從最新日往回數（SQL `SUM(CASE WHEN) OVER` 可讀性差）
  - Lookback 一律以**交易日**為單位（`ORDER BY trade_date DESC LIMIT N`），非 calendar days

## M18 使用者註冊系統完成（2026-04-21）

### 最終範圍（與原規劃差異）
- **Auth**：Email/password 單純註冊登入（**無** Gmail OAuth、無 email 驗證、無密碼重設）。未來要加 OAuth 只需在 `users` 加 `provider` 欄位 + 新 callback
- **Session**：Server-side session（UUID token in httpOnly cookie，30 天過期，可 revoke）；非 JWT、非 localStorage
- **Telegram 綁定**：整個 drop，不做 `user_telegram_bindings`
- **Admin 預設帳號**：`admin@always-stock.dev` / `forwork`（可用 `ADMIN_EMAIL` / `ADMIN_PASSWORD` env 覆寫）
- **⚠️ 為何不是 `admin@local`**：Pydantic `EmailStr` 會拒收無 TLD 或 RFC 2606 保留 TLD（`.local` / `.test` / `.localhost` / `.internal` / `.invalid` / `.example`）的 email，`/api/auth/login` 會 422 而進不了 handler。預設必須是**真實 TLD**的 email。`tests/test_auth_router.py::test_admin_seeder_default_email_passes_pydantic_emailstr` 保護這個 invariant

### DB Schema
- `users`：`id / email (unique) / password_hash (bcrypt) / name / is_admin / is_active / created_at / last_login_at`
- `user_sessions`：`session_id (UUID) / user_id / created_at / expires_at / last_seen_at / user_agent / ip_address / revoked_at`
- Migration：`backend/migrate_add_users.py`（`Base.metadata.create_all()`，idempotent）

### API
- `POST /api/auth/register`（password ≥ 8 碼，自動登入）
- `POST /api/auth/login`（uniform 401 避免 email 枚舉）
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Gating 範圍

#### 後端 `Depends(require_user)`
- `/api/backtest/interpret` / `run` / `advice`（L3 回測全部需登入）

#### 後端分層 rate limit（`/api/analysis/trade-quality` 維持公開）
- 未登入：**3/day** by IP
- 已登入：**30/day** by `user:{id}`
- 實作：`backend/app/rate_limit.py` 的 `trade_quality_limit_value(key: str)` 依 key prefix 決定限額（**slowapi dynamic limit signature 必須吃 `key: str`，不是 `Request`**）
- Storage: in-memory（夠用；未來多機部署再換 Redis）

#### 前端 `<RequireAuth>` 包住的路由
- `/industries/[industryName]`
- `/stocks/[stockId]`
- `/stocks/[stockId]/backtest`

#### 前端公開路由
- `/`（首頁，含 `<TradeQualityAnalysis />`）
- `/login`

### 關鍵 Gotcha
- `get_optional_user` 必須寫 `request.state.auth_user_id = user.id`，否則 rate limit key 無法辨識使用者
- FastAPI 測試用 `app.dependency_overrides[require_user] = lambda: test_user` 繞過認證
- slowapi counter 跨測試累加，fixture 需要 `limiter.reset()`
- Pydantic `EmailStr` 需安裝 `email-validator` 套件
- 前端所有 API 呼叫改用 `apiFetch`（`credentials: "include"` wrapper），否則 cookie 不會帶
- CORS middleware 必須 `allow_credentials=True`
- 密碼雜湊用 bcrypt（`backend/app/auth.py::hash_password` / `verify_password`）

## FinMind inst_flow amount_est 漏寫 bug 修復（2026-04-22）

### 問題現象
- L0 / L1 產業卡片在切到 FinMind 後，近期日期可能顯示「法人買賣超 +0.0 億」
- 單日 `inst_stock_flow` 有 `buy_shares / sell_shares`，但 `buy_amount_est / sell_amount_est / net_amount_est` 為 `NULL`

### 根因
- `backend/etl/finmind_inst_flow_sdk.py` 只寫 shares，漏掉 `*_amount_est`
- `backend/etl/aggregate_industry_flow.py` 聚合讀的是 `*_amount_est`，所以 `industry_daily_flow` 會被聚合成 0.0

### 修法
1. `backend/etl/finmind_inst_flow_sdk.py` 在 groupby 後 join `daily_price.close_price`，寫入 `*_amount_est = shares * close_price`
2. 新增 `backend/scripts/backfill_inst_flow_amount_est.py`，回填既有 `source='finmind'` 且 `*_amount_est IS NULL` 的資料
3. 回填後重跑 `python backend/rebuild_industry_flow.py --from 2026-04-10 --skip-master`
4. 補跑中斷日期的 `run_finmind_etl_sdk.py`

### Gotcha
- 金額單位是元，前端再自行除以 `1e8` 轉億
- 若個別 `(trade_date, stock_id)` 缺 `daily_price.close_price`，amount fallback 為 `0.0`

## Industry date fallback + admin login gotcha（2026-04-22）

- `IndustryDashboard` / `StockList` 對應的 `/api/industries*` 路由現在應比照 `/market`：
  若使用者選到非交易日，後端自動 resolve 到 `<= requested_date` 的最近交易日，而不是直接 404
- 首頁點產業時，必須帶 **目前 component state 的 date**，不能帶外層舊的 query param date，否則會出現 UI 選了 `3/4`、實際跳頁卻還是舊日期的 race condition
- `/login` 前端不能對 login mode 一律套 `minLength=8` / `password.length < 8` 驗證，否則預設 admin 帳號 `admin@always-stock.dev / forwork`（7 碼）永遠送不到後端
- 註冊仍維持最少 8 碼；只有登入要允許短於 8 碼的既有帳號
