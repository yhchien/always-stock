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
  - GitHub Actions：`daily_etl_update.yml`（台北週一~五 18:00 全量 ETL）、`broker_trade_backfill.yml`（每小時 broker_trade_agg backfill）
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
- M18: 使用者註冊系統（Email/password + server-side session + RequireAuth；M17 公開但分層 rate limit；admin email / password 由 Render env var `ADMIN_EMAIL` / `ADMIN_PASSWORD` 設定）
- M19: 關注買進清單（單一清單上限 20 檔，加入 popup 填買進日/均價；L0 HotMoneyList、L1 StockList、L2 個股頁右下「加入清單」；Navbar「我的清單」；/watchlist 卡片含未實現損益 + 交易分析深連結 M17；資料綁 user_id）
- M22: 熱錢湧入個股排行（L0 底部 Top 20 / L1 頂部 Top 10，近 N 日三大法人累計買超；spec 在 [docs/plans/hot_money_list_spec.md](docs/plans/hot_money_list_spec.md)）
- M21: Trade Quality Context 資料管線（6 個 section 預聚合 JSON：industry/chip/peer_rank/fundamental/price_structure/news_stub；deterministic + no hindsight；入口 `build_trade_quality_context(db, stock_id, buy_date)`；`GET /api/analysis/context` 需登入；實作 [docs/plans/m21_context_pipeline_implementation.md](docs/plans/m21_context_pipeline_implementation.md)）

### 進行中
- M13 關鍵券商分點：ETL 模組與 `broker_trade_agg` backfill 已完成；L2 券商面板在 2026-04-19 主動隱藏（產品優先序下調），未來視需要復活

### 待開始
- M12 自然語言策略
- M14 輿情分析
- M15 Telegram 電子報
- M20 交易分析擴充（預期 45% 報酬率加碼建議 + 風報比 1:1.75）
- M23 每日異常訊號清單（**改為使用者手動觸發**；前端 `DailySignalsPanel`「重新產生」按鈕 → POST `/api/signals/regenerate` → FastAPI BackgroundTasks 跑 pipeline；deterministic filter 建候選池 + LLM 上網查公司業務／集團／龍頭；輸出 LEADER / FOLLOWER / LAGGARD 三類；另有 `/signals/archive` 的 40 交易日追蹤總表，並新增 `/api/signals/archive/completed` 封存移出 40 日後的 cycle 摘要；不預測報酬、不出買賣建議；GitHub Actions cron 已停用，`workflow_dispatch` 保留作管理備援；spec [docs/plans/m23_daily_signals_spec.md](docs/plans/m23_daily_signals_spec.md)）
- M24 自訂進出場策略回測（M11 擴充；使用者自設分層進場 / 追價 / 攤平 / 停損停利規則，引擎回測 edge；LLM 為現場判斷層，trigger 觸發時依當下籌碼/產業/技術給「適合執行 yes/no」提示，不替使用者寫規則）

> M18 → M19 → M20 依序執行。M19 已完工（2026-04-23），M20 擴充建立在 M19 卡片帶入 context 之上。
> M21 與 M20 平行但互補：M20 改 prompt、M21 改 backend context 組裝，兩者合起來才能讓 M17 分析真正精準。
> M23 LLM 是「資料翻譯員」（解釋觸發訊號）；M24 LLM 是「現場提醒員」（trigger 當下給判斷）。LLM 拔掉系統還能跑（filter / 回測結果還在）— 是輔助層不是核心，**有它更好、沒它也不殘**。

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

## 最近重要修正（2026-04-27）

- L0 `/api/industries` 已加 `resolved_date` 粒度的 60 秒 server-side cache；同一天短時間重複請求不可再重跑整段產業查詢
- `industry_daily_flow.streak` 已下沉為 ETL 持久化欄位；L0 不可再 request-time 回掃最近 31 個交易日計算 streak
- `industry_daily_flow.streak` 的 schema 演進要靠顯式 ensure（`ALTER TABLE ... ADD COLUMN streak`）；`Base.metadata.create_all()` 只建新表，不會替既有表補欄位
- 若要修正歷史 streak，應優先跑 `rebuild_industry_flow.py`（升序重建），不要只改 API 端計算
- M23 Step 0 / research 改為顯式走 OpenAI Responses API `tools=[{"type":"web_search"}]`；不能只靠 prompt 文字寫「請上網查」
- `market_context.taiex_change_pct` / `otc_change_pct` 改為 backend authoritative：從 DB snapshot deterministic 帶入，LLM 不可改寫或補 0
- M23 OpenAI client 必須顯式設 `timeout=120`、`max_retries=1`；否則單一 `responses.create()` 卡住時，job 會一直停在 `llm_research` 或 `llm_explain` 的同一個 batch 進度
- M23 batch 建議拆開調：`research` 可維持 8，`explain` 應降到 4；因為 explain prompt 較長，較容易在單次 call 卡住
- M23 explanation 已改成兩階段：
  - 全候選先做短 decision（`WATCH/REMOVE + short_reason`）
  - 只對最後 `WATCH` 名單補長理由（250-350 字）
- M23 現在對 Responses API 顯式帶 `prompt_cache_key`（market / research / decision / watch-reason 分開），利用固定長 prompt 前綴降低 latency
- M23 模型分層已接好：`OPENAI_SIGNALS_MARKET_MODEL` / `OPENAI_SIGNALS_RESEARCH_MODEL` / `OPENAI_SIGNALS_DECISION_MODEL` / `OPENAI_SIGNALS_REASON_MODEL`
- M23 任何 LLM fallback 都必須保留 `llm_diagnostic`，至少含 `stage / model / status / use_web_search / prompt_cache_key`
- `llm_diagnostic.status` 目前標準值：`ok` / `api_key_missing` / `openai_exception` / `empty_output` / `invalid_json`
- Step 0 market fallback 文案不可再籠統寫成「OpenAI 服務不可用」；必須帶出較精確原因（例如 API key 缺失、OpenAI 例外、空回應、非 JSON）
- research / decision / watch-reason 三段若 fallback，也要把診斷掛回各股票項目，避免 snapshot 成功但無法判斷是哪一層退回保守結果
- M23 現在走 Responses API + `web_search` tool；預設 fallback 不可再用 `gpt-4o-search-preview`，避免線上帳號回 `404 Model not found`
- M23 pipeline 的 research / decision / watch-reason batch 現在允許有限度並行（concurrency=2）；若要再加速，優先調這個並行度，不要先無限制放大 batch
- `DailySignalsPanel` 卡片要提供明確的個股/K線入口，不要只剩股票代號文字 link
- M23 候選池目前採較保守來源範圍：`TOP_INDUSTRIES_LIMIT=6`、`TOP_STOCKS_LIMIT=30`、`TOP_STOCKS_INNER=6`
- M23 laggard 候選雖仍是 `hits >= 2`，但新增硬條件 `total_institution_flow_1d > 0`，避免把純量價轉強但法人尚未回補的邊緣股送進 LLM
- M23 在 `after_hard` 後新增 `LLM_INPUT_HARD_LIMIT=50`；排序優先序是 `LEADER > FOLLOWER > LAGGARD_CANDIDATE`，同類內再看 `in_top_stocks_3d / in_top_industries_3d / total_institution_flow_3d / total_institution_flow_1d / price_change_5d`
- 目前預設配置：
  - `OPENAI_SIGNALS_MARKET_MODEL=gpt-5.4-mini`
  - `OPENAI_SIGNALS_RESEARCH_MODEL=gpt-5.4-mini`
  - `OPENAI_SIGNALS_DECISION_MODEL=gpt-5.4`
  - `OPENAI_SIGNALS_REASON_MODEL=gpt-5.4-mini`
- M17 / Trade Quality Analysis 現在對同 stock_id + buy_date 有 5 分鐘 in-memory cache；重複查同一標的時應先命中 cache 再考慮重新打 OpenAI
- 首頁首屏效能：`TradeQualityAnalysis` 保持先載，`DailySignalsPanel` / `HotMoneyList` / `IndustryDashboard` 改 deferred mount，避免首次同時打多支 API
- 觀察清單上限已調整為 30；前後端文案、capacity 常數、API 限制需同步

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

> **2026-04-27 修正**：先前估算「SDK `stock_id_list=[...]` async batch = 1 req」是錯的。SDK 內部對每個 `data_id` 都打一次 v4 endpoint，仍是 per-stock 計費（1592 檔 ≈ 1500 req / 步）。daily_etl_update workflow 實測：
> daily_price 1466 → inst_flow +1708 → daily_valuation +1591 → monthly_revenue 已 6012/6000 超標 → 後續 financial / broker 全跳過。
>
> 已改為走 **dataset-level batch**：純 v4 REST，**不帶 `data_id`**、僅帶 `dataset` + `start_date` / `end_date`，單次拉全市場該區間資料，**1 quota per dataset**。

| 場景 | 舊（per-stock SDK list） | 新（dataset-level REST） | 節省 |
|------|----------------------|----------------------|-----|
| 單日 daily_price | ~1500 req | 1 req | **-99.9%** |
| 單日完整 ETL（5 dataset） | ~7500 req（必爆） | ~5 req | **-99.9%** |
| 一年 backfill | ~365K req | ~5 × 12 = 60 req | **-99.98%** |

**dataset 對應**：
- `daily_price` → `TaiwanStockPrice`
- `inst_flow` → `TaiwanStockInstitutionalInvestorsBuySell`（**舊名 `TaiwanStockInstitutionalInvestors` 已被 v4 enum 拒收**）
- `daily_valuation` → `TaiwanStockPER`
- `monthly_revenue` → `TaiwanStockMonthRevenue`
- `financial_statement` → `TaiwanStockFinancialStatements`
- `margin_trade` → `TaiwanStockMarginPurchaseShortSale`（**v4 dataset-level fetch 只回 `start_date` 當日資料**，必須逐交易日呼叫；ETL 模組內部 loop daily_price.trade_date，每天 1 quota）

實作位置：`backend/etl/finmind_sdk_client.py` 的 `_fetch_dataset_for_range()` + 6 個 `fetch_*_dataset()` wrapper；ETL 模組拿到全市場 DataFrame 後以 `df[df["stock_id"].isin(stock_ids)]` 過濾到 stocks_master 範圍。

**broker_trade_agg 例外**：`TaiwanStockTradingDailyReport` 必須帶 `data_id` 不接受 dataset-level 呼叫，仍是 per-stock。已從 `run_finmind_etl_sdk.py` 預設步驟拔掉；`broker_trade_backfill.yml`（每小時 cron）獨立處理。要強制跑時用 `--steps broker_trade_agg`。

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

## Daily ETL 配額重試 + 假日自動跳過（2026-04-22）

### 背景
- 2026-04-22 00:15（台北）的排程又卡配額（6362/6000），workflow 因 `insufficient_quota` 被當成 pass 但其實全沒跑
- 假日（國定假日）cron 不會排除，ETL 會一路跑每個 step 才發現空資料，浪費時間與配額

### 改動
1. **`backend/etl/finmind_daily_price_sdk.py`**
   - `daily_price` 回空資料時依 `client.quota_info` 判斷：
     - 配額健康（`ok` / `warning`）→ `status: "holiday"`（非交易日）
     - 配額 `critical` → `status: "no_data"`（交給既有 CRITICAL retry）
   - 原本會回 `error`，現在區分假日 vs FinMind 慢同步

2. **`backend/run_finmind_etl_sdk.py`**
   - `daily_price` 回 `holiday` → 直接短路，後續 5 個 step 全標 `skipped_holiday`
   - `RESUMEABLE_STEP_STATUSES` 加入 `holiday` / `skipped_holiday`
   - `determine_overall_status()`：daily_price holiday → 整體 `holiday`
   - `main()` 新增 **exit code 5 = holiday**（workflow 視為 pass、不 retry）
   - `_run_critical_step_with_retry()` 維持原邏輯：只對 `no_data` retry

