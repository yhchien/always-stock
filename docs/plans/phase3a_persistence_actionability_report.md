# Phase 3A：Persistence Actionability Test（2026-07-24）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、Candidate Selection、
> Phase 2、Phase 2.5、`continuation_quality_state`、Persistence 定義、既有
> threshold、Hard Exclusion、LLM。不做 re-entry、換股、加碼、資金配置、完整
> Portfolio Backtest——只驗證「單一股票提前退出」這個動作本身的淨經濟價值。
>
> 腳本：`backend/analyze_phase3a_actionability.py`
> 母體：617-dedup replay cohort 全部 617 檔（含完整 Day10 baseline 者，本輪
> **617/617 全數納入**，無資料不足需排除的案例）
> 輸出：`/tmp/phase3a_persistence_actionability_all.csv`（617 筆）、
> `/tmp/phase3a_persistence_actionability_summary.csv`、
> `/tmp/phase3a_big_loser_saved_cases.csv`、`/tmp/phase3a_winner_foregone_cases.csv`

## 核心問題

> 假設歷史上真的依照既有 Persistence 規則退出單一股票，「避免的後續虧損
> （Saved Loss）」是否大於「因此錯失的大牛股後續漲幅（Foregone Upside）」？

## 方法論揭露（必要，非隱藏限制）

- **Persistence 定義沿用 Phase 2.7 原始定義**：以既定 7 個 observation
  （Day0/1/2/3/5/7/10）為單位，「連續 N 個」指這 7 個 observation 陣列中的
  連續索引，不是連續交易日。
- **Trigger 偵測邏輯（本輪必要的操作化選擇，誠實揭露）**：Phase 2.7 原始
  `persistence` 統計只描述「第一次進入風險狀態後，連續維持了幾次」；本輪
  要回答「如果真的即時盯著看、什麼時候會觸發退出」，因此改用**逐一掃描 7
  個 observation、追蹤連續符合次數，第一次達到門檻的那個 observation 即為
  觸發點**（若中途曾出現風險狀態但未連續到位、之後又中斷，之後真的連續到位
  時仍會觸發）——這是把「描述性統計」轉成「即時監控觸發點」必要的詮釋，
  沒有改變 threshold 或規則定義本身。
- **執行假設**：訊號 observation 隔天的**開盤價**執行退出（`next_day_open`）
  ——**本輪 617×3=1851 次觸發，全部 100% 都有下一交易日開盤價可用，
  0 次需要退回 close fallback**，執行假設乾淨、無資料缺口需要揭露。
- **Baseline**：`first_seen` 持有到 Day10 的報酬（用同一份 trajectory 的
  Day10 offset 值，非另外抓 cohort 現成欄位，確保與本輪其他分析同一套資料
  來源）。**617/617 全數有完整 Day10 資料**，無需排除任何股票。

## 一、核心 Trade-off Table

| Metric | Rule A（AT_RISK+≥3） | Rule B（FAILED≥2） | Rule C（FAILED≥3） |
|---|---|---|---|
| Trigger Rate（全體） | 26.1%（161/617） | 22.2%（137/617） | 17.5%（108/617） |
| Baseline Mean Return | 4.81% | 4.81% | 4.81% |
| Counterfactual Mean Return | 4.49% | 4.41% | 4.71% |
| **Overall Mean Delta** | **-0.32pp** | **-0.40pp** | **-0.10pp** |
| Overall Delta 95% Bootstrap CI | **[-0.63, -0.06]** | **[-0.75, -0.11]** | [-0.29, +0.07] |
| Overall Median Delta | 0.0pp | 0.0pp | 0.0pp |
| Total Positive Delta | +284.2pp | +243.9pp | +169.4pp |
| Total Negative Delta | -483.5pp | -492.6pp | -231.7pp |
| **Net Delta** | **-199.3pp** | **-248.7pp** | **-62.2pp** |
| BIG_LOSER Trigger Rate | 81.8% | 71.2% | 65.2% |
| BIG_LOSER Mean Saved Loss | +2.95pp | +3.36pp | +2.26pp |
| NEVER_WORKED Trigger Rate | 91.7% | 95.8% | 89.6% |
| NEVER_WORKED Mean Saved Loss | +3.31pp | +3.37pp | +2.26pp |
| WINNER Trigger Rate | 6.0%（10/166） | 3.0%（5/166） | 0.6%（1/166） |
| WINNER Mean Foregone Upside | -12.99pp | -25.52pp | -21.66pp |
| WINNER Max Foregone Upside | -30.23pp | -53.43pp | -21.66pp |
| <=-10% Left Tail | 66→**69**（惡化 -4.5%） | 66→**67**（惡化 -1.5%） | 66→65（微幅改善 +1.5%） |
| <=-15% Left Tail | 23→16（改善 +30.4%） | 23→18（改善 +21.7%） | 23→22（微幅改善 +4.3%） |
| <=-20% Left Tail | 6→**8**（惡化 -33.3%） | 6→6（無變化） | 6→**8**（惡化 -33.3%） |

