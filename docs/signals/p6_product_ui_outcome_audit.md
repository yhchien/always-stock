# P6 Product UI and Outcome Analytics Audit

稽核日：2026-07-30
政策：P6 是 P0～P5 的產品化與事後品質觀察層。資料流只允許
`production decision → outcome analytics`，Outcome、人工註記與圖表不得回饋候選資格、
Global Selector、P4 state machine、Prompt v7 或 replay decision。

## A. Repository Audit

| File / surface | Symbol / current behavior | P6 gap / resolution | Status |
|---|---|---|---|
| `frontend/src/app/page.tsx` | 首頁內嵌 `DailySignalsPanel` | 無 signals product IA；保留舊首頁，新增五個獨立路由 | `FOUND_AND_EXTENDED` |
| `frontend/src/components/DailySignalsPanel.tsx` | 最新 watchlist 卡片、P3 buckets | 不適合日期比較與 outcome；不破壞舊頁，新增 date-aware Recommendations | `FOUND_ACCEPTABLE` |
| `frontend/src/app/signals/observations/page.tsx` | P4 list/detail/timeline | 缺 product nav、搜尋、asset/episode filter、多 episode、前態/失效維度/版本 | `FOUND_AND_FIXED` |
| `frontend/src/app/signals/archive/page.tsx` | 舊 30-trading-day watch archive | 是追蹤週期呈現，不是 P3 Day10 正式推薦 outcome | `FOUND_BUT_SEPARATE` |
| `backend/app/signals/archive.py` | Day2 baseline、tracking Day10/20/30 | 與 P6「推薦日收盤 → 第 10 個後續交易日收盤」不同；保留歷史 archive 語意，P6 統一使用 `day10_v1` | `AUDITED` |
| `backend/app/models.py` | snapshots、hits、completed archive、P4 episodes/reviews | 缺全體 global-eligible outcome cache 與人工註記 | `FOUND_AND_FIXED` |
| `backend/app/routers/signals.py` | latest/date snapshot、P4、archive API | 缺 recommendation navigation、Outcome summary/timeseries/items/CSV/observation/review API | `FOUND_AND_FIXED` |
| `backend/app/signals/observation_lifecycle.py` | backend authoritative P4、detail timeline | P6 只加 derived `previous_status` 與 episode history；未改 state transition | `FOUND_AND_EXTENDED` |
| `backend/app/main.py` / `observation_schema.py` | targeted `create_all`，無 Alembic | repository 沒有正式 migration framework；P6 沿用 additive bootstrap，相容舊部署 | `AUDITED` |
| `frontend/package.json` | ECharts 6 / echarts-for-react | 重用既有圖表庫 | `VERIFIED` |
| `backend/run_v6_llm_validation.py` / P4 replay | point-in-time decision replay | 不讀 P6 cache；以 import/payload guard 測試鎖定 | `VERIFIED` |

P4 startup bootstrap 保留；repository 目前沒有 Alembic。P6 table 由
`outcome_schema.ensure_outcome_tables()` 與 web startup 的 targeted `create_all` 冪等建立，
不修改 source snapshot、P3 decision 或 P4 review。

## B. UI Architecture

| Route | Responsibility |
|---|---|
| `/signals` | 最新交易日、P3/P4/Outcome 核心摘要與完整性入口 |
| `/signals/recommendations` | 日期導覽、完整 Funnel、RECOMMEND 主清單、NOT_SELECTED、true REMOVE、Technical Failure |
| `/signals/observations` | status/search/asset/episode filter、initial thesis、current evidence、Review timeline、多 episode |
| `/signals/outcomes` | Day10 核心目標、selection/observation analytics、趨勢、分頁明細、CSV、人工檢查 |
| `/signals/debug` | job、prompt/selection/score/tracking versions、容量、完整性與技術失敗 |

共用元件／helper：

- `SignalProductNav`
- `SignalFunnel`
- `SelectionReasonBadge`
- `OutcomeMetricCard`
- `OutcomeDistributionChart`
- `OutcomeTimeseriesChart`
- `signalP6Presentation.ts`：集中 P3/P4/Outcome/Reason/Review 中文 mapping

所有新頁有 loading、error、empty/partial-data 狀態；表格與 Funnel 可水平捲動，metric
cards 使用 responsive grid。圖表有標題、期間、樣本數、tooltip、空狀態，且以文字與
中性色呈現，不把綠／紅等同買賣建議。