3. **`.github/workflows/daily_etl_update.yml`**
   - `timeout-minutes: 75 → 240`（首次 75 + sleep 90 + 重試 75 + buffer）
   - 三段 step：`etl1` →（僅在 exit 2 時）`sleep 5400` → `etl2`
   - 最終狀態評估：`etl2.etl_exit || etl1.etl_exit`
   - exit `0 / 1 / 5` → pass；`2 / 3` → fail
   - `insufficient_quota` 不再被當成靜默 pass；1.5h 後重試一次；假日自動跳過不 retry

4. **`backend/tests/test_run_finmind_etl_sdk.py`** 新增 3 案例：
   - `daily_price holiday` → 整體 `holiday`
   - CRITICAL step 回 `holiday` 不觸發 `no_data` retry
   - `skipped_holiday` 為 resumeable status

### Gotcha
- Holiday 偵測用「配額健康」當 signal；配額 critical 時即使 daily_price 空，也退回 `no_data` 走原 retry 路徑
- 若真實交易日遇到 FinMind 10+h 延遲導致 23:00 還沒資料，會誤判為假日 → 可接受的 trade-off
- workflow exit code 語義：`0 ok / 1 partial / 2 quota / 3 error / 5 holiday`

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
- **Admin local auth**：帳號 / 密碼由 Render env var `ADMIN_EMAIL` / `ADMIN_PASSWORD` 設定（給開發者繞過 Gmail 用）
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

## Phase 4：異常訊號 + 自訂策略回測（規劃中，2026-04-23 啟動，2026-04-24 修訂）

兩個獨立 milestone，共同核心：**deterministic filter / 引擎是骨幹，LLM 是輔助層**。

### 設計原則
- LLM 不預測股價、不替使用者挑股、不出操作手冊
- LLM 做兩件事：(1) 把 deterministic 訊號翻成中文白話（解釋層）、(2) 在 trigger 觸發當下依當下 context 給 yes/no 提示（現場判斷層）
- 拔掉 LLM 系統還能跑：filter 結果還在、回測結果還在
- 「有它更好、沒它也不殘」— 這是 LLM 在本專案的標準位置

### 廢棄的舊規劃（2026-04-24 review 後）
- ~~每日 AI 推薦 3 檔 60 天 +45%~~ — 60 天 +45% ≈ 年化 800% 不現實；LLM 也不產 alpha
- ~~LLM 出買進後操作手冊~~ — 攤平本身是 blow-up 風險策略；LLM 出規則容易 overfit；使用者拿到「AI 規則」反而更難違背
- 同時也廢棄前一版 M23 / M24 的「零 LLM」走極端設計，改回「核心引擎 + LLM 輔助」

---

### M23 每日異常訊號清單（2026-04-25 改版）

> **Canonical spec**：[docs/plans/m23_daily_signals_spec.md](docs/plans/m23_daily_signals_spec.md)
> LLM prompt：[backend/app/prompts/watch-list-stock.md](backend/app/prompts/watch-list-stock.md)

**你想解決的問題**：每天早上需要一份「今天值得看一下」的清單，不用自己翻產業排行 + 法人 + 融資融券一檔一檔比對；並且要找出「熱錢主線正在擴散到哪裡」（不只是已經漲的）。

**輸出三類股票**（無預測 / 無目標價 / 無 BUY-SELL，僅 WATCH / REMOVE）：
- **LEADER**：產業中最早上漲、漲幅領先、資金排名靠前、法人連買、量能放大、題材明確
- **FOLLOWER**：與 LEADER 同產業 / 同供應鏈、已同步上漲但漲幅不如 LEADER、籌碼仍支持
- **LAGGARD**：同產業 LEADER 已漲、該股漲幅落後、業務題材高度相關、法人/量能開始轉強、技術 early_turn

每檔附 **500–1000 字繁體中文 reason**（13 點強制要點，見 prompt「reason 寫作規則」）。

**Pipeline（10 步）**：data ingestion → industry rank → stock rank → candidate pool → peer/group expand → deterministic filter → LLM research → LLM explanation → persist snapshot → update job status。詳見 spec §2 / §5。

**Deterministic 部分（DB + 程式）**：
- 候選池：top_stocks_3d 40 + top_industries_3d 10 成分股 + 熱門產業龍頭 + 同供應鏈 + 集團股（spec §6，目標 60–120 檔）
- LEADER / FOLLOWER / LAGGARD candidate 預分類（spec §7）
- Hard exclusions：ETF、金融股、流動性不足、近 3 日漲超 15%（spec §9.1）
- Soft filters：retail_overheated / distribution / range_bound（spec §9.2）

**LLM 部分**（**支援 web search 的模型**，例如 `gpt-4o-search-preview`）：
- 上網查 market_state（VIX / 美股 / 台指期 / USD-TWD）→ STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK
- 上網查公司業務、產業鏈位置、題材延續性（≥ 1Q 才合格）、龍頭股 / 集團股表現
- 一檔一檔不行（cost 高），**5~10 檔 batch 一次 prompt**

**前置工作**：
- ✅ 新增 `margin_trade` 表 + `etl/finmind_margin_trade_sdk.py`（FinMind `TaiwanStockMarginPurchaseShortSale`；併入 `run_finmind_etl_sdk.py` 為 step 7，non-CRITICAL；2026-04-25 完成。2026-04-27 切換為 dataset-level fetch + 補齊 2026-03-26 ~ 2026-04-24 共 25,168 筆 backfill）
- ✅ 新增 `signal_snapshots` 表（一日一筆 UPSERT；存完整 LLM JSON + cost tracking；2026-04-25 model 完工）
- ✅ 新增 `signal_generation_jobs` 表（job_id / status / progress_pct / current_stage；給前端進度條 polling；2026-04-25 model 完工）
- 🚧 M23 延伸：40 交易日訊號追蹤清單（watchlist 命中歷史、hit count、追蹤第 N 天、報酬率與報告時間軸），spec 在 [docs/plans/m23_signal_archive_spec.md](docs/plans/m23_signal_archive_spec.md)
- 2026-04-30 新增 `signal_watch_completed_archives`：當一檔股票完成一個 40 交易日追蹤 cycle 後，封存 `first_seen_date / hit_count / return_day_10_pct / return_day_20_pct / return_day_30_pct / return_day_40_pct / completed_trade_date`；若未來同檔重新被抓到，會以新的 `first_seen_date` 再新增一列
- 2026-04-30 修正 `signal_watch_returns` 更新口徑：同一檔股票在 active 40 日追蹤 cycle 內的所有 `signal_watch_hits`，都要一起同步 `baseline_trade_date / baseline_price / latest_eval_trade_date / latest_eval_price / return_pct`；不能只更新最新一列，否則第 2 天後部分列會停在 `0%` 或舊值
- 2026-04-30 再補一層 guard：若 `trade_date == baseline_trade_date`（也就是第 2 天建立 baseline 的當天），即使人工或排程同日重跑 `run_signal_archive_returns.py`，`latest_eval_price` 也要維持 `baseline_price`，`return_pct` 必須強制是 `0.0`，不可先用同日收盤價算出正負報酬
- 這類 `40日追蹤` 報酬率修正 deploy 後，必須手動補跑 `backend/run_signal_archive_returns.py` 一次，才會把 DB 內既有 active rows 回補正確
- ✅ `main.py` lifespan 新增 `_ensure_m23_tables()`：自動 idempotent `CREATE TABLE IF NOT EXISTS`（仿 M18/M19 pattern）

**API**：
- `GET /api/signals/latest`（公開）
- `GET /api/signals/snapshot/{date}`（公開）
- `GET /api/signals/jobs/latest`（公開，前端 polling 用）
- `GET /api/signals/quota`（登入後可讀；前端 disable 與剩餘次數顯示用）
- `POST /api/signals/regenerate`（登入即可；每帳號每日 3 次、`failed` 不計次、同日全站 15 次上限、同日 running job 拒絕並發）

**觸發方式：使用者手動**（2026-04-27 改版，原排程已停用）
- 觸發路徑：前端 `DailySignalsPanel`「重新產生」按鈕 → POST `/api/signals/regenerate` → FastAPI `BackgroundTasks` 在 Render web service 直接執行 pipeline
- `.github/workflows/daily_signals.yml`：cron 已移除；保留 `workflow_dispatch` 作管理備援（例如 prod backfill 或 Render background task 暫不可用時用 `gh workflow run` 補跑）
- **Render web service 必須設 `OPENAI_API_KEY` env**（與 GitHub secret 是兩套，frontend 觸發走 Render 不走 Actions runner）

**前端 L0 tab bar UX**（`<DailySignalsPanel />`）：
- 版位：L0 首頁 TradeQualityAnalysis 之後、HotMoneyList 之前
- 4 個 tab：LEADER / FOLLOWER / LAGGARD / REMOVED（顯示各組 count）
- **跳跳跳通知**：localStorage 存 `last_seen_snapshot_date`，比對最新 snapshot 有更新 → header 旁顯示綠色 `animate-ping` 點 + 「新」字；點任一 tab 後寫回 localStorage 取消通知
- **多工背景產生**：點「重新產生」→ POST `/api/signals/regenerate` → 回 202 + job_id → server BackgroundTasks 跑 → 使用者可以離開頁面繼續用其他功能
- **進度條**：留在頁面時 polling `/api/signals/jobs/latest` 每 3 秒一次；顯示 `progress_pct` 與 `progress_label`（例：「正在分析第 28 / 45 檔」）
- **重產額度**：header 讀 `/api/signals/quota` 顯示今日剩餘次數；達每日 3 次時按鈕 disable；若當次 job `failed`，額度自動釋回
- **追蹤入口**：`DailySignalsPanel` header 已提供 `40日追蹤` 入口，進到 M23 訊號追蹤清單頁；頁面目前包含 active summary 與 completed archive 兩張表，completed table 初期無資料時顯示「暫無資料」
- **首頁 bootstrap**：除了 deferred mount，首頁現在還會先集中預抓 `latest trade date`、`latest signals snapshot`、`latest signal job`、`market hot money`、`industries`，再把初始 payload 灌給各 panel，避免 mount 後各元件再各自重打一次
- **首頁 client cache**：前端目前對 `latest trade date`、`/api/industries`、`/api/market/hot-money`、`/api/signals/latest` 做短 TTL client cache；但 `DailySignalsPanel` 在 regenerate 後重抓 snapshot 時必須 `bypassCache`，避免看到剛重產前的 stale 資料
- **首頁 loading UX**：首屏現在有 boot loading overlay，會顯示正在載入哪幾塊資料與總進度；不要回退成只有整頁 skeleton / spinner 而沒有載入語意
- **40日追蹤頁文案**：報酬率規則要直接寫成「第一個交易日抓到 = `--`、第二個交易日用 `(open + close) / 2` 建 baseline 並固定 `0.00%`、第三個交易日起才開始計算報酬率」，避免使用者把「第二天」誤解成「第二次命中」
- 離開頁面再回來：mount 時 polling 自動接上最新進度（不依賴 long-lived connection）

**使用流程**：
1. 早上開首頁 → tab bar 旁有跳跳跳綠點 → 知道有新報告
2. 點 tab 看 LEADER / FOLLOWER / LAGGARD 各組 → reason 一目了然
3. 對某檔有興趣 → 點卡片跳 L2 深入研究
4. 也可以隨時點「重新產生」觸發新一輪分析（背景跑、不擋使用者操作）

---

### M24 自訂進出場策略回測

**你想解決的問題**：買進一檔股票後常常面臨「該攤平 / 該停損 / 該追加碼 / 該落袋」，沒有事先想好的紀律。需要一個工具「驗證自己的操作規則有沒有 edge」，並在當下提供現場判斷。

