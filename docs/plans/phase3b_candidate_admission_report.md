# Phase 3B：Candidate Admission Path Audit（2026-07-27）

> 純研究，不修改任何 production 程式碼、Candidate Selection threshold、A/B/C/D
> 定義、Phase 2、Phase 2.5、Hard Exclusion、LLM、momentum_score。不新增
> Admission Score / 技術指標 / 法人模型 / Exit Rule。不做 Portfolio Backtest /
> threshold search。
>
> 腳本：`backend/analyze_phase3b_admission_path.py`
> 母體：617-dedup replay cohort **擴充至 624 檔**（首次抓到日 2026-04-13 ~
> **2026-07-09**，59 個不重複日期；原 617 檔母體只到 07-07，本輪多納入
> 07-08/07-09 兩天新抓到的 7 檔股票，僅到 07-09 是因為 Day10 前瞻報酬需要
> 之後還有 10 個交易日資料，目前資料庫最新只到 07-24，07-09 是唯一能有完整
> Day10 結果的最晚抓到日）
> 輸出：`/tmp/phase3b_candidate_admission_all.csv`（624 筆）、
> `/tmp/phase3b_source_combination_summary.csv`、
> `/tmp/phase3b_single_vs_multi_source.csv`、`/tmp/phase3b_daily_cohort_summary.csv`

## 核心問題

> 今天魚尾抓進來的股票，從「它為什麼會被抓進來」這件事本身，能不能找到一群
> 「大量增加候選數量，卻很少增加真正 Winner，反而帶進較多 Big Loser」的
> 結構性雜訊來源？

## 方法論揭露

- **B/C/D 直接沿用既有 replay JSON 的 pool flag**（`in_price_momentum_pool` /
  `in_acceleration_pool` / `in_fundamental_pool`，本來就是 Day0 deterministic
  產物）；**A（法人資金）需要重建**：對每個唯一 catch_date 重跑
  `candidate_pool.ingest_data` + `compute_rankings`（純 ranking 重建，非全
  pipeline），取得 `top_stocks_3d`（熱錢前 30）+ `top_industries_3d`（前 10
  非金融產業）+ 集團擴散（`top_stocks_3d[:6]` 的同集團成員），三者聯集即為
  source_A。
- **D 通道全期間 0 命中**（見下）——這是誠實觀察到的現象，本輪未深入追查
  根因（超出候選來源稽核範圍），只如實記錄。

## 一、必要揭露

**1. A/B/C/D 各自帶進多少股票**（n=624）：

| Source | n | 佔比 |
|---|---|---|
| A（法人資金） | 499 | 80.0% |
| B（價格動能） | 75 | 12.0% |
| C（動能加速） | 137 | 22.0% |
| D（基本面動能） | **0** | **0.0%** |

**D 通道整個 59 天窗口內完全沒有貢獻任何候選**——這是本輪最意外的發現之一，
純粹描述性記錄，未深入追查（可能與月營收公告時間差 / `CHANNEL_D_LIMIT=20`
上限 / 這段期間基本面訊號本來就少有關，本輪不做因果推論）。

**2. 最常見的 Source Combination**：A-only（415 檔，66.5%）>> C-only（76
檔，12.2%）> AC（58 檔，9.3%）> B-only（47 檔，7.5%）> AB（25 檔，4.0%）>
BC（2 檔）/ABC（1 檔，皆 n<15 僅描述性）。**ABCD 四通道同時命中 0 次**。

## 二、Source Combination Outcome Table

| Source | n | Winner% | Neutral% | BigLoser% | mean10d | median10d |
|---|---|---|---|---|---|---|
| **COHORT BASELINE** | 624 | 26.6% | 62.8% | 10.6% | 4.75% | — |
| A | 415 | 25.5% | 65.5% | 8.9% | 4.63% | 2.66% |
| B | 47 | 31.9% | 51.1% | 17.0% | 4.10% | 2.35% |
| C | 76 | 23.7% | 60.5% | 15.8% | 3.66% | 0.00% |
| AB | 25 | 24.0% | 60.0% | 16.0% | 2.99% | 2.57% |
| **AC** | 58 | **34.5%** | 56.9% | **8.6%** | **8.39%** | 1.50% |
| BC（n=2） | 2 | 0.0% | 100.0% | 0.0% | -2.27% | — |
| ABC（n=1） | 1 | 100.0% | 0.0% | 0.0% | 15.77% | — |