## C. Outcome Definition

唯一 P6 定義：

```text
outcome_definition_version = day10_v1
outcome_horizon = DAY10
entry_price_definition = signal_date_close
exit_price_definition = tenth_subsequent_market_trade_date_close
```

Day10 是推薦日之後第 10 個「全市場有效交易日」，週末、假日、停市日不算。Entry 是正式
推薦日收盤，Exit 是第 10 個後續市場交易日的個股收盤；該股票必要日期缺價時標
`OUTCOME_DATA_MISSING`，不跳到未定義的替代日期。

```text
return >= +10.0% → WINNER
-10.0% < return < +10.0% → NEUTRAL
return <= -10.0% → BIG_LOSER
市場尚未走滿 10 個後續交易日 → IMMATURE
Entry / Exit 必要價缺漏 → OUTCOME_DATA_MISSING
```

IMMATURE 與 Missing 均不進成熟分母，也不以 0% 補成 Neutral。

舊 `signal_watch_completed_archives.return_day_10_pct` 仍代表舊 watch cycle 的 Day2
baseline tracking metric；它不是 P6 正式推薦 outcome，P6 API 不混用該欄。

## D. Core Goals

固定測試樣本：

```text
Recommended Count = 10
Winner = 5
Neutral = 3
Big Loser = 2
Acceptable Rate = (5 + 3) / 10 = 80%
Acceptable Target Met = true
Winner > Neutral = true
Big Loser Rate = 20%
Global-Eligible Winners = 7
Recommended Winners = 5
Winner Recall = 71.43%
```

另輸出每日 Global Eligible / Recommended / NOT_SELECTED 與
`1 - Recommended / Global Eligible` 壓縮率。Global selection failed date 不建立
global-eligible outcome rows，因此不會進 Winner Recall 分母。

## E. Observation Metrics

`signal_observation_outcome_metrics` 使用：

```text
definition_version = p6_observation_outcome_v1
premature_stop_definition_version = stop_day10_plus10_v1
```

- Caution event recovery：某次 CAUTION 後，在 STOP 前出現 CONTINUE。
- Episode recovery：episode 曾 CAUTION 且在 STOP 前至少恢復一次。
- Premature Stop Candidate：stop date 收盤後第 10 個後續交易日收盤報酬 `>= +10%`。
- Stop Before Big Loss：初始推薦收盤第一次觸及 `<= -10%` 前已 STOP。
- Average Days to Stop：使用市場交易日，並分 immediate / sustained / external thesis。
- Re-recommended：同股票 STOPPED 後的新 episode，保留 episode id 與交易日間隔。

UI 固定說明 premature stop 只表示停止後重新走強、需人工檢查，不代表原決策錯誤；
STOPPED 仍沿用「不構成賣出、停損或看空建議」。

## F. API / Data Model

新增 API：

```text
GET   /api/signals/recommendations?date=YYYY-MM-DD
GET   /api/signals/outcomes/summary
GET   /api/signals/outcomes/timeseries
GET   /api/signals/outcomes/items
GET   /api/signals/outcomes/items?export=csv
GET   /api/signals/outcomes/observations
GET   /api/signals/outcomes/review-queue
PATCH /api/signals/outcomes/review-queue/{id}
```

新增 additive tables：

- `signal_outcome_metrics`
- `signal_observation_outcome_metrics`
- `signal_outcome_review_queue`

主要 index 覆蓋 signal date、stock、decision + label + date、outcome label、matured date、
prompt family、selection、observation/episode/stop date。Outcome 唯一鍵為：

```text
signal_date + stock_id + outcome_horizon + outcome_definition_version
```

人工 PATCH 需要既有 authentication，只允許 `UNREVIEWED | REVIEWED` 與最長 2,000 字
note；不接受 outcome label 或 decision 欄位。前端不以 HTML 注入 note。

Backfill：

```bash
cd backend
python3 run_signal_outcome_backfill.py \
  --start-date 2026-04-01 \
  --end-date 2026-07-29 \
  --outcome-version day10_v1
```

它會冪等重建指定日期區間 cache，輸出 calculated / immature / missing / failed，且不修改
原始 snapshots。Daily cron 與手動 pipeline 完成後才 refresh P6；production decision
已經結束。

## G. Leakage Guard