**三條規則的 Overall Mean Delta 與 Net Delta 全部是負值**——這是本輪最重要
的核心發現：**平均而言，退出動作讓整體報酬變差，不是變好**。Rule A/B 的
95% Bootstrap CI 完全落在負值區間（不含 0），代表這個負面效果具統計穩健性、
不是雜訊；Rule C 的 CI 跨過 0（[-0.29, +0.07]），代表 Rule C（門檻最嚴、
觸發最少）的負面效果沒有到統計上可以完全確認，但方向仍是負的。

## 二、令人意外但誠實的發現：Left Tail 在 -10% / -20% 兩個門檻反而惡化

**這不是報告錯誤，是真實現象，需要解釋**：Baseline 的 <=-10% 定義就是
`BIG_LOSER` 這 66 檔股票；counterfactual 卻多出 3（Rule A）/1（Rule B）
檔落入 <=-10%——**新增的這幾檔，是原本 baseline 收在 -10% 以上（甚至是
WINNER 或 OTHER）的股票，因為中途曾經歷一段短暫但深的回檔、被 Persistence
規則在低點附近觸發退出，鎖住了一個原本只是暫時性、後來會反彈回去的大跌**。
换句話說：**Persistence exit 不只是「少賺了原本會發生的漲幅」，還會主動把
「原本沒事、後來會反彈回正常」的股票，實際變成一筆真正的大虧紀錄**——這比
單純「錯過上漲」更嚴重，是本輪最需要誠實揭露的風險。

## 三、時機分析：訊號來得太晚

BIG_LOSER 觸發退出時，**74.1%（Rule A）/ 68.1%（Rule B）/ 76.7%（Rule C）
的案例，訊號 observation 當下報酬其實已經 <=-10%**——換句話說，Persistence
confirm 的時候，虧損多半已經造成，只是「還沒更慘」而已。這與 Phase 2.7 的
「觸發時平均報酬約 -5%」描述性結論看似矛盾，但差異在於：Phase 2.7 是用
Day0 起算、只看「第一次進入風險狀態」的持續性；本輪的即時監控觸發邏輯要求
連續達標（尤其 Rule C 要連續 3 次 FAILED），需要更多天數才能真正 confirm，
這段等待期間虧損已經持續擴大——**「等到確認」與「馬上反應」之間存在明確的
時間代價，這是本輪的重要新發現**。

## 四、NEVER_WORKED 子群體（Persistence 理論上最適用的對象）

| | Rule A | Rule B | Rule C |
|---|---|---|---|
| Trigger Rate | 91.7% | 95.8% | 89.6% |
| Mean Saved Loss | +3.31pp | +3.37pp | +2.26pp |
| <=-10% count（baseline→counterfactual） | 48→34 | 48→31 | 48→35 |
| <=-15% count | 16→7 | 16→8 | 16→13 |
| <=-20% count | 1→2（惡化） | 1→1 | 1→3（惡化） |

**這是三個規則表現最好的子群體**——高觸發率（89.6%~95.8%）、正向 saved
loss、且 <=-10%/<=-15% 的 left tail 都確實下降（尤其 Rule A/B）。但即使在
這個最有利的子群體，<=-20% 這個最極端尾部仍然出現微幅惡化（1→2、1→3），
再次印證第二節提到的「短暫深回檔被鎖住」現象並非只發生在 WINNER 身上。