## 三、Single-source vs Multi-source

| Source Count | n | Winner% | BigLoser% | Mean10d |
|---|---|---|---|---|
| 1 SOURCE | 538 | 25.8% | 10.6% | 4.44% |
| 2 SOURCES | 85 | 30.6% | 10.6% | 6.55% |
| 3 SOURCES（n=1） | 1 | 100.0% | 0.0% | 15.77% |

**2 個來源同時成立時 winner rate 略高（30.6% vs 25.8%）、mean return 明顯較高
（6.55% vs 4.44%），但 big_loser rate 完全相同（10.6% vs 10.6%）**——多來源
沒有帶來額外風險，且平均表現略優，但差距不算戲劇性，n=85 也還算中等樣本，
不宜過度解讀成「多來源=更好」的因果結論。

## 四、Single-source Noise 檢查（核心關切）

| Source | n | 佔全體 | Winner% | BigLoser% | Mean10d |
|---|---|---|---|---|---|
| A-only | 415 | 66.5% | 25.5% | 8.9% | 4.63% |
| **B-only** | 47 | 7.5% | **31.9%** | **17.0%** | 4.10% |
| C-only | 76 | 12.2% | 23.7% | 15.8% | 3.66% |
| D-only | 0 | — | — | — | — |

**B-only 雖然 big_loser rate 最高（17.0%，baseline 的 1.6 倍），但 winner
rate 同時也是三者中最高（31.9%，高於 baseline 26.6%）**——這不符合「大量
候選、很少 Winner、較多 Loser」的雜訊定義，而是一個「高波動、兩極化」的
bucket：風險高，但 Winner 濃度也高，不是「低價值」。

**C-only 是唯一同時 winner 略低（23.7% < baseline 26.6%）、big_loser 略高
（15.8% > baseline 10.6%）的單一來源**，方向上最接近「雜訊」定義，但差距
不算戲劇性（winner 只低 2.9pp），未達到 removal simulation 設定的門檻（見
第七節）。

## 五、Source Addition Value（descriptive，非因果）—— 最重要的正向發現

| 基準 | +組合 | n | Winner% | BigLoser% | Mean10d |
|---|---|---|---|---|---|
| A-only（25.5%／8.9%） | +AC | 58 | **34.5%** | **8.6%** | **8.39%** |
| A-only | +AB | 25 | 24.0% | 16.0% | 2.99% |
| B-only（31.9%／17.0%） | +AB | 25 | 24.0% | 16.0% | 2.99% |
| C-only（23.7%／15.8%） | +AC | 58 | **34.5%** | **8.6%** | **8.39%** |

**AC 組合在每一個比較基準上都明顯優於單獨的 A-only 或 C-only**（winner rate
從 25.5%/23.7% 提升到 34.5%，big_loser rate 從 8.9%/15.8% 降到 8.6%，mean
return 從 4.63%/3.66% 大幅提升到 8.39%）——這是本輪最一致、最值得記住的
正向訊號。**相對地，AB 組合沒有展現同樣的綜效**（加了 B 之後 big_loser rate
反而從 A-only 的 8.9% 惡化到 16.0%）。這只是描述性觀察，本輪不建立因果宣稱
或新 threshold。

## 六、Source Combination × EXTENDED_3D

| Source | EXTENDED n | EXTENDED BigLoser% | non-EXTEND n | non-EXTEND BigLoser% |
|---|---|---|---|---|
| A | 45 | 26.7% | 370 | 6.8% |
| B | 30 | 23.3% | 17 | 5.9% |
| C | 24 | 20.8% | 52 | 13.5% |
| AB | 15 | 20.0% | 10 | 10.0% |
| AC | 13 | 30.8%(winner) / 0.0%(loser) | 45 | 11.1%（方向反轉，n 小） |

