# Phase 3I — Frozen Dual-Branch Shadow Validation

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。
> Frozen rule commit：`c094178c6c49274483c851ff010ef1427f103cc2`。
> Config hash：`a1b9b6306a17bb9b089c2695ac7dbdef29e6455d76827ec96614000f578d98a0`。
> 實際價格／正式 snapshot 只到 2026-07-27；未虛構 7/28 action。

## 結論先行

最終決策：**BOTH_BRANCHES_PENDING_SAMPLE**。

- Branch N：**NORMAL_PENDING_SAMPLE**。獨立驗證 cohort 中 NORMAL eligible=0；
  Dataset C 全部為 RISK_OFF，且 7/27 後尚無實際入庫交易日。Phase 3H 的
  historical n=5 不重複計入 Phase 3I。
- Branch R：**RISK_PENDING_SAMPLE**。Existing Pending baseline=140、R1 total
  selected=29，但 matured selected 只有
  5，matured dates=1；
  baseline matured Loser=6，尚未達 10。
- 7/27 有正式 WATCH snapshot，但 frozen raw-union／Top120 row frame／Phase2
  survivor row 只到 7/24。該日 episode denominator 保持 unavailable，
  不以 WATCH 24 檔冒充 Phase2 eligible universe。

這不是 PASS 或 FAIL。兩條 frozen rule 保持不變，後續只能補成熟結果與新的
實際交易日，不能再調 threshold、換 bundle 或開始 Phase 3J。

## Frozen config reproducibility

| branch | audit_item | source_rows | mismatch_rows | reproducible | validation_status |
|---|---|---|---|---|---|
| N | D0_A_RECOMPUTE | 168 | 0 | True | REPRODUCIBLE |
| N | D1_C_RECOMPUTE | 168 | 0 | True | REPRODUCIBLE |
| R | P4_MARKET_PLUS_SURVIVAL_RECOMPUTE | 473 | 0 | True | REPRODUCIBLE |
| BOTH | CONFIG_HASH | 1 | 0 | True | FROZEN |

Branch N D0-A mismatch=0、
D1-C mismatch=0；Branch R P4
mismatch=0。因此 frozen predicate
本身可完整重現，沒有標記 `FROZEN_RULE_NOT_REPRODUCIBLE`。

## Branch N — NORMAL Day1-C

- source rows=140；`EXCLUDED_NON_NORMAL`=140。
- eligible=0、Day1-C pass=0、selected dates=0。
- Promotion W/N/L=0/0/0；Safe、Winner Dominance、Top-K comparison 均
  `NOT_ASSESSABLE_NO_INDEPENDENT_NORMAL_SAMPLE`。
- Day3 不作 rescue；未產生任何 HIGH_CONVICTION promotion outcome。

## Branch R — Market + Survival

| policy | eligible_count | selected_count | matured_selected_count | pending_selected_count | selected_dates | coverage | winner_count | neutral_count | loser_count | loser_rate | safe_rate | winner_recall | loser_rate_reduction_vs_baseline_pp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R0_WEAK_MARKET_PHASE2_BASELINE | 140 | 140 | 18 | 122 | 10 | 1.0000 | 1 | 11 | 6 | 0.3333 | 0.6667 | 1.0000 | 0.0000 |
| R1_FROZEN_MARKET_PLUS_SURVIVAL | 140 | 29 | 5 | 24 | 6 | 0.2071 | 1 | 4 | 0 | 0.0000 | 1.0000 | 1.0000 | 33.3333 |
| R2_FROZEN_SURVIVAL_ONLY_DIAGNOSTIC | 140 | 68 | 5 | 63 | 10 | 0.4857 | 1 | 4 | 0 | 0.0000 | 1.0000 | 1.0000 | 33.3333 |

目前只有 2026-07-13 的 18 筆 episode 完成 Day10。R1 的全 pending+成熟
selected=29，Coverage=20.7%，但正式 outcome
指標只能使用 matured selected=5：
W/N/L=1/4/0、
Loser Rate=0.0%、Safe=100.0%。這些值全部標記
`EARLY_READ_ONLY`，不得和 Phase 3G Dataset B 歷史結果合併宣稱通過。

R1 zero-primary-date rate=40.0%。matured outcome
只來自 1 個日期，因此存在單日主導，尚不能檢驗跨日穩定性。

