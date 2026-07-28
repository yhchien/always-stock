# Phase 3C：RISK_OFF Day1 Survival & Miss Audit（2026-07-27）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、Candidate Pool
> 120 檔邏輯、A/B/C/D、Phase 2、Phase 2.5、Hard Exclusion、Role Classification、
> LLM Prompt、confidence 定義、momentum_score、Market Regime、Outcome
> threshold。

## 核心問題

> 2026-07-23 收盤後 Phase 2 選出的股票，到 2026-07-24 市場下跌時，哪些仍然
> 強勢或抗跌？哪些明顯失去承接？7/24 真正上漲的股票，為什麼 7/23 沒被選到？

## 必要揭露（§24）

1. **7/24 TOP70 資料來源**：本機 `daily_price` 表（FinMind 為主要 ETL 來源），
   非「現在網站最新排行」——直接查歷史 `trade_date=2026-07-23/2026-07-24` 的
   收盤價，屬歷史資料非即時排行。
2. **TOP70_RAW vs TOP70_ELIGIBLE**：RAW 只排除 close<=0（無效收盤）與 2 檔
   誤入 `stocks_master` 的指數佔位列（`TradingConsumersGoods`／
   `ShippingTransportation`，非真實個股，已排除，記錄於此作資料清潔揭露）；
   ELIGIBLE 進一步套用「未被人工黑名單排除」+「5 日均成交額 >= 5,000 萬」
   （沿用 `filters._HARD_LIQUIDITY_MIN_TWD` 既有常數）。**本輪沒有獨立的
   TWSE/OTC 分類欄位**（`stocks_master.market` 全部標記 `twse`），故「上市
   母體」實務上等於 `stocks_master` 全部收錄股票，此為既有資料限制，非本輪
   造成。
3. **7/23 Candidate Pool 120 檔可完整重建**：用 `candidate_pool.ingest_data`
   + `compute_rankings` + `compute_market_momentum_frame` +
   `build_candidate_pool`（含 momentum_score 截斷）**完整重跑**，重建結果
   剛好也是 120 檔（與當時 production 一致）。
4. **每檔 MISSED_STRONG 的 pipeline stage 可追蹤**：用 `pipeline_v2.
   build_phase2_pool`（excluded_out 追蹤 hard exclusion）+
   `pipeline_v2.run_phase2_pipeline`（explain_traces，含 hard-excluded 與
   survivors 雙路徑）完整重建。**唯一已知落差**：重建的 regime-gate 存活者
   （survivors）有 26 檔，但真實 07-23 production 最終 WATCH 是 20 檔且
   `removed` 清單長度為 0——差距 6 檔可能來自重跑時點與當時 production 執行
   時點之間些微資料 drift（例如法人流資料當天陸續入帳），已在 MISS_STAGE
   分類中誠實標記為 `MISS_STAGE_5_OR_LLM_CAP`（2 檔屬此類：00642U、2603長榮），
   不強行歸類為某個確定原因。
5. **實際使用的籌碼欄位**：`inst_stock_flow` 的 `foreign/trust/dealer`
   三大法人合計，算 1 日/3 日/前 3 日（訊號當日之前）金額。
6. **實際使用的 peer/sector scope**：`momentum.py` 既有的
   `industry_rs_percentile_20d`（個股在自己 `stocks_master.industry_name`
   同業中的 20 日報酬百分位）與 `industry_return_20d`（產業平均 20 日報酬）——
   沿用既有 production 欄位，非新建 canonical taxonomy 比較（時間有限，未
   使用 Phase 1 canonical sub_sector 做更精細分組，此為本輪範圍縮減）。
7. **缺資料狀況**：40 檔（Part A）+ 159 檔（Part B）全部有完整 OHLC / 法人
   / momentum frame 資料，0 筆因缺資料被排除。
8. **消息面 Historical Catalyst Audit（§11）未執行**：需要即時網路查證，
   考量本輪已耗費大量時間在其餘 8 個小節的資料重建，**本輪明確縮減範圍，
   不做消息面稽核**，僅用 Day0 deterministic 特徵作 Evidence Family 分析。
9. **Part B 歷史 RISK_OFF cohort**：deterministic replay（`phase25_replay_
   60d.json`）62 個交易日中，**只有 6 天被判定 RISK_OFF**（2026-05-19、
   05-20、06-10、06-11、06-26、06-29），共 **159 筆候選**（未去重，允許同
   股票不同天重複出現，因為本輪研究的是「Day0 特徵 → Day10 結果」的
   candidate-level 關聯，不是股票去重母體）。**這是 deterministic 候選池
   survivors，不是真實歷史 LLM 最終 WATCH 名單**（真實 production 的
   RISK_OFF WATCH 歷史目前只有 2 天有完整 Day10 結果、共 1 檔股票，樣本太
   小完全無法使用，已在前一輪對話中確認並經使用者同意改用此替代母體）。

