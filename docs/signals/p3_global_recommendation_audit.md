# P3 Global Recommendation Selection Audit

稽核日：2026-07-29
政策：Phase 2 eligibility 與「今日正式推薦」分離。所有研究成功、assessment 成功且
沒有真實 veto 的候選，必須在同一次 global selection 中完整比較；推薦數為 0 到全部，
不得使用固定 Top-K、比例、rank cutoff、source/asset/cluster quota。

## A. 稽核結論

P3 已完成 production pipeline、LLM contract、原子驗證、snapshot/API、observation、
replay 與 UI 接線。

改造前：

```text
Phase 2 eligible → 分批逐檔 WATCH/REMOVE → WATCH 直接成正式清單
```

改造後：

```text
A/B/C/D raw union
→ Phase 2 全部 llm_eligible
→ batch research
→ 逐檔 eligibility / true-veto assessment
→ true REMOVE 分離
→ 所有其餘候選建立 Compact Selection Cards
→ one-shot Global Selector
→ RECOMMEND / NOT_SELECTED
→ 只有 RECOMMEND 產生長理由、正式 observation 與主清單 rank
```

## B. File / Symbol Audit

| File | Symbol / surface | 改造前 | P3 要求 | Status |
|---|---|---|---|---|
| `backend/app/signals/pipeline.py` | `run_signal_pipeline_sync` | 每檔 WATCH 直接進長理由與 final watchlist | assessment 後完整全體比較；三桶與技術失敗分離 | `FOUND_AND_FIXED` |
| `backend/app/signals/llm_caller.py` | `run_explanation_batch` / `_run_decision_chunk` | WATCH/REMOVE 同時扮演 eligibility 與正式決策 | 收斂為 ELIGIBLE/REMOVE assessment；ELIGIBLE 非正式推薦 | `FOUND_AND_FIXED` |
| `backend/app/signals/global_selector.py` | cards / capacity / validation | 不存在 | one-shot cards、0..all、完整 alignment、rank override、reason enum | `ADDED` |
| `backend/app/prompts/global-recommendation-selector-v1.md` | `p3_global_v1` | 不存在 | 明確禁止 Top-K/ratio/quota/cap，僅 RECOMMEND/NOT_SELECTED | `ADDED` |
| `backend/app/signals/llm_caller.py` | `assemble_final_output` | 只輸出 WATCH，REMOVE 未進 payload | watchlist=RECOMMEND、not_selected、true removed 分桶 | `FOUND_AND_FIXED` |
| `backend/app/signals/archive.py` | `persist_signal_watch_hits` | final watchlist 全部新增 hit | 因主清單只含 RECOMMEND，自然只對推薦新增 observation | `VERIFIED` |
| `backend/app/routers/signals.py` | `_serialize_snapshot` | watchlist/removed/summary | additive `not_selected`、`technical_failures`；舊 snapshot 空陣列 | `FOUND_AND_FIXED` |
| `frontend/src/lib/api.ts` | signal contracts | 無 P3 status/rank/reason/capacity | optional additive P3 types | `FOUND_AND_FIXED` |
| `frontend/src/components/DailySignalsPanel.tsx` | main list | WATCH 全部顯示 | 主清單只顯示 RECOMMEND；中性未入選折疊區 | `FOUND_AND_FIXED` |
| `frontend/src/components/SignalProcessingSummary.tsx` | funnel/warning | Research/Decision/Unprocessed | Global/Recommend/Not selected/Removed/technical 與原子失敗警示 | `FOUND_AND_FIXED` |
| `backend/run_v6_llm_validation.py` | point-in-time replay | v6 WATCH/REMOVE replay | v6.1 research + P3 三態、versions、cards；明示不用 outcome | `FOUND_AND_FIXED` |

## C. Decision Semantics 與 Guardrails

最終狀態：

- `RECOMMEND`：有正向且連貫 thesis，並具同日相對優勢。只有這一桶進主
  `watchlist`、有 `recommendation_rank`、長理由與新 observation。
