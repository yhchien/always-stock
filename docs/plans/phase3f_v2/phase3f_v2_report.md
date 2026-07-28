# Phase 3F v2：Current-Data Regime-Adaptive Momentum & Watchlist Audit

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。  
> 主樣本：2026-05-28～2026-07-24 的 40 個連續交易日。  
> Episode 可比口徑：Phase 3E frozen cohort（A=246、B=87、C=140）；5 日 reset 僅列敏感度，原因見限制。  
> 資料標記：`RECONSTRUCTED_NOT_PRODUCTION_EXACT`。空頭 Winner 結論：`INSUFFICIENT_DOWNTREND_WINNER_SAMPLE / LOW_WINNER_SAMPLE`。

## 結論先行

最終分類：**LOSER_CONTROL_ONLY**。

Dataset B baseline Loser Rate 為 44.8%。在不使用 Dataset B
調 threshold 的前提下，表現最佳的固定政策是 **POLICY_3_POCKET_STOCK_SURVIVAL**；selected
episodes=31、Safe Rate=
80.6%、Loser Rate=
19.4%、Coverage=
35.6%。目前只有 1 個 Dataset B
Winner，因此這只能回答 Loser control / contraction / abstention，不能宣稱已建立
空頭 Winner 模型。

## 研究口徑與限制

- Phase 3E 的 246／87／140 是「40 日視窗內首次出現」的 frozen cohort；嚴格把
  離開 Top120 5 日後重進全算新 episode，會改變既有 denominator。由於缺少視窗前
  frozen Top120 與完整 production WATCH gaps，本報告保留 frozen cohort 作主結果，
  另在 episode/lifecycle 檔保留 `RESET_GAP5_SENSITIVITY`，不冒充 production-exact。
- Dataset C 全部維持 `PENDING_FORWARD`，Day1/3/5 只作 live shadow，未參與
  outcome、threshold、bundle 選擇或 policy 選擇。
- Policy 0 歷史 deterministic survivors 不完整，主統計使用
  `RECONSTRUCTED_TOP120_FIRST_SEEN_PROXY`；正式 WATCH 只用於 Part D 與 7/22～7/24。
- Watchlist 評估狀態：`AVAILABLE`（completed=314）。

## Part A — Market Path

Dataset A 固定的三個候選 threshold（全部在套用 Dataset B 前決定）：

| feature | dataset_a_p30 | first_weak_date |
|---|---|---|
| stocks_above_ma20_pct | 39.21 | 2026-07-07 |
| breadth_change_3d | -6.74 | 2026-07-08 |
| market_return_5d | -2.01 | 2026-07-08 |

2026-07-02 後最早 Market Path WEAKENING/RISK_OFF：**2026-07-08**；
production 首次 RISK_OFF：**2026-07-14**；相差 **3 個交易日**。

| dataset | market_path_state | selected_count | winner_rate | neutral_rate | loser_rate | safe_rate | winner_dominance |
|---|---|---|---|---|---|---|---|
| A | NORMAL | 168 | 8.9% | 79.8% | 11.3% | 88.7% | 10.1% |
| A | RISK_OFF | 35 | 11.4% | 68.6% | 20.0% | 80.0% | 14.3% |
| A | WEAKENING | 43 | 7.0% | 65.1% | 27.9% | 72.1% | 9.7% |
| B | NORMAL | 63 | 1.6% | 52.4% | 46.0% | 54.0% | 2.9% |
| B | RISK_OFF | 13 | 0.0% | 46.2% | 53.8% | 46.2% | 0.0% |
| B | WEAKENING | 11 | 0.0% | 72.7% | 27.3% | 72.7% | 0.0% |
| A+B | NORMAL | 231 | 6.9% | 72.3% | 20.8% | 79.2% | 8.7% |
| A+B | RISK_OFF | 48 | 8.3% | 62.5% | 29.2% | 70.8% | 11.8% |
| A+B | WEAKENING | 54 | 5.6% | 66.7% | 27.8% | 72.2% | 7.7% |

## Part B — Momentum Pocket