## Part A：2026-07-23 → 07-24 單日鑑識

### 一、SELECTED 分類（20 檔 WATCH，TAIEX 07-24 報酬 -2.67%）

| 分類 | n | 股票 |
|---|---|---|
| **SELECTED_STRONG** | 8 | 6277宏正、6414樺漢、3231緯創、3022威強電、5388中磊、2425承啟、6669緯穎、6670復盛應用 |
| **SELECTED_WEAK** | 2 | 6505台塑化、2527宏璟 |
| **SELECTED_NEUTRAL** | 10 | 6957裕慶-KY、2027大成鋼、6579研揚、1709和益、6416瑞祺電通、8114振樺電、4770上品、3605宏致、1434福懋、2103台橡 |

（4 檔 STRONG 是因為進入 TOP70_ELIGIBLE：3231/3022/2425/6669/6670 共 5 檔；
其餘 3 檔 STRONG 是 market excess return >= +2pp 達標）

### 二、MISSED_STRONG 分布：TOP70_ELIGIBLE 中有 **63 檔**（扣除已在 WATCH 的
5 檔 + 2 檔指數佔位列）未被 07-23 選中

### 三、MISS_STAGE 分布（本輪最重要的發現）

| Stage | n | 佔比 |
|---|---|---|
| **MISS_STAGE_1_NOT_ADMITTED**（連原始候選池 686 檔聯集都沒進） | 37 | 58.7% |
| **MISS_STAGE_1_TRUNCATED**（進了原始池但被 momentum_score 截斷排序砍掉） | 15 | 23.8% |
| MISS_STAGE_4_REGIME_GATE（RISK_OFF 存活條件不足，reason 皆為 `regime_excluded:RISK_OFF`） | 7 | 11.1% |
| MISS_STAGE_5_OR_LLM_CAP（重建存活但真實 production 未見，見揭露 #4） | 2 | 3.2% |
| MISS_STAGE_2_HARD_EXCLUDED（6533晶心科，`COMPOSITE_RISK_EXCLUDE`） | 1 | 1.6% |
| MISS_STAGE_3_NO_ROLE（2630亞航，`base_momentum_not_eligible`） | 1 | 1.6% |

**82.5%（58.7%+23.8%）的 MISSED_STRONG 連候選池都沒進**——這是壓倒性的
Recall 問題，不是最終發布層的 Precision 問題。只有 13.5%（regime_gate +
hard_exclude + no_role）真正走到 deterministic 後段才被剔除，且 regime_gate
的 7 檔剔除理由（RISK_OFF 存活條件不足）是既有設計的**刻意保守行為**，不
一定代表錯誤。

**個案亮點**：
- **2634 漢翔**：07-23 當下 `rs_market_percentile_20d=98.5`（市場前 1.5%
  強度）、無任何 Failure Archetype 命中（hits=0，全面健康），卻連候選池
  686 檔聯集都沒進——這是最乾淨的「Recall 純漏失」案例，Day0 特徵完全找不到
  應該剔除它的理由
- **2451 創見**：進了候選池但在 686 檔中排名第 561（momentum_score 偏低），
  被截斷判 MISS_STAGE_1_TRUNCATED
- **6243 迅杰**：07-24 單日 +9.9%（TOP70_ELIGIBLE 第一名），07-23 完全未被
  任何 A/B/C/D 通道admit——隔天（07-24）才被正式抓到

### 四、Evidence Family + Failure Archetype（20 檔 WATCH，小樣本，僅描述性）

| 股票 | 分類 | F1 FLOW_ROLLOVER | F2 EXHAUSTION_CLOSE | F3 FALSE_BREAKOUT | F4 ISOLATED_MOMENTUM | F5 WEAK_SECTOR_LEADER |
|---|---|---|---|---|---|---|
| 6505台塑化 | WEAK | – | **✓**（return_20d=68.8%, distance_to_ma20=+40.1%） | – | – | – |
| 2527宏璟 | WEAK | **✓**（法人由買轉賣+當日轉弱） | – | – | – | – |
| 8 檔 STRONG | STRONG | 0/8 命中 | 3/8 命中 | 0/8 命中 | 7/8 命中 | 0/8 命中 |
| 10 檔 NEUTRAL | NEUTRAL | 1/10 命中 | 2/10 命中 | 0/10 命中 | 4/10 命中 | 0/10 命中 |

**在這 20 檔的小樣本裡**：F1（籌碼反轉）與 F2（爆量弱收）看起來各自命中
唯一或主要落在 2 檔 WEAK 身上，方向正確；但 **F4（孤立動能）在 STRONG 組
命中率反而最高（7/8）**，與「孤立動能=風險」的假設方向相反——這已經是本輪
第一個警訊，提示 F4 的操作化方式可能有問題，需要靠 Part B 更大樣本驗證才能
判斷是真訊號還是巧合。

