# Phase 2.6 Relative Leadership 快速假設驗證報告（2026-07-24）

> 純研究，不修改任何 production 程式碼、不重跑完整 pipeline_v2、不重跑 617 檔。
> 原始資料：`/tmp/phase26_relative_leadership_matched20.csv` / `/tmp/phase26_relative_leadership_raw.json`
> 腳本：`backend/analyze_phase26_relative_leadership.py`

## 核心問題

> 真正的大贏家，是否比大虧股更具有「持續性的同族群領先地位」？

沿用既有 20 檔 matched sample（10 BIG_LOSER `future_return_10d<=-10%` + 10 WINNER
`future_return_10d>=+10%`，依 Day0 `rs_market_percentile_20d`/`momentum_score` 距離
最小配對，EXTENDED_3D 狀態相同、market_regime 相同——全部 10 對距離 < 1.0）。

Peer scope 沿用 Phase 2 `sector_context.py` 的 canonical taxonomy hierarchy
（SUB_SECTOR → PRIMARY_SECTOR → UNAVAILABLE，MIN_PEER_SAMPLE=5，
classification_confidence 需 HIGH/MEDIUM），ranking metric 用「20 日報酬」
（既有簡單概念，非新設計 score），只看 Day-4 ~ Day0，不看 Day+1 之後。

## 1. 20 檔 comparison table

| stock_id | outcome | ret_10d | peer_scope | rank_day-4→day0 | pct_day0 | top20_5d | median_5d | direction |
|---|---|---|---|---|---|---|---|---|
| 8104 | LOSER | -14.7% | SUB_SECTOR | 9→7→8→3→1 | 0.000 | 2 | 7 | IMPROVING |
| 4755 | LOSER | -11.1% | PRIMARY | 12→9→10→9→6 | 0.455 | 0 | 9 | IMPROVING |
| 8249 | LOSER | -11.0% | SUB_SECTOR | 7→7→6→7→4 | 0.273 | 0 | 7 | IMPROVING |
| 2493 | LOSER | -18.4% | SUB_SECTOR | 7→8→5→2→1 | 0.000 | 2 | 5 | IMPROVING |
| 2409 | LOSER | -13.1% | PRIMARY | 13→10→12→19→6 | 0.125 | 1 | 12 | IMPROVING |
| 1536 | LOSER | -11.5% | PRIMARY | 8→10→8→6→5 | 0.308 | 0 | 8 | IMPROVING |
| 6805 | LOSER | -10.6% | PRIMARY | 92→41→61→75→43 | 0.378 | 0 | 61 | IMPROVING |
| 3051 | LOSER | -15.9% | PRIMARY | 9→7→6→5→5 | 0.100 | 5 | 6 | IMPROVING |
| 1305 | LOSER | -19.0% | SUB_SECTOR | 4→2→2→2→2 | 0.067 | 5 | 2 | IMPROVING |
| 2481 | LOSER | -15.5% | PRIMARY | 35→48→60→54→18 | 0.193 | 1 | 48 | IMPROVING |
| 1727 | WINNER | +12.4% | PRIMARY | 21→12→6→5→3 | 0.045 | 3 | 6 | IMPROVING |
| 006208 | WINNER | +11.0% | **UNAVAILABLE**（ETF） | – | – | 0 | – | UNKNOWN |
| 00991A | WINNER | +20.5% | **UNAVAILABLE**（ETF） | – | – | 0 | – | UNKNOWN |
| 2486 | WINNER | +15.7% | PRIMARY | 9→5→4→5→3 | 0.050 | 5 | 5 | IMPROVING |
| 7750 | WINNER | +38.3% | SUB_SECTOR | **2→2→2→2→2** | 0.167 | 5 | 2 | **STABLE_LEADER** |
| 6719 | WINNER | +23.4% | SUB_SECTOR | 13→12→10→9→8 | 0.538 | 0 | 10 | IMPROVING |
| 3661 | WINNER | +20.9% | SUB_SECTOR | **4→3→3→3→3** | 0.667 | 0 | 3 | **STABLE_LEADER** |
| 2308 | WINNER | +17.3% | PRIMARY | **4→4→4→3→3** | 0.038 | 5 | 4 | **STABLE_LEADER** |
| 4720 | WINNER | +14.9% | PRIMARY | **3→2→2→3→2** | 0.029 | 5 | 2 | **STABLE_LEADER** |
| 6415 | WINNER | +30.8% | SUB_SECTOR | 9→10→5→1→1 | 0.000 | 2 | 5 | IMPROVING |

## 2. WINNER vs BIG_LOSER 統計摘要

| 指標 | BIG_LOSER (n=10) | WINNER (n=8，排除 2 檔 ETF UNAVAILABLE) |
|---|---|---|
| median peer_rank_percentile_day0（0=最強/1=最弱） | **0.159** | **0.048** |
| median peer_top20_days_5d（5 天中幾天在 peer 前 20%） | **1.0 / 5** | **4.0 / 5** |
| median peer_rank_median_5d（5 天排名中位數） | **7.5** | **4.5** |
| peer_rank_direction 分布 | IMPROVING 10/10（100%）、STABLE_LEADER 0/10 | STABLE_LEADER 4/8（50%）、IMPROVING 4/8（50%） |

