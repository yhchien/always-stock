# Architecture Overview

always-stock 專案架構總覽 — 技術選擇、通訊方式、部署演進。

## 專案用途

追蹤台股三大法人（外資、投信、自營商）每日買賣超，
按 Fugle 子產業分類呈現資金流向排行，支援 drill-down 到個股走勢圖。
附帶 Telegram Bot 讓使用者在手機查個股籌碼 + AI 分析。

---

## 系統架構圖

```
使用者（瀏覽器）                   使用者（Telegram）
       │                                │
       ▼                                ▼
┌─────────────┐                 ┌──────────────┐
│  Frontend   │  HTTP JSON      │ Telegram Bot │
│  Next.js    │ ──────────┐     │ long-polling  │
│  (Vercel)   │           │     └──────┬───────┘
└─────────────┘           │            │
                          ▼            ▼
                  ┌─────────────────────────┐
                  │     Backend API         │
                  │     FastAPI (Render)    │
                  │     /api/industries     │
                  │     /api/stocks         │
                  │     /api/brokers        │
                  │     /api/realtime       │
                  └───────────┬─────────────┘
                              │ SQLAlchemy ORM
                              ▼
                  ┌─────────────────────────┐
                  │     PostgreSQL          │
                  │     Render Managed DB   │
                  │     (Singapore)         │
                  └─────────────────────────┘
                              ▲
                              │ 每日寫入
                  ┌─────────────────────────┐
                  │     ETL 排程            │
                  │     Render Cron Job     │
                  │     週一至五 19:00/21:30│
                  └─────────────────────────┘
                              ▲
                              │ 抓取
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ TWSE T86 │   │ TWSE MI  │   │ FinMind  │
        │ 法人買賣 │   │ 收盤價   │   │ 股票基本 │
        └──────────┘   └──────────┘   └──────────┘
```

---

## 技術選擇與理由

### 後端：FastAPI (Python)

| 項目 | 說明 |
|------|------|
| **選擇** | FastAPI + Uvicorn |
| **為什麼** | ETL 抓資料用 Python 生態最方便（requests、pandas）；FastAPI 效能好、自帶 Swagger API 文件、async 支援 |
| **考慮過的替代** | Flask（功能太陽春，沒有自動 docs）、Django（太重，不需要 admin/ORM migration） |

### 前端：Next.js (React)

| 項目 | 說明 |
|------|------|
| **選擇** | Next.js 16 + React 19 + App Router |
| **為什麼** | 元件化開發效率高、SSR/SSG 支援好、Vercel 一鍵部署零設定 |
| **考慮過的替代** | Vue/Nuxt（可行但 React 生態更大）、純 HTML + jQuery（不好維護大型 SPA） |

### UI / 圖表

| 項目 | 說明 |
|------|------|
| **UI 元件** | shadcn/ui + Tailwind CSS |
| **為什麼** | 深色主題好客製、元件品質高、不綁死特定 CSS 框架 |
| **考慮過的替代** | MUI（太 Google 風格）、Ant Design（較大陸風格） |
| **圖表** | ECharts (echarts-for-react) |
| **為什麼** | K 線圖（candlestick）功能完整、中文文件多、開源免費 |
| **考慮過的替代** | Chart.js（K 線圖支援弱）、Highcharts（商用要授權費）、TradingView widget（太重、客製化受限） |

### ORM：SQLAlchemy

| 項目 | 說明 |
|------|------|
| **選擇** | SQLAlchemy 2.0 |
| **為什麼** | Python 標準 ORM，同一份 model 可同時支援 SQLite 和 Postgres，切換只需改連線字串 |
| **考慮過的替代** | 直接寫 raw SQL（不好維護）、Tortoise ORM（太小眾、社群小） |

### 資料庫

| 階段 | 選擇 | 為什麼 |
|------|------|--------|
| **開發期** | SQLite | 零設定、單一檔案、本地開發極方便 |
| **上雲後** | PostgreSQL (Render Managed) | 雲端標準、支援多連線、不用管檔案傳輸、可靠度高 |

考慮過的 DB 替代：

- MySQL — 可以，但 Postgres 功能更強（JSON、window function、CTE）
- PlanetScale — 免費方案已取消
- Supabase Postgres — 可行，但只提供 DB，沒有 worker/cron

### Telegram Bot

| 項目 | 說明 |
|------|------|
| **選擇** | python-telegram-bot（long-polling 模式） |
| **為什麼** | 不需要設定 webhook 或固定 domain，Python 原生、適合搭配 FastAPI 同 codebase |
| **考慮過的替代** | Discord Bot（但 Telegram 行動端體驗更好）、LINE Bot（API 較煩瑣、Messaging API 有月費） |

### AI 分析

