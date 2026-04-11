# Repo Restructure Plan

這份文件說明 `always-stock` 在進入多服務架構後，repo 可以如何整理，讓部署、文件、維運腳本、以及未來基礎設施設定更清楚。

## 目標

- 保留目前單 repo 開發體驗
- 讓 frontend / backend / docs / scripts / infra 邊界更清楚
- 為 Vercel + Render + Postgres 架構預留空間

## 建議結構

```text
always-stock/
├── backend/
│   ├── app/
│   ├── etl/
│   ├── scripts/
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   └── package.json
├── docs/
│   ├── architecture/
│   ├── migration/
│   ├── operations/
│   ├── deployment/
│   └── quality/
├── infra/
│   ├── render/
│   ├── vercel/
│   └── sql/
└── README.md
```

## docs 建議拆分

目前 `docs/` 已有多份規劃文件，之後可逐步整理成：

- `docs/architecture/`
  - current state
  - target architecture
- `docs/migration/`
  - sqlite to postgres
  - fly to render/vercel
- `docs/deployment/`
  - local
  - staging
  - production
- `docs/operations/`
  - runbook
  - backup / restore
  - bot operations
- `docs/quality/`
  - data quality
  - observability

## infra 建議內容

`infra/` 可以放：

- Render service 設定範本
- Vercel 專案設定筆記
- SQL migration / index script
- 之後若導入 IaC，可逐步放進來

## backend 建議整理

若之後功能持續增長，可以考慮拆分：

- `backend/app/routers/`
- `backend/app/services/`
- `backend/app/repositories/`
- `backend/app/schemas/`
- `backend/app/core/`

目標是把：

- HTTP router
- 查詢邏輯
- DB access
- settings / logging

逐步拆開，而不是全部堆在 router 或 ETL script。

## scripts 建議

`backend/scripts/` 保留：

- 本地開發輔助
- migration / backfill helper
- deployment helper

但長期不建議把 production 運維嚴重依賴放在 ad-hoc shell script 上。

## README 原則

README 應聚焦：

- 專案是什麼
- 怎麼快速跑起來
- 現在的主架構
- 重要文件入口

細節則下放到 `docs/`，避免 README 再度膨脹。

## 什麼時候再實際重構目錄

現在先不用為了美觀大搬家。

較適合的時機：

- `database.py` 雙資料庫支援完成後
- migration script 開始落地時
- staging 環境建起來時

先把方向定清楚，再做低風險搬移。