**EXTENDED_3D 在幾乎每個 admission source 內都維持既有的風險區分力**（A/B
兩者 big_loser rate 在 EXTENDED 組都是 non-EXTEND 組的 3.5~4 倍），再次確認
EXTENDED_3D 是跨 admission path 都成立的既有風險訊號（沿用既有觀察，非本輪
新發現）。AC 組合的 EXTENDED 子集只有 13 檔，方向不穩定，僅供參考。

## 七、Noise Efficiency Metric

| Source | n | Winners | BigLosers | Candidates/Winner | BigLosers/Winner |
|---|---|---|---|---|---|
| A | 415 | 106 | 37 | 3.92 | 0.35 |
| B | 47 | 15 | 8 | 3.13 | 0.53 |
| **C** | 76 | 18 | 12 | **4.22（最差）** | **0.67（並列最差）** |
| **AB** | 25 | 6 | 4 | 4.17 | **0.67（並列最差）** |
| **AC** | 58 | 20 | 5 | **2.90（最佳）** | **0.25（最佳）** |

**AC 是全體效率最佳的組合**（每找到 1 檔 Winner 只需要 2.9 檔候選、只帶進
0.25 檔大輸家）；**C-only 與 AB 是效率最差的兩組**（每找到 1 檔 Winner 需要
4.17~4.22 檔候選、帶進 0.67 檔大輸家）。

## 八、Daily Cohort + Time Robustness

- **C-only 沒有集中在少數異常日期**：單日最高只有 5 檔（04-29、05-05、
  05-12 各 5 檔），分散在多個日期，不是單一極端日造成的假象。
- **Time Robustness（前半 vs 後半）**：

| Source | 前半 Winner% / BigLoser% (n) | 後半 Winner% / BigLoser% (n) |
|---|---|---|
| A | 34.1% / 7.3% (n=205) | 17.1% / 10.5% (n=210) |
| B | 31.0% / 13.8% (n=29) | 33.3% / 22.2% (n=18) |
| C | 34.5% / 13.8% (n=29) | 17.0% / 17.0% (n=47) |
| **AC** | **34.6% / 7.7%** (n=26) | **34.4% / 9.4%** (n=32) |

**A-only 與 C-only 在後半段 winner rate 都明顯下滑**（34%→17%），但這更像是
**整體市場後半段轉弱**（本輪新併入的 07-08/07-09 兩天 mean return 分別是
-10.57%/-11.23%，遠差於全期平均，印證後半段市場條件確實惡化），而非
admission source 本身失效。**AC 組合是唯一前後半段 winner rate 幾乎不變
（34.6%→34.4%）的組合**，穩健性最高。

## 九、Removal Simulation（§16/17）

**沒有找到任何 source combination（n>=15）符合「winner_rate 明顯低於
baseline 70%」的雜訊候選門檻**——programmatic 檢查沒有觸發任何 removal
simulation。這代表：**本輪找不到一個「可以安全移除、大量減少候選、同時
保留絕大多數 Winner」的 admission source**。

## 回答 10 個核心問題

**1. A/B/C/D 各自帶進多少股票？** A=499（80.0%）、B=75（12.0%）、
C=137（22.0%）、D=0（0.0%）。

**2. 最常見的 Source Combination？** A-only（66.5%）遠遠領先，其次 C-only
（12.2%）、AC（9.3%）、B-only（7.5%）、AB（4.0%）。

**3. A-only/B-only/C-only/D-only 的 Outcome 分布？** A-only 最貼近 baseline
（25.5%/8.9%）；B-only 高波動（31.9% winner／17.0% big_loser，兩者都高於
baseline）；C-only 是唯一雙向都偏弱的單一來源（23.7%/15.8%）；D-only 無
樣本可評估。

