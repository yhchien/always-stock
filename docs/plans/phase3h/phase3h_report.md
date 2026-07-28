# Phase 3H — Normal-Regime Winner Enrichment & Confirmation Timing Audit

> 研究日：2026-07-28。純研究 / Shadow Validation；production 零修改。
> Primary：Dataset A、Market Path=NORMAL、frozen first-seen episode。
> Chronological split：5 discovery dates／2 validation dates／2 locked-evaluation dates。
> Dataset B stress 與 Dataset C 非 NORMAL 全數排除 Winner threshold 選擇。

## 結論先行

最終分類：**PROVISIONAL_WINNER_ENRICHMENT**。

NORMAL baseline：n=168，W/N/L=
15/134/19，
Safe=88.7%，Winner Dominance=10.1%。

全樣本最低風險門檻下 Winner Dominance 最高的固定 policy 是
**P7_D3_A**：n=25、
dates=7、Coverage=14.9%、
W/N/L=6/18/
1、Safe=96.0%、
Winner Dominance=25.0%。
相近 Coverage Top-K 是 TOP3_MOMENTUM_RANK，
Winner Dominance=20.0%。
Locked-evaluation slice n=24、Winner=1；該 slice
僅 1 個 Winner。P7_D3_A 在 locked slice 選出
n=6、W/N/L=0/
6/0，沒有捕捉該 Winner，
因此不能提供正向 winner-enrichment confirmation。本輪只能保留 provisional
分級，不能把 discovery／validation 方向延伸成 locked-slice 成功宣稱。

## Sample 與 sub-regime

| normal_subregime | LOSER | NEUTRAL | WINNER | n |
|---|---|---|---|---|
| BULL_NORMAL | 4 | 9 | 1 | 14 |
| RANGE_NORMAL | 15 | 125 | 14 | 154 |

BULL_NORMAL 僅 14 筆，未達 50；RANGE_NORMAL 雖有 154 筆，但為避免為單一
sub-regime 另調 threshold，本輪依規格使用 `NORMAL_COMBINED`，sub-regime 只描述。
Dataset C 的 140 筆全為 WEAKENING/RISK_OFF，pending shadow 中明列排除原因。

## Day0 positive structure

最穩定 Winner／Neutral 差異：

| feature | observed_winner_direction | effect_size_winner_vs_neutral | dates_with_winner_neutral_comparison | direction_consistency_by_date |
|---|---|---|---|---|
| return_10d | HIGHER | 0.9625 | 6 | 0.8333 |
| return_5d | HIGHER | 0.8513 | 6 | 0.8333 |
| close_progress_5d | HIGHER | 0.8513 | 6 | 0.8333 |
| market_excess_return_5d | HIGHER | 0.8192 | 6 | 0.8333 |
| trend_efficiency_10d | HIGHER | 0.7638 | 6 | 0.8333 |

固定 bundle 最佳：**P3_D0_C**，n=25、
Winner Dominance=26.3%、Safe=76.0%、
Winner Recall=33.3%。三個 Day0 bundle 都未達
Loser Rate <=20%；因此 P3 只是 Day0 中名目 Dominance 最高者，不是合格
promotion policy。Freshness 的 first-seen/top120 欄位在
primary first-seen cohort 中結構性退化為常數；可檢驗的 freshness 主要來自
RS-above-80 與 volume-expansion onset，不能把 `first_seen_flag` 當成增量。

## Day1 follow-through

| feature | observed_winner_direction | effect_size_winner_vs_neutral | dates_with_winner_neutral_comparison | direction_consistency_by_date |
|---|---|---|---|---|
| day1_momentum_rank_change | HIGHER | 0.1349 | 5 | 1.0000 |
| day1_momentum_rank | LOWER | -0.1066 | 5 | 1.0000 |
| day1_rs_change | HIGHER | 0.5429 | 6 | 0.8333 |
| day1_rs_market_pct | HIGHER | 0.4932 | 6 | 0.8333 |
| day1_volume_ratio | HIGHER | 0.4199 | 6 | 0.8333 |

最佳 Day1 bundle：**P4_D1_A**，identification Winner Dominance=23.8%、
Promotion Winner Rate=20.8%、
Promotion Damage=0.0%、n=24。

## Day3 persistence

| feature | observed_winner_direction | effect_size_winner_vs_neutral | dates_with_winner_neutral_comparison | direction_consistency_by_date |
|---|---|---|---|---|
| market_excess_return_day0_to_day3 | HIGHER | 1.9369 | 6 | 1.0000 |
| sector_excess_return_day0_to_day3 | HIGHER | 1.6034 | 5 | 1.0000 |
| volume_confirmation_day1_to_day3 | HIGHER | 1.4303 | 6 | 1.0000 |
| return_day0_to_day3 | HIGHER | 1.3612 | 6 | 1.0000 |
| momentum_rank_change_day0_to_day3 | HIGHER | 1.3062 | 5 | 1.0000 |

最佳 Day3 bundle：**P7_D3_A**，identification Winner Dominance=25.0%、
Promotion Winner Rate=28.0%、
Promotion Damage=0.0%、n=25。

## Policy comparison