**第一階段：使用者自訂規則（核心，使用者寫不是 LLM 寫）**

四區塊 form：
- 分層進場：基準買進價 + 下跌加碼階梯（跌 -X1% 加碼 Y1% 資金 …）
- 追價加碼：漲 +A1% 加碼 B1% 資金、漲 +A2% 加 B2% 資金 …
- 停損：絕對價 / 基準 -X% / 跌破 N 日均線（三選一或多）
- 停利：目標價 / 漲幅 +X% / 跌破 N 日均線確認

**第二階段：歷史回測（核心）**

引擎拿這組規則套在該股近 3 年資料，輸出：
- 觸發進場次數、勝率、平均達停利的實際報酬、平均停損實際虧損
- 過程中最大帳面虧損
- 累計報酬 vs Buy & Hold 同期
- equity curve、每層成交點標記、累計投入資金曲線

**回測引擎擴充**（現有 `backend/app/backtest_engine.py` 為 long-only + 單一進場點）：
- 多層分批進場 / 加碼（position sizing）
- 每層獨立成交價 + 累計持倉追蹤
- 停損停利擴充支援絕對價 / 均線條件（既有 `stop_loss_pct` / `take_profit_pct` 為基礎延伸）

**第三階段：LLM 現場判斷（輔助）**

當使用者**已買進**且**價格走到下一個 trigger 點**時，LLM 用當下的籌碼 / 產業 / 技術 / 基本面 / 題材給判斷：

- 情境 A：規則寫「跌 -5% 加碼」今天觸發 → LLM 提示：「外資 5 日連賣、產業熱度退潮、跌破 60 日均線、季 EPS 低於預期 → 建議考慮停損不攤平」
- 情境 B：規則寫「漲 +8% 加碼」今天觸發 → LLM 提示：「法人連買、突破前高 + 量能放大、月營收 YoY +30% → 可加碼，注意短線過熱」

LLM 做：在 trigger 觸發當下，把 deterministic 抓出的籌碼/技術/基本面狀態翻成「適合 / 不適合執行」判斷
LLM 不做：替使用者寫規則、告訴使用者「該買哪檔」、取代回測結果（回測說沒 edge 就不該無腦執行）

**入口**
- `/watchlist/[entry_id]/strategy`（從 watchlist 卡片點「回測操作策略」進入）
- L2 個股頁新 tab「操作策略回測」（沒持股也能玩）

**API / UI**
- API：`POST /api/backtest/custom-strategy`（繼承 M11 既有回測輸出格式 + 新欄位）+ `POST /api/strategy/check-trigger`（trigger 觸發時呼叫 LLM 給現場判斷）
- UI：新元件 `CustomStrategyPanel`（沿用 `BacktestEquityChart`）四區塊 form + equity curve + 分層成交標記 + LLM 現場提示卡片

**目標參數不寫死**
- 使用者自己輸入目標報酬 / 可容忍回撤，回測結果顯示是否達成
- 不預設 25% / 10% 等具體數字（前一版規劃寫死的數字捨棄）

**與 M17 / M19 銜接**
- 從 watchlist 卡片進入時 `buy_price` 自動填入 avg_price
- 從 /stocks/{id} 進入是空白 form

---

### 整體 LLM 定位（橫跨 M23 / M24）

| 角色 | 做什麼 | **不**做什麼 |
|------|--------|-----------|
| **解釋層**（M23） | 翻譯 deterministic 訊號 + 上網查公司業務／集團／龍頭比對；判斷 market_state；產 LEADER/FOLLOWER/LAGGARD 三類 reason | 不預測股價、不出目標價、不排推薦度、不發 BUY/SELL |
| **現場判斷層**（M24） | 在 trigger 觸發當下給 yes/no 提醒 | 不替使用者寫規則、不取代歷史回測 |

決策權永遠在使用者手上：
- M23 篩出清單給看，使用者決定要不要研究
- M24 規則使用者寫、回測算 edge、LLM 在當下提醒，使用者決定要不要按下加碼鍵

## M18 使用者註冊系統完成（2026-04-21）

### 最終範圍（與原規劃差異）
- **Auth**：Email/password 單純註冊登入（**無** Gmail OAuth、無 email 驗證、無密碼重設）。未來要加 OAuth 只需在 `users` 加 `provider` 欄位 + 新 callback
- **Session**：Server-side session（UUID token in httpOnly cookie，30 天過期，可 revoke）；非 JWT、非 localStorage
- **Telegram 綁定**：整個 drop，不做 `user_telegram_bindings`
- **Admin 帳號**：由 `ADMIN_EMAIL` / `ADMIN_PASSWORD` env 設定（必填，未設時 `get_admin_password()` 會 raise；`_seed_admin_user` 啟動時失敗會被 except 吃掉，server 仍會起來但 admin 帳號未 seed）
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

> **狀態（2026-04-22 更新）**：Code 層已於 commit `6deefae` 修復並 push（`finmind_inst_flow_sdk.py` 修法 + `backfill_inst_flow_amount_est.py` + 單元測試），但 **prod DB backfill 尚未執行**，所以 4/10~4/15 的 `inst_stock_flow.buy_amount_est` 仍為 0.0、`industry_daily_flow` 法人金額仍為 0。要完全落地還需執行：
> 1. `python backend/scripts/backfill_inst_flow_amount_est.py`（對 prod 連線）
> 2. `python backend/rebuild_industry_flow.py --from 2026-04-10 --skip-master`
> 3. 補跑 4/20 / 4/21 因配額中斷的 `valuation / monthly_revenue / financial_statement`

### 問題現象
- L0 / L1 產業卡片在切到 FinMind 後，近期日期可能顯示「法人買賣超 +0.0 億」
- 單日 `inst_stock_flow` 有 `buy_shares / sell_shares`，但 `buy_amount_est / sell_amount_est / net_amount_est` 為 `NULL`（或預設 0）

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
- `/login` 前端不能對 login mode 一律套 `minLength=8` / `password.length < 8` 驗證，否則少於 8 碼的既有帳號（含 admin 若 `ADMIN_PASSWORD` 設成短密碼）永遠送不到後端
- 註冊仍維持最少 8 碼；只有登入要允許短於 8 碼的既有帳號

## Render production M18 表沒建起來修復（2026-04-22）

### 問題
- Render 後端 log 顯示 `psycopg.errors.UndefinedTable: relation "users" does not exist`
- 啟動 `_seed_admin_user` 失敗被 `except Exception` 吃掉；`POST /api/auth/login` 500 → 前端看到 `Failed to fetch`
- 根因：`backend/migrate_add_users.py` 從未在 Render 上手動跑過

### 修法（backend/app/main.py）
- `_seed_admin_user` 改成啟動時先 `Base.metadata.create_all(bind=engine, tables=[User.__table__, UserSession.__table__])` 再 seed admin
- `create_all` 是 `CREATE TABLE IF NOT EXISTS`，對既有資料 no-op；不會 DROP / ALTER
- 未來 Render 重啟或新機器部署都會自動 idempotent 建表，不需要人工進 shell 跑 migration
- schema 演進（加欄位、改型別）仍需另外寫 migration，`create_all` 管不了

## 首頁 UX 修復（2026-04-22）

### Navbar 登入按鈕 loading 卡住
- 問題：`AuthProvider` 在 mount 時打 `/api/auth/me`，Navbar 在 `status === "loading"` 只顯示 `…`；Render 冷啟動慢時按鈕長時間看不見
- 修法：[frontend/src/components/Navbar.tsx](frontend/src/components/Navbar.tsx) 把 loading 狀態也顯示「登入/註冊」按鈕，加上 `opacity-70` + `animate-pulse` 小圓點提示仍在載入
- 已登入使用者若誤點，`/login` 頁本身的 `useAuth` 會自動 `router.replace(next)` 跳回首頁，不影響流程

### 產業法人流向預設日期
- 問題：首頁預設 `todayInTaipei()`，台灣白天打開時當日 ETL 還沒跑，顯示空或 resolve 到昨天造成 UI 日期與資料不一致
- 修法：[frontend/src/app/page.tsx](frontend/src/app/page.tsx) 改成先用 `todayInTaipei()` 當 initial state，mount 後打 `fetchLatestTradeDate()`（`GET /api/market/latest-trade-date`）拿 DB 最近有資料的交易日並覆寫；有 `?date=` query param 時尊重使用者選擇，不覆寫

## M22 熱錢湧入個股排行完成（2026-04-22）

### 後端
- [backend/app/hot_money_service.py](backend/app/hot_money_service.py) — L0/L1 共用服務
  - `get_recent_trade_dates(db, end_date, days, stock_ids=None)`：以 `inst_stock_flow.trade_date DESC LIMIT N` 取 N 個交易日（非曆日）
  - `compute_hot_money(db, end_date, days, limit, stock_ids=None) -> HotMoneyResult`：聚合 `inst_stock_flow.net_amount_est`，SQL 層 `inst_type.in_(("foreign","trust","dealer"))` 過濾非三大法人
  - `price_change_pct`：窗口尾日 `close_price` / 窗口首日前一交易日 `close_price` - 1；任一端缺值回傳 `None`
- API endpoints：
  - `GET /api/market/hot-money?date=&days=3&limit=20` — L0 全市場
  - `GET /api/industries/{industry_name}/hot-money?date=&days=3&limit=10&sub_industry=` — L1 單產業
- 共用 pydantic schema：[backend/app/routers/market.py](backend/app/routers/market.py) 的 `HotMoneyResponse` + `serialize_hot_money_result`，由 industries router import 重用避免 duplication
- 測試：[backend/tests/test_hot_money_service.py](backend/tests/test_hot_money_service.py)（9 案例）+ [backend/tests/test_hot_money_router.py](backend/tests/test_hot_money_router.py)（10 案例）全部 pass

### 前端
- [frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) — 共用元件，props `{ industryName?, subIndustry?, date, days?, limit?, title? }`
  - `industryName` 有值 → 呼叫 L1 endpoint，預設 `limit=10`；無值 → L0，預設 `limit=20`
  - 點列跳 `/stocks/{id}?date={date}`
  - useEffect dependency `[industryName, subIndustry, date, days, effectiveLimit]` → **日期改變自動重新 fetch**
- L0 放 [frontend/src/app/page.tsx](frontend/src/app/page.tsx) 最下方（`IndustryDashboard` 下）
- L1 放 [frontend/src/components/StockList.tsx](frontend/src/components/StockList.tsx) 最上方（header 下、主表格上）

### Gotcha（L0 日期同步 bug 修正）
- 初版 page.tsx 的 `defaultDate` 是 `useState` initial value，只在 mount 時讀一次 `queryDate`
- 當使用者透過 `IndustryDashboard` 的 date picker 換日期 → `onDateChange` 把 URL 改成 `?date=X` → `queryDate` 更新，但 `defaultDate` 不會 re-sync，導致 `HotMoneyList` 停在舊日期
- 修法：把 `defaultDate` 改成 **derived value** `queryDate ?? latestTradeDate ?? todayInTaipei()`，`latestTradeDate` 才是 state；任何一邊變動都會讓 `defaultDate` 重算
- L1 的 StockList 內部管自己的 `date` state，靠 `setDate` 更新，HotMoneyList 直接收 prop 無此問題

## M19 關注買進清單完成（2026-04-23）