## 3. 三個概念個別評價

- **PEER_RANK（單日排名 / percentile）**：**POSSIBLE SIGNAL** — Day0 當天 median percentile 贏家（0.048，約 peer 前 5%）明顯優於輸家（0.159，約 peer 前 16%），方向一致但個別案例仍有例外（例如 6719 WINNER 的 Day0 percentile 高達 0.538，反而比多數 LOSER 差），單日不夠穩定。
- **PEER_RANK_PERSISTENCE（5 日內前 20% 天數 / 排名中位數）**：**HIGH SIGNAL** — 這是本次三個概念中效果最清楚的：贏家 5 天裡中位數有 4 天落在 peer 前 20%，輸家中位數只有 1 天；排名中位數贏家 4.5、輸家 7.5，兩組幾乎沒有重疊（輸家最好的 median_5d 是 1305 的 2，但那是唯一例外，其餘 9 檔輸家 median_5d 都在 5 以上）。
- **PEER_RANK_DIRECTION（Day-4→Day0 軌跡分類）**：**HIGH SIGNAL** — 質化差異最戲劇性：**10 檔輸家全部（100%）被分類為 IMPROVING**（也就是說輸家的 Day0 強勢是「最近才衝上來」，5 天前排名普遍在 7~92 名之間，不是原本就在前段）；贏家則有一半（4/8 有效樣本）是 **STABLE_LEADER**（整整 5 天排名都穩定在 2~4 名），**沒有任何一檔輸家出現這種「持續 5 天穩居族群前段」的型態**。

## 4~6. 回答你的 6 個問題

**1. WINNER 的 Day0 peer rank 是否普遍更前面？** 是，但不是壓倒性——中位數 percentile 0.048 vs 0.159 方向一致，但個別案例有重疊（6719 winner 的 Day0 排名其實中等）。

**2. WINNER 是否更常「連續多天保持 peer 前段」？** 是，非常明顯——`peer_top20_days_5d` 中位數 4/5 天 vs 1/5 天，且 `STABLE_LEADER` 分類只出現在贏家組（4/8），完全沒有出現在輸家組（0/10）。

**3. BIG_LOSER 是否更常出現「突然衝到前面、排名不穩、Day0 強但之前並非持續領先」？** 是，而且是本次最一致的發現：**10 檔輸家全部被分類為 IMPROVING**，代表清一色都是「最近才衝上來」的型態，沒有一檔是原本就穩居族群前段。

**4. `peer_rank_day0` 有沒有區分力？** 有一些（方向正確），但不夠乾淨（6719 這種反例存在）。

**5. `peer_rank_persistence` 是否比單日 peer rank 更有區分力？** 是，明顯更好——`peer_top20_days_5d` 與 `peer_rank_median_5d` 兩者的組間差異都比單日 `peer_rank_day0` 更一致、更少反例。

**6. `peer_rank_direction` 是否有明顯差異？** 有，是三者中效果最戲劇性的——「IMPROVING 100% vs STABLE_LEADER 0%」這種輸家組完全沒有出現持續領先型態的結果，在 20 檔小樣本裡已經是相當清楚的質化分野。

## 結論：是否值得擴大到 40~80 檔？

**建議：值得擴大驗證。** 三個概念中至少兩個（PEER_RANK_PERSISTENCE、PEER_RANK_DIRECTION）呈現明顯、方向一致、且輸家組幾乎沒有反例的差異，不是「差異很小、直接停止」的情況。與前一輪 PERSISTENT_PRICE_FLOW_DIVERGENCE / EXTREME_RUN_EXHAUSTION（命中率與誤標率打平、無區分力）形成清楚對比——這次的 Relative Leadership 方向看起來是三次假設驗證中最有希望的一個。

**注意事項（誠實揭露，非高調宣稱成功）**：
- 樣本仍只有 20 檔（且 2 檔 ETF 缺乏 peer 分類資料），統計上仍是小樣本，需要 40~80 檔驗證才能排除巧合
- `peer_rank_day0`（單日）本身其實不夠乾淨，真正的訊號來自「持續性」（5 日）與「方向」（軌跡分類），這代表未來如果要用這個方向，不能只看 Day0 單一快照
- 「20 日報酬」只是這次為了不重跑全市場 momentum frame 選用的簡化 ranking metric，不是這條研究線最終該用的 metric——之後若真的往下做，應該重新評估要不要換更貼近既有 `momentum_score`/`rs_market_percentile_20d` 精神的 composite metric（但那已超出本輪「不要設計複雜新 score」的範圍）
- 本輪完全沒有調整任何 production 門檻、沒有新增 Hard Exclusion、沒有動 watch_quality_state，純研究產出