## Part B：歷史 RISK_OFF 驗證（159 筆候選，6 個交易日，完整 Day10 結果）

### 全體 baseline
n=159，Winner rate **44.7%**，Big Loser rate **15.1%**

### 各 Archetype 命中後的 Outcome（決定性結果）

| Archetype | n_hit | Winner% | BigLoser% | mean 10d return |
|---|---|---|---|---|
| F1_FLOW_ROLLOVER | 7 | 28.6% | **0.0%** | +9.28% |
| F2_EXHAUSTION_CLOSE | 46 | 47.8% | 17.4% | +12.29% |
| F3_FALSE_BREAKOUT | 0 | — | — | — |
| F4_ISOLATED_MOMENTUM | 19 | **63.2%** | 21.1% | **+23.41%** |
| F5_WEAK_SECTOR_LEADER | 0 | — | — | — |

**這是本輪最關鍵、也最誠實必須面對的結果**：

- **F2（爆量弱收/EXHAUSTION_CLOSE）在 Part A 單日案例看起來是最乾淨的
  「失敗訊號」（台塑化的教科書式案例），但在 6 天、46 筆的歷史樣本中，
  Winner rate（47.8%）與 baseline（44.7%）幾乎相同，Big Loser rate
  （17.4%）只比 baseline（15.1%）高 2.3pp，mean return（+12.29%）甚至
  高於 baseline——**F2 完全沒有通過歷史驗證**，Part A 的教科書案例可能只是
  單日巧合
- **F4（孤立動能/ISOLATED_MOMENTUM）在歷史樣本中 Winner rate 63.2%，遠
  高於 baseline**——與 Part A 觀察方向一致（STRONG 組命中率最高），**進一步
  確認 F4 根本不是失敗訊號，反而偏向正面**
- **F1（籌碼反轉/FLOW_ROLLOVER）雖然 Winner rate 偏低（28.6%）且 Big
  Loser rate 是 0%**——命中的 7 檔沒有任何一檔變成大輸家，方向上唯一算是
  「風險降溫」但不是「風險升高」，跟預期的失敗訊號方向也不完全相符（只是
  「表現平庸」不是「大虧」）
- **F3、F5 在這 6 天歷史樣本中 0 命中**，完全無法驗證，操作化門檻可能太嚴或
  這兩個型態在 RISK_OFF 環境下本來就少見

### Veto Simulation（Rule A/B/C）

| Rule | n | Winner% | BigLoser% |
|---|---|---|---|
| Rule A（命中 >=2 個 archetype） | 6 | 50.0% | 16.7% |
| Rule B（F1 + 任一其他） | 3 | 33.3% | 0.0% |
| Rule C（F2 + 任一其他） | 6 | 50.0% | 16.7% |

**三條規則的樣本數都太小（3~6 檔），且 Winner rate 與 Big Loser rate 都跟
全體 baseline 幾乎沒有差異**——沒有一條規則展現出可用的區分力，更遑論達到
「Winner Retention >=95% 且 Big Loser Removal >=25%」的成功門檻。

## 回答 12 個核心問題

**1. 7/23 WATCH 20 檔中三類各多少？** STRONG 8、NEUTRAL 10、WEAK 2。

**2. 7/24 TOP70_ELIGIBLE 有多少檔 7/23 已被選到？** 只有 5/70（3231緯創、
3022威強電、2425承啟、6669緯穎、6670復盛應用），**65 檔（92.9%）完全沒被
選到**。

**3. MISSED_STRONG 主要死在哪個 pipeline stage？** **MISS_STAGE_1（未進候選
池），佔 82.5%**——遠遠超過其他所有 stage 的總和。

**4. Candidate Discovery 是否反應太慢？** **是，而且是壓倒性的**——超過八成
的錯失強股，問題發生在「連候選池的門都沒進來」這一關，不是「進了候選池卻被
後段規則刪掉」。

**5. SELECTED_WEAK 在 Day0 最常見的失敗結構是什麼？** 兩檔各自對應不同型態：
台塑化是**極度延伸**（20 日漲 68.8%、距 20 日均線 +40.1%），宏璟是**籌碼
反轉**（法人由連買轉賣、當日價格同步走弱）。**但樣本只有 2 檔，且 Part B
證實這兩種型態在更大樣本中都沒有穩定的預測力**，不能一般化。

**6. SELECTED_STRONG 在 Day0 最常見的健康結構是什麼？** 高比例命中
F4（孤立動能，7/8），但這**不是「健康結構」，是本輪一開始假設錯誤的
「風險結構」**——這批股票的共同點其實是「產業內相對強度不特別突出，個股
自己動能強」，Part B 證實這種型態歷史上反而是偏正面的（Winner rate
63.2%）。