### 後端
- [backend/app/models.py](backend/app/models.py) — 新增 `UserWatchlist` model：`(user_id, stock_id, buy_date, avg_price)` + UNIQUE `(user_id, stock_id)`
- [backend/app/main.py](backend/app/main.py) — lifespan `Base.metadata.create_all` 自動建表（idempotent，避免 Render 手動 migration）
- [backend/app/routers/watchlist.py](backend/app/routers/watchlist.py) — 4 個 CRUD endpoints（全部 `Depends(require_user)`）：
  - `GET /api/watchlist` → `{ items, total, capacity }`（join `stocks_master` + 最近 `daily_price.close_price`，回傳 `unrealized_pct`）
  - `POST /api/watchlist` → 201；`404` unknown stock / `409` duplicate / `409` at cap（20）
  - `DELETE /api/watchlist/{entry_id}` → 204；僅能刪自己 entry（他人 entry 回 `404`，不洩漏存在性）
  - `DELETE /api/watchlist` → 204 bulk clear
- [backend/tests/test_watchlist_router.py](backend/tests/test_watchlist_router.py) — 14 案例 pass（空清單、auth 缺、add/dup/404/at cap、delete own/other/unknown、clear own only、null price、invalid avg_price）
- 常數 `WATCHLIST_MAX_ENTRIES = 20`

### 前端
- [frontend/src/lib/api.ts](frontend/src/lib/api.ts) — `WatchlistItem` / `WatchlistResponse` 型別 + 4 個 API function；`fetchWatchlist` 於 401 回空 response（匿名時靜默）
- [frontend/src/lib/watchlist.tsx](frontend/src/lib/watchlist.tsx) — `WatchlistProvider` context（mirror `AuthProvider`），exposes `items/total/capacity/has/entryIdOf/add/remove/clear/refresh/isReady`；auth status 變化時自動 refresh
- [frontend/src/components/AppProviders.tsx](frontend/src/components/AppProviders.tsx) — `<AuthProvider><WatchlistProvider>{children}</WatchlistProvider></AuthProvider>` 雙層包裝
- [frontend/src/components/WatchlistAddDialog.tsx](frontend/src/components/WatchlistAddDialog.tsx) — base-ui `@base-ui/react/dialog`（**不是 shadcn CLI**；專案既有 base-ui primitives）；輸入 buy_date（預設台北今天）+ avg_price
- [frontend/src/components/WatchlistAddButton.tsx](frontend/src/components/WatchlistAddButton.tsx) — 共用按鈕，5 狀態：未登入（→ /login）/ 載入中 / 已加入（綠 disabled）/ 已滿 20/20（琥珀，→ /watchlist）/ 可加入（天藍，開 dialog）；`e.stopPropagation()` 防止觸發父層 row 導航
- 入口整合：
  - [frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) — 表格新增「清單」欄（compact variant）
  - [frontend/src/components/StockList.tsx](frontend/src/components/StockList.tsx) — 每張個股卡片右下角（compact variant）
  - [frontend/src/app/stocks/[stockId]/page.tsx](frontend/src/app/stocks/[stockId]/page.tsx) — 個股頁 header 右側（default variant）
- [frontend/src/app/watchlist/page.tsx](frontend/src/app/watchlist/page.tsx) — 新路由（`<RequireAuth>`）：持股卡片顯示買進日/均價/最新收盤/未實現損益 %；右上角 ✕ 單檔移除；頂部「清空清單」→「確定清空？」兩段式確認；每張卡片「交易分析 →」按鈕 `router.push("/?stock_id=XXX&buy_date=YYYY-MM-DD#trade-quality")`
- [frontend/src/components/TradeQualityAnalysis.tsx](frontend/src/components/TradeQualityAnalysis.tsx) — URL prefill：讀 `useSearchParams()`，有 `stock_id`+`buy_date` 時 one-shot 呼叫 `searchStocks` 解析後觸發 `handleAnalyze`；外層 `id="trade-quality" scroll-mt-16` 支援 hash anchor
- [frontend/src/components/Navbar.tsx](frontend/src/components/Navbar.tsx) — 已登入且不在 `/watchlist` 時顯示「我的清單 N/20」

### Gotcha
- **`useSearchParams` 在 Next 16 必須包 `<Suspense>`**：`frontend/src/app/page.tsx` 的 `HomeContent` 已在 Suspense 內；TradeQualityAnalysis 直接在其中使用即可
- **URL prefill 用 key-based ref**：`lastPrefillKeyRef.current = "sid|bd"`，與上次比對；確保同一個 mounted component 下連點多檔不同股票會重新 prefill（不能用布林 one-shot 守門）
- **Prefill 後延後呼叫 analyze**：先 set `selected`，再用 `pendingAnalyze` flag + 第二個 effect 等 state commit 後才呼叫 `handleAnalyze`，避免用 stale `selected=null`
- **UI 不採 shadcn CLI**：專案 dep 只裝 `shadcn` 本體但**所有 UI primitive 都是 base-ui**（`select.tsx` 已採 `SelectPrimitive from "@base-ui/react/select"`）；新 dialog 也用 `@base-ui/react/dialog`，別跑 `npx shadcn@latest add dialog` 污染
- **apiFetch 必帶 `credentials: "include"`**：watchlist API 全走 session cookie；若直接 `fetch` 會變匿名呼叫 → 401
- **刪除他人 entry 回 404 不是 403**：避免枚舉攻擊（existence oracle）
- **卡片停靠按鈕 bubble**：`WatchlistAddButton` / 清單 ✕ 按鈕皆需 `e.stopPropagation()`，否則會觸發 HotMoneyList / StockList row 的 `router.push` 跳到個股頁

### Code review 後補強（2026-04-23）
- `UserWatchlist.user_id` 加上 `ForeignKey("users.id", ondelete="CASCADE")`：未來刪帳號時 watchlist 自動清除，不留孤兒列
- POST 加 `buy_date > _today_taipei()` guard（400 `買進日期不能是未來`），避免 M20 context 被未來日期汙染
- **`_today_taipei()` 以 `Asia/Taipei` 為準**（follow `analysis.py:42` 既有 `TAIPEI_TZ = ZoneInfo("Asia/Taipei")` pattern）：Render server 跑 UTC，若用 `date.today()` 會在台北 00:00~08:00 把使用者選的「今天」誤判為未來；測試用 `monkeypatch.setattr(watchlist_module, "_today_taipei", lambda: date(...))` freeze 時間驗證
- `avg_price` **暫留 Float**（與 `daily_price.close_price` 一致）；M20 加碼建議納入 avg_price 精確運算時一併換 `Numeric(12, 4)`（model column + migration）
- `router/watchlist.py` 檔頭註記：20 檔 cap 為 best-effort，雙 tab 並發可能插到第 21 筆（機率極低、無正確性影響）
- `api.ts` 移除死判斷：`204` 已屬 `res.ok=true`，`&& res.status !== 204` 不會 evaluate，改為單純 `if (!res.ok)`

### Daily ETL target date 跨日 bug 修復（2026-04-23）

**問題**：2026-04-22 的排程跑起來 DB 完全沒新資料，卡在 4/21 → 4/23 之間整天空白。

**根因**：
- `.github/workflows/daily_etl_update.yml` cron `0 15 * * 1-5`（UTC 15:00 = 台北 23:00）
- GitHub Actions cron 常延遲 10~90 分鐘，4/22 實際 UTC 16:12（台北次日 00:12）才 run
- `TARGET_DATE=$(date +%F)` 在 `TZ=Asia/Taipei` 下給**次日**（4/23，尚未開盤）
- FinMind 回空資料 → 新的 holiday short-circuit（2026-04-22 引入）判定為假日 → 7 個 step 全 skip
- 4/22 的資料根本沒被抓過

**修法**：
- `Resolve target date` 改成 `TARGET_DATE=$(date -d '4 hours ago' +%F)`
- 即使 cron 延誤到台北 00:30，往前推 4 小時仍落在當日 20:30 → `date +%F` 給正確的當日
- 手動觸發（`workflow_dispatch`）填 `target_date` input 時尊重之，不受 offset 影響

**Backfill 4/22**：`gh workflow run daily_etl_update.yml --ref main -f target_date=2026-04-22`（run 24828955408）

**Gotcha**：GitHub Actions cron 延遲是常態；任何「跑當日」的 workflow 都要內建足夠 offset buffer，避免邊界日跨天

### M19 上線後 bug 修復（2026-04-23）
M19 merge 之後使用者回報四個問題，一次修掉：

1. **登入後加入清單仍 401「未登入或登入階段已失效」（跨站 cookie）**
   - 根因：[backend/app/auth.py](backend/app/auth.py) 的 `set_session_cookie` 把 `samesite` 寫死 `"lax"`。Production 部署是 Vercel（前端）↔ Render（後端）跨站，Lax cookie 不會被瀏覽器帶到跨站 `fetch()`（即使 `credentials: "include"`），`/api/watchlist` POST 拿不到 session → 401
   - 修法：`samesite = "none" if is_cookie_secure() else "lax"`；production 設 `COOKIE_SECURE=true` 自動切 None，本地 dev（兩端都是 localhost，same-site）繼續 Lax
   - 為何不直接永遠 None：Chrome 拒絕 `SameSite=None` 但 `Secure=false` 的 cookie，本地 dev http:// 會破
   - Regression test：[backend/tests/test_auth_router.py](backend/tests/test_auth_router.py) `test_session_cookie_samesite_follows_secure_flag`

2. **L0 點「加入清單」整列跳到個股頁（click 冒泡）**
   - 根因：[frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) `<TableRow onClick>` 包整列，`WatchlistAddButton` 內的 `e.stopPropagation()` 只擋按鈕本體；點到 `<TableCell>` 邊緣 / padding 時事件仍會冒泡到 row
   - 修法：在「清單」那格 `<TableCell>` 直接加 `onClick={(e) => e.stopPropagation()}`，整格都是安全區
   - 通則：**有 `onClick` 的 row 裡若放互動元素，包住元素的 cell 也要 stopPropagation**

3. **L0 加入清單按鈕太小看不到**
   - 根因：[frontend/src/components/WatchlistAddButton.tsx](frontend/src/components/WatchlistAddButton.tsx) compact variant 原本 `px-2 py-0.5 text-xs`，在深色表格對比太弱
   - 修法：compact 統一改 `inline-flex ... px-3 py-1 text-xs font-medium`；可加入狀態填 sky-600 + 白字強對比；表格欄寬 `w-24 → w-28`

4. **L0 版位重排**
   - [frontend/src/app/page.tsx](frontend/src/app/page.tsx) 順序改為：交易分析 → 熱錢排行 Top 20 → 產業流向

## M21 Trade Quality Context 資料管線完成（2026-04-24）

### Scope
- Phase A：純 context layer + API endpoint + 測試；**不動 M17 既有 prompt / router / 前端**
- 輸出：6 個 section 的結構化 JSON，deterministic + no-hindsight
- 後續 Phase B（獨立任務）才會修 M17 prompt 讓 AI 消費這份 context

### 檔案結構（[backend/app/analysis/](backend/app/analysis/)）
- `context_thresholds.py` — 所有 lookback / threshold 常數（module-level，**無 env override**）
- `industry_signals.py` — PART 1（hot_score / hot_level / price_strength / volume_trend / institution_flow / capital_type / is_false_hot）
- `chip_signals.py` — PART 2（foreign/trust/dealer_buy_days / volume_trend / price_trend / is_accumulation / chip_strength）
- `peer_rank.py` — PART 3（return/volume/institution 三個 top-percentile + leader_or_follower 四條件投票）
- `fundamental_signals.py` — PART 4（revenue_yoy / revenue_mom from `monthly_revenue`；`guidance` 永遠 null）
- `price_structure.py` — PART 5（slope trend / is_breakout 20d / is_consolidation 10d / is_accelerating）
- `news_stub.py` — PART 6（純字串組合，query_stock / query_industry / date_end；**不 query DB**）
- `context_builder.py` — 主入口 `build_trade_quality_context(db, stock_id, buy_date) -> dict`

