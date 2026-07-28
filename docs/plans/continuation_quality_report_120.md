# Phase 2.6 Continuation / Hold Quality Research — 132-stock 擴大確認報告（2026-07-24）

> 純研究，不修改 production 程式碼、不重跑 617 檔 full pipeline、不修改既有
> `tracking_state`。延續 [continuation_quality_report.md](continuation_quality_report.md)
> （40 檔：20 WINNER + 20 BIG_LOSER）的方法論，擴大到**全部可用的 66 檔
> BIG_LOSER**（matched 對應 66 檔獨立 WINNER，共 132 檔——魚尾 60 天 replay 視窗
> 內 `future_return_10d<=-10%` 的候選總共只有 66 檔，已是全部母體，非任意子集）。
>
> 腳本：`backend/analyze_phase26_continuation_quality.py --matched
> /tmp/phase26_matched_120.json`
> 原始資料：`/tmp/continuation_quality_matched132.csv`（924 列 = 132 檔 × 7 個
> offset）/ `/tmp/continuation_quality_132_raw.json`
> Matched sampling：`/tmp/phase26_matched_120.json`（66 配對，distance median
> 1.57，56/66 配對 distance<5.0；因為使用了全部 66 檔母體而非任選 60 檔，配對
> 品質比 40 檔版本略寬，見下方誠實揭露）

沿用完全相同的 `continuation_quality_state`（HEALTHY/CAUTION/AT_RISK/FAILED）
規則與門檻（一個字都沒改），只是把樣本從 40 檔擴大到 132 檔。

## 40-stock → 132-stock 對照表

| 指標 | 40-stock (20+20) | **132-stock (66+66)** | 變化 |
|---|---|---|---|
| median 首次 AT_RISK+ 出現日 | Day1 | **Day1** | 不變 |
| median 首次 FAILED 出現日 | Day2.5 | **Day3** | 略晚一點點 |
| 首次 AT_RISK 時平均報酬 | -3.57% | **-5.02%** | 略惡化但仍遠早於 -10% |
| 首次 FAILED 時平均報酬 | -5.46% | **-7.02%** | 同上 |
| **BIG_LOSER Early Detection Rate** | 100%（20/20） | **92.4%（61/66）** | 下降但仍非常高 |
| 從未在 Day10 內偵測到 AT_RISK+ 的大虧股 | 0/20 | **0/66** | **保持 0**——沒有一檔完全漏網 |
| WINNER 曾觸及 AT_RISK+ | 55%（11/20） | **62.1%（41/66）** | 略上升 |
| WINNER 曾觸及 FAILED | 20%（4/20） | **24.2%（16/66）** | 略上升 |
| FAILED 後最終恢復（佔曾 FAILED 的比例） | 100%（4/4） | **81.2%（13/16）** | 下降，出現真正沒恢復的案例 |
| NEVER_WORKED（大虧股從未真正賺過） | 85%（17/20） | **72.7%（48/66）** | 下降但仍是多數 |
| ROUND_TRIP_FAILURE（曾賺後吐回轉負） | 15%（3/20） | **27.3%（18/66）** | 上升 |
| CLEAN_TREND_WINNER（全程未觸及 AT_RISK/FAILED） | 45%（9/20） | **37.9%（25/66）** | 下降 |
| VOLATILE_WINNER | 55%（11/20） | **62.1%（41/66）** | 上升 |
| LOSER median MFE / MAE | 0% / -13.93% | **0% / -15.69%** | 相近 |
| WINNER median MFE / MAE | 24.60% / -0.14% | **26.04% / 0.00%** | 相近 |

## 誠實解讀：這次跟 Relative Leadership 的「訊號消失」完全不同

前一輪 Relative Leadership 從 20 檔擴到 40 檔時，`peer_rank_percentile_day0` 的
組間差異幾乎完全消失（0.159 vs 0.048 → 0.171 vs 0.167），是典型的「小樣本巧合」。
**這次 40→132 檔（3.3 倍樣本數）的結果方向完全沒有反轉，多數指標只是從
「近乎完美」小幅收斂到「非常好但有雜訊」**：

