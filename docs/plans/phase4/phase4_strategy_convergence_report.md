# Phase 4A / 4B — Bull-Market Strategy Convergence

> 研究日：2026-07-29。純研究；production 零修改。Phase 4 strategy 與 data split 均在 outcome evaluation 前封存並驗證 SHA256。

## 1. Corrected Timeline

| execution date | status | experiments |
|---|---|---|
| 2026-07-24 | SHADOW_ONLY | P3A_PERSISTENCE_DIRECT_EXIT |
| 2026-07-27 | INCOMPLETE | P3D_FUNDAMENTAL_CHANNEL_D |
| 2026-07-27 | PASS | P3D_MOMENTUM_RANK_GRADIENT |
| 2026-07-27 | REJECT | P3B_SOURCE_GROUP_AUTO_REMOVAL, P3C_RISKOFF_FAILURE_VETO, P3D_FIXED_SIZE_REPLACEMENT |
| 2026-07-27 | SHADOW_ONLY | P3C_CANDIDATE_DISCOVERY_RECALL |
| 2026-07-27 | WEAK | P3B_AC_SOURCE_EFFICIENCY |
| 2026-07-28 | INCOMPLETE | P3I_NORMAL_FROZEN_BRANCH, P3I_RISK_FROZEN_BRANCH |
| 2026-07-28 | REJECT | P3G_POCKET_INCREMENT, P3G_EARLY_MARKET_TRANSITION, P3H_DAY0_POSITIVE_BUNDLES |
| 2026-07-28 | SHADOW_ONLY | P3G_POINT_IN_TIME_WATCHLIST, P3H_FIXED_TOPK |
| 2026-07-28 | WEAK | P3E_RISKOFF_EXTREME_ACCELERATION_FILTER, P3F_REGIME_ADAPTIVE_CONTRACTION, P3G_STOCK_SURVIVAL_GATE, P3G_MARKET_PLUS_SURVIVAL, P3H_DAY1_DAY3_CONFIRMATION |

Phase 3G 的較嚴謹 attribution 覆蓋 Phase 3F：Pocket 為 `REJECT`；修正 Outcome leakage 後的 watchlist 仍僅 `SHADOW_ONLY`。Phase 3I 沒有把 Phase 3H/3G 升級，因為兩條 frozen branch 都沒有足夠獨立成熟樣本。

## 2. Experiment Ledger

| status | count |
|---|---:|
| PASS | 1 |
| WEAK | 6 |
| SHADOW_ONLY | 4 |
| REJECT | 6 |
| INCOMPLETE | 3 |

完整逐實驗欄位見 `phase4_experiment_ledger.csv`；人類可讀時間線見 `phase4_experiment_ledger.md`。

## 3. Eligible Evidence

唯一可進自動 Phase 4 的證據是 `P3D_MOMENTUM_RANK_GRADIENT`（`PASS / RANK`）。它確認既有 momentum order 有穩定 Winner 梯度，但也確認前段 Big Loser 較高，所以不允許把它改寫成 hard filter。沒有任何 `PASS / FILTER`，也沒有任何已通過的固定 Top-K。

## 4. Frozen Strategies

| strategy | availability | exact behavior |
|---|---|---|
| S0_BASELINE | AVAILABLE | keep every baseline daily candidate event |
| S1_VALIDATED_CONSERVATIVE | NOT_AVAILABLE | No PASS, bull-eligible evidence permits FILTER usage. |
| S2_VALIDATED_RANKING | AVAILABLE | keep every baseline daily candidate event; rank by the existing production/replay candidate order; do not apply a fixed Top-K because no fixed K is PASS evidence |
| S3_VALIDATED_HYBRID | NOT_AVAILABLE | S1 is unavailable; hybrid requires both S1 and S2. |

Strategy manifest SHA256：`383d2249dd4f342821458d9abaa7322fec32927126d9dff877b77ebf3e69422d`。

## 5. Development Tournament

- Regime enum：`BULL_TREND`。
- Replay sample range：2026-04-13～2026-06-05。
- Development bull dates：8；Holdout bull dates：1。
- Development daily candidate count：min=120、median=120.0、max=120。
- Primary sector 不存在於 frozen Phase 3D raw-union snapshot，因此欄位保留 null；未從未封存來源回填。
- Split 單位是已重建的 replay sample date；不是宣稱擁有連續 production snapshot。

| strategy | n kept/original | compression | winner retention | big-loser removal | mean Δ pp | mean Δ 95% CI | decision |
|---|---:|---:|---:|---:|---:|---:|---|
| S0_BASELINE | 960/960 | 0.0% | 100.0% | 0.0% | 0.000 | [0.000, 0.000] | REFERENCE |
| S2_VALIDATED_RANKING | 960/960 | 0.0% | 100.0% | 0.0% | 0.000 | [0.000, 0.000] | WEAK_GATE_3 |

Bootstrap 使用交易日為 block，固定 seed=20260729、5000 次；沒有把同日股票視為互相獨立。

## 6. Champion Decision

`S2_VALIDATED_RANKING` 保留 960/960 筆，Winner Retention=100.0%、Mean Δ=0.000pp，但 Big Loser Removal=0.0%，在 Gate 3 即判定 `WEAK`；因為沒有 PASS Top-K，不能把排序任意截斷來製造壓縮。

Development 沒有 Challenger 通過全部四關，故沒有建立 `phase4_champion_manifest.json`。

## 7. Holdout Result

**Holdout not executed because no development strategy passed all gates.**

## 8. Final Decision

# NO_CHAMPION

目前 Phase 3A～3I 的既有證據不足以形成安全的 Day0 Candidate Compression。正向的 momentum ranking 證據只支持排序，不支持未驗證的刪除門檻。

## 9. Prohibited Follow-up

- 不得根據 Holdout 修改策略或用同一 Holdout 重測修改版。
- 不得由 removed Winner 個案新增 stock-specific 例外。
- 不得降低 95% Winner Retention 門檻。
- 不得為湊 Top 20 增加未驗證規則或測試 Top 15/18/20/22/25。
- 不得把 WEAK、SHADOW_ONLY、REJECT 或 INCOMPLETE evidence 改名後放入自動壓縮。
- 不得啟動 Phase 3J、修改 production、LLM、A/B/C/D、Phase 2/2.5、Hard Exclusion 或做 portfolio backtest。