## 五、ROUND_TRIP_FAILURE（僅觀察，未嘗試新增規則救援）

| | Rule A | Rule B | Rule C |
|---|---|---|---|
| Trigger Rate | 55.6%（10/18） | 5.6%（1/18） | **0.0%（0/18）** |
| Mean Saved Loss | +1.38pp | +3.09pp | N/A |

Rule C（FAILED persistence>=3）對 ROUND_TRIP_FAILURE **完全沒有觸發過**，
直接印證 Phase 2.9 的結論——這類股票沒有足夠可靠的早期訊號，本輪**依指示
不新增任何 Profit Retention 或新 drawdown threshold 來救援**，誠實接受
「Persistence 對這類失敗型態幫助有限」的現實。

## 六、Bootstrap 穩健性 + Time Split

- **Bootstrap**：Rule A/B 的 Overall Delta CI 完全落在負值（穩健的負面
  效果，非少數極端股票驅動）；BIG_LOSER saved loss 的 CI 對三個規則都完全
  落在正值（[1.64,4.16]／[2.07,4.63]／[1.22,3.37]），代表「Persistence 對
  loser 有正向 saved loss」這件事本身是穩健的；但 WINNER foregone 的 CI
  也完全落在負值且區間寬（尤其 Rule B 的 [-40.3,-15.8]），代表對 winner
  的傷害同樣穩健、且量級波動大。
- **Time Split**（前半 n=308 / 後半 n=309，依 first_seen_date 切分，非
  out-of-sample test，只是穩健性檢查）：三個規則的方向在前後半段**大致
  一致，沒有出現「前半有效、後半完全反轉」**（Rule A/B 前後半 Overall
  Mean Delta 都是負值；Rule C 前半微幅正值 +0.06pp、後半微幅負值
  -0.26pp，接近零、方向不穩定但本來就在 CI 跨零的規則上，不算矛盾）。
  值得注意 Rule B 的 winner_foregone 後半段（28.88pp）遠高於前半段
  （12.11pp）——但這個 split 建立在**極小樣本**上（Rule B 全體只有 5 檔
  觸發的 winner，切一半後每邊只有 2~3 檔），**必須誠實揭露這個特定數字
  極不可靠**，不應過度解讀。

## 回答 9 個核心問題

**1. Rule A（AT_RISK persistence>=3）真的退出後，Overall Mean Return 變好
還是變差？** **變差**——Overall Mean Delta -0.32pp，Bootstrap CI 完全落在
負值 [-0.63,-0.06]，統計上穩健地變差。

**2. Rule B（FAILED persistence>=2）結果如何？** **變差，且是三條規則中
最差的**——Overall Mean Delta -0.40pp，CI [-0.75,-0.11] 完全負值；Winner
Max Foregone Upside 高達 53.43pp，是三條規則中對單一大贏家傷害最重的。

**3. Rule C（FAILED persistence>=3）結果如何？** **接近持平、方向仍為負但
不具統計顯著性**——Overall Mean Delta -0.10pp，CI [-0.29,+0.07] 跨越 0；
是三條規則中負面影響最小的，但也是 BIG_LOSER saved loss 最小、訊號最晚的
規則（觸發時 76.7% 已經 <=-10%）。

**4. 哪條 Rule 最能降低 <=-10%/<=-15%/<=-20% Left Tail？** **沒有一條規則
在三個門檻上全面勝出**——<=-15% 門檻上 Rule A 表現最好（+30.4% 改善）；但
<=-10% 與 <=-20% 兩個門檻，**三條規則多數呈現惡化而非改善**（Rule A/B 在
<=-10% 惡化，Rule A/C 在 <=-20% 惡化）。只有 Rule C 在 <=-10% 有微幅
（+1.5%）改善。**沒有找到能同時在三個門檻都可靠降低尾部風險的規則**，
本輪未因此嘗試調整 threshold 去湊出更好看的結果。

