# Phase 2.8：Normal Winner Pullback vs Round-Trip Failure Validation（2026-07-24）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、`continuation_quality_
> state`、`tracking_state`、`momentum_freshness`、Hard Exclusion、LLM、Candidate
> Selection、既有 threshold（含 `drawdown_from_max<=-8%`）。純離線分析既有
> 132-stock trajectory 資料，未重新查資料庫、未重跑 617 檔 full pipeline。
>
> 腳本：`backend/analyze_phase28_normal_pullback.py`
> 輸出：`/tmp/phase28_normal_pullback_winners.csv`（23 檔候選）、
> `/tmp/phase28_roundtrip_vs_pullback_matched.csv`（18 vs 18 matched，P0-P4）

## 核心問題

> 當兩檔股票都曾經上漲、且都開始從局部高點回檔時：「最後重新轉強的大贏家」
> 和「最後把獲利吐光的大輸家」，能不能在回檔早期被區分？

## 一、必要揭露（§22）

**1. 66 WINNER 中符合 NORMAL_PULLBACK_WINNER 的數量**：**23 檔**（peak 不落在
最後一個 offset、且 peak 之後至少有一次 drawdown_from_max<=-5%）。>=18，因此
從 23 檔中挑出與 18 檔 ROUND_TRIP_FAILURE 最匹配的 18 檔，**未使用全部 23 檔
做主要比較**（但同時保留「全部 23 檔」作 robustness test 對照組，見下）。

**2. 完成配對數**：18/18（全部配對成功，使用「每個 loser 找自己完整候選清單
中第一個未被取走的」演算法，非單純 best-choice-only greedy）。

**3. MFE（peak_return）配對品質：差，必須誠實揭露**——這是本輪最重要的限制：

| | ROUND_TRIP_FAILURE (n=18) | NORMAL_PULLBACK_WINNER matched (n=18) |
|---|---|---|
| median peak_return | **6.10%** | **25.21%** |
| peak_return 範圍 | 3.19% ~ 18.58% | 14.7% ~ 60.6% |

**66 檔 WINNER 中，凡是「曾經真正回檔過（drawdown<=-5%）」的，幾乎都是 MFE
在 14.7% 以上的大贏家**——23 檔候選裡最低的 peak_return 也有 14.7%，跟
ROUND_TRIP_FAILURE 的中位數 6.1%（甚至最高也只 18.58%）幾乎沒有重疊區間。
**這不是配對演算法的問題，是這批資料本身的結構性現象**：MFE 較小
（僅 3~10%）又真正發生過 >=5% 回檔的股票，在這 66 檔 WINNER 母體裡幾乎不
存在——合理的解讀是，「MFE 很小又真的回檔」的股票，多數當下就直接跌破
+10% 的最終門檻，根本不會被歸類進 WINNER 組。distance median 因此高達
47.81（0/18 配對 distance<10）。**這代表本報告的 matched 比較不是嚴格的
「同 MFE、同回檔幅度」比較，讀者需要對後續數字打折扣解讀**，好消息是：
用 `profit_retention_ratio`（浮盈保留率，已用各自 peak_return 正規化）可以
部分修正這個規模落差，詳見第三節。

**4. Pullback magnitude 配對品質**：P1 drawdown 中位數 ROUND_TRIP -6.75% vs
NPW -12.21%——NPW 的**原始回檔百分點反而更大**（因為它們的 peak 更高，有更多
「安全距離」可以回檔）。這進一步說明第 3 點的規模落差是系統性的，不是偶然。

**5. 每個 P0/P1/P2/P3 的有效樣本 n**：P0=18v18、P1=18v18、P2=15v11、
P3=14v5、P4=11v1（NPW 樣本從 P3 起迅速萎縮，**P4 起 NPW 只剩 1 檔，不可用**）。

## 二、Robustness Test（§12）：真正有回檔的 Winner，persistence 還乾淨嗎？

用**全部 23 檔候選**（非僅 matched 18 檔）跟 Phase 2.7 PART A 的「全體 66 檔
WINNER（含未回檔）」結果對照：

| 訊號 | Phase 2.7（全體 66 WINNER，Day0 起算） | 本輪（23 檔真正回檔 WINNER，peak 起算） |
|---|---|---|
| AT_RISK+ persistence >=3 | 9.1% | **8.7%**（幾乎相同） |
| FAILED persistence >=3 | 0.0% | **0.0%**（維持零誤觸） |

**結論：沒有 selection bias。** 即使只看「真正發生過明顯回檔」的贏家子集，
persistence>=3 的低誤觸率幾乎沒有變化——上一輪的乾淨結果不是因為多數 Winner
根本沒回檔造成的假象。

## 三、核心 Comparison Table（§13，post-peak，累計整個觀察窗）

| metric | ROUND_TRIP_FAILURE (n=18) | NORMAL_PULLBACK_WINNER matched (n=18) |
|---|---|---|
| median peak_return | 6.10% | 25.21% |
| median P1 drawdown | -6.75% | -12.21% |
| ever AT_RISK after peak | 100.0% | 83.3% |
| **AT_RISK persistence >=2** | **77.8%** | **38.9%** |
| **AT_RISK persistence >=3** | **55.6%** | **11.1%** |
| ever FAILED after peak | 44.4% | 5.6% |
| FAILED persistence >=2 | 5.6% | 0.0% |
| FAILED persistence >=3 | 0.0% | 0.0% |
| median worst drawdown after peak | -23.48% | -12.98% |
| not recovered by last obs | 83.3% | 55.6% |
| re-break previous peak | 0.0% | 5.6% |
| ever REACCELERATING | 0.0% | 0.0% |

**repair_label 分布**（純 descriptive，依各股自己 post-peak 軌跡判定，非依組別預設）：
ROUND_TRIP_FAILURE：FAILED_REPAIR 17/18（94.4%）、HEALTHY_REPAIR 1/18；
NORMAL_PULLBACK_WINNER：FAILED_REPAIR 12/18（66.7%）、HEALTHY_REPAIR 6/18。
**即使是最終獲利出場的 NPW，也有 2/3 在過程中呈現「未真正健康修復」的軌跡
特徵**——這再次印證「中途震盪／技術上未完全修復」不等於最終失敗，repair_label
本身不是可靠的終局判斷依據，只能當描述性參考。

## 四、Magnitude-aligned 比較（§9，兩組都回檔到 <=-5% 時，n=18 vs 18）

這是本輪最關鍵、最公平的比較（不看「第幾天」，看「回檔到同樣幅度時」）：

| 指標 | ROUND_TRIP_FAILURE | NORMAL_PULLBACK_WINNER |
|---|---|---|
| current_return | -3.21% | +14.63% |
| drawdown_from_max | -9.45% | **-12.53%**（NPW 原始回檔反而更大） |
| **profit_retention_ratio** | **-0.79**（已淨虧損） | **+0.63**（仍保留 63% 浮盈） |
| excess_return_vs_market_3d | -6.2% | -6.0%（**幾乎無差異**） |
| continuation_quality_state 分布 | AT_RISK 66.7%／CAUTION 22.2%／FAILED 11.1% | AT_RISK 72.2%／CAUTION 16.7%／FAILED 5.6%／HEALTHY 5.6% |
| AT_RISK+ 比例（此刻） | 77.8% | **77.8%（完全相同）** |

**誠實的意外發現**：在「回檔幅度相近」的這個當下橫切面，`continuation_
quality_state`（CONTINUATION_STATE family）跟 `excess_return_vs_market_3d`
（RELATIVE_PERFORMANCE family）**幾乎沒有區分力**（77.8% vs 77.8%、-6.2% vs
-6.0%）。唯一顯示清楚差異的是 **`profit_retention_ratio`**（PROFIT_PATH
family 用 peak_return 正規化後的浮盈保留率）——這是因為 ROUND_TRIP 的
peak 本來就小（中位數 6.1%），同樣回檔到 -9.45pp 就已經吃光獲利轉虧；NPW
的 peak 大（中位數 25.21%），即使回檔 -12.53pp（絕對百分點還更大）仍保留
六成多浮盈。**這代表：用「絕對回檔百分點」或「當下 continuation_quality_
state」在這個時間點無法區分兩組，但用「相對自己高點的保留比例」可以。**

## 五、Earliest Reliable Divergence Point（§15）——與 Phase 2.7 PART B 的樂觀
結論不同，本輪必須修正

用 offset-aligned running AT_RISK+ streak（從 peak 累計到當前 offset，皆為
兩組都有資料的位置）：

| rel_offset | n (RT / NPW) | streak>=2 (RT / NPW) | gap |
|---|---|---|---|
| P0 | 18 / 18 | 0.0% / 0.0% | 0 |
| P1 | 18 / 18 | 5.6% / 5.6% | **0（完全相同）** |
| P2 | 15 / 11 | 53.3% / 45.5% | 7.8pp（樣本已開始縮水） |
| P3 | 14 / 5 | 71.4% / 60.0% | 11.4pp（NPW n=5，不可靠） |
| P4 | 11 / 1 | — | NPW 樣本剩 1 檔，不可用 |

**這與 Phase 2.7 PART B 的結論不同，必須誠實修正**：PART B 當時把
ROUND_TRIP_FAILURE 拿去跟「大多還在創高、幾乎沒有 post-peak 資料」的舊
matched winner（n=4 於 PEAK+1）比較，得到「PEAK+1 就已經 66.7pp 落差」的
結論。**本輪換成真正發生過回檔的 NORMAL_PULLBACK_WINNER（n=18 於 P1）後，
P1 的落差完全消失（5.6% vs 5.6%）**，P2 起才出現个位數到十位數的落差，且
P2 起樣本數已經開始縮水（NPW 只剩 11、5、1 檔）。**换句话说，這個「早期分
岔」訊號有很大一部分來自於「跟錯的比較對象」（沒回檔的贏家），不是真正的
早期分岔能力。**

依 §15 要求「至少兩個 Evidence Family 同方向」檢驗磁 magnitude-aligned 這個
最早、樣本最完整（18v18）的檢查點：**只有 PROFIT_PATH（profit_retention_
ratio）顯示差異，CONTINUATION_STATE 與 RELATIVE_PERFORMANCE 在此刻幾乎沒有
差異**——未達到「至少兩個 family 同方向」的門檻。**CONTINUATION_STATE 的
persistence 優勢要等到用「整個觀察窗（P0~最後可得 offset）累計」才會顯現
（第三節的 77.8%/38.9%、55.6%/11.1%），這已經不是「早期」的概念，而是「整
個 ~2 週窗口內最終累積」的概念。**

## 六、False Exit Risk Table（§16）

| condition | RoundTrip Capture | NPW(matched) False-Exit | NPW(all, n=23) False-Exit |
|---|---|---|---|
| ever AT_RISK | 100.0% | 83.3% | 87.0% |
| **AT_RISK persistence >=2** | **77.8%** | **38.9%** | 34.8% |
| **AT_RISK persistence >=3** | **55.6%** | **11.1%** | 8.7% |
| ever FAILED | 44.4% | 5.6% | 4.3% |
| FAILED persistence >=2 | 5.6% | 0.0% | 0.0% |
| FAILED persistence >=3 | 0.0% | 0.0% | 0.0% |

（以上都是 post-peak 累計整個觀察窗，不是特定 offset）

## 七、訊號形成時的殘餘獲利與時機（AT_RISK persistence>=2 首次觸發）

18 檔 ROUND_TRIP_FAILURE 中，**15 檔**曾觸發 AT_RISK persistence>=2（3 檔
6830/6451/4566 從未觸發）。觸發當下的報酬：
- **8 檔（53%）觸發時報酬仍在 -10% 以上**（例：1617 -0.20%、2409 -4.55%、
  1717 -6.00%、6133 -6.10%、2369 -6.50%、6805 -7.25%、1447 -7.81%、
  7721 -7.99%）——這些案例確實提供了有意義的提前預警空間
- **5 檔（33%）觸發時已經跌破 -10%**（3021 -10.99%、6285 -14.72%、
  3717 -11.48%、2331 -11.89%、9910 -15.85%）——這些案例訊號來得太晚，
  已無「提前處理」的實際價值
- 2 檔（6770、3533）觸發於 day10（觀察窗最後一天），已經來不及

## 回答 23 個問題中的 7 個核心問題

**1. 在真正經歷明顯 pullback 的 Winner 中，AT_RISK persistence >=3 是否仍然
少見？** **是**——23 檔全體真正回檔的 Winner 中僅 8.7%（2 檔）觸發，與 Phase
2.7 全體 66 檔 WINNER 的 9.1% 幾乎相同，**證實不存在 selection bias**。

**2. FAILED persistence >=3 是否仍保持接近零誤觸？** **是**——matched 18 檔
與全體 23 檔候選都是 **0.0%**，維持零誤觸。

**3. ROUND_TRIP_FAILURE 與 NORMAL_PULLBACK_WINNER 在相似回撤幅度下，最早在
哪個 observation 開始可靠分岔？** **誠實結論：沒有找到滿足「>=2 個 Evidence
Family 同方向」的早期可靠分岔點**。Magnitude-aligned 檢查點（兩組都回檔約
-9~-13pp 時，18v18 樣本）只有 PROFIT_PATH 一個 family 顯示差異；
CONTINUATION_STATE 與 RELATIVE_PERFORMANCE 在此刻幾乎無差異。Offset-aligned
P1（18v18）也完全無差異（5.6% vs 5.6%）。CONTINUATION_STATE persistence 的
優勢要到「整個觀察窗累計」才顯現，此時已不算「早期單點」。

**4. 最有區分力的是哪一類 Evidence？** **PROFIT_PATH（profit_retention_
ratio，用 peak_return 正規化的浮盈保留率）**，在樣本最完整（18v18）、時間
最早（magnitude-aligned 首次回檔到 -5% 附近）的檢查點就顯示清楚差異
（-0.79 vs +0.63）；CONTINUATION_STATE persistence 是第二有力但**較慢**顯現
的家族（需要累計整個觀察窗）；RELATIVE_PERFORMANCE 在本輪兩個比較點皆無
區分力；MOMENTUM_STATE 本報告未單獨列出但先前 Phase 2.7 PART B 已顯示其
雜訊最大。

**5. 哪個 candidate condition 的 Capture vs False-Exit trade-off 最合理？**
**AT_RISK persistence >=3**：Capture 55.6% vs False-Exit 11.1%（matched）/
8.7%（全體）——capture 是 false-exit 的 5~6.4 倍，是本輪最平衡的選擇。
`AT_RISK persistence >=2` capture 更高（77.8%）但 false-exit 也升到
34.8~38.9%（capture/false-exit 比僅 2.0~2.2 倍），trade-off 較差。

**6. 訊號形成時 median current_return / drawdown_from_peak / residual
profit 是多少？是否還有提前處理空間？** 以 AT_RISK persistence>=2 首次觸發
為準（15 檔有觸發）：殘餘報酬從 +（微幅正）到 -16% 都有，**53% 在跌破 -10%
之前觸發（仍有提前處理空間），33% 在跌破 -10% 之後才觸發（已錯過提前處理
時機）**——這是一個「一半有用、一半太晚」的混合結果，不是全面提前預警。

**7. 是否已經有足夠證據，值得進下一階段設計 research-only Exit Candidate
State？** **PARTIAL（部分足夠，不建議現在直接進三層 State 設計）**。理由：
- Persistence-based 訊號（AT_RISK/FAILED persistence>=2/3）本身穩健、
  robustness test 通過、capture/false-exit trade-off 合理——**這部分足夠**
- 但 §15 要求的「至少 2 個 Evidence Family 在早期同步確認」**沒有達成**：
  只有 PROFIT_PATH 一個 family 在早期（magnitude-aligned、18v18 樣本）顯示
  差異，CONTINUATION_STATE 的優勢要等到整個觀察窗累計才出現（不算早期），
  這代表若現在把「早期分岔」當作三層 State 設計的核心賣點，**證據並不足夠**
- 建議下一步（若要繼續）：先單獨、更嚴謹地驗證 `profit_retention_ratio`
  本身的獨立區分力與時機（它是本輪唯一真正「早」且「樣本完整」的訊號），
  而不是急著把「早期分岔」跟「persistence 累計優勢」混在一起做成一個
  三層 Exit Candidate State

## Stop / Success Criteria 檢核（§19/§20）

**Success 部分符合**：#1（NPW FAILED persistence>=3 仍低，✓）、#5（找到
AT_RISK persistence>=3 這個合理 trade-off 的 condition，✓）。
**Success 部分未完全符合**：#3（相似 drawdown 下，只有 1 個 family 顯示
持續惡化，不是「多個 family」）、#4（33% 案例的分岔點已晚於 -10%）。

**Stop 條件均未觸發**（NPW persistence>=3 仍低、非全部 4 個 family 都無差異
——PROFIT_PATH 仍有差異、沒有為了製造分岔而新增 threshold），**因此不需要
完全停止這條研究線**，但基於上述 Success 條件只部分符合，**不建議現在就
提出三層 Exit Candidate State 的 conceptual design**——本報告依 §18 指示，
在證據不完整的情況下不提出該設計。

## 本輪禁止事項確認

未修改任何 production 程式碼、`continuation_quality_state`/`tracking_state`/
`momentum_freshness` 規則與門檻（含 `drawdown_from_max<=-8%` 原樣沿用）、
Hard Exclusion、LLM、Candidate Selection；未新增第 5 個 Evidence Family；
未重跑 617 檔 full pipeline；未為湊 18 檔放寬 NORMAL_PULLBACK_WINNER 到沒有
真正回檔的 Winner（23 檔全部通過 -5% 回檔篩選）；未 hardcode stock_id 邏輯
（僅用於 CSV 輸出識別）；未做正式 backtest 或 SELL rule；未提出三層 Exit
Candidate State 設計（因證據不足，依 §18 指示保留）。