- Dataset B 唯一 Winner：6416(NARROW_LEADERSHIP)。
- Dataset B Loser 位於 NO_POCKET：7/39
  （17.9%）。
- CONFIRMED_POCKET：n=15，Safe Rate=40.0%；
  NO_POCKET：n=13，Safe Rate=46.2%。
- NARROW_LEADERSHIP：n=17，Winner/Neutral/Loser=
  1/10/6。

## Part C — Policy 比較

| policy | selected_count | selected_dates | average_selected_per_day | zero_primary_date_rate | coverage | winner_rate | neutral_rate | loser_rate | safe_rate | winner_recall | loser_removal_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POLICY_0_CURRENT_BASELINE_PROXY | 87 | 6 | 14.50 | 0.0% | 100.0% | 1.1% | 54.0% | 44.8% | 55.2% | 100.0% | 0.0% |
| POLICY_1_MARKET_CONTRACTION | 67 | 6 | 11.17 | 0.0% | 77.0% | 1.5% | 53.7% | 44.8% | 55.2% | 100.0% | 23.1% |
| POLICY_2_POCKET_GATE | 62 | 5 | 10.33 | 16.7% | 71.3% | 1.6% | 54.8% | 43.5% | 56.5% | 100.0% | 30.8% |
| POLICY_3_POCKET_STOCK_SURVIVAL | 31 | 5 | 5.17 | 16.7% | 35.6% | 3.2% | 77.4% | 19.4% | 80.6% | 100.0% | 84.6% |
| BUNDLE_A_DIAGNOSTIC | 36 | 6 | 6.00 | 0.0% | 41.4% | 2.8% | 72.2% | 25.0% | 75.0% | 100.0% | 76.9% |
| BUNDLE_B_DIAGNOSTIC | 32 | 6 | 5.33 | 0.0% | 36.8% | 3.1% | 68.8% | 28.1% | 71.9% | 100.0% | 76.9% |
| BUNDLE_C_DIAGNOSTIC | 15 | 5 | 2.50 | 16.7% | 17.2% | 0.0% | 66.7% | 33.3% | 66.7% | 0.0% | 87.2% |

Bundle 由 Dataset A 選擇，固定為 **bundle_A**；Dataset B 不重新調整。

| policy | selected_count | coverage | loser_rate | safe_rate | winner_recall | loser_removal_rate |
|---|---|---|---|---|---|---|
| BUNDLE_A_DIAGNOSTIC | 36 | 41.4% | 25.0% | 75.0% | 100.0% | 76.9% |
| BUNDLE_B_DIAGNOSTIC | 32 | 36.8% | 28.1% | 71.9% | 100.0% | 76.9% |
| BUNDLE_C_DIAGNOSTIC | 15 | 17.2% | 33.3% | 66.7% | 0.0% | 87.2% |

## Part D — Watchlist Lifecycle

- Loser Early Warning Rate：67.1%
- Median Warning Lead Time：3.0
- Winner Premature Removal Rate：11.9%
- Winner Retention Until Target：9.5%

| lifecycle_state | n | winner_rate | neutral_rate | loser_rate |
|---|---|---|---|---|
| CONTINUATION | 206 | 32.0% | 58.7% | 9.2% |
| DETERIORATING | 157 | 16.6% | 70.1% | 13.4% |
| HEALTHY_PULLBACK | 87 | 19.5% | 56.3% | 24.1% |
| NEW_DISCOVERY | 333 | 6.9% | 70.0% | 23.1% |
| REACCELERATING | 197 | 12.7% | 73.6% | 13.7% |
| STALE | 1 | 100.0% | 0.0% | 0.0% |

## 20 個必答問題

1. **最早轉弱證據**：2026-07-08。
2. **production 晚多久**：3 個交易日（production RISK_OFF=2026-07-14）。
3. **最早轉弱 feature**：見 Part A threshold 表；規則只用 breadth20、breadth change 3d、market return 5d。
4. **NORMAL／WEAKENING／RISK_OFF baseline**：見 Part A 表，A/B 分開揭露。
5. **Opportunity Density 是否更早**：7/2 後第一個 LOW/VERY_LOW 日期為
   2026-07-07；
   它只使用當時已知 Day1/Day3，未看 Day10。