| 項目 | 說明 |
|------|------|
| **選擇** | OpenAI GPT（`/ai` 指令） |
| **為什麼** | 籌碼分析摘要品質好、API 穩定 |
| **考慮過的替代** | Gemini（有設 secret 但尚未正式使用）、本地 LLM（推論太慢、硬體需求高） |

---

## 通訊方式

```
前端 ←──── HTTP GET JSON ────→ 後端 API
                                   │
                                   ├── SQLAlchemy ORM ──→ PostgreSQL
                                   │
Telegram ←── Bot long-polling ─→ 後端 Bot process
                                   │
ETL cron ──── 定時執行 ────────→ TWSE / FinMind HTTP API
                                   │
                                   └── 寫入 ──→ PostgreSQL
```

- **前端 → 後端**：純 HTTP GET，JSON 回應，透過 `NEXT_PUBLIC_API_URL` 設定後端位址
- **後端 → DB**：SQLAlchemy ORM，連線字串由 `DATABASE_URL` 環境變數決定
- **ETL → 資料源**：HTTP 抓取 TWSE/FinMind 公開 API，解析後寫入 DB
- **Bot → 使用者**：Telegram Bot API long-polling，不需要額外設定 webhook

---

## 部署演進

### 階段 1：本地開發

```
全部在 localhost
├── Frontend: npm run dev (port 3000)
├── Backend:  uvicorn (port 8000)
├── DB:       SQLite 檔案 (backend/db/tw_stock.db)
├── ETL:      macOS launchd 排程
└── Bot:      本地 long-polling
```

### 階段 2：Fly.io 單機部署（已停用）

```
Fly.io Tokyo (nrt)
├── always-stock-api (shared-cpu-1x / 512MB)
│   ├── FastAPI + Telegram Bot + cron ETL 全包一台
│   └── Volume: 12GB, SQLite 掛載在 /data
└── always-stock-web (shared-cpu-1x / 512MB)
    └── Next.js standalone
```

**遇到的問題：**

- SQLite 有 ~5GB，上傳到 Fly volume 極痛苦（要壓縮 → 分片 → 逐片上傳 → 遠端合併）
- API + Bot + cron 全跑同一台，任何一個掛掉就全部重啟
- Fly.io volume 停機也持續計費

### 階段 3：Render + Vercel（現在搬移中）

```
Render (Singapore)
├── Web Service  → FastAPI 後端 API
├── Worker       → Telegram Bot (long-polling)
├── Cron Job     → 每日 ETL（週一至五）
└── Postgres DB  → 獨立託管資料庫

Vercel
└── Frontend     → Next.js（自動從 GitHub 部署）
```

**為什麼搬到 Render + Vercel：**

- DB 變成獨立服務（Postgres），不再需要管檔案傳輸
- 服務拆開：API / Bot / ETL 各自獨立，互不影響
- Vercel 部署 Next.js 零設定，推 code 自動上線
- 兩者都有免費方案，個人專案成本趨近 $0

**為什麼選 Render 不選其他：**

| 平台 | 優點 | 為什麼沒選 |
|------|------|-----------|
| **Render** ✓ | 免費 Postgres + Web Service + Worker + Cron，GitHub 自動部署 | — |
| Railway | 介面好用 | 免費額度少（$5/月 credit） |
| Supabase | Postgres 品質好 | 只有 DB，沒有 worker/cron |
| AWS (ECS/RDS) | 最彈性 | 設定太複雜，個人專案不值得 |
| Heroku | 老牌 PaaS | 免費方案已取消 |

---

## Fly.io 善後

搬移完成後，Fly.io 上的資源可以全部刪除：

| 資源 | 目前狀態 | 停機計費 | 建議 |
|------|---------|---------|------|
| always-stock-api machine | stopped | 不收費 | 刪除 |
| always-stock-web machine | stopped | 不收費 | 刪除 |
| stock_data volume (12GB) | created | **~$1.20/月** | 搬移驗證完成後刪除 |

本地已有完整的 `backend/db/tw_stock.db` 作為最終備份，風險極低。

---

## 環境變數總覽

### 後端（Render Web Service）

| 變數 | 用途 |
|------|------|
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `CORS_ORIGINS` | 允許的前端 origin（逗號分隔） |
| `TZ` | `Asia/Taipei` |

### Bot（Render Worker）

| 變數 | 用途 |
|------|------|
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token |
| `OPENAI_API_KEY` | OpenAI GPT（AI 籌碼分析） |
| `TZ` | `Asia/Taipei` |

### ETL（Render Cron Job）

| 變數 | 用途 |
|------|------|
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `FINMIND_API_TOKEN` | FinMind API（可選，免費 tier 夠用） |
| `TZ` | `Asia/Taipei` |

### 前端（Vercel）

| 變數 | 用途 |
|------|------|
| `NEXT_PUBLIC_API_URL` | 後端 API 的完整 URL |