| policy | selected_count | selected_dates | coverage | winner_count | neutral_count | loser_count | safe_rate | winner_dominance | winner_recall | promotion_winner_rate | promotion_damage_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1_D0_A | 54 | 9 | 32.1429 | 6 | 36 | 12 | 77.7778 | 14.2857 | 40.0000 | 11.1111 | 0.0000 |
| P2_D0_B | 43 | 9 | 25.5952 | 5 | 28 | 10 | 76.7442 | 15.1515 | 33.3333 | 11.6279 | 0.0000 |
| P3_D0_C | 25 | 6 | 14.8810 | 5 | 14 | 6 | 76.0000 | 26.3158 | 33.3333 | 20.0000 | 0.0000 |
| P4_D1_A | 24 | 6 | 14.2857 | 5 | 16 | 3 | 87.5000 | 23.8095 | 33.3333 | 20.8333 | 0.0000 |
| P5_D1_B | 14 | 6 | 8.3333 | 3 | 11 | 0 | 100.0000 | 21.4286 | 20.0000 | 21.4286 | 0.0000 |
| P6_D1_C | 5 | 4 | 2.9762 | 3 | 2 | 0 | 100.0000 | 60.0000 | 20.0000 | 60.0000 | 0.0000 |
| P7_D3_A | 25 | 7 | 14.8810 | 6 | 18 | 1 | 96.0000 | 25.0000 | 40.0000 | 28.0000 | 0.0000 |
| P8_D3_B | 21 | 7 | 12.5000 | 5 | 15 | 1 | 95.2381 | 25.0000 | 33.3333 | 28.5714 | 0.0000 |
| P9_D3_C | 2 | 1 | 1.1905 | 1 | 1 | 0 | 100.0000 | 50.0000 | 6.6667 | 50.0000 | 0.0000 |
| S0_DAY0_ONLY | 25 | 6 | 14.8810 | 5 | 14 | 6 | 76.0000 | 26.3158 | 33.3333 | 20.0000 | 0.0000 |
| S1_DAY1_ONLY | 5 | 4 | 2.9762 | 3 | 2 | 0 | 100.0000 | 60.0000 | 20.0000 | 60.0000 | 0.0000 |
| S2_DAY3_ONLY | 2 | 1 | 1.1905 | 1 | 1 | 0 | 100.0000 | 50.0000 | 6.6667 | 50.0000 | 0.0000 |
| P10_STAGED_DAY1_DAY3 | 6 | 4 | 3.5714 | 3 | 3 | 0 | 100.0000 | 50.0000 | 20.0000 | 50.0000 | 0.0000 |

正式 P10 staged policy：n=6、Winner Dominance=50.0%、
Promotion Winner Rate=50.0%、median delay=1.0、
pre-confirmation median=6.54%、
post-promotion median=10.56%。P10 未達 n>=20／dates>=5，
不得用其名目上的高 Dominance 宣稱 staged 有效。

False confirmations：Promotion Neutral=47 rows、
Promotion Loser=22 rows。這些是 policy-row（同一 episode
可出現在不同固定 policy），不可當獨立股票數。共同特徵逐列保存在
`phase3h_false_confirmations.csv`，未用來回頭調 threshold。

## 24 個必答答案

1. NORMAL completed：168；Winner=15、Neutral=134、Loser=19。
2. BULL/RANGE 是否可分開：否；BULL_NORMAL n=14，使用 NORMAL_COMBINED。
3. Baseline Safe=88.7%，Winner Dominance=10.1%。
4. Day0 穩定差異：見 Day0 feature table；只採至少 4 日期可比較者。
5. Freshness 增量：first-seen/top120 freshness 退化為常數；RS/volume onset 結果見 D0-A。
6. RS slope vs 靜態 RS：effect size 與 date consistency 見 univariate CSV，不以單一 median 宣稱。
7. Flow persistence：D0-B 相對 D0-A 的完整 coverage/W/N/L 見 Day0 policy CSV。
8. Trend efficiency：D0-C 相對 D0-B 的結果見 Day0 policy CSV。
9. D0 最佳：P3_D0_C。
10. Day1 最穩定差異：day1_momentum_rank_change, day1_momentum_rank, day1_rs_change。
11. D1 最佳：P4_D1_A。
12. Day3 是否優於 Day1：是；並需同看 promotion outcome。
13. D3 最佳：P7_D3_A。
14. Confirmation 是否提高 Dominance：最佳增量=-1.3 pp。
15. Promotion 剩餘漲幅：最佳 confirmation post-promotion median=0.92%。
16. Confirmation 是否太晚：未達 CONFIRMATION_TOO_LATE 定義。
17. Day0 Winner 被 staged 錯過：12/15。
18. 通過 confirmation 的 Neutral：47 policy rows；逐列特徵見 false-confirmations CSV。
19. 通過 confirmation 的 Loser：22 policy rows；逐列特徵見 false-confirmations CSV。
20. Staged 是否優於 Day0-only：否；P10 n=6、dates=4。
21. 是否優於相近 Coverage Top-K：是。
22. Safe>=80% 且 Dominance>50%：否。
23. 最佳合格樣本 Winner Dominance=25.0%，Coverage=14.9%。
24. 最終結論：**PROVISIONAL_WINNER_ENRICHMENT**。

## Leakage、資料限制與整合邊界

- PIT valid=504/504 =
  100.0%；Day1 decision 沒有 Day3 feature。
- Promotion Outcome 全部從 promotion close 重算，沒有拿 Day0 close 冒充可交易勝率。
- Role/confidence 只在正式 WATCH snapshot 有部分 coverage，不作 threshold 或 Top-K
  成功宣稱。
- 未使用 Optional Historical Extension：無法證明舊 60～62 日 replay 的所有欄位
  與 frozen current window 完全同版。
- NORMAL 不套 Survival hard gate；WEAKENING/RISK_OFF 留在 Phase 3G
  Market-conditioned Survival Shadow；Pocket 只作 context。
- 未修改 production、A/B/C/D、Top120、momentum_score、Outcome threshold、
  WATCH、Market Regime 或交易策略；未做 portfolio backtest。
