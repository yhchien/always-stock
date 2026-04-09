# always-stock 專案記憶

## 專案概述

**always-stock**：台股產業別三大法人資金流向分析儀表板（原名 tw-stock-dashboard，2026-04 更名）。

## 技術堆疊
- **Backend**: FastAPI + SQLAlchemy, Python 3.9+
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **DB**: SQLite (`backend/db/tw_stock.db`)
- **ETL 資料來源**: TWSE 公開資料（T86、STOCK_DAY_ALL）、FinMind API、Fugle 子產業分類
- **Bot**: Telegram Bot（long-polling）+ Gemini AI 籌碼分析
- **排程**: macOS launchd（本地）/ cron（Fly.io，19:00 + 21:30 台灣時間）
- **部署**: Fly.io（API: always-stock-api.fly.dev / 前端: always-stock-web.fly.dev）

## Milestones 進度

### 已完成（截至 2026-04-08）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M9: AI 籌碼分析（`/ai` 指令，接 Gemini）
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