- `NOT_SELECTED`：候選仍有效，但正向案例或相對優勢不足。一定有
  `selection_reason_code/selection_reason`，永遠沒有 `veto_reason`，不代表永久負面或停止追蹤。
- `REMOVE`：只接受 backend max、具前提的 factual veto 或具 Phase 2 evidence 的
  quality veto；在 global selector 前決定。
- `RESEARCH_FAILED` / `DECISION_FAILED`：技術處理失敗，不轉成 REMOVE。
- `GLOBAL_SELECTION_FAILED`：全體比較原子失敗，不產生任何正式推薦。

`UNCONFIRMED` 不等於 REMOVE。若 LLM 嘗試以 `BUSINESS_MISMATCH` REMOVE，但欄位實際為
`UNCONFIRMED`，backend 會拒絕這個 veto，清掉 `veto_reason`，仍送入全體比較。

Quality veto 必須同時有：

1. `momentum_freshness`；
2. `quality_evidence`；
3. 對應 quality assessment 為 `LOW` 或 `WEAK`。

`MATERIAL_NEGATIVE_EVENT` / `DATA_CONTRADICTION` 另須有
`veto_evidence.summary` 與至少一個可追溯來源 URL；一般短理由不足以構成 factual veto。

## D. Global Selection Contract

每個 eligible 恰有一張同 schema card，依現行 deterministic processing order 標：

- `backend_priority_rank`
- `backend_priority_total`
- `backend_priority_percentile`

Rank 是 backbone/tie preference，不是 cutoff。只要較低 backend rank 被推薦而較高者
未入選，該推薦必須提供 `rank_override=true`、`rank_override_reason` 與
`relative_advantage`。

`NOT_SELECTED` reason code 固定九種：

```text
LOWER_RELATIVE_PRIORITY
POSITIVE_CASE_INCOMPLETE
CATALYST_UNCONFIRMED
PARTICIPATION_NOT_DISTINCTIVE
EVIDENCE_COHERENCE_WEAK
THESIS_OVERLAP
SETUP_NEEDS_CONFIRMATION
RESEARCH_CONFIDENCE_LOW
NO_DISTINCT_DAILY_EDGE
```

`THESIS_OVERLAP` 額外要求合法 `overlap_with` 與 `overlap_reason`。同產業、同題材、
同集團本身不觸發上限；8 檔同 cluster 全部推薦是合法輸出。

Backend 以 O(n) set/map 驗證：

- selection version/date/complete
- 全體一對一 alignment
- missing/duplicate/unknown stock
- decision enum
- NOT_SELECTED reason enum與必填文字
- RECOMMEND thesis/relative advantage/basis
- 連續、唯一、從 1 起的推薦 rank
- rank override 與 overlap 前提

## E. Atomic Failure、容量與 Observation

Compact cards 的候選數、serialized bytes、保守 estimated input tokens、output reserve
與 model context limit 全部寫入 `processing_summary`。若單次完整 payload 超限，直接
`GLOBAL_SELECTION_CONTEXT_EXCEEDED`；沒有 tournament 或部分 selection。

任何 selector API/schema/alignment 失敗：

- `global_selection_status=FAILED`
- `selection_complete=false`
- job=`partial_failure`
- `watchlist=[]`、`not_selected=[]`
- 保存 true removed、compact cards、diagnostic 與 error
- 不呼叫長理由、不寫 `signal_watch_hits`
- 不 fallback 成全推薦、全未入選或全 REMOVE

成功時只有 `RECOMMEND` 進 `persist_signal_watch_hits()`。NOT_SELECTED/REMOVE 不刪除
之前日期的 hit、不封存、不重設既有追蹤週期；tracking review/action 留給 P4。
若同日重跑原本成功、但新一輪 selector 原子失敗，只清除該 snapshot date 的 stale hit，
更早日期的 active-cycle hits 完全保留。

## F. Versions、API 與 UI

