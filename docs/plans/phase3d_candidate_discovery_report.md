# Phase 3D：Candidate Discovery Recall & Truncation Value Audit（2026-07-27）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、Candidate Pool
> 120 檔上限、A/B/C/D threshold、momentum_score、Hard Exclusion、Phase 2、
> Phase 2.5、Role、LLM、confidence、Market Regime、Outcome threshold。

## 核心問題

> 686→120 的 momentum_score 截斷排序是否真的提高 Winner rate、降低 Big
> Loser rate？哪些未來 Winner 完全沒命中 A/B/C/D？能否在候選池仍維持 120
> 檔的前提下，用候選替換增加 Winner Recall 而不增加 Big Loser？

## 必要揭露（§31）

1. **Exchange/security type metadata 來源**：沿用 Phase 1
   `SecurityClassification`（`is_etf`/`is_financial`/`asset_type`）與既有
   `exclusions.is_etf()` regex，**未建立新的 FinMind exchange metadata**——
   `stocks_master.market` 欄位仍全部標記 `twse`，**無法可靠區分 TWSE 上市／
   TPEX 上櫃／興櫃**，標記為 `EXCHANGE_CLASSIFICATION_INCOMPLETE`，本輪「上市
   母體」實質上等於 `stocks_master` 全部收錄股票。
2. **研究日期範圍**：20 個交易日，6 天沿用 Phase 3C 已重建的 RISK_OFF
   momentum frame（2026-05-19/05-20/06-10/06-11/06-26/06-29），14 天新選樣
   （2026-04-13~07-06，均勻涵蓋 BULL_TREND/VOLATILE_RANGE，見下方 regime
   分布）——**依使用者指示採中等樣本規模，非全部 62 天**。
3. **Candidate-day cohort**：8,936 筆（stock×date pair，未去重），其中
   8,321 筆有完整 Day10 forward return（93.1%，其餘因交易日曆邊界缺值）。
   Regime 分布：BULL_TREND 4,298、VOLATILE_RANGE 2,378、RISK_OFF 2,260。
4. **每日 raw union 大小**：中位數約 780 檔（範圍 314~1099），**20 天中有
   多天 raw union 顯著小於「600~700 檔」原先預期**（例如 05-20 僅 334
   檔、06-11 僅 314 檔）——這代表原始候選聯集規模本身也會隨市場狀況（法人
   資金集中度、動能訊號密度）明顯波動，非固定量級。
5. **momentum_score tie-break**：沿用 production 既有排序
   `(-momentum_score, -total_institution_flow_3d, stock_id)`，未修改。
6. **A/B/C/D 實際 threshold**（讀自 `momentum.py` 原始碼，未修改）：
   - B：`rs_market_percentile_20d>=85` 或 `rs_industry_percentile_20d>=80`
     或（`distance_to_20d_high>=0` 且 `volume_1d_to_20d_avg>=1.2`）或
     （`return_percentile_60d>=85` 且 `return_5d` 達標），上限 40 檔
   - C：`rs_rank_improvement_5d>=200` 且 `rs_market_percentile_20d>=70`，
     上限 20 檔
   - D：`revenue_yoy>15`（連兩月加速）或
     `revenue_yoy_industry_percentile>=80`，上限 20 檔
7. **Normalized distance**：本輪**未系統性建立**完整的 686 檔全市場
   near-miss 標準化距離計算（需要對「完全未進 raw union」的每日 800~1100
   檔全市場非候選股票重新算 momentum frame，屬於額外的大規模重建，超出中等
   樣本規模的時間預算）——**這是本輪明確揭露的範圍縮減**，Part C 因此改用
   輕量化的「Day10 outcome 規模統計」+「三檔案例的手動追蹤」，未做全量
   near-miss 分類（見下方「本輪範圍縮減」說明）。
8. **D Channel 健康稽核結果（意外但決定性的發現）**：直接查 `monthly_revenue`
   表確認，**`yoy_pct` 在 2026-01~05 月全部 100% 是 NULL**（1078~1084 檔
   資料，`has_yoy=0`），**6 月僅 812/1084（74.9%）有值**，7 月尚無資料
   （公告截止日未到）。這與 CLAUDE.md 記載「2026-07-21 已修復 monthly_
   revenue.yoy_pct 全市場 NULL 問題並回補 2026-01~06」的說法**不一致**——
   目前資料庫實際查詢結果顯示回補**沒有完整生效或已經遺失**，本輪誠實記錄
   現況，不代表本輪造成或修復此問題。