## Existing Pending vs Prospective

- Existing Pending：Dataset C 140 rows；R1 selected=29，
  matured selected=5。
- Prospective New Data：0 rows。2026-07-27 後尚無實際入庫交易日。
- 方向一致性：`NOT_ASSESSABLE_NO_PROSPECTIVE_DATA`，不是「一致」，也不是「相反」。

## Data quality

| branch | audit_item | affected_rows | denominator_rows | missing_rate | severity | data_quality_blocker |
|---|---|---|---|---|---|---|
| BOTH | FROZEN_RULE_REPRODUCIBILITY | 0.0000 | 473.0000 | 0.0000 | OK | False |
| R | MISSING_REQUIRED_SURVIVAL_FEATURE | 0.0000 | 140.0000 | 0.0000 | OK | False |
| N | INDEPENDENT_NORMAL_COHORT_AVAILABLE | 0.0000 | 140.0000 |  | SAMPLE_PENDING | False |
| BOTH | 2026_07_27_PHASE2_INPUT_SNAPSHOT |  |  |  | BLOCKER | True |
| BOTH | LATEST_SOURCE_DATES | 0.0000 | 0.0000 | 0.0000 | INFO | False |
| BOTH | FROZEN_COHORT_PRODUCTION_EXACTNESS | 140.0000 | 140.0000 | 0.0000 | WARNING | False |

`DATA_QUALITY_BLOCKER`=**YES**，原因是 7/27
無法還原完整 Phase2 input snapshot；Frozen Dataset C 本身仍依規格保留為
`RECONSTRUCTED_NOT_PRODUCTION_EXACT` Existing Pending cohort。

## 24 個必答答案

1. Frozen config 是否完整可重現：是；N/R mismatch 都為 0。
2. 驗證期間是否發生規則或程式版本變更：Frozen config 無變更；7/23 prompt v5→v6 已記錄，但不進兩條 deterministic rule。
3. Branch N eligible episodes：0。
4. Branch N Day1-C pass：0。
5. Branch N 涵蓋交易日：0。
6. Branch N Promotion W/N/L：0/0/0。
7. Branch N Promotion Safe Rate：NA。
8. Branch N Promotion Winner Dominance：NA。
9. Branch N Promotion Winner Count >=8：否，count=0。
10. Branch N 優於相近 Coverage Top-K：不可評估。
11. Branch N 通過所有正式門檻：否；狀態為 `NORMAL_PENDING_SAMPLE`，不是 FAIL。
12. Branch R baseline episodes：total=140，matured=18。
13. Branch R baseline Loser：matured Loser=6。
14. Market + Survival 保留：total=29，matured=5。
15. Branch R Loser Rate：0.0%，EARLY_READ_ONLY。
16. Branch R Safe Rate：100.0%，EARLY_READ_ONLY。
17. Branch R 相較 baseline 降低多少 Loser Rate：33.3 pp，僅單一成熟日期。
18. Branch R 0 檔日期比例：40.0%。
19. Branch R 是否錯刪所有 Winner：否；baseline Winner=1、R1 Winner=1。
20. Branch R 通過所有正式門檻：否；狀態為 `RISK_PENDING_SAMPLE`，不是 FAIL。
21. Existing Pending 與 Prospective 方向一致：不可評估，Prospective=0。
22. 是否有單一日期或股票主導：是；所有 matured R 結果來自 7/13。
23. 是否存在 DATA_QUALITY_BLOCKER：是；7/27 Phase2 row-level input 不可還原。
24. 最終決策：**BOTH_BRANCHES_PENDING_SAMPLE**。

## 禁止事項與 Phase 3 結束邊界

- 沒有修改 production、A/B/C/D、Top120、momentum_score、Hard Exclusion、
  Outcome、正式 WATCH／PRIMARY 或交易策略。
- 沒有用 Day3 rescue、Pocket gate、Watchlist action、模型、grid search 或
  portfolio backtest。
- Pending 沒有標成 Neutral；Excluded rows 保留在 row-level artifact。
- Phase 3I 後不開始 Phase 3J。只有 frozen shadow maturity update，
  或在兩條 branch 達正式 minimum sample 後進行一次最終 Go/No-Go。