### 對外 API
- `GET /api/analysis/context?stock_id=<id>&buy_date=<YYYY-MM-DD>` (`backend/app/routers/analysis.py`)
- 認證：`Depends(require_user)`（初版需登入，與 M17 前端入口一致；未來視需要放寬）
- `buy_date` 行為（決策 3b）：未指定時 fallback `get_latest_industry_trade_date(db)`，與 M17 一致
- Raises：
  - `404` unknown stock（`ValueError` from `build_trade_quality_context` → HTTPException）
  - `404` 資料庫無交易日資料（`_resolve_buy_date` 回 None）
  - `401` 未登入

### 關鍵 gotcha
- **Python 3.9 相容**：型別註記不能用 `list[float] | None`，要用 `Optional[List[float]]`（`from typing import List, Optional`）
- **institution_flow 空資料**：回 `"none"` 字串（代表無參與），**不是** `None`（保留 `None` 給 unknown 語義）；否則 `_compute_hot_score` 會把所有 weight 算成 0 而不是正確 flag 為 null
- **is_false_hot 輸入是 price_strength，不是 volume_trend**：spike 檢測（`max(recent_3d) >= baseline × 1.5`）與 volume_trend 分類（3d avg vs baseline）是兩個 orthogonal signals；單一大量的日子會把 3d avg 推進 `expanding_3d`，但不代表不該被標為 false hot
- **no-hindsight**：所有 section 都用 `trade_date <= buy_date`；lookback 皆以**交易日**計（`ORDER BY trade_date DESC LIMIT N`），**非曆日**
- **data_quality_notes 政策**：永遠 null 的欄位（`industry_news_heat` / `guidance`）**不**寫入 notes（決策 4b，避免每次 response 都有噪音）；notes 只在動態缺料（peer 不足、price history < 21 天、monthly_revenue 缺）時才加
- **peer_rank top-percentile convention**：`0.0 = 最強` / `1.0 = 最弱`（產業排名第 1 回 0.0）；leader 判定 4 條件滿足 >= 2 條
- **chip 連續買超日數**：從最新日往前走，碰到非正值 net_shares 就停；無資料時該欄位回 0，不 raise

### 測試覆蓋
- `tests/test_industry_signals.py`（17 案例）
- `tests/test_chip_signals.py`（18 案例）
- `tests/test_peer_rank.py`（8 案例）
- `tests/test_price_structure.py`（13 案例）
- `tests/test_context_builder.py`（11 案例：schema shape / unknown stock raise / notes 組合 / fundamental null / news stub / happy snapshot / deterministic）
- `tests/test_analysis_context_router.py`（5 案例：401 / 200 happy / buy_date fallback / 404 no trade dates / 404 unknown stock）

### 落地計畫與 spec
- 實作計畫：[docs/plans/m21_context_pipeline_implementation.md](docs/plans/m21_context_pipeline_implementation.md)
- 輸出 schema + 門檻說明：[docs/plans/trade_quality_context_spec.md](docs/plans/trade_quality_context_spec.md)

### Review P1 修正：peer_ids 查詢加下界（2026-04-24）
- **問題**：8 個 `stock_id IN (peer_ids) AND trade_date <= buy_date` 查詢缺下界，大產業（半導體 60+ 檔 × 2500+ 交易日 × 8 queries）會搬 10+ 萬列進 Python
- **修法**：新增 [backend/app/analysis/_helpers.py](backend/app/analysis/_helpers.py) 兩個 helper：
  - `fetch_active_peer_ids(db, industry_name)` — 取代 industry_signals / peer_rank 裡各自實作的 `_active_peer_ids`
  - `resolve_query_start_date(db, buy_date)` — 以 `SELECT DISTINCT trade_date FROM daily_price ORDER BY DESC OFFSET (N-1) LIMIT 1` 反推交易日下界（N = max lookback 21 日），自動跳過週末 / 春節長假
- **架構**：`context_builder` 預先算 `peer_ids` + `query_start_date` 各一次，往下傳給 `compute_industry_signals` / `compute_peer_rank`；兩個 entry function 都保留 optional kwargs 預設 None（未提供時自行 compute），向後相容測試
- **8 個加下界的查詢**：
  - `industry_signals.py`：`_industry_price_strength` / `_industry_volume_trend` / `_recent_flow_dates` / `_count_spike_days`
  - `peer_rank.py`：`_peer_returns` / `_peer_volume_ratios` / `_peer_institution_intensity` / `_peer_breakouts`
- **P2 順手處理**：`chip_signals._classify_price_trend` 的 `max_single_day_pct` 加註解說明是雙向絕對值（tests 所有 72 案例 pass）
- **為何用交易日反推而非 calendar offset**：春節長假 calendar offset 會切過頭；trading-day reversal 保證永遠剛好 N 筆資料，不受休市影響

## M21 Phase B：M17 prompt 吃 context pipeline（2026-04-24）

### 改法
- [backend/app/routers/analysis.py](backend/app/routers/analysis.py)：`analyze_trade_quality` 在 `_collect_context` 後新增 `_build_deterministic_context()`，呼叫 `build_trade_quality_context(db, stock_id, buy_date)` 取得 6 section JSON
- `_build_user_message(context, m21_context, warnings)` 新增 `[M21 預聚合訊號（deterministic，結論層）]` 區塊，`json.dumps(..., ensure_ascii=False, indent=2)` 直接序列化 6 section 到 user message
- raw OHLC / 法人從 10 日縮到 **5 日**，前綴「僅供對照」—— 讓 AI 以 M21 結論為主，raw 只做 sanity check；revenue 維持 3 個月
- [backend/app/prompts/trade_quality.md](backend/app/prompts/trade_quality.md) + [docs/trade_quality_prompt.md](docs/trade_quality_prompt.md) 頂部加「輸入格式（M21 預聚合訊號）」說明，明列 7 個 section 語義 + 直接對應 prompt 內「產業熱錢等級 S/A/B/C」「籌碼集中度」「Leader/Follower」等強制規則
- `rating` / `classification` / JSON contract 完全不動，前端不需改

### Fallback 設計
- `build_trade_quality_context` 丟非預期例外（`RuntimeError` 等非 `ValueError`）→ logger.exception + `warnings.append("deterministic 訊號管線暫時不可用...")`，user message 顯示「（不可用：請以下方原始資料自行推論）」
- `ValueError`（stock not found）仍依既有路徑回 404（`_collect_context` 先擋）
- 不阻斷 OpenAI 呼叫，確保 context pipeline 掛掉時 M17 仍能以 raw-only 模式工作

### 測試
- [backend/tests/test_analysis_router.py](backend/tests/test_analysis_router.py) 新增 2 案例：
  - `test_trade_quality_user_message_includes_m21_deterministic_block`：斷言 6 section 關鍵字出現在 user message
  - `test_trade_quality_falls_back_to_raw_when_m21_context_fails`：mock `build_trade_quality_context` 丟 `RuntimeError`，仍應回 200 + warnings 含提示
- 全 tests 結果：44 pass（analysis router + context router + context builder），整 backend suite 370 pass（唯一 fail 是 `test_engine_connects`，worktree 無 sqlite 檔，與本改動無關）

### Gotcha
- **不要在 `_collect_context` 內呼叫 build_trade_quality_context**：兩個 function 有不同錯誤處理契約（raw context 缺資料 → warnings；deterministic pipeline 掛掉 → warning + fallback）；分開才能讓 router 層決定如何 fallback
- **M21 JSON 用 `ensure_ascii=False`**：保留中文產業名稱（`AI 伺服器` 等）避免轉 `\u...` 浪費 token 且失去可讀性
- **`rating` / `classification` 契約不能動**：M19 卡片「交易分析」深連結與前端 Rating 色塊已硬依賴 5 階 + A/B/C，改 prompt 時也禁止變動這兩欄值域
- **raw OHLC 從 10 縮到 5 日**是 Phase B 的刻意設計：M21 已經把價格結構（trend / breakout / consolidation / accelerating）結論化了，raw 只需保留到 AI 能驗證「這 5 天真的在上漲」即可，節省 token 讓更多預算分給 M21 JSON

## M17 SSE 進度條（2026-04-24）

### 背景
- `POST /api/analysis/trade-quality` 整體耗時 5~30 秒（OpenAI 占 80%+），前端僅顯示 spinner + 「系統正在還原當天市場情境…」固定文字，使用者無法知道目前在等什麼。

### 改法
- 後端新增 `POST /api/analysis/trade-quality/stream`：與原 endpoint 同輸入，回 `application/x-ndjson`（line-delimited JSON）
  - 共用 `_collect_context` / `_build_deterministic_context` / `_build_user_message` / `_call_openai` / `_normalize_response`，邏輯零分叉
  - Pre-flight（stock 不存在 / prompt 缺檔 / 未開盤）在 stream 開始前 raise `HTTPException`，讓 4xx/5xx 走正常 HTTP 錯誤通道
  - Generator 依序 yield：`collect_raw` → `build_context` → `openai_call` → `done(payload=jsonable_encoder(TradeQualityResponse))`
  - OpenAI 不可用 → 仍以 `done` event 完成，payload `source="unavailable"`
  - Generator 內部例外 → yield `error` event；前端 throw 對應 `Error`
- 原 `POST /api/analysis/trade-quality` **保留**（M19 watchlist 深連結走的是非 stream 版，不需要進度條）
- 前端 [frontend/src/lib/api.ts](frontend/src/lib/api.ts) 新增 `streamTradeQuality(payload, onEvent, options)`：用 `fetch().body.getReader()` + `TextDecoder` 解析 NDJSON；最終 throw 或回 `TradeQualityResponse`
  - 同檔案的舊 `analyzeTradeQuality` 已無 caller → 一併刪除
- 前端 [frontend/src/components/TradeQualityAnalysis.tsx](frontend/src/components/TradeQualityAnalysis.tsx) 把 `analyzeTradeQuality` 換成 `streamTradeQuality`：
  - 新增 `progressStage` / `progressLabel` state，每收到一個 event 就更新
  - Loading UI 從 spinner + Skeleton 改為「label + 百分比 + emerald 進度條」；stage→% 對照：collect_raw 15 / build_context 35 / openai_call 60 / done 100

### Gotcha
- **NDJSON 不是 SSE**：用 `application/x-ndjson` 而非 `text/event-stream`，因為前端只需要單向收 event，不需要 EventSource 的 reconnect / event-name 機制；NDJSON 解析簡單、TestClient 也能直接 split lines 驗證
- **`jsonable_encoder` 取代 `.dict()`**：Pydantic v1/v2 序列化方法不同；`jsonable_encoder` 是 FastAPI 通用安全做法，避免 `date` / `datetime` 序列化坑
- **Pre-flight vs in-stream 例外**：stock 找不到一定要在 stream 開始前 raise，否則 HTTP 200 + done event with error payload 在前端 fetch 邏輯比較難區分
- **`_STREAM_HEADERS` 必加**（`X-Accel-Buffering: no` + `Cache-Control: no-cache`）：Vercel ↔ Render 中間 nginx 預設會 buffer 整段 response，NDJSON 進度會被攢一起送 → progress bar 跳一下就到 done，UX 等於沒做。本地 dev 不會察覺差異，prod 才看得出來。兩個 `StreamingResponse`（market_closed_stream + main generate）都要加
- **Generator 內不可 raise HTTPException**：headers 已 commit，raise 不會變 4xx，只會變成 broken stream（前端 reader 看到 EOF 而不是錯誤訊息）。所有預期 4xx 路徑必須在 pre-flight 檔下；generator 內的 `except` 統一 emit `error` event 給前端
- **進度百分比是視覺提示，不是真實進度**：OpenAI call 60% 一段會「卡」最久（5~25 秒），最後一口氣跳到 100%；這是刻意設計（avoid fake animated progress），label 同步更新即可

## M23 slice 4：signals/ 模組骨架完成（2026-04-25）