## Part A：候選母體標記

沿用既有 `SecurityClassification`，`EXCHANGE_CLASSIFICATION_INCOMPLETE`（見
揭露 #1）。未發現本輪需要特別處理的資產分類問題。

## Part B：Truncation Value Audit（決定性結果）

### Rank Bucket Summary（8,321 筆，全樣本）

| Rank | n | Winner% | Neutral% | BigLoser% | mean10d | median10d |
|---|---|---|---|---|---|---|
| 1-40 | 800 | 31.2% | 52.1% | 16.6% | +5.76% | +1.74% |
| 41-80 | 800 | 26.2% | 58.4% | 15.4% | +4.03% | +1.18% |
| 81-120 | 799 | 23.0% | 64.1% | 12.9% | +3.86% | +0.95% |
| 121-160 | 799 | 17.1% | 73.8% | 9.0% | +3.25% | +0.49% |
| 161-200 | 798 | 12.5% | 79.8% | 7.6% | +1.66% | +0.26% |
| 201-300 | 1965 | 12.4% | 80.8% | 6.9% | +1.58% | +0.31% |
| 301-500 | 1822 | 9.9% | 83.0% | 7.1% | +0.73% | +0.00% |
| 501+ | 538 | 8.2% | 86.6% | 5.2% | +0.79% | +0.00% |

**momentum_score 呈現清楚、單調的梯度**：排名越前，Winner rate 越高
（31.2%→8.2%）、mean/median return 越高，**同時 Big Loser rate 也越高**
（16.6%→5.2%）——這代表 momentum_score 排名前段本質上是「高報酬也高波動」，
不是單純「越前面越安全」，但整體梯度單調、沒有反轉，**證實排序本身確實
攜帶真實資訊，不是雜訊**。

### Top120 vs Rank121-200（核心比較，date-block bootstrap）

| | n | Winner% | BigLoser% | mean | median |
|---|---|---|---|---|---|
| Top120（已選中） | 2,399 | 26.8% | 15.0% | +4.55% | +1.28% |
| Rank121-200（截斷排除） | 1,597 | 14.8% | 8.3% | +2.46% | +0.37% |

**Winner rate 差距 12.0pp，date-block bootstrap（以日期為抽樣單位）95%
CI = [+8.52, +15.82]pp，完全不跨零**——這是統計上穩健的差距，不是少數
日期或極端股票造成的巧合。

### Rank81-120 vs Rank121-160（截斷邊界附近）

| | n | Winner% | BigLoser% |
|---|---|---|---|
| Rank81-120 | 799 | 23.0% | 12.9% |
| Rank121-160 | 799 | 17.1% | 9.0% |

邊界附近同樣存在真實差距（+5.9pp Winner，但 Big Loser 也高 +3.9pp）——
**截斷線本身沒有「一刀切錯」的證據，排序在邊界附近仍持續攜帶資訊**。

### 全市場 baseline 對照（最重要的脈絡）

| 分組 | n | Winner rate |
|---|---|---|
| Top120（Phase 2 最終候選池） | 2,399 | **26.8%** |
| Union 但被截斷（rank121+） | 5,922 | 11.9% |
| 完全未進 raw union | 19,209 | 13.7% |
| **全市場整體 baseline** | 27,530 | **14.4%** |

**Top120 的 Winner rate（26.8%）是全市場 baseline（14.4%）的 1.86 倍**，
證實候選發現+截斷排序整體上確實有效濃縮了未來贏家，不是隨機挑選。

## Part C：Admission Coverage（誠實的中性結果）

**完全未進 raw union 的股票裡，Winner rate 是 13.7%，只比全市場 baseline
（14.4%）略低，遠低於 Top120 的 26.8%**——這代表：**「完全沒被任何
A/B/C/D 通道注意到」的股票，整體而言跟隨機亂選差不多，並不存在一個可以
輕易挖出來的「高品質未覆蓋池」**。這跟 Phase 3C 單日案例給人的印象（漢翔
「明明很強卻沒被抓到」）不完全一致，需要在此更正。

### 案例更正（誠實揭露）

重新核對 Phase 3C 報告內容後發現一處**錯誤陳述需要更正**：

