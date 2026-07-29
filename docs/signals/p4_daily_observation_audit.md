# P4 Daily Observation Lifecycle Audit

稽核日：2026-07-29
政策：P3「今日正式推薦」與 P4「既有推薦是否繼續觀察」完全分離。所有 active
observations 在每個有效交易日都必須 review，不依賴 A/B/C/D re-hit、P3 rank 或今日是否
再次推薦。

## A. 稽核結論

P4 已完成 additive data model、每日 pipeline、point-in-time evidence、外部 thesis
assessment、backend authoritative state machine、冪等 persistence、P3 conflict、
API、最小 UI、replay 與 scale guard。

`STOP_OBSERVING` 不是 SELL、停損或看空；它只停止該 recommendation episode 的魚尾追蹤。
既有 `signal_watch_hits` 與 30 日績效 archive 保留，P4 不刪歷史 hit，也不以
NOT_SELECTED/REMOVE 自動停止既有 observation。

## B. File / Symbol Audit

| File | Symbol / surface | Current behavior / required change | Impact | Status |
|---|---|---|---|---|
| `backend/app/models.py` | `SignalObservation` / `SignalObservationReview` | 舊系統只有 hit/archive，無獨立 lifecycle；新增 episode 與每日 review 表 | additive schema | `ADDED` |
| `backend/app/observation_schema.py` | `ensure_observation_tables` | web startup 與 cron 必須安全建立 additive tables | DB startup | `ADDED` |
| `backend/app/signals/observation_lifecycle.py` | evidence / assessment / state machine / persistence | active observation 全量每日檢查；LLM 只驗外部 thesis；backend 決定狀態 | production lifecycle | `ADDED` |
| `backend/app/signals/pipeline.py` | `_run_p4_tracking` | P3 成功、P3 selector 失敗、有效交易日 P3=0 都要執行 P4 | job / snapshot | `FOUND_AND_FIXED` |
| `backend/app/signals/archive.py` | `persist_signal_watch_hits` / episode gap | 保留 P3 hit、30 日績效與既有 5 missed trade dates episode gap | no destructive migration | `FOUND_ACCEPTABLE` |
| `backend/app/prompts/tracking-review-v1.md` | `p4_tracking_v1` | date-bounded external thesis assessment，不准 BUY/SELL 或 backend override | prompt/version | `ADDED` |
| `backend/app/routers/signals.py` | `/observations*` | list/detail/daily summary，舊 P3 contract 不變 | additive API | `ADDED` |
| `frontend/src/lib/api.ts` | P4 contracts/fetchers | optional、獨立 P3/P4 status | additive client API | `ADDED` |
| `frontend/src/app/signals/observations/page.tsx` | list/filter/detail/timeline | 基本 observation UI、technical warning、non-sell warning | new route | `ADDED` |
| `backend/run_p4_tracking_replay.py` | chronological replay | 推薦日起逐交易日、read-only、date-bounded | offline validation | `ADDED` |
| `backend/run_daily_signals.py` | schema bootstrap | daily entry point 先確保 P4 tables；P4 本身接在既有 job 後半段 | scheduler | `FOUND_AND_FIXED` |
| `backend/run_v6_llm_validation.py` | P3 replay | 只驗 P3 research/selection，不負責 P4 episode replay | none | `FOUND_BUT_OUT_OF_SCOPE` |

## C. Data Model 與 Episode

`signal_observations` 保存：

- stock/name/asset type、唯一 `episode_id`
- `OBSERVING | CAUTION | STOPPED`
- recommendation date、initial thesis snapshot、baseline completeness
- latest lifecycle state、consecutive caution count、stop date/reason
- selection version 與 timestamps

`signal_observation_reviews` 每個 observation / trading date 唯一，保存：

- `CONTINUE | CAUTION | STOP_OBSERVING | REVIEW_FAILED`
- reason codes/text、caution/failed dimensions
- 完整 backend evidence、external assessment、market context、persistence warning
- technical status、`p4_tracking_v1`、`p4_state_v1`