### Scope（10 切片中的第 4 片）
- 對應 spec §14：建立 `backend/app/signals/` 7 個模組的「契約面」（簽章 + docstring + stub）
- 兩個模組**完整實作**：`exclusions.py`（純規則）+ `pipeline.py`（status 流轉 / progress / UPSERT）
- 四個模組**簽章 + stub**：`candidate_pool.py` / `classification.py` / `filters.py` / `llm_caller.py`（slice 5/6 各自填）
- 一個資料檔：`group_stocks.json`（5 大集團白名單）

### 落地檔案
- [backend/app/signals/__init__.py](backend/app/signals/__init__.py) — 模組總覽 + 對應 spec 章節 + re-export `run_signal_pipeline_sync`
- [backend/app/signals/exclusions.py](backend/app/signals/exclusions.py)（**完整**）：
  - 8 個 helper：`is_etf` / `is_financial` / `is_blacklisted` / `should_exclude` / `load_group_stocks` / `find_group_for_stock` / `get_group_members` / `get_group_leader`
  - ETF 規則 `^00\d{2,}$` 或名字含 `ETF / 指數型基金 / 指數股票型`；金融規則 `industry_name` 含 `金融 / 銀行 / 保險 / 證券`
  - `EXCLUSION_BLACKLIST: Set[str] = set()`（手動黑名單，第一版空）
  - `_GROUP_STOCKS_CACHE` module-level 快取，`load_group_stocks(force_reload=True)` 可強制重讀
  - `_meta` 開頭的 key 自動過濾（不會出現在 group dict）
- [backend/app/signals/group_stocks.json](backend/app/signals/group_stocks.json) — 5 大集團（鴻海 / 台塑 / 國巨 / 聯電 / 聯發科），每組 `leader` + `members`，`leader` 必須在 `members` 內
- [backend/app/signals/candidate_pool.py](backend/app/signals/candidate_pool.py)（stub）：3 個函式 `ingest_data` / `compute_rankings` / `build_candidate_pool`，slice 5 填
- [backend/app/signals/classification.py](backend/app/signals/classification.py)（stub）：`PRELIM_TYPE_LEADER/FOLLOWER/LAGGARD_CANDIDATE` 常數 + `classify_stocks`，slice 5 填
- [backend/app/signals/filters.py](backend/app/signals/filters.py)（stub）：4 個 `HINT_*` 常數 + `apply_hard_exclusions` / `apply_soft_filters`，slice 5 填
- [backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)（stub）：`DEFAULT_BATCH_SIZE = 8` / `DEFAULT_MODEL = "gpt-4o-search-preview"` + 4 個函式 `run_research_batch` / `run_explanation_batch` / `assemble_market_context` / `assemble_final_output`，slice 6 填
- [backend/app/signals/pipeline.py](backend/app/signals/pipeline.py)（**完整**）：
  - 7 stage 常數對齊 `models.SignalGenerationJob.current_stage` enum：`STAGE_INGEST/RANK/CANDIDATE/FILTER/LLM_RESEARCH/LLM_EXPLAIN/PERSIST`
  - `run_signal_pipeline_sync(job_id, target_date, *, session_factory=None)` — cron / BackgroundTasks 共用入口
  - `_set_progress(db, job, *, status, stage, pct, label)` — 每 stage 結束 commit 一次（前端 polling 即時看到）
  - `_mark_done` / `_mark_failed`（先 `db.rollback()` 清 session error state，再 re-fetch job 寫狀態）
  - `_persist_snapshot` — `(snapshot_date)` UPSERT：existing 則 setattr 覆蓋 + 更新 `generated_at`，無則 `db.add(SignalSnapshot(...))`
  - LLM Research stage 為 batched loop（spec §5 Step 7：「一次 prompt 處理 5~10 檔」），每 batch commit 一次 progress
  - try/except 包整段：失敗時 `_mark_failed` 寫 traceback[:2000] 後 **re-raise**（讓 caller 紀錄；測試也能 `pytest.raises`）

### 測試
- [backend/tests/test_signals_exclusions.py](backend/tests/test_signals_exclusions.py)：19 案例
  - autouse fixture `_reset_group_stocks_cache` 清 module cache，避免測試殘留
  - ETF / 金融 / 黑名單 / `should_exclude` 整合 / `group_stocks.json` 載入正確性 / leader-member 一致性
- [backend/tests/test_signals_pipeline.py](backend/tests/test_signals_pipeline.py)：6 案例
  - `session_factory` fixture 用 in-memory SQLite + `Base.metadata.create_all` per test
  - `_stub_all_stages_noop(monkeypatch)` 把全部 stage function 換成 noop（happy path）
  - 失敗路徑：第一個 stage 拋 `NotImplementedError`、filter stage 拋 `RuntimeError` 中間掛掉、`job_id` 不存在 `ValueError`
  - Happy path：status=done + progress_pct=100、payload 欄位寫入 SignalSnapshot、同日重跑 UPSERT 不違反 unique
- 全 backend suite：413 pass，1 pre-existing fail（`test_engine_connects` worktree 沒 sqlite 檔）+ 5 pre-existing errors（`test_finmind_sdk_integration` 需 API token），與 slice 4 無關

### Gotcha
- **`monkeypatch.setattr(module, "name", value)` 預設 `raising=True`**：被 patch 的 attribute 必須先存在於 module，否則 `AttributeError`。所以 `llm_caller.assemble_final_output` 雖然 slice 6 才實作，slice 4 也**必須先放 stub**（簽章對齊 pipeline 的呼叫）才能讓測試 monkeypatch 成功
- **`_mark_failed` 必須先 `db.rollback()`**：上一個 stage 拋例外後 session 處於 error state，不 rollback 直接 commit 會把整個 transaction 噴掉；rollback 後再 `db.get(SignalGenerationJob, job_id)` re-fetch（不能用例外前抓的 ORM instance，已經 detached）
- **stage progress commit 在 stage 開始前**：前端 polling 看到 `current_stage=filter / pct=30` 表示「正在跑 filter」；若 filter 拋例外，DB 仍保留這個進度（test_pipeline_marks_failed_when_filter_stage_raises 驗證）讓使用者看得到失敗點
- **pipeline 不能用 request session**：spec §11.5 明確要求；本實作預設 `SessionLocal` 從 `app.database` import，測試傳 `session_factory=in_memory_factory`
- **`_persist_snapshot` 用 `(snapshot_date)` 當 key UPSERT 不是 `(snapshot_date, job_id)`**：spec 設計每天一份 snapshot，重跑會覆蓋；測試 `test_pipeline_upserts_existing_snapshot_on_rerun` 驗證重跑後 `job_id` 已更新為最後一次
- **stage function 全 raise NotImplementedError**：slice 4 跑真實 pipeline 會在 stage 1 ingest 即 failed，這是預期行為；測試靠 monkeypatch 替換為 noop 才能覆蓋 happy path

### 下一步（slice 5）
- 填 `candidate_pool.py` / `classification.py` / `filters.py` 的 deterministic 規則
- 接 `daily_price` / `inst_stock_flow` / `industry_daily_flow` / `margin_trade` / `daily_valuation` / `monthly_revenue` 算 hot_score / 法人連買日 / soft hint 等
- slice 6 才接 OpenAI（`llm_caller`）

## M23 slice 5：deterministic filter 三層完成（2026-04-26）

### Scope（10 切片中的第 5 片）
- `candidate_pool.py` / `classification.py` / `filters.py` 三模組從 stub 換成完整實作
- 對應 spec §6（候選池）/ §7（LEADER/FOLLOWER/LAGGARD 預分類）/ §9（hard exclusions + soft filters）
- 全 deterministic、純規則；slice 6 才接 OpenAI（LLM research / explanation）

### 落地檔案（覆蓋 stub）
- [backend/app/signals/candidate_pool.py](backend/app/signals/candidate_pool.py)（~600 行）：
  - 三函式 `ingest_data` / `compute_rankings` / `build_candidate_pool`，依 spec §5 step 1-4 串接
  - 候選池來源 union：top_stocks_3d 40 + top_industries_3d 10 成分股 + 熱門產業龍頭 + 同產業同 sub_industry 擴散 + 集團股（`exclusions.load_group_stocks`）
  - 每檔股票算 `industry_count` / `industry_rank_5d` / `industry_rank_net_3d` / `consecutive_buy_days_3d` / `volume_5d_to_60d_ratio` / `price_change_3d/5d/1d` / `total_institution_flow_1d/3d/5d` / `margin_change_3d` / MA5 / MA10 / OHLC / volume ratios（給 §7 §9 用）
  - 常數：`TOP_INDUSTRIES_LIMIT=10`、`TOP_STOCKS_LIMIT=40`、`TOP_STOCKS_INNER=10`、`POOL_SOFT_TRIGGER=150`、`POOL_HARD_LIMIT=120`
  - 軟上限超過 → 依「LEADER candidate（rank 高） > FOLLOWER candidate > 其他」截斷至 hard limit
- [backend/app/signals/classification.py](backend/app/signals/classification.py)（~200 行）：
  - LEADER：`industry_rank_5d` 前 30%（`ceil(count * 0.3)`）+ `industry_rank_net_3d` 前 20% + `consecutive_buy_days_3d >= 2` + `volume_5d_to_60d_ratio >= 1.5`
  - FOLLOWER：同產業已有 LEADER + `0 < price_change_5d < leader_gain × 0.7` + `total_institution_flow_3d > 0`
  - LAGGARD_CANDIDATE：guard（同產業 LEADER 漲 ≥ 5%）+ 4 條件中 hits ≥ 2（gap ≥ 5pct / net_1d>0 OR vol_1d_to_5d>1.2 / 站上 5MA OR 10MA；guard 自身已算 1 hit）
  - 三類都不符 → **剔除**（不原地保留）
- [backend/app/signals/filters.py](backend/app/signals/filters.py)（~210 行）：
  - Hard exclusions（直接剔除）：ETF / 金融 / 黑名單（`exclusions.should_exclude`）+ `total_institution_flow_5d < 0` 但**非** LAGGARD + `price_change_3d > 15%` + `avg_turnover_5d < 5e7`
  - Soft filters（標 hint，不剔除）：`HINT_WEAKENING` / `HINT_RETAIL_OVERHEATED` / `HINT_DISTRIBUTION` / `HINT_RANGE_BOUND`，多條件可同時命中
  - distribution 包兩條件（爆量不漲 / 高檔長上影），命中其一即算

### 測試
- [backend/tests/test_signals_candidate_pool.py](backend/tests/test_signals_candidate_pool.py)：13 案例（in-memory SQLite，seed 全市場 master/price/flow，驗證 ingest / rank / pool 正確；用 monkeypatch 把 `POOL_SOFT_TRIGGER=5` / `POOL_HARD_LIMIT=3` 模擬截斷）
- [backend/tests/test_signals_classification.py](backend/tests/test_signals_classification.py)：21 案例（template helper + override pattern；LEADER 4 條件各別 fail / FOLLOWER paired with LEADER / LAGGARD 2 hits 各種組合 / 整體優先序 / 多 leader 取 max gain）
- [backend/tests/test_signals_filters.py](backend/tests/test_signals_filters.py)：23 案例（hard exclusion 各條件、邊界 15% 不算、None 視為缺資料；soft filter 各 hint 個別觸發 + 不觸發 + 多重觸發；不修改原 dict）
- 全 backend suite：470 pass、1 pre-existing fail（`test_engine_connects` 是 worktree 沒 sqlite 檔，非 slice 5 影響）