**5. BIG_LOSER 平均少虧多少？** Rule A +2.95pp、Rule B +3.36pp、
Rule C +2.26pp——**方向正確但量級不大**，且 74~77% 的觸發案例訊號當下已經
跌破 -10%，代表這個「少虧」多半發生在已經很深的虧損之後。

**6. NEVER_WORKED 平均少虧多少？** Rule A +3.31pp、Rule B +3.37pp、
Rule C +2.26pp——與 BIG_LOSER 整體數字相近，是本輪唯一 <=-10%/<=-15%
tail 兩個門檻都確實改善的子群體，但 <=-20% 極端尾部仍微幅惡化。

**7. WINNER 被錯誤提前退出多少？** 觸發率雖低（0.6%~6.0%），但**代價巨大且
不對稱**：Rule A 平均少賺 12.99pp（最嚴重案例 30.23pp）、Rule B 平均少賺
25.52pp（最嚴重案例 **53.43pp**）、Rule C 平均少賺 21.66pp（唯一 1 檔就是
21.66pp）。**規則越嚴（要求連續 FAILED 次數越多），觸發次數越少，但一旦
誤觸發在 winner 身上，代價反而越大**（因為需要更深、更持久的回檔才能連續
達標，代表誤觸發的 winner 當時經歷了更劇烈的回檔）。

**8. Saved Loss 與 Foregone Upside 放在一起計算後，是否存在正向 Economic
Value？** **不存在，三條規則的 Net Delta 全部是負值**（-199.3pp / -248.7pp
/ -62.2pp）。即使 BIG_LOSER 的 saved loss 統計上穩健為正（Bootstrap CI 全
正值），觸發在少數 WINNER 身上的 foregone upside 量級遠大於個別 loser 的
saved loss（12.99~25.52pp vs 2.26~3.36pp，約 4~11 倍），**在動能股票池中，
少數大贏家貢獻的漲幅集中度，足以讓「盡量避開大跌」策略在總量上得不償失**。

**9. 最終結論**：**SHADOW_ONLY**。

理由：
- Persistence 對 BIG_LOSER / NEVER_WORKED **確實具有穩健、可信的風險辨識
  價值**（Bootstrap CI 皆為正值），不是完全沒用（不到 REJECT 的程度）
- 但真的把它拿來當**自動退出動作**執行時：(a) Net Delta 三條規則全部為
  負；(b) <=-10% 與 <=-20% 兩個 Left Tail 門檻在多數規則下反而**惡化**
  （因為會鎖住本來只是暫時性、之後會反彈的深回檔）；(c) 對少數 WINNER
  的傷害量級（12.99~25.52pp、最嚴重達 53.43pp）遠大於對多數 LOSER 的
  幫助量級（2.26~3.36pp）；(d) 訊號確認時機偏晚（68~77% 觸發時已經
  <=-10%），提前處理空間有限
- 依 §26 Stop Criteria：「Loser 確實少虧，但 Winner 少賺更多」與
  「Overall Mean Return 明顯下降」**兩項在 Rule A/B 都明確成立**——
  依規則應該停止把 Persistence 直接當 Exit Action
- **不建議進入 PROCEED（不設計 research-only Exit Candidate State 的實際
  賣出邏輯）**；建議 Persistence 繼續只作為**風險警示（Shadow Warning）**
  ——例如在既有系統中標示「此股票 Persistence 已達警戒」供人工判斷參考，
  而不是設計成自動觸發退出的規則

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate Selection、Phase 2、Phase 2.5、
`continuation_quality_state`、Persistence 定義本身、既有 threshold、Hard
Exclusion、LLM；未新增 Rule D、未新增 Profit Feature、未新增 Round-Trip
Feature；未做 threshold search（看完結果後未調整任何門檻，Rule A/B/C 三個
數字維持原定義）；未做 re-entry / 換股 / 加碼 / 資金配置 / 完整 Portfolio
Backtest；未 hardcode stock_id 邏輯（僅用於 CSV 識別）；本輪結論為
SHADOW_ONLY，依 §28 不設計正式 Exit Candidate 賣出邏輯。