P3 只有正式 `RECOMMEND` 能建立 observation。Active episode 再次 RECOMMEND 不重設
started date、initial thesis 或 caution history。同日新 episode 不 review；下一交易日開始。
STOPPED 股票只有在既有 5 missed trading days gap 成立時才能建立新 episode。

Legacy hits 可一次性 non-destructive bootstrap；初始欄位不足會標
`LEGACY_INCOMPLETE`，只能增加 data-quality caution，不能靠缺漏直接 STOP。

## D. Evidence 與 Selection Independence

每次 review 以 observation stock IDs 批次建 evidence，不從當日候選集合做 admission：

- 現價與 episode Day1/3/5/10 returns（只保存 evidence，不作 outcome optimization）
- market/peer RS、距高點、ATR、量能、法人 1/3/5 日參與
- deterministic signals、entry/tracking/freshness/watch quality
- hard exclusion、persistence warning、market regime、data quality
- initial recommendation thesis 與截至 review date 的外部驗證

資料存取是 batch/O(n)；沒有 per-observation DB query，也沒有 total observation cap。
`TRACKING_RESEARCH_BATCH_SIZE` 只控制每次 API batch，不是總量截斷。

有效交易日即使 P3 候選池為 0，仍會寫 0-recommendation snapshot 並完成 P4。完全沒有
交易資料的日期才保留原本 no-data 行為。

## E. Authoritative State Machine

Backend 優先序：

1. 單檔技術失敗 → `REVIEW_FAILED`，狀態與 caution count 不變。
2. 六個 hard reason 或 `TRACKING_INVALIDATED` → 立即 `STOP_OBSERVING`。
3. 合法外部 thesis invalidation → 立即 `STOP_OBSERVING`。
4. Recovery → 清除核心 caution，回 `CONTINUE`。
5. 最新兩次成功 review 的多核心持續失效 → `STOP_OBSERVING`。
6. 其餘 warning → `CAUTION`；無 warning → `CONTINUE`。

六個 backend hard reason：

```text
MANUAL_BLACKLIST
FAILED_FOLLOW_THROUGH_CURRENT_EPISODE
STRUCTURE_DAMAGED
LIQUIDITY_FAILURE
COMPOSITE_RISK_EXCLUDE
REVERSAL_FAILURE
```

外部 invalidation 只允許：

```text
BUSINESS_MISMATCH
THEME_MISMATCH
FALSE_SUPPLY_CHAIN_LINK
MATERIAL_NEGATIVE_EVENT
DATA_CONTRADICTION
```

`MATERIAL_NEGATIVE_EVENT` / `DATA_CONTRADICTION` 必須有 summary、URL、published date，
且 published date 不得晚於 review date；backend 另記 retrieved date。
`UNCONFIRMED`、沒有新新聞或 research unavailable 都不等於 invalidated。

Sustained stop 的核心維度只有：

```text
MOMENTUM_STRUCTURE
PARTICIPATION
CATALYST_THESIS
```

必須連續兩次成功 CAUTION、至少兩個核心維度重疊，且重疊集合含 momentum 或
participation。`MARKET_CONTEXT`、`PERSISTENCE_WARNING`、`DATA_QUALITY` 永不計入。

## F. Failure、Idempotency 與 P3 Conflict

- 同日 rerun upsert 同一 review，依該日最後有效結果重算 caution count。
- 單檔 assessment 失敗不影響其他 observation；整批失敗形成 partial failure。
- 技術失敗不轉成 CAUTION/STOP。
- P3 global selector 原子失敗時，P4 仍 review 既有 observations。
- `P3 RECOMMEND + P4 CAUTION` 合法，API/UI 分別顯示。
- `P3 RECOMMEND + P4 STOP` 保存 recommendation 與 stop evidence，另產生
  `TRACKING_SELECTION_CONFLICT`，job 為 partial failure；同日不重開 episode。

