# P2 Financial and ETF Selection Parity Audit

稽核日：2026-07-29
政策：`COMMON_STOCK`、`FINANCIAL`、`ETF` 具有相同 selection status；商品類型只控制
證據是否適用，不是候選資格、hard gate、base eligibility、regime gate 或排序訊號。

## A. 結論

P2 已完成 production、legacy fallback、LLM prompt、snapshot/API 與 UI 的平權接線。
原有流動性、人工黑名單、結構破壞、複合風險、反轉失效、過熱 warning 等規則全部保留，
但不再因「金融股」或「ETF」這個身分淘汰。

不做歷史 snapshot 回填；新 snapshot 以 score/prompt version 明確區分。

## B. 找到的根因

1. A 產業榜在排序迴圈明確 `continue` 金融產業。
2. `build_candidate_pool()` 在 A 產業榜與個股榜同時為空時提前 return，B/C/D 無法獨立啟動。
3. legacy `filters._is_hard_excluded()` 仍透過 `should_exclude()` 刪除 ETF/金融。
4. Momentum Score 把基本面「缺漏」與「商品不適用」都當 0 分，無法表達 ETF N/A。
5. ETF 判斷主要依 `00` prefix/name heuristic，沒有優先使用 canonical classification。
6. v1/v4/v5 executable prompts 仍含 ETF/金融 hard removal 文字。
7. API/UI 沒有 score applicability 欄位，也無中性商品類型提示。

## C. Candidate admission 與 gate 修正

- A 產業 ranking：所有產業先按近 2 日法人淨買超排序取前 10；金融產業同規則。
- 當日賣超煞車：金融產業仍照常適用，沒有白名單。
- A/B/C：商品類型 invariant。
- D：只看實際可用的月營收證據；ETF 公司基本面不適用，因此不會靠缺資料進 D。
- A 空時仍計算 B/C/D；B-only、C-only、D-only、B+C+D 均有回歸測試。
- raw union 只排除非 active master 與人工黑名單。
- legacy hard filter 與 Phase 2 hard/base/regime gate 均不讀商品類型作 eligibility。
- `should_exclude()` 保留 API 相容，但政策語意收斂為「只判人工黑名單」。

## D. 商品分類來源

候選池一次批次查詢：

1. `etf_classification` 存在該代號 → `ETF`（ETN 也走 ETF evidence contract）。
2. `security_classification.is_financial=true` → `FINANCIAL`。
3. 其他 canonical security → `COMMON_STOCK`。
4. 分類缺席才 fallback 到名稱、產業與代號 heuristic；會寫 warning 與最多 20 個代號。

查詢包在 SAVEPOINT；舊部署尚未建立 additive classification tables 時，caller transaction
仍可繼續並使用 fallback。分類只影響 research/evidence applicability，不影響 admission。

## E. Momentum Score applicability contract

Production mode：

```text
SIGNALS_MOMENTUM_SCORE_MODE=applicability_aware
score version=v3_applicability_aware
```

基本面狀態：

| 資產 | 有實際公司月營收 | 無資料 |
|---|---|---|
| COMMON_STOCK | AVAILABLE，正常計分 | MISSING，0 分且不重配 |
| FINANCIAL | AVAILABLE，正常計分 | MISSING，0 分且不重配 |
| ETF | NOT_APPLICABLE | NOT_APPLICABLE |

ETF 的基本面 10 分從適用分母排除，核心 90 分按 `100/90` 正規化，之後才套用相同
risk penalties。一般股/金融股 MISSING 不正規化，避免把資料缺漏假裝成 N/A。

新增 debug/snapshot 欄位：

- `applicable_score_weight`
- `missing_score_weight`
- `not_applicable_score_weight`
- `score_before_penalty`
- `risk_penalty_total`
- `momentum_score`
- `fundamental_applicability`
- `momentum_score_version`

`SIGNALS_MOMENTUM_SCORE_MODE=legacy` 可重現歷史公式；只有在該模式下，
`SIGNALS_MOMENTUM_SCORE_AVAILABLE_WEIGHT` 才控制舊 v1/v2 missing-normalization。

## F. LLM、API、UI 與版本

- 預設 prompt version：`v6.1`。這是 v6 selection-authority 方法論的 parity 增量版。
- v6/v6.1 明確要求 ETF 改查追蹤指數、資產類別、策略、成分與曝險；公司營收、產品、
  供應鏈為 N/A，不可當弱勢。
- 金融股仍按公司研究；有營收正常驗證，沒有只能是 MISSING/UNCONFIRMED。
- v1/v4/v5 replay prompts 已移除資產類型 hard removal，避免 fallback 重新引入歧視。
- watchlist item 保留 `asset_type`；`signal_metrics` 以 optional additive 欄位輸出上述 debug。
- 歷史 snapshot 缺欄位時前端不顯示 applicability debug，不會 crash。
- 卡片與 dialog 使用完全相同的 slate 中性商品 badge；不改卡片色階、排序或權重。
- 動能 debug panel 將 `NOT_APPLICABLE` 顯示為「不適用（N/A）」，
  `MISSING` 顯示為「資料缺漏（Missing）」。
- `processing_summary` 新增 `momentum_score_version` 與 `momentum_score_mode`。

## G. 驗證證據

針對性 backend regression：

```text
318 passed
```

覆蓋金融 A ranking/當日賣超、B/C/D-only、可靠 ETF 分類、三資產等輸入 score、
ETF N/A normalization、一般股 missing、金融 available/missing、penalty-after-normalization、
legacy replay、legacy/Phase 2 hard/base/regime parity、prompt parity 與 pipeline snapshot。

Scale guard（`tracemalloc`，100/500/1000）：

| candidates | pytest call duration |
|---:|---:|
| 100 | < 0.005s |
| 500 | 0.01s |
| 1000 | 0.02s |

每組 peak allocation 由測試保證 `< 32 MiB`，每組時間保證 `< 2s`；實作為逐檔 O(n)
score，沒有 per-candidate DB query。

Frontend：

```text
P2/UI targeted: 7 passed
Full suite: 75 passed, 18 failed
```

完整 frontend suite 的 18 個失敗與 P1 baseline 相同，集中於 BacktestPanel、StockList、
StockChart；本次新增 4 個 badge tests 全數通過。全 repo lint/typecheck 另有本次變更前
既存問題：login page `<a>`、兩個 effect 同步 setState，以及 tsconfig 未載入 Jest globals。
本次變更檔的 targeted eslint 已通過。

Backend 完整 `tests/` run 為 1122 passed / 20 failed；其中 19 個是 P1 baseline 已知的
auth-disabled / rate-limit / watchlist 測試，另 1 個是 local SQLite verification URL
指向不存在的暫存 parent。P2 targeted 318 tests 無失敗。

## H. 風險、回滾與維運

- 回滾 production score：設 `SIGNALS_MOMENTUM_SCORE_MODE=legacy`；若需重現舊 v2，再設
  `SIGNALS_MOMENTUM_SCORE_AVAILABLE_WEIGHT=true`。
- 回滾 prompt 實驗：`SIGNALS_FORCE_PROMPT_VERSION=v6`（或 v1/v4/v5）。
- 不建議把 `should_exclude()` 恢復成商品類型 gate；這會讓 legacy fail-safe 與 Phase 2
  再次產生政策分叉。
- canonical classification 缺席不會阻斷 daily job，但 warning 應納入資料品質監控。
- ETF 若誤連到公司營收資料，仍固定為 NOT_APPLICABLE，不會吃到錯誤基本面分數。
