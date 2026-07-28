# Phase 3G — Policy Attribution & Point-in-Time Lifecycle Validation

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。  
> Frozen primary cohort：A=246、B=87、C=140；`RECONSTRUCTED_NOT_PRODUCTION_EXACT`。  
> 最新可用價格／正式 WATCH：2026-07-27／2026-07-27；7/28 尚未入庫，未虛構 action。

## 結論先行

最終分類：**PROVISIONAL_SURVIVAL_SIGNAL / MARKET_INCREMENT_CONFIRMED / NO_POCKET_INCREMENT / LOSER_CONTROL_ONLY**。

- Survival Only 將 Dataset B Loser Rate 從 44.8% 降至
  **25.0%**，n=36、Coverage=41.4%，
  Safe Rate=75.0%，Loser Wilson 95% CI=
  13.8%–41.1%。
  9 個 Loser 分布在 6 個日期中的 5 日，並非單日結果。它是主要個股層
  Loser-control 訊號，但沒有達到 Safe Rate >=80%。
- Market + Survival 再把 B Loser Rate 降至 **18.2%**，
  相對 Survival Only 改善 6.8 pp；
  A 僅由 15.2% 變為 16.2%。
- Pocket + Survival 的 B Loser Rate=25.7%，未優於 Survival Only；
  Full=19.4%，也未優於 Market + Survival=18.2%。
- Watchlist 已修正 Day-0 barrier 與 action-date Outcome leakage。舊 lifecycle
  有 1050/1383 列呈現 entry Outcome
  複製模式，分類為 `LIFECYCLE_OUTCOME_LEAKAGE_FOUND`。

## Part A — Policy Attribution

| dataset | policy | selected_count | coverage_pct | winner_count | loser_count | loser_rate_pct | safe_rate_pct | winner_recall_pct |
|---|---|---|---|---|---|---|---|---|
| A | P0_BASELINE | 246 | 100.0000 | 22 | 38 | 15.4472 | 84.5528 | 100.0000 |
| A | P1_MARKET_ONLY | 196 | 79.6748 | 17 | 26 | 13.2653 | 86.7347 | 77.2727 |
| A | P2_POCKET_ONLY | 131 | 53.2520 | 20 | 29 | 22.1374 | 77.8626 | 90.9091 |
| A | P3_SURVIVAL_ONLY | 92 | 37.3984 | 6 | 14 | 15.2174 | 84.7826 | 27.2727 |
| A | P4_MARKET_PLUS_SURVIVAL | 74 | 30.0813 | 6 | 12 | 16.2162 | 83.7838 | 27.2727 |
| A | P5_POCKET_PLUS_SURVIVAL | 51 | 20.7317 | 6 | 8 | 15.6863 | 84.3137 | 27.2727 |
| A | P6_FULL | 35 | 14.2276 | 5 | 5 | 14.2857 | 85.7143 | 22.7273 |
| B | P0_BASELINE | 87 | 100.0000 | 1 | 39 | 44.8276 | 55.1724 | 100.0000 |
| B | P1_MARKET_ONLY | 67 | 77.0115 | 1 | 30 | 44.7761 | 55.2239 | 100.0000 |
| B | P2_POCKET_ONLY | 74 | 85.0575 | 1 | 32 | 43.2432 | 56.7568 | 100.0000 |
| B | P3_SURVIVAL_ONLY | 36 | 41.3793 | 1 | 9 | 25.0000 | 75.0000 | 100.0000 |
| B | P4_MARKET_PLUS_SURVIVAL | 33 | 37.9310 | 1 | 6 | 18.1818 | 81.8182 | 100.0000 |
| B | P5_POCKET_PLUS_SURVIVAL | 35 | 40.2299 | 1 | 9 | 25.7143 | 74.2857 | 100.0000 |
| B | P6_FULL | 31 | 35.6322 | 1 | 6 | 19.3548 | 80.6452 | 100.0000 |

口徑說明：P2 的 Pocket-only 使用「任何既有 active pocket」而不套 market-state
收縮；P6 則精確重建 Phase 3F 的 state-aware pocket + Bundle A。Phase 3F 的
P1 market contraction 本身以 70/90 survival percentile 做發佈收縮，因此
P1/P4 是「鎖定的 market publishing policy」而非純粹市場方向因果估計。這項結構
限制已保留在 policy matrix，沒有為了得到更漂亮歸因而另調 threshold。

Dataset A Survival Gate 錯刪 Winner：
16/22
（Winner Recall=27.3%）。

## Part B — Early Market Transition

Composite PROTECTIVE 的 raw first alert 是 **2026-06-26**，但它位於
Dataset A 且之後恢復，視為 false-alarm audit，不冒充 July transition 成功。
7/2 後第一次短暫 alert=2026-07-02；第一次連續兩個 replay sessions
維持 PROTECTIVE=2026-07-07，只比 Phase 3F 7/8 提前
1.0 session。7/2 前
PROTECTIVE false-alarm days=3。

