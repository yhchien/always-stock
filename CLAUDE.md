# always-stock 專案記憶

## 部署 / 上雲前必讀

處理部署、資料搬移、Fly.io、Render、Postgres、雲端 DB 切換相關任務前，先閱讀：

- `docs/deployment_strategy.md`
- `docs/flyio_sqlite_upload.md`
- `README.md` 中的部署章節
- `infra/render/render.yaml.template`

## 專案概述

**always-stock**：台股產業別三大法人資金流向分析儀表板（原名 tw-stock-dashboard，2026-04 更名）。

## 技術堆疊
- **Backend**: FastAPI + SQLAlchemy, Python 3.9+
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **DB**: SQLite (`backend/db/tw_stock.db`)
- **ETL 資料來源**: TWSE 公開資料（T86、STOCK_DAY_ALL）、FinMind API、Fugle 子產業分類
- **Bot**: Telegram Bot（long-polling）+ OpenAI GPT 籌碼分析
- **排程**: macOS launchd（本地）/ cron（Fly.io，19:00 + 21:30 台灣時間）
- **部署**: Fly.io（API: always-stock-api.fly.dev / 前端: always-stock-web.fly.dev）

## Milestones 進度

### 已完成（截至 2026-04-08）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M9: AI 籌碼分析（`/ai` 指令，接 OpenAI GPT）
- M10: Fly.io 雲端部署（API + Bot + 前端 + cron ETL + persistent volume 12GB）

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

- README 已補上完整資料流與頁面 flow，位置在專案架構後，方便快速 onboarding
- L2 頁面 `StockChart` 與 `BrokerPanel` 必須共用同一個 `date` query param，避免同頁不同日期資料混用
- L0 / L1 前端預設日期必須用 `Asia/Taipei`，不可用 `toISOString().slice(0, 10)`，否則台灣凌晨會落到前一天
- 即時報價 API `/api/realtime/quotes` 單次上限 50 檔；前端若要查整個產業，必須自動分 batch，不能假設所有股票可一次取回
- `industry_daily_flow` 仍是 L0 主查詢來源；不要把產業聚合搬回 API 臨時計算或前端計算
- `SKILL.md` 的產業分類規則已同步為實作現況：以 Fugle mapping 為主，FinMind / TWSE 類別僅作 fallback
- backend `get_db()` 測試已改為驗證 `close()` 被呼叫，不再依賴 SQLAlchemy `Session.is_active` 判斷關閉狀態
- `backend/app/routers/industries.py` 的 streak 查詢已改成 `select(subquery.c.trade_date)`，避免 SQLAlchemy `SAWarning`

## 資料狀態（2026-04-10）

- 本地 backfill 已重新補跑大部分歷史缺口
- `inst_stock_flow` / `industry_daily_flow` 仍缺 3 天：`2019-04-04`、`2023-04-03`、`2026-02-18`
- 上述 3 天重抓時，TWSE `MI_INDEX` 回傳「沒有符合條件的資料」，暫列為資料源特殊日
- `daily_price` 仍有 5 天 `OHLC` 缺漏：`2023-05-05`、`2023-09-19`、`2024-01-17`、`2024-02-29`、`2024-07-11`
- 這 5 天已重抓一次，`close_price` 與後續 flow 可更新，但 `open/high/low` 仍為空，推測是資料源回傳本身缺欄位
- Fly.io 狀態檢查：
  - `always-stock-api` 為 `stopped`
  - `always-stock-web` 為 `suspended`
  - 線上目前沒有持續執行中的 app machine
