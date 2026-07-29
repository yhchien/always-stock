# P5 Prompt Family v7 Consolidation Audit

稽核日：2026-07-29
政策：P5 只收斂 Prompt 政策、階段責任、輸入／輸出契約與版本追蹤，不改變
P0～P4 的候選資格、全體比較、推薦桶或 observation state machine。

## A. 稽核結論

Production 預設已切換為 `SIGNALS_PROMPT_FAMILY=v7`。六個 LLM stage 使用同一份
`shared-policy-v7.md`，再與各自的 stage prompt deterministic 組裝。每次 call 記錄
family、shared/stage version、assembled SHA256 與 payload metrics；未知 family
直接 fail closed，不會靜默落回 v1。

Rollback 僅允許 `legacy_split`，固定映射至 P2～P4 已驗證的 v6.1、
`p3_assessment_v1`、`p3_global_v1`、`p3_reason_v1`、`p4_tracking_v1`。
它不會恢復 WATCH-all、資產類型排除、沒有 global selector 或沒有 P4 lifecycle 的舊流程。

## B. Prompt Family 與職責

| Stage | v7 file / version | 唯一職責 | 不得輸出 |
|---|---|---|---|
| Shared | `shared-policy-v7.md` / `v7` | Backend 權威、時間點隔離、資產平權、繁中、安全邊界 | stage decision |
| Research | `candidate-research-v7.md` / `v7_research` | 外部業務、題材、曝險、催化劑、矛盾與來源 | selection / lifecycle decision |
| Assessment | `candidate-assessment-v7.md` / `v7_assessment` | 逐檔 eligibility / legal veto assessment | RECOMMEND / NOT_SELECTED |
| Global | `global-recommendation-selector-v7.md` / `v7_global_selector` | 所有 eligible cards 一次全體比較 | REMOVE / quota / Top-K |
| Reason | `recommendation-reason-v7.md` / `v7_reason` | 只為 RECOMMEND 產生六段繁中說明 | 重新決策 |
| Tracking | `tracking-review-v7.md` / `v7_tracking` | 截至 review date 的外部 thesis assessment | CONTINUE / CAUTION / STOP |

P4 state machine 仍為 `p4_state_v1`，沒有因 Prompt 改版而 bump。

## C. Router、Composition 與 SHA

`backend/app/signals/prompt_family.py` 是集中式 registry。組裝器：

1. 讀取單一 shared policy；
2. 加入固定 stage boundary；
3. 加入一份 stage prompt；
4. 對實際 assembled bytes 計算 SHA256；
5. 以 in-process cache 避免每次 call 重讀檔案。

Snapshot `processing_summary` 保存：

```text
prompt_family_version
shared_policy_version
research_prompt_version
assessment_prompt_version
global_selector_version
reason_prompt_version
tracking_prompt_version
tracking_state_machine_version
prompt_sha256.{research,assessment,global_selector,reason,tracking}
```

P4 Review 在 `backend_evidence_json._prompt_metadata` 保存同一份 family metadata，
API timeline 另投影 family、shared version 與 tracking assembled SHA。舊 snapshot/review
沒有欄位時仍以 optional/null contract 顯示，不會 crash。

## D. Input Allowlist 與 Payload Metrics

- Research 只接標的身分、資產類型、產業、role、題材候選及必要 research context。
- Assessment 只接 backend max、freshness、quality evidence 與 research validations。
- Global Selector 只接 date、selection version 與完整 compact cards。
- Reason 只接已推薦論點、相對優勢、必要 research/backend/evidence/margin/momentum 摘要。
- Tracking 只接 initial thesis、current backend evidence summary 與 latest valid review。

每次 `_call_llm_json` diagnostic 保存：

```text
candidate_count
serialized_bytes
estimated_input_tokens
estimated_output_reserve
model_context_limit
```

一般 production log 不輸出完整 Prompt 或 research payload。

Synthetic research input 對照（相同 25/50/100/200 candidates；legacy evidence view
對 v7 allowlist；bytes 含 user payload，不含 output）：

| candidates | legacy bytes | v7 bytes | reduction |
|---:|---:|---:|---:|
| 25 | 45,766 | 8,371 | 81.7% |
| 50 | 91,541 | 16,721 | 81.7% |
| 100 | 183,091 | 33,421 | 81.7% |
| 200 | 366,291 | 66,921 | 81.7% |