依賴方向：

```text
SignalSnapshot + DailyPrice + P4 Observation/Review
→ app/signals/outcome_metrics.py
→ P6 cache / API / UI
```

禁止方向由測試保護：

- candidate pool、pipeline、global selector、P4 lifecycle、prompt family、LLM caller 不 import
  `outcome_metrics` 或 `SignalOutcomeMetric`。
- Compact Selection Cards 即使上游 item 帶 `outcome_label/day10_return` 也會投影刪除。
- Tracking Prompt input 只挑 initial thesis 與當日 backend evidence，忽略 initial snapshot
  中任何 Day10/outcome 欄。
- Manual Review 只改 review row；測試驗證 cache label、P3 decision、source snapshot 不變。
- Replay modules 不 import Outcome cache；P4 state machine 維持 `p4_state_v1`。

P6 沒有固定 Top-K、candidate truncation、sector/theme/source/asset quota，也沒有 outcome
threshold optimization 或自動 prompt tuning。

## H. Test / Scale Evidence

Focused tests 覆蓋：

- ±10% 精確邊界、10 個後續市場交易日、週末跳過
- IMMATURE / Missing 不當 Neutral
- 5 Winner / 3 Neutral / 2 Big Loser 的 80% 核心目標
- Winner Recall、NOT_SELECTED Winner、global failure 排除
- Caution recovery、premature stop、version
- API summary/timeseries/pagination/filter/CSV/empty/invalid interval
- Manual Review 不改 outcome/decision/snapshot
- selector/tracking payload leakage 與 import guard
- Recommendations rank、四桶分離、P3/P4 badge、global failure
- Outcome denominator/targets、集中中文 mapping 與完整 Funnel

Synthetic SQLite scale：

| rows | summary | timeseries | page query | CSV serialization | peak allocation |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.0718s | 0.0082s | 0.0086s | 0.0384s | 7.538 MiB |
| 10,000 | 0.0533s | 0.0098s | 0.0075s | 0.3626s | 9.680 MiB |
| 50,000 | 0.1039s | 0.0244s | 0.0081s | 1.7498s | 9.815 MiB |

Summary/timeseries 使用 DB aggregate；items 使用 backend pagination；CSV 以
`yield_per(1000)` 逐批讀取並以 500 rows/chunk 串流回應，不建立完整匯出字串。
Frontend 不載入全部歷史再 filter。

實際結果：

```text
Backend P6 focused:           18 passed
Backend P0～P6 focused:       440 passed
Backend full tests/:          1222 passed, 21 failed
Frontend P2/P3/P4/P6 targeted: 23 passed
Frontend full:                91 passed, 18 failed
Frontend production build:    passed
P6 targeted ESLint:           passed
```

Backend full 的 21 failures 與 P4/P5 baseline 相同：auth-disabled/rate-limit/watchlist
契約，以及 local 環境將 `DATABASE_URL` 指向受 sandbox DNS 阻擋的 remote PostgreSQL；
P6 新增／修改 focused tests 無 failure。

Frontend full 的 18 failures 與 P5 baseline 相同，集中於 BacktestPanel、StockList、
StockChart；新增 P6 tests 全數通過。全 repo ESLint 維持既有 3 errors（login `<a>`、
phase2 page 與 StickyHorizontalScroll effect 同步 setState）和 2 test warnings；P6
變更檔 targeted ESLint 無錯誤。獨立 `tsc --noEmit` 仍受既有 tsconfig 未載入 Jest
globals 影響；Next production build 的 application TypeScript 檢查通過。

## I. Backward Compatibility / Rollback

- 舊 snapshot 缺 P3 buckets/processing metadata 時，現有 `/latest` 與舊首頁維持相容。
- P6 cache 是衍生資料，可刪除後由 backfill 重建；source decision 不受影響。
- 回滾 UI/API/cache code 不需要改寫 snapshots、hits、observations 或 reviews。
- P4 startup ensure 保留；P6 使用相同 additive compatibility strategy。

## J. Out of Scope

本次沒有實作：

```text
Production threshold optimization
Automated prompt tuning
Portfolio Backtest
Position Sizing
BUY / SELL
Stop-loss / Take-profit
Broker integration
Automatic order execution
```

所有結果只供事後評估與人工改進，不會自動切換 production 版本或宣稱某版本「最佳」。