- **Early Detection Rate 從 100% 降到 92.4%，但沒有崩潰**——66 檔大虧股裡，
  **沒有一檔完全沒被系統偵測到**（0 檔漏網），只是有 5 檔的「第一次 AT_RISK」
  剛好發生在報酬已經跌破 -10% 之後（也就是偵測「稍微慢了」，不是「完全沒抓到」）
- **WINNER 誤判率小幅上升**（AT_RISK 55%→62%、FAILED 20%→24%），仍在合理範圍，
  沒有出現「誤判率暴增到跟命中率打平」的情況（這才是真正該停止的訊號，前一輪
  PRICE_FLOW_DIVERGENCE/EXTREME_RUN_EXHAUSTION 就是這種情況）
- **需要誠實揭露的新發現**：「FAILED 後最終恢復」比例從 100%（4/4）降到 81.2%
  （13/16）——代表**放大樣本後，出現了 3 檔「曾經 FAILED 且到 Day10 都沒有恢復」
  但最終 10 日報酬仍 `>=+10%` 的案例**。這代表 `continuation_quality_state`
  在 Day10 當下的判讀，跟最終 10 日報酬不是 100% 對齊——這是合理的，因為
  `continuation_quality_state` 回答的是「到目前為止這個 thesis 還成立嗎」，
  不是「未來會不會翻正」；一檔股票可以在 Day10 當下確實顯示轉弱訊號，但因為
  價格本身噪音或還沒來得及在 Day10 之後真正反彈，仍算在「10 日報酬 >=10%」
  這個嚴格定義裡（例如 Day8~10 才開始急拉，Day10 當天的滾動評估還沒跟上）

## ROUND_TRIP_FAILURE 比例上升的意義

40 檔時 ROUND_TRIP_FAILURE 只佔 15%，132 檔上升到 27.3%——這代表**用全部母體
驗證後，「Day0 selection 本來就抓對方向、但後續 Hold/Exit management 沒接住
獲利」的失敗型態，比小樣本顯示的更常見（略高於四分之一）**。這對下一階段若真
的要做任何 shadow production 設計，是一個值得記住的重要修正：不能只設計
「盡早剔除從未賺錢的假訊號」，也要考慮「已經賺錢但正在吐回」這個獨立子問題
（本輪 `PROFIT_PATH` family 的 `drawdown_from_max<=-8%` 條件已經涵蓋這個情境，
從結果看運作正常）。

## VOLATILE_WINNER 比例上升的意義

62.1% 的贏家（相對 40 檔時的 55%）在過程中會出現 AT_RISK/FAILED——**這進一步
強化了「贏家中途震盪是常態，不是例外」的結論**，讓「不要因為短期 AT_RISK/FAILED
就判死」這件事變得更加重要而非更不重要。

## 結論

**132 檔（全母體）確認結果穩健，方向與 40 檔一致，沒有出現訊號崩潰**。與
Relative Leadership 那種「差異明顯縮小、應該停止」的情況不同，這次是「差異
小幅收斂但核心結論（Early detection 早、FAILED 誤判可控、多數 FAILED 會恢復）
依然成立」的情況，符合 §17 的「繼續投入」判準。下一階段若要往 production
shadow field 前進，應該優先處理：
1. `FAILED-but-recovered` 從 100%→81.2%，代表 FAILED 判斷需要更細緻的「持續
   天數」概念（本輪報告已提出這個方向），而非單看某一天的瞬時狀態
2. ROUND_TRIP_FAILURE 佔比上升到 27.3%，代表 `PROFIT_PATH` family 的吐回偵測
   邏輯值得單獨拉出來驗證其獨立區分力
3. 這次已經是全部 66 檔大虧股母體，若要再擴大樣本，只能靠放寬 `future_return_
   10d<=-10%` 門檻本身（例如改成 <=-8%）來取得更多樣本，但那已經改變了
   「大虧股」的定義，需要另外決定是否合理

全程沒有修改任何 production 程式碼或門檻，`continuation_quality_state` 純粹
是 shadow research 欄位。