6. **Dataset B 唯一 Winner pocket**：NARROW_LEADERSHIP。
7. **Loser 是否集中 NO_POCKET**：17.9%。
8. **CONFIRMED 是否改善 Safe Rate**：confirmed=40.0%，
   no-pocket=46.2%。
9. **NARROW Outcome**：W/N/L=1/10/6。
10. **最有效 Policy**：POLICY_3_POCKET_STOCK_SURVIVAL（以 Dataset B Loser control 比較，不用它調 threshold）。
11. **Dataset A 是否大量錯刪 Winner**：該政策 Winner Recall=
    22.7%。
12. **NORMAL 提高 Winner Dominance**：最佳描述值=
    18.4%；成熟 Winner<10 的切片一律 `LOW_WINNER_SAMPLE`。
13. **WEAKENING 平均每日檔數**：
    1.25（不是 production 固定 cap）。
14. **RISK_OFF + NO_POCKET 是否 0 檔**：樣本 n=5、Loser Rate=
    40.0%；Policy 2/3 會 abstain。
15. **Bundle A/B/C**：Dataset A 固定選 bundle_A；B 結果見表。
16. **NEW／CONTINUATION／REACCELERATING／STALE**：見 lifecycle 表；這是 candidate-day，
    未與 first-seen denominator 混用。
17. **能否提前警示 -6%**：67.1%，
    樣本限制 `AVAILABLE`。
18. **是否錯殺 Winner**：Premature Removal=11.9%。
19. **7/22～7/24 分類**：逐檔見 `phase3f_v2_20260722_20260724_audit.csv`，共 50 列。
20. **唯一最終結論**：**LOSER_CONTROL_ONLY**。

## 資料工程附錄

- `D_DATA_GAP`：最近月營收 coverage：

| revenue_year | revenue_month_num | row_count | yoy_count | coverage |
|---|---|---|---|---|
| 2026 | 3 | 1083 | 0 | 0.0% |
| 2026 | 4 | 1083 | 0 | 0.0% |
| 2026 | 5 | 1084 | 0 | 0.0% |
| 2026 | 6 | 1085 | 812 | 74.8% |

- `6243_DISCREPANCY`：本輪 current-code B 條件通過但 raw union 缺席的日期數=
  **11**。全部可由 B 通道 `CHANNEL_B_LIMIT=40` 解釋：6243 雖通過任一 raw B 條件，但依 rs_market / return_20d 排序後的 pre-limit rank 為 [104, 94, 89, 105, 68, 58, 46, 46, 95, 41, 220]，均未進前 40；7/23 為第 220 名。這不是 raw-union 遺漏，而是既有 B channel cap 的正常結果。
- `EXCHANGE_CLASSIFICATION_INCOMPLETE`：market counts={'twse': 1615}；
  canonical primary sector=1290/1615。
- `PRODUCTION_WATCH_HISTORY`：40 日中正式 snapshot coverage=
  100.0%。
- `FROZEN_DAILY_SNAPSHOT`：{'code_version': False, 'prompt_version': True, 'raw_union': True, 'top120': True, 'deterministic_survivors': True, 'llm_input': False, 'llm_output': True, 'final_watch': True, 'market_path': False, 'pocket_state': False, 'lifecycle_state': False, 'shadow_output': False}。本輪新增輸出是 research artifact，
  尚未改 production daily persistence。

## 禁止事項確認

未修改 production、Candidate Pool、A/B/C/D、Top120、momentum_score、Hard
Exclusion、Phase 2/2.5、Role、LLM Prompt、confidence、Market Regime、正式
WATCH 或 Outcome threshold；未把 Dataset C 當 Neutral；未使用 forward return
建立 Day0 feature；未在 Dataset B 搜 threshold；未建立人工大總分、深度模型、
新增 Bundle D、股票代號規則或 Portfolio Backtest。