### Gotcha
- **FOLLOWER vs LAGGARD 重疊**：`price_change_5d=0` 時 FOLLOWER 失敗（要求 > 0），但會落入 LAGGARD（gap = leader_gain - 0 通常 ≥ 5pct）。測試應斷言 `prelim_type != FOLLOWER`，**不可斷言 `stock_id not in result`**，否則 LAGGARD 也算 in result 會誤判。同 issue 在 `test_follower_dropped_when_3d_flow_not_positive`
- **`_is_top_pct` 邊界用 `ceil`**：`industry_count=10`、`pct=0.3` → threshold `ceil(10*0.3) = 3`，rank=4 不通過；`pct=0.2` → threshold 2，rank=3 不通過。`industry_count=0` 視為失敗（避免 div by zero）
- **distribution 高檔長上影公式**：`high - close > (close - open) × 2 AND close < high × 0.97`。紅 K（body 為負）時 inequality 自動成立，配合 close < high × 0.97 仍能正確抓到「紅 K + 拉回」的派發 pattern（不額外加紅 K guard）
- **soft filter 不修改原 dict**：用 `{**c, "soft_hints": hints}` shallow copy；`apply_soft_filters` 不可 mutate input（pipeline 可能對候選池有其他引用）
- **hard exclusions 用候選池欄位即可**：`db / target_date` 暫保留簽章但不查 DB，因為 `should_exclude` + 其他條件全部用 candidate_pool 算好的欄位

### 下一步（slice 6）
- 填 `llm_caller.py`：`run_research_batch` / `run_explanation_batch` / `assemble_market_context` / `assemble_final_output`
- 接 OpenAI `gpt-4o-search-preview`（spec §5 step 7-8）
- batch 5~10 檔一次 prompt（成本控制）

## M23 slice 6：llm_caller.py 完整實作（2026-04-26）

### Scope（10 切片中的第 6 片）
- `llm_caller.py` 從 stub 換成完整實作；接 OpenAI `gpt-4o-search-preview`（支援 web search）
- 對應 spec §3.2 / §5 step 0+7+8+9 / §10 LLM I/O contract
- 全 mock 單元測試覆蓋（不打真實網路、不依賴 API key）

### 落地檔案
- [backend/app/prompts/watch-list-stock.md](backend/app/prompts/watch-list-stock.md) — 525 行 buy-side 分析師 prompt 從 main 專案複製進 worktree（spec §10 全文 I/O contract + 13 點 reason 寫作規則）
- [backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)（~470 行）：
  - 4 個 public function 簽章與 pipeline.py 對齊（slice 4 預先放 stub 才能讓測試 monkeypatch 成功）
  - `assemble_market_context(db_market_snapshot, *, model)` — Step 0：判斷 STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK
  - `run_research_batch(stocks_batch, market_context, *, model)` — Step 7：上網查公司業務 / 產業鏈 / 題材 / 集團 / 龍頭，輸出 `type` + `business_summary` + `theme` + `group_info` + `leader_check`
  - `run_explanation_batch(research_results, market_context, *, model)` — Step 8：依 market_state gating → `signals` + `decision` (WATCH/REMOVE) + 500–1000 字 reason；caller 不需自己分 batch（內部依 `DEFAULT_BATCH_SIZE` 拆 chunk）
  - `assemble_final_output(market_context, explanation, *, candidate_pool_size, model, total_tokens)` — Step 9：拆 watchlist / removed、計算 `summary` 4 欄、組裝 spec §10.2 完整 schema
  - `_call_llm_json(system_prompt, user_msg, *, model)` 內部統一入口；`_extract_json` 容錯解析（去 ` ```json ... ``` ` markdown fence）；4 個 fallback function 對應 4 種失敗路徑

### 常數
- `DEFAULT_BATCH_SIZE = 8`（spec §5 Step 7「5~10 檔/批」中位數）
- `DEFAULT_MODEL = "gpt-4o-search-preview"`
- `_MAX_OUTPUT_TOKENS = 8000`（reason 500-1000 字 × 8 檔 batch 預留充足）
- `_PROMPT_PATH = backend/app/prompts/watch-list-stock.md`

### 測試
- [backend/tests/test_signals_llm_caller.py](backend/tests/test_signals_llm_caller.py)：26 案例
  - `_extract_json` 6 案例（plain JSON / fence with lang / fence without lang / garbage / empty / whitespace）
  - `assemble_market_context` 5 案例（happy / fence / api_key 缺 / invalid JSON / OpenAI 例外）
  - `run_research_batch` 5 案例（empty / 對齊 / 缺檔 fallback / 整體失敗 / 缺 research key）
  - `run_explanation_batch` 5 案例（empty / decision+reason / chunk 分批 / 整體失敗 / 缺檔 fallback）
  - `assemble_final_output` 5 案例（split / summary count / total_tokens / empty / unknown decision treated as remove）
- 全 backend suite：496 pass（slice 5 470 + slice 6 26）、1 pre-existing fail（`test_engine_connects` worktree 沒 sqlite 檔）

### Gotcha
- **`gpt-4o-search-preview` 不支援 `temperature` 與 `response_format=json_object`**：跟 M17 `_call_openai` 用法不一樣；本實作 `_call_llm_json` 只傳 `model / messages / max_completion_tokens`，靠 prompt instruction 「JSON only, no markdown fence」+ `_extract_json` 防禦性解析
- **fallback 預設 `decision=REMOVE`**（保守）：LLM 不可用時不該誤標 WATCH；fallback dict 標 `_unavailable: True` 給 traceability
- **stock alignment by `stock_id` / `stock` key**：LLM 回應順序可能與輸入不同，缺檔需走 fallback 補齊；用 `by_id` dict 對齊
- **`run_explanation_batch` 內部分批**：caller 可一口氣傳 60+ 檔進來，不用自己 chunk；測試 `test_run_explanation_batch_chunks_by_default_batch_size` 用 `monkeypatch.setattr(llm_caller, "DEFAULT_BATCH_SIZE", 4)` + 13 檔驗證 4 次 LLM call
- **System prompt 每次 LLM call 都重新 `_load_system_prompt()`**：方便編輯 prompt 不用重啟 server；FAQ 性能：FS read 一次成本可接受、且第一版 prompt 525 行不大
- **markdown fence 移除 logic**：`_extract_json` 先 `strip`、startswith `\`\`\`` 時找第一個 `\n` 切掉開頭、endswith `\`\`\`` 切結尾；防 LLM 偶發加 ` ```json ... ``` ` 包裝；無 fence 時直接 `json.loads`
- **slice 4 pipeline 測試需更新**：`test_pipeline_marks_failed_when_first_stage_raises_not_implemented` 名稱不再準確（slice 5 ingest_data 已實作；slice 6 llm_caller 也不再 raise NotImplementedError），改為 `test_pipeline_marks_failed_when_ingest_stage_raises` 並用 monkeypatch 注入 `_boom`，驗證一樣的失敗路徑契約

### 下一步（slice 7~10）
- slice 7：`run_signal_pipeline_async` BackgroundTasks wrapper + `/api/signals/regenerate` rate limit + concurrency guard
- slice 8：`/api/signals/latest` / `/api/signals/snapshot/{date}` / `/api/signals/jobs/latest` 三個公開 GET endpoint
- slice 9：`.github/workflows/daily_signals.yml` cron 03:00 台北 + smoke test
- slice 10：前端 `<DailySignalsPanel />` L0 tab bar UX + pulse 通知 + 進度條 polling

## M23 slice 7 API endpoints + cron entrypoint 完成（2026-04-26）

落在 branch `claude/angry-cerf-8755da`。將 spec §11 的 4 個 endpoint 與 §11.6 的 cron 入口整合進 FastAPI app；slice 8/9（前端 + workflow）獨立進行，不在本切片範圍。

### 落地檔案
- [backend/app/routers/signals.py](backend/app/routers/signals.py) — 新 router；4 個 endpoint：
  - `GET /api/signals/latest`（公開；DB 無 snapshot → 404 `No snapshot yet`）
  - `GET /api/signals/snapshot/{snapshot_date}`（公開；無 → 404）
  - `GET /api/signals/jobs/latest`（公開；無 job → **回 null（200）**，不 404，前端少寫一個分支）
  - `POST /api/signals/regenerate`（`Depends(require_user)` → 401／同日 running job → 409／user 同日 ≥10 → 429／全站同日 ≥10 → 429／成功 → 202 + `{job_id, snapshot_date}`，`BackgroundTasks` 排程 `_run_pipeline_safely`；2026-04-27 從 1/5 放寬到 10/10）
- [backend/run_daily_signals.py](backend/run_daily_signals.py) — cron 入口（spec §11.6）；4h offset 推算 target_date；建 `SignalGenerationJob(triggered_by="cron")` 後 inline 同步跑 pipeline
- [backend/app/main.py](backend/app/main.py) — `from app.routers import (..., signals, ...)` + `app.include_router(signals.router, prefix="/api")`

### Pydantic schema（spec §10.3 + §11.3）
- `SnapshotResponse`：`{ snapshot_date, generated_at, llm_model, data: { market_context / watchlist / removed / summary / candidate_pool_size / final_watchlist_size } }`
- `JobResponse`：`{ job_id, snapshot_date, status, current_stage, progress_pct, progress_label, started_at, finished_at, error_message }`
- `RegenerateAcceptedResponse`：`{ job_id, snapshot_date }`

### 限頻 / concurrency 實作
- 全部走 DB COUNT/SELECT，**沒接 slowapi**（spec §11.4 明寫 in-memory by user_id + snapshot_date，但 DB 查就夠用、且 cron job 也算進全站 10/day 額度，不需要 slowapi 的進階 key 機制）
- 常數 `USER_DAILY_REGENERATE_LIMIT=10` / `GLOBAL_DAILY_REGENERATE_LIMIT=10` 集中在 `signals.py` 頂部（2026-04-27 從 1/5 放寬到 10/10，給 prod 測試 / admin 重產彈性）
- 同日 user 額度與 concurrency guard **平行檢查不同條件**：concurrency 看 `status in ("pending","running")`，user 限頻看「不論成敗都計 1」（避免 user 連按 N 次都失敗也不 reset）

### Cron entrypoint exit code（spec §11.6）
- `0=ok / 1=no_data / 2=llm_error / 3=db_error`
- 例外分類靠訊息關鍵字：`"no candidate" / "no data" / "no trade"` → 1；`"openai" / "llm" / "prompt"` → 2；其他全部 → 3
- `_resolve_target_date_from_now()` 用 `Asia/Taipei` + `now - 4h` 推 `.date()`；保證即使 GitHub Actions cron 延遲到 04:00~06:00 仍 resolve 為昨日
- argv 第一個位置可手動覆寫 `YYYY-MM-DD`

### Pipeline 注入點
- BackgroundTasks 包 `_run_pipeline_safely(job_id, target_date)`：catch 所有 exception 不讓 worker crash；pipeline 自身會把 `job.status="failed"` + `error_message` 寫進 DB，所以這層只 log
- 餵 `session_factory=SessionLocal` 給 `run_signal_pipeline_sync`（spec §11.5：不能用 request session）

### 測試
- [backend/tests/test_signals_router.py](backend/tests/test_signals_router.py) — 15 案例（latest 404 / latest happy / snapshot 404 / snapshot happy / snapshot bad date 422 / jobs/latest null / jobs/latest happy / regenerate 401 / 202 happy + DB job + background call / 409 concurrency / 429 user / 429 global / fallback today / `_resolve_target_date` 兩個 unit）
- [backend/tests/test_run_daily_signals.py](backend/tests/test_run_daily_signals.py) — 6 案例（argv 解析 / 4h offset mock / 三類 exit code 分類 / ValueError fallback）
- 全 132 個 signal-related 測試 + 全 backend suite 517 pass（與 slice 6 baseline 一致）