各組 allowlist 建立與 serialization `< 2s`、peak allocation `< 32 MiB`；候選全量
O(n) 處理，沒有 total candidate cap。Global/Tracking 的 assembled system prompt
因納入 shared policy 會比舊單檔略大，但 user payload 已移除整包 snapshot/history。

## E. Backend Validators

- Research：date、完整一對一 stock alignment、duplicate/missing/unknown、validation enum、
  theme enum、source type、URL、published date、future evidence、material contradiction。
- Assessment：完整 alignment、二態 assessment、quality enum、veto enum、URL/date；
  之後仍由 P3 backend 驗證 factual/quality veto prerequisites。
- Global Selector：原 P3 O(n) schema/alignment/rank/reason/overlap/rank override validator
  原封保留，只把 expected version 改由 family router 注入。
- Reason：六個非空 bullet sections、段落數量與 margin object。
- Tracking：原 P4 assessment、mismatch、invalidation reason、material evidence 與
  review-date boundary validator原封保留；最後 action 仍由 `p4_state_v1` 決定。

非法輸出會成為該 stage technical failure；不會轉成 REMOVE、NOT_SELECTED、
RECOMMEND、CAUTION 或 STOP。

## F. Point-in-time、Injection 與語言

Shared Policy 明確要求外部資料不得晚於 `date`／`review_date`，並將網頁文字視為資料而非
指令。所有 stage prompt 禁止 BUY／SELL、目標價、預測報酬、持倉與停損停利建議；
人類欄位使用繁體中文，enum 與 JSON key 維持英文。共用術語見
`docs/signals/v7_terminology.md`。

## G. Replay、Workflow、UI 與回滾

- P3 replay：`run_v6_llm_validation.py ... --prompt-family v7|legacy_split`
- P4 replay：`run_p4_tracking_replay.py ... --prompt-family v7|legacy_split`
- replay output 包含完整 family/stage/SHA metadata，仍為 read-only 且遵守 point-in-time。
- GitHub daily workflow 的手動參數收斂為 `prompt_family` choice，預設 v7。
- Daily Signals pipeline funnel 顯示 `Prompt v7` badge，title 提供六個 stage version；
  歷史資料缺欄位時不顯示。

Rollback：

```text
SIGNALS_PROMPT_FAMILY=legacy_split
```

不再用 `SIGNALS_FORCE_PROMPT_VERSION` 控制 production family；舊 v1/v4/v5 force
只在明確選擇 `legacy_split` 的 replay regression 中保留。

## H. Executable Prompt Audit

| Prompt | Classification | Activation |
|---|---|---|
| v7 family | `ACTIVE` | production default |
| v6.1 + P3/P4 split prompts | `ROLLBACK_SUPPORTED` | only `legacy_split` |
| v1 / v4 / v5 | `REPLAY_ONLY` | legacy caller explicit version tests/replay |
| older combined WATCH-all behavior | `DEPRECATED` | no router entry |
| unknown family | `REJECTED` | fail closed |

## I. Verification

P5 focused tests cover routing、composition、SHA、allowlists、strict schemas、future dates、
prompt injection policy、v7 adapter、0-recommend selector 與 25/50/100/200 scale guard。
P0～P4 focused regressions continue to cover asset parity、no truncation、global atomicity、
veto prerequisites、observation state machine、idempotency、conflicts and replay.

實際結果：

```text
Backend P5 focused:          17 passed
Backend P0-P5 focused:       170 passed
Backend full tests/:         1204 passed, 21 failed
Frontend P2-P5 targeted:     16 passed
Frontend full:               84 passed, 18 failed
Frontend production build:   passed
```

Backend 21 個失敗與 P4 local baseline 相同：19 個 auth-disabled／rate-limit／watchlist
測試，以及 2 個 local database environment failures（目前 `.env` 指向 remote PostgreSQL，
sandbox 無法解析 host，測試則預期 SQLite）。P5 新增／修改的 focused tests 無失敗。

Frontend 18 個失敗與 P4 baseline 相同，集中於 BacktestPanel、StockList、StockChart；
P2～P5 targeted 與 production TypeScript build 均通過。全 repo eslint 仍是既有 3 errors
（login `<a>`、phase2 page 與 StickyHorizontalScroll effect 同步 setState）及 2 個
測試 warning；P5 變更檔 targeted eslint 無錯誤。