版本欄位分開記錄：

```text
research_prompt_version = v6.1
assessment_prompt_version = p3_assessment_v1
global_selector_version = p3_global_v1
reason_prompt_version = p3_reason_v1
```

既有 snapshot `prompt_version` 仍保留給舊 consumer；以上四個明確版本另存在
`processing_summary`，避免用單一 label 混淆不同 stage。

API 相容保留 `watchlist/removed/summary`，新增：

- `not_selected`
- `technical_failures`
- `summary.selection_summary`

UI 主列表只讀 `watchlist`。未入選以 slate 中性折疊區顯示股票、商品 badge、
backend rank、theme cluster、reason code/reason。Global selection 失敗時明確顯示
「本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單」。歷史 snapshot
缺欄位時不顯示折疊區。

## G. Test 與 Scale Evidence

Backend P3 targeted 覆蓋：

- 8 檔全推薦、8 檔全未入選、mixed
- 同 cluster 無 cap、THESIS_OVERLAP 前提
- rank backbone/override、無 cutoff
- common/financial/ETF 與 A/B/C source 描述性平權
- UNCONFIRMED 不 REMOVE、MISMATCH 真實 REMOVE、quality veto prerequisites
- 37 cards missing/duplicate/unknown/rank duplicate/invalid reason 原子失敗
- context exceeded 與 LLM failure 無 fallback
- 10 RECOMMEND / 20 NOT_SELECTED / 5 REMOVE：只有 10 檔長理由與 observation
- global failure 保存 cards 且 0 observation
- API 四桶分離

Synthetic scale guard 使用 25/50/100/200 cards，逐組驗證：

- compact serialization
- payload bytes / estimated tokens
- O(n) alignment validation
- final assembly 與 API response serialization
- duration `< 2s`
- peak allocation `< 32 MiB`

本機實測（相同 synthetic schema）：

| cards | serialized bytes | estimated input tokens | validation | peak allocation |
|---:|---:|---:|---:|---:|
| 25 | 32,541 | 12,847 | 0.116 ms | 0.322 MiB |
| 50 | 65,089 | 23,697 | 0.193 ms | 0.617 MiB |
| 100 | 130,285 | 45,429 | 0.382 ms | 1.208 MiB |
| 200 | 260,675 | 88,892 | 0.731 ms | 2.417 MiB |

Frontend targeted 覆蓋：

- processing funnel 新計數
- atomic failure warning
- 歷史空 bucket 安全
- NOT_SELECTED 折疊區與 P2 ETF/金融中性 badge

實際結果：

```text
Backend P3 focused:           124 passed
Backend full tests/:          1147 passed, 20 failed
Frontend P2/P3 targeted:      11 passed
Frontend full:                79 passed, 18 failed
```

P3 focused 124 個測試全部通過。完整 backend 的 20 個失敗與 P2 基線數量相同：
19 個 auth-disabled/rate-limit/watchlist 測試，加上 local SQLite verification URL 被
既有 path normalization 指到不存在 parent。P3 新增/修改測試無失敗。

Frontend 完整 suite 的 18 個失敗與 P2 基線完全相同，集中於 BacktestPanel、
StockList、StockChart；本次 P2/P3 targeted 11 個測試全數通過。全 repo lint 仍是既有
3 errors：login page `<a>`，以及 phase2 page / StickyHorizontalScroll 的 effect 同步
setState；本次變更檔 targeted eslint 通過。

## H. Out of Scope / Rollback

本次沒有實作：

- P4 tracking review/actions/lifecycle
- P5 完整 v7 research redesign
- P6 tracking 全 UI 與 outcome dashboard
- 歷史 snapshot 回填
- outcome-optimized replay

回滾 global selector 可 revert P3 commit；不應以固定 Top-K 或把 ELIGIBLE 直接恢復成
正式推薦作為長期 fallback。若 production selector 失敗，設計上的安全狀態就是保存研究、
0 正式推薦、partial failure，待修復或重跑。
