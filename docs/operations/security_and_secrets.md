# Security and Secrets

這份文件整理 `always-stock` 在新架構下的 secrets 管理方式，目標是讓 frontend、API、Bot、ETL、DB 的權限邊界清楚且可維護。

## 原則

- secrets 不進 repo
- staging 與 production 分開
- frontend 不持有 backend-only secret
- Bot、API、ETL 使用最小必要權限
- database 憑證要能輪替

## 服務與 secrets 邊界

### Frontend

前端只應持有 public 設定，例如：

- `NEXT_PUBLIC_API_URL`

前端不應持有：

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `FINMIND_TOKEN`

### Backend API

需要：

- `DATABASE_URL`
- `CORS_ORIGINS`
- `TZ`

視功能需要：

- `FINMIND_TOKEN`

原則：

- API 不直接持有 Telegram bot token，除非實作上真的共用 process

### Telegram Bot

需要：

- `DATABASE_URL`
- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `TZ`

視功能需要：

- `FINMIND_TOKEN`

### ETL / Cron Job

需要：

- `DATABASE_URL`
- `TZ`

視資料源需要：

- `FINMIND_TOKEN`

## 建議環境變數清單

### 共用

- `TZ=Asia/Taipei`
- `DATABASE_URL`

### Frontend

- `NEXT_PUBLIC_API_URL`

### Backend API

- `CORS_ORIGINS`

### Bot

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`

### ETL

- `FINMIND_TOKEN`

## 權限建議

### Database

建議至少區分：

- app role：API / Bot 讀寫
- migration role：schema 調整
- readonly role：報表或臨時分析

若目前規模先不分 role，至少要保留未來可拆的能力。

### OpenAI / LLM

- 只提供給 bot 或特定 AI 分析服務
- 不注入 frontend
- staging 與 production key 分開

### Telegram Bot Token

- 只提供給 bot worker
- 不放進 API service
- token 洩漏時可快速輪替

## 輪替策略

- 每次平台遷移或權限調整後做一次 secrets 盤點
- 高風險 token 洩漏後立即輪替
- 輪替後驗證 bot、API、cron job 都可正常運作

## 文件與流程

建議建立一份不進 repo 的 secrets inventory，至少包含：

- secret 名稱
- 使用服務
- 平台位置
- 最後更新時間
- 輪替負責人

## 事故處理

若懷疑 secrets 外洩：

1. 先輪替 token / password
2. 重啟受影響服務
3. 檢查近期 logs
4. 確認沒有異常資料寫入或濫用
5. 補記事件時間線與後續改善