### Gotcha
- **`_run_pipeline_safely` 必須 monkeypatch**：router test 用 in-memory SQLite + dependency_overrides，但 `run_signal_pipeline_sync` 內呼叫 `SessionLocal()` 會走預設連線而非測試 engine，所以測試直接攔截 `_run_pipeline_safely` 紀錄 `(job_id, target_date)` 而不真跑
- **fallback target_date 用今天 + DB 計次仍在當天**：DB 完全空時 `_resolve_target_date()` 回 `date.today()`，user 10/day 與全站 10/day 仍按「今天」計；cron 第一次部署到空 DB 時也能正常觸發
- **regenerate 第二次 429 user 限頻測試**：第一次成功後 job 是 `pending` 狀態，會卡住第二次的 concurrency guard（409）；測試需要先把它標 `done` 才能驗證 user 限頻 429
- **path param `snapshot_date` 型別解析失敗回 422**：FastAPI 對 `date` 型 path param 自動 422，不是 400；測試 `test_snapshot_invalid_date_format_returns_422` 鎖這個合約
- **jobs/latest 用 `Optional[JobResponse]` + 回 None**：Pydantic 序列化 None → `null`；前端直接 `if (!job)` 判斷，不需要 try/catch 404

### 下一步（slice 8~10）
- slice 8：前端 `<DailySignalsPanel />` L0 tab bar UX + pulse 通知 + 進度條 polling
- slice 9：`.github/workflows/daily_signals.yml` cron 03:00 台北 + smoke test
- slice 10：手動觸發驗證 prod，沒問題後等 cron 03:00 自動跑

## M23 slice 8 + 9：前端 panel + GitHub Actions workflow（2026-04-26）

落在 branch `claude/angry-cerf-8755da`，與 slice 7 同 branch（slice 7~9 一起 merge 上 main 才能讓 cron 跑得起來）。

**Why**：spec §13 前端 L0 tab bar UX + spec §12 GitHub Actions 排程；前者是 user-facing 入口、後者是每日自動產生 snapshot 的觸發器。slice 7 完成後對外有 API 但「沒有人會去打」、cron 也沒接 → 必須 8/9 同時上線才算可用閉環。

**How to apply**：
- 修進度條樣式 / pulse 動畫 → 動 `frontend/src/components/DailySignalsPanel.tsx`（單一檔案、無拆分子元件）
- 修 polling 間隔 → 動 `frontend/src/lib/useSignalJobPolling.ts` 頂部 `POLL_INTERVAL_MS = 3000`；錯誤 backoff 是 `* 2`（6 秒）
- 改 cron 時間 → 動 `.github/workflows/daily_signals.yml` `cron: '0 19 * * 1-5'`（UTC，= 台北 03:00 週二~週六）
- 改 retry 邏輯 → 不要學 `daily_etl_update.yml` 加 `sleep 5400` retry，因為 LLM 失敗用 retry 通常還是會掛（不像 quota 重試會解）；signal pipeline 失敗（exit 2/3）直接 fail workflow，靠 user 點「重新產生」處理

**前端結構**（`DailySignalsPanel.tsx`）：
- header：折疊鈕（▸/▾，預設 collapse）+「今日異常訊號清單」+ pulse badge（有新訊號時）+ snapshot_date / generated_at
- 進度條：`useSignalJobPolling()` 回傳 `job` 為 `pending`/`running` 時顯示，progress_pct + progress_label
- 4 tabs（base-ui）：LEADER / FOLLOWER / LAGGARD / REMOVED，每個 tab 顯示對應 count
- SignalCard：股票連結 + decision badge（LEADER 綠 / FOLLOWER 藍 / LAGGARD 琥珀）+ 產業/子產業 + 主題 + 訊號 chips（資金/籌碼/融資券/技術）+ reason 中文白話
- RemovedCard：紅色 REMOVED 徽章 + 排除原因
- 「重新產生」按鈕 5 狀態（spec §13.5）：未登入 disabled「重新產生（需登入）」/ running disabled「產生中…」/ 送出中「送出中…」/ 載入中 disabled / 可觸發 enabled「重新產生」
- 點任何 tab 或展開 panel → 寫入 `always-stock:signals:last_seen_snapshot_date` → 清掉 pulse
- 折疊狀態存 `always-stock:signals:collapsed`（預設 collapse）

**GitHub Actions workflow**（`daily_signals.yml`）：
- 觸發：cron `0 19 * * 1-5`（UTC = 台北 03:00 週二~週六）+ workflow_dispatch（吃 `target_date` input）
- target_date：吃 input 優先；沒帶則 `date -d '4 hours ago' +%F`（同 daily_etl_update.yml 防 cron 跨日）
- timeout 90 min（LLM 60~120 檔 × ~20s/檔 ≈ 30~60 min）
- env：`DATABASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL`（fallback `gpt-4o-search-preview`）
- exit code 對應：0/1 → workflow pass（1 = no_data 為合理結果，週末或無候選池）；2/3 → workflow fail（LLM / DB 錯誤需人工介入）
- 不做 `daily_etl_update.yml` 的 sleep+retry：FinMind quota 等 1.5h 會解、OpenAI 失敗多半是模型/prompt 問題 retry 沒用

**Gotcha**：
- **無 `node_modules` 在 worktree**：本切片 frontend 改動沒跑 `npx tsc --noEmit` / `next build`；type 錯誤靠 PR CI / vercel preview 抓。Component 用的型別都是 `frontend/src/lib/api.ts` 既有 export，型別契合度應該高
- **base-ui Tabs.Panel `value` prop**：base-ui 用 `value` 比對 `Tabs.Root` 的 `value` 決定哪個 panel 顯示；不是 shadcn `data-state="active"`。`TabsContent` (= `TabsPrimitive.Panel`) 接 `value` 自動切換
- **`fetchLatestSignalSnapshot` 404 → null**：M23 slice 7 的 endpoint 第一次 deploy 時 DB 無 snapshot，前端不能 throw、要顯示「目前尚無訊號清單」；`api.ts` 的 helper 已實作 404 → null
- **localStorage 永遠包 try/catch**：SSR + 隱私模式 + iframe 都可能噴；用 `try { window.localStorage.getItem(...) } catch { /* ignore */ }`
- **Polling cleanup**：`useSignalJobPolling` 用 `cancelledRef` + `clearTimeout(timer)`，unmount / `bumpKey` 變動時都會中斷；點「重新產生」後 `setBumpKey((k) => k + 1)` 觸發 effect 重啟（沒有 long-lived connection）
- **`job.progress_pct` 可能 > 100 / < 0**：前端用 `Math.min(100, Math.max(0, x))` clamp；後端 pipeline 寫入時雖然應該 0~100 但 UI 不能假設

**slice 10（最後一片）**：
- 部署後手動 `gh workflow run daily_signals.yml --ref main -f target_date=2026-04-25` 觸發一次
- 觀察 Render log + DB 寫入 `signal_snapshots` / `signal_generation_jobs`
- 開首頁 `https://...vercel.app/`（已登入帳號）→ 看 panel 是否能展開、訊號是否顯示、pulse 動畫是否運作
- 沒問題就等 cron 03:00 自動跑（週二~週六）

## M23 slice 11：code review patches（2026-04-26）

slice 1~9 完成後 review 出 3 個小瑕疵，集中於 slice 11 修掉，讓整條 pipeline 真正可上 prod。

**修法 1：`llm_caller.DEFAULT_MODEL` 吃 `OPENAI_MODEL` env**
- 原本 hardcode `DEFAULT_MODEL = "gpt-4o-search-preview"`，workflow 雖然 export 了 `OPENAI_MODEL` 但 `llm_caller` 從未讀取
- 修法（[backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)）：
  ```python
  _FALLBACK_MODEL = "gpt-4o-search-preview"
  DEFAULT_MODEL = os.getenv("OPENAI_MODEL", _FALLBACK_MODEL)
  ```
- 為何不用 `app.settings.get_openai_model()`：那個 helper 預設回 `gpt-4o-mini`，不支援 web search，會讓 M23 LLM stage 全掛
- Module-level snapshot：function 預設參數 (`def f(model=DEFAULT_MODEL)`) 在 import 時 capture 一次值，後續 caller 不需要顯式傳入 model 也能吃到 env

**修法 2：`build_candidate_pool` 空 list → 短路 `ValueError`**
- 原本 pipeline 拿到空 pool 還是會繼續送空 batch 給 LLM、最後寫一筆 `watchlist=[]` 的 done snapshot；cron exit 永遠 0，無法區分「真的沒抓到」與「成功但 0 檔」
- 修法（[backend/app/signals/pipeline.py:110](backend/app/signals/pipeline.py)）：在 `build_candidate_pool` 之後加：
  ```python
  if not pool:
      raise ValueError(f"no candidate stocks for target_date={target_date}")
  ```
- 既有 pipeline exception handler 會 `_mark_failed` (status="failed") 並 re-raise；`run_daily_signals._classify_exit_code` 抓 "no candidate" 子字串映射到 exit 1（no_data，workflow 仍 pass）
- 觸發情境：週末 / 假日跑、target_date DB 無交易資料、市場太冷沒任何個股通過篩選

**修法 3：`build_candidate_pool` 截斷排序註解**
- spec 描述「LEADER candidate (rank 高) > FOLLOWER candidate > 其他」應優先保留，但截斷發生在 `classification.classify_stocks()` 之前，這時候還沒有 `prelim_type` 可用
- 修法（[backend/app/signals/candidate_pool.py:242](backend/app/signals/candidate_pool.py)）：加 8 行註解說明用 `total_institution_flow_3d`（三大法人 3 日累計淨買超）做 LEADER-aware proxy 排序：
  - LEADER 通常法人連買金額最大 → 排序前段
  - LAGGARD / 弱勢 → 法人金額 ~0 或負 → 截斷時優先丟
- 實務上 60~120 檔幾乎觸發不到 SOFT_TRIGGER=150 hard limit；這段是安全網，未來真要更精準可加 lite 預分類

**測試更新（[backend/tests/test_signals_pipeline.py](backend/tests/test_signals_pipeline.py)）**：
- `_stub_all_stages_noop` 與 `test_pipeline_marks_failed_when_filter_stage_raises` 把 `build_candidate_pool` stub 從 `[]` 改成 `[{"stock_id": "_dummy"}]`（slice 11 後空 pool 會 raise，會跑不到後續 stage）
- 新增 `test_pipeline_raises_value_error_when_candidate_pool_empty` 驗證空 pool 短路路徑：raise ValueError + status=failed + finished_at 寫入
- `test_pipeline_persists_snapshot_with_payload_fields` 的 `candidate_pool_size` 斷言從 `0` 改 `1`（dummy pool 長度）
- 既有 `test_classify_exit_code_no_data` 已驗證 `ValueError("no candidate stocks for date") → exit 1`

**測試結果**：52 M23 tests pass + 509 全 backend tests pass（`test_engine_connects` 為 worktree sqlite 路徑問題，與本切片無關）

**Gotcha**：
- **不要把 `if not pool:` 移進 `build_candidate_pool`**：keep candidate_pool 為純 deterministic transform（input → output 不丟例外）；pipeline 才是 orchestration 層、由它決定「沒 pool = 給 cron 看 exit 1」的語義
- **Module-level `os.getenv()` snapshot 要在 import 時跑**：若改用 `def get_default_model(): return os.getenv(...)` 則 function 預設參數會 evaluate 一次（仍 capture 同一份），但 import-time 寫法更直觀也不會有 monkey-patch surprise
- **`_classify_exit_code` 已 covered**：`backend/tests/test_run_daily_signals.py:47` 已斷言 `_classify_exit_code(ValueError("no candidate stocks for date")) == 1`，本切片不需新增 cron 端測試

**狀態**：9/10 + slice 11 patches，剩 slice 10（prod smoke test）。slice 7~9 + slice 11 整條同 branch (`claude/angry-cerf-8755da`)，merge 上 main 後即可手動觸發 cron 驗證。