## G. API / UI

API：

```text
GET /api/signals/observations
GET /api/signals/observations?status=OBSERVING|CAUTION|STOPPED
GET /api/signals/observations/tracking-summary
GET /api/signals/observations/{observation_id}
```

UI route：`/signals/observations`

基本畫面提供：

- 三狀態 filter、首次推薦日、最新 review 日、連續 caution、停止日期與最新原因
- initial recommendation thesis、latest backend/external evidence、caution dimensions
- 依日期排列的 Review timeline
- P3「今日推薦」與 P4 observation status 獨立 badge
- `REVIEW_FAILED` 顯示「本次追蹤檢查未完成，維持上一個有效狀態」
- STOPPED detail 固定顯示「停止觀察…不構成賣出建議」

## H. Point-in-time Replay

```bash
cd backend
python run_p4_tracking_replay.py 2026-07-01 2026-07-29 \
  --observation-id 12 --out /tmp/p4-replay.json
```

Replay：

- 從每個 selected episode 的推薦日起依交易日順序重建，即使輸出 window 較晚也不跳過前態。
- 每日 prompt 鎖定該 `review_date`，material evidence 禁止未來 published date。
- 不寫 Observation、Review、Snapshot 或 WatchHit。
- `REVIEW_FAILED` 保持前態；不以未來狀態、Day10 outcome 或 threshold optimization 回填。
- 輸出 previous status、decision、reason、dimensions、caution count、stop reason 與版本。

## I. Test / Scale Evidence

Focused backend 覆蓋：

- 無 candidate re-hit、P3=0 trading day、same-day new episode
- 七種 backend immediate stop 與五種 external invalidation
- UNCONFIRMED/material evidence/date boundary
- persistence only、market only、one-day reversal
- sustained two-dimensional failure、non-core exclusion、recovery
- technical failure、same-day idempotency、legacy baseline、P3/P4 conflict、episode restart
- COMMON_STOCK/FINANCIAL/ETF lifecycle parity
- API list/detail/summary、point-in-time replay read-only
- 25/50/100/200 observations 全量 persistence + serialization

Scale guard 每組要求 duration `< 2s`、peak allocation `< 32 MiB`；實作沒有 total limit。

Frontend targeted 覆蓋中性 status wording、technical failure wording 與既有 P2/P3
components。本次變更檔 targeted eslint 通過。全 repo typecheck 仍受既有 Jest globals
未納入 tsconfig 影響。

實際結果：

```text
Backend P4 focused:          123 passed
Backend full tests/:         1187 passed, 21 failed
Frontend P2/P3/P4 targeted:  16 passed
Frontend full:               84 passed, 18 failed
Frontend production build:   passed
```

Backend full 的 P4 新增／修改測試沒有失敗；21 個失敗為目前 local baseline：
19 個 auth-disabled/rate-limit/watchlist 測試，以及 2 個由既有 SQLite verification URL
指向不存在 parent 所造成的 database tests。Frontend 18 個失敗仍集中於
BacktestPanel、StockList、StockChart，與 P3 baseline 相同；P4 targeted 與 production
TypeScript build 均通過。

全 repo eslint 保留既有 3 errors（login `<a>`、phase2 page 與
StickyHorizontalScroll effect 同步 setState）及 2 個測試 warning；P4 變更檔 targeted
eslint 無錯誤。

## J. Rollback / Out of Scope

- 回滾可 revert P4 commit；不應以停止寫新 Review 的方式假裝 lifecycle 成功。
- P5 prompt family redesign、P6 完整 tracking dashboard/outcome analytics 不在本次。
- 不做歷史 snapshot outcome optimization，也不自動調 sustained caution threshold。
- 既有 30 日 archive 繼續做績效觀察，但不再代表 P4 lifecycle decision。