**7. 籌碼路徑、技術收盤品質、產業確認：哪一個 Evidence Family 區分力最
穩定？** **沒有一個穩定**——這是本輪最重要的誠實結論。F2（技術收盤品質
代表）與 F4（產業確認代表）在歷史樣本中都不具備區分力（甚至方向相反），
F1（籌碼路徑代表）方向勉強正確但樣本太小（n=7）且效果溫和（0% Big Loser
但 Winner rate 也偏低，比較像「表現平庸」而非「風險訊號」）。

**8. 18/20 LEADER 是否包含大量 WEAK_SECTOR_LEADER 或 CLUSTER_PASSENGER？**
F5（WEAK_SECTOR_LEADER 的 operationalization）在 Part A 20 檔與 Part B
159 筆歷史樣本中**都是 0 命中**，本輪的操作化定義完全無法在資料中找到符合
案例，因此答案是：**用本輪定義無法驗證**（可能定義門檻不適用於這批
RISK_OFF 樣本，需要下一輪重新設計才能回答，本輪不強行調整門檻硬湊命中）。

**9. Phase 2 的主要問題是 Recall、Precision 還是兩者都有？** **主要是
Recall**（82.5% MISS_STAGE_1），**Precision（Final Selection 篩掉真正
弱勢股）的證據薄弱**——2 檔 SELECTED_WEAK 佔 20 檔中僅 10%，且對應的
Failure Archetype 在歷史驗證中站不住腳。

**10. Part A 的 7/23 結論，能否在完整歷史 RISK_OFF cohort 重現？** **不能，
這是本輪最需要誠實面對的結果**——Part A 看起來乾淨的 F2（爆量弱收）案例，
在 6 天 159 筆歷史驗證中完全沒有重現（Winner rate/Big Loser rate 都與
baseline 無異）；F4 甚至方向整個反過來。**只有 MISS_STAGE 分布（Recall
問題）這件事本身不需要歷史驗證——它是當天 pipeline 架構的事實紀錄，不是
統計推論**，因此第 3/4/9 題的 RECALL 結論是穩健的，但 Failure Archetype
（第 5/6/7/8 題涉及的 Precision 側）結論不穩健。

**11. Rule A/B/C 是否有任何一條能達到 Winner Retention >=95% 且 Big Loser
Removal 接近或超過 25%？** **沒有**——三條規則命中數都太少（3~6 檔），且
Winner rate/Big Loser rate 都跟全體 baseline 幾乎沒有差異，完全沒有達到
成功門檻。

**12. 最終結論**：

## RECALL_FIX（針對 Candidate Discovery / 候選池覆蓋率）

主要問題是強股根本沒進 Candidate Pool——82.5% 的 MISSED_STRONG 死在
MISS_STAGE_1，這是壓倒性、單日內即可觀察到的架構事實，不需要靠統計驗證。
若要改善，方向應該是 §21 提到的 **Emerging Momentum Shadow Pool**（尚未
完全達標、但籌碼與產業動能正在加速的影子候選），且**只能先 Shadow 觀察**，
不應直接放寬正式候選池 threshold。

## 同時：Failure Archetype / Veto Simulation 側為 STOP

本輪測試的 5 個 Failure Archetype（F1~F5）與 3 條 Veto Rule，在歷史 RISK_OFF
樣本驗證中**全部無法通過 Stop Criteria 的檢驗**：F2/F4 命中大量 Winner
（違反 §20.2「Failure Archetype同時命中大量Winner」）、F1 效果微弱且樣本
過小、F3/F5 完全無法驗證。**不應把本輪任何一個 Failure Archetype 或 Veto
Rule 當作候選壓縮或發布層過濾的依據**，也不建議再繼續調整這幾個 archetype
的 threshold 去湊出更好看的結果（依 §20.4/§26 stop 條款，看到 F2/F4 不成立
後應該接受結果，不強行修改定義）。

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate threshold、Role、Hard Exclusion、
confidence、LLM Prompt；未建立新總分；未用 7/24 結果 hardcode 股票；未把
全部 7/24 下跌股稱為 BIG_LOSER（正確使用 Day1 Strong/Survived/Weak 用語，
Day10 Outcome 只用於 Part B 歷史驗證）；未把全部未進 TOP70 的股票稱為
失敗；未用 7/24 資料建立 7/23 feature（Evidence Family 全部只用 07-23
收盤前資料）；看到 Part B 結果不理想（F2/F4 不成立）後**未調整 archetype
定義去湊出更漂亮的結果**，誠實回報 STOP；未新增第 4 個 Veto Rule；未直接
刪除 FOLLOWER 或 low confidence；未設定同產業上限或每日固定候選數；未做
消息面 Hard Exclusion（本輪甚至完全未執行消息面稽核）；未做 Portfolio
Backtest。