**2634 漢翔**：Phase 3C 報告曾寫「連候選池 686 檔聯集都沒進」，**這是錯的**
——實際資料顯示漢翔**確實進了 raw union（第 171 名），只是被 momentum_score
截斷排序推出 120 名之外**，正確分類應為 `MISS_STAGE_1_TRUNCATED`（Phase 3C
的 CSV 檔案其實本來就正確記錄為 TRUNCATED，只有報告內文的個案敘述寫錯，
本輪予以更正）。漢翔的 `rs_market_percentile_20d=98.5`、
`rs_industry_percentile_20d=97.1` 都遠超 B 通道門檻，但 `return_5d=-4.03%`
（近 5 日走弱）拖累了 momentum_score 排名——這是一個真實的「長期強、短期
弱」的截斷案例，不是覆蓋率問題。

**2451 創見**：`rs_market_percentile_20d=13.9`、`rs_industry_percentile_20d
=19.6`、`rs_rank_improvement_5d=150`（未達 C 門檻 200）——**Day0 特徵確實
偏弱**，進入 raw union（第 561 名）很可能是透過 A 通道（產業成分股，非個股
本身動能），截斷淘汰合理，不是漏失。

**6243 迅杰**：`rs_market_percentile_20d=84.2`（僅差 0.8pp 未達 B 門檻 85）、
但 `rs_industry_percentile_20d=91.7`（**已超過 B 通道另一條件的門檻 80**）
——照 B 通道邏輯（兩條件為 OR 關係）**理論上應該被 admit**，但重建結果顯示
它完全不在 raw union 裡。**這是一個尚未解開的不一致，誠實標記為
`UNRESOLVED_DISCREPANCY`**，可能原因是兩次個別重建（分別存 momentum_frame
與 full_pool ranks）之間 DB 資料有些微 drift，也可能是實作上的真實落差，
**本輪未進一步深究，留給下一輪需要時專門調查**，不在此下確定結論。

## Replacement Simulation（Simulation A/B/C，date-based，非用結果選股）

| Simulation | 替換數 | Winner 變化 | Big Loser 變化 |
|---|---|---|---|
| A（換 rank116-120） | 5 | **-6** | -4 |
| B（換 rank111-120） | 10 | **-5** | -10 |
| C（換 rank101-120） | 20 | **-16** | -18 |

**三個模擬全部顯示 Winner count 減少**——沒有一個達到「Winner 增加」的
成功門檻（§23 第 2 條）。這與 Part B 的單調梯度發現完全一致：既然排序在
邊界附近本身就持續攜帶真實資訊，用「排名稍後」的候選替換「排名稍前」的
候選，本質上就是拿較弱的股票換較強的股票，沒有免費午餐。

## D Channel Health Audit

**分類結論：`D_DATA_GAP`（確認）**。20 天 raw union 中 D 通道命中數 **0/
8,936**，`revenue_yoy` 欄位 **0/8,936 有值**——直接查資料庫證實
`monthly_revenue.yoy_pct` 2026 年 1~5 月全部 NULL（1078~1084 檔資料，
`has_yoy=0`），6 月僅 74.9% 有值。**這不是門檻不可達或 pipeline bug，是
最上游的資料缺口**，且與既有文件記載的「已回補」狀態不符，需要另外確認
資料回補是否真的持久生效。

## 回答 12 個核心問題

**1. momentum_score 是否呈現穩定單調梯度？** **是**，Winner rate 由
31.2%（rank1-40）單調遞減到 8.2%（rank501+），Big Loser rate 同步遞減
（16.6%→5.2%），排序整體具備真實資訊。

**2. Rank81-120 是否真的優於 Rank121-160？** **是**（23.0% vs 17.1%
Winner，date-block bootstrap 對 Top120 vs Rank121-200 的整體差距 95% CI
完全為正），截斷邊界沒有失去排序價值的證據。

**3. Rank121-200 中有多少 Day10 Winner 被截掉？** 1,597 筆候選中
14.8%（約 236 筆）是 Winner，但這個 rate 明顯低於 Top120 的 26.8%——
「被截掉的」本身品質就比較弱，不是被錯殺的高品質候選。

**4. Top120 中有多少 Day10 Big Loser 被保留？** 15.0%（360 筆），略高於
Rank121-200 的 8.3%——這是排序「高報酬伴隨高波動」特性的自然結果，不是
排序失靈。

**5. Candidate Pool 120 的主要問題是截斷排序錯誤還是 Admission Coverage
不足？** **都不是決定性問題**——截斷排序本身有效（Part B 證實），而
Admission Coverage 的「未覆蓋」股票整體 Winner rate（13.7%）也只是略低於
市場 baseline（14.4%），**沒有找到大規模、系統性的覆蓋率漏洞**。真正驅動
「候選池外仍有大量絕對數量的 Winner」的原因，主要是「未覆蓋母體本身就很
大」（19,209 vs Top120 的 2,399），而非「未覆蓋母體品質特別高」。