Early PROTECTIVE 在 B selected=50、
Coverage=57.5%、Loser=20、
Loser Rate=40.0%；無保護 Loser=39。
在 A Winner Recall=59.1%。因此判定：
**NO_EARLY_SIGNAL**。

## Part C — Pocket Activity vs Durability

- Dataset B 唯一 Winner 所在象限：Q2_ACTIVE_FRAGILE。
- A：Active Durable Safe=80.4%（n=102），
  Active Fragile Safe=71.4%（n=35）。
- B：Active Durable Safe=52.9%（n=70），
  Active Fragile Safe=64.3%（n=14）。
- Active Durable + Survival：A/B Safe=92.3%/
  75.0%，Coverage=15.9%/
  36.8%。未形成 A/B 一致且不只靠 Coverage 收縮的增量，
  Pocket 維持 descriptive context。

## Part D — Point-in-Time Watchlist

- point-in-time valid：2038/2038
  = 100.0%。
- Loser Early Warning：116/
  173 = 67.1%；
  median lead=2.0 sessions。
- Loser Early Removal：55/
  173 = 31.8%。
- Winner Premature Warning=67.0%；
  Winner Premature Removal=21.4%；
  Winner Healthy Retention（action-date ratio）=56.7%。
- 判定：**NO_ACTIONABLE_SIGNAL**。
  `DETERIORATING` 只產生 WARNING；REMOVE 必須至少兩個 evidence family
  連續兩個 action dates 同步惡化。

7/22～可用截止日的 action 分布：

| action_date | KEEP_SHADOW | REMOVE_SHADOW | RISK_BREACHED | WARNING_SHADOW |
|---|---|---|---|---|
| 2026-07-22 | 8 | 0 | 0 | 0 |
| 2026-07-23 | 19 | 0 | 0 | 4 |
| 2026-07-24 | 21 | 0 | 5 | 8 |
| 2026-07-27 | 18 | 2 | 11 | 6 |

## 22 個必答答案

1. Policy 3 效果主因：Stock Survival 是主要第一層，Market 在其上仍有保護增量；Pocket 無增量。
2. Survival Only B Loser Rate：25.0%。
3. Survival Only B Safe >=80%：否，75.0%。
4. Market 額外增量：有，B Loser Rate 再降 6.8 pp。
5. Pocket 額外增量：無。
6. Full 優於 Survival Only：B Loser Rate 較低，但不優於 Market + Survival，不能歸功 Pocket。
7. A Survival 錯刪 Winner：16 檔，保留 6/22。
8. 最早 raw alert=2026-06-26（false-alarm audit）；7 月轉弱第一次 alert=2026-07-02，第一次持續 alert=2026-07-07。
9. 持續訊號是否比 7/8 早至少兩日：否。
10. A false alarm：3 個 market days；Winner Recall=59.1%。
11. 現有 Pocket 是否只代表 Activity：目前證據支持降為 Activity/context。
12. Active Durable 優於 Active Fragile：A/B Safe 見 Part C；需方向一致才成立。
13. B 唯一 Winner 象限：Q2_ACTIVE_FRAGILE。
14. Durability 在 Survival 外增量：未確認。
15. 原 lifecycle Outcome 複製／bias：有，`LIFECYCLE_OUTCOME_LEAKAGE_FOUND`。
16. action 是否 PIT valid：已產生 100.0% 合法列；7/28 無資料不產生假列。
17. Loser Early Warning：67.1%。
18. Winner Premature Warning：67.0%。
19. Winner Premature Removal：21.4%。
20. Watchlist 適合 WARNING 或 REMOVE：尚未達 provisional threshold。
21. 每日逐股 action：見 `phase3g_20260722_20260728_live_replay.csv`；實際資料止於 2026-07-27。
22. 最終結論：**PROVISIONAL_SURVIVAL_SIGNAL / MARKET_INCREMENT_CONFIRMED / NO_POCKET_INCREMENT / LOSER_CONTROL_ONLY**。

## 研究限制與禁止事項確認

- Dataset B 僅 1 Winner 且 6 個 signal dates，標記 `SHORT_STRESS_WINDOW`；
  未使用 bootstrap 宣稱穩健。
- 7/28 資料未入庫；7/27 缺 frozen Top120 raw-union frame 的欄位保持 null，
  不用 WATCH ordinal 冒充 momentum rank。較舊 pocket/market source date 均明列，
  且 `max_source_date <= action_date`。
- 未修改 production、A/B/C/D、Top120、momentum_score、Outcome threshold、
  WATCH、Market Regime 或交易策略；未用 Dataset C 調 threshold；未做 portfolio backtest。