**4. Single-source vs Multi-source 是否存在穩定差異？** 2-source 組
（n=85）winner rate 與 mean return 都略優於 1-source（30.6% vs 25.8%、
6.55% vs 4.44%），big_loser rate 完全相同（10.6% vs 10.6%）——方向上「多
來源沒有更差」，但沒有戲劇性優勢，n 也不算大，不宜視為決定性結論。

**5. 哪個 Admission Path 的 Candidates per Winner 最差？** **C-only（4.22）
與 AB（4.17）並列最差**；AC 最佳（2.90）。

**6. 哪個 Admission Path 的 Big Losers per Winner 最差？** **C-only 與 AB
並列最差（皆 0.67）**；AC 最佳（0.25）。

**7. 是否存在「候選很多、Winner 很少、Loser 明顯偏多」的 Noise Group？**
**沒有找到符合完整定義的 Noise Group**。C-only 是唯一雙向都偏弱的候選，但
差距不夠戲劇性（winner 只低 baseline 2.9pp）；B-only 雖然 big_loser 率
最高，但 winner 率也同步是單一來源中最高的，**不符合「低 Winner」這個必要
條件**——這正是 §24 Stop Criteria 明確描述的情境：「看似很差的 group 同時
也包含大量 Winner」。

**8. 若純研究移除最差 Noise Group：Candidate Count 可下降多少？Winner
Retention 是多少？Big Loser Removal 是多少？** **無法回答，因為沒有任何
group 通過移除模擬的門檻**（programmatic 檢查 0 組符合，見第九節）——本輪
誠實地找不到一個「移除後 Winner Retention 仍然很高、同時候選數或 Big Loser
明顯下降」的候選群。

**9. 這個差異前半/後半時間是否一致？還是只來自少數特殊日期？** C-only 的
分布跨多個日期（非單日異常）；但 A-only/C-only 的 outcome 在前後半段有
明顯落差（winner rate 34%→17%），**這個落差更可能反映整體市場後半段轉弱
（本輪新併入的 07-08/07-09 平均報酬 -10.57%/-11.23%，明顯差於全期平均），
不是 admission source 本身在後半段失效**。唯一在前後半段都穩定的是 AC
組合。

**10. 最終結論**：**STOP**（作為「候選來源＝壓縮切入口」這個假說本身）。

理由：
- 沒有任何 source combination 同時符合「數量夠大 + winner 明顯偏低 + big
  loser 明顯偏高 + 可安全移除」的完整條件（§23 Success Criteria 未達成）
- 唯一 big_loser 率最高的 B-only，同時也是 winner 率最高的單一來源——正是
  §24 Stop Criteria 明確描述「看似很差的 group 同時也包含大量 Winner」的
  情境，代表移除它會不成比例地犧牲 Winner
- Removal Simulation 程式化檢查 0 組通過門檻，不是「找不到就放寬標準」，
  是誠實回報「這條路目前打不開」
- 依 §24 指示：**下一步應該轉往 Cross-sectional Dominance（同日候選相對
  支配關係）研究方向**，而不是繼續在 Candidate Source 這條軸上調 threshold
  或合併 group 硬湊出結果

**額外值得記住的正向發現（非本輪結論主軸，但有價值）**：**AC（法人資金 +
動能加速）組合在效率、時間穩健性、EXTENDED 交叉分析上全面優於單獨的 A 或
C**——這不是「移除雜訊」的切入口，而是「哪種組合特別值得注意」的線索，若
未來要做 Phase 3C 式的 Source-Conditional Confirmation 研究，AC 這個組合
的綜效成因值得優先探討。

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate Selection threshold、A/B/C/D 定義、
Phase 2、Phase 2.5、Hard Exclusion、LLM、momentum_score；未新增 Admission
Score / 技術指標 / 法人模型 / Exit Rule；未做 Portfolio Backtest 或
threshold search；未為了找到漂亮結果合併 source group；未 hardcode
stock_id；未直接限制每天候選數量上限、sector cap 或 source cap；本輪結論
為 STOP，依指示不直接改動 Candidate Selection，改為建議下一步轉向
Cross-sectional Dominance 研究方向。