**6. 完全沒有命中 A/B/C/D 的 Day10 Winner 有多少？** 20 天合計 **2,626
筆**（candidate-day 計，非去重股票數），平均每天約 131 筆，但如上所述，
這個絕對數字大主要是因為分母（未覆蓋母體 19,209 筆）本身就很大，相對
Winner rate（13.7%）並不特別突出。

**7. 這些 Admission Missed Winner 主要集中在哪種 Near-Miss？** **本輪未
完整建立**（見必要揭露 #7 的範圍縮減說明）——只對 3 個案例做了手動追蹤，
不足以回答全量分布，需要下一輪專門的大規模 near-miss 重建才能回答。

**8. Near-Miss Winner 與 Near-Miss Big Loser 能否區分？** **本輪未驗證**
（同上，範圍縮減）。

**9. Simulation A/B/C 是否有任何一個能維持 120 檔、增加 Winner 且不增加
Big Loser？** **沒有，三個都減少 Winner**，完全未達成功門檻。

**10. 2634／2451／6243 各自屬於哪種漏失原因？** 2634 漢翔＝
**TRUNCATION（截斷排序，非覆蓋率問題，本輪更正 Phase 3C 的錯誤陳述）**；
2451 創見＝**合理截斷（Day0 特徵確實偏弱）**；6243 迅杰＝
**UNRESOLVED_DISCREPANCY（理論上應通過 B 通道但實際未進入 raw union，
原因不明，需下一輪調查）**。

**11. D 通道為 0：是沒有訊號、門檻不可達、資料問題還是 pipeline bug？**
**確認為 D_DATA_GAP**——`monthly_revenue.yoy_pct` 2026 年 1~5 月全部
NULL，6 月僅 75% 覆蓋，是最上游資料缺口，非門檻或程式邏輯問題。

**12. 最終結論**：

## NO_ACTIONABLE_FIX（截斷/替換方向）+ D_DATA_GAP（獨立確認）

**Candidate Replacement 方向：NO_ACTIONABLE_FIX**——Part B 證實截斷排序
本身有效（統計穩健的單調梯度），Replacement Simulation 三個規模都讓
Winner count 減少，Admission Coverage 的「未覆蓋」母體整體 Winner rate
也只是貼近市場 baseline，沒有找到「候選池仍維持 120 檔、增加 Winner 又
不增加 Big Loser」的可行修正訊號。**依 §29 Stop Criteria：Top120 明顯
優於所有 outside bucket、Replacement 增加 Winner 的同時沒有不成比例減少
Big Loser（事實上 Winner 直接減少）——應該接受目前 Candidate Discovery
的 Recall/Precision 取捨，不繼續擴大候選池或做候選替換**。

**D Channel：獨立確認為 D_DATA_GAP**，這是本輪額外、決定性的發現——
不需要調整 D 通道 threshold 或邏輯，需要的是回頭確認
`monthly_revenue.yoy_pct` 的回補是否真的持久生效（此為資料工程問題，
非本輪選股邏輯範圍）。

**遺留的不確定項（誠實揭露，非本輪結論主軸）**：6243 迅杰的
UNRESOLVED_DISCREPANCY 與 Part C 的 near-miss 全量分類，都因本輪採用
中等樣本規模（20 天）而未能完整處理，若未來要繼續深究 Admission Coverage
的細緻分類，需要另一輪專門重建全市場「未覆蓋股票」的 momentum frame。

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate Pool 120 上限、A/B/C/D threshold、
momentum_score、Hard Exclusion、Phase 2、Phase 2.5、Role、LLM、
confidence、Market Regime、Outcome threshold；未新增 E 通道；未建立
Admission Score 或加權總分；Replacement Simulation 排序只用 Day0 已知的
raw_union_rank（非用 Day10 Outcome 選股票）；未把 7/24 Top70 當主要標籤；
看完 replacement 結果不理想後未調整替換數量或 rank bucket 邊界去湊漂亮
結果；未 hardcode 2634／2451／6243 的分析邏輯（三檔僅作案例展示，分類
依實際重建資料判定）；未修改 D 通道門檻；未做 Portfolio Backtest；未修改
Phase 2 最終發布邏輯。
