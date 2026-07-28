# Phase 2.7 PART B：ROUND_TRIP_FAILURE Research 報告（2026-07-24）

> 純研究，不修改 production 程式碼、不修改既有規則或門檻。純離線分析既有
> 132-stock continuation quality 資料，未重新查資料庫。
>
> 腳本：`backend/analyze_phase27_roundtrip.py`
> Matched sampling：`/tmp/phase27_roundtrip_matched.json`（18 檔
> ROUND_TRIP_FAILURE，各自依 MFE 相近原則配對 1 檔 WINNER；次要條件 Day0
> momentum_score/rs_market_percentile_20d/EXTENDED_3D/regime）
> 原始資料：`/tmp/phase27_roundtrip_matched36.csv`

## 核心問題

> 對曾經賺錢的股票，能不能在還沒從高點回落變成 -10% 以前，辨認它已經從
> 「正常回檔」轉變成「真正的 ROUND_TRIP_FAILURE」？

## 誠實揭露：Matched sampling 品質限制

66 檔 BIG_LOSER 中只有 18 檔屬於 ROUND_TRIP_FAILURE（MFE>=3%），其 MFE 範圍是
3.19%~18.58%；但 66 檔 WINNER 的 MFE 中位數高達 26%，**多數 WINNER 的 MFE 遠
高於這 18 檔 ROUND_TRIP_FAILURE**，導致低 MFE 的 round-trip 案例（4566、2409、
1717、7721、6133、2331、9910，MFE 僅 3~5%）配對距離偏大（distance 20~37，遠
高於其他配對的 0.6~14）——這代表**這幾檔的比較基準不夠理想**，解讀時需要打
折扣，其餘 11 檔（distance<15）配對品質可信。

## 誠實揭露：MATCHED_WINNER 在 PEAK+2/+3 幾乎沒有資料

18 檔 matched WINNER 中，只有 **4 檔**在 PEAK+1 還有觀測值，**PEAK+2/+3 完全
沒有資料**。原因：多數 WINNER 的 7-observation 序列中，**報酬最高點（PEAK）
就出現在最後一個 offset（Day10）**——也就是說，典型 WINNER 到 Day10 為止都還
在創新高、根本沒有進入「回檔期」，自然沒有「peak 之後」的資料可比較。**這個
現象本身就是本研究最重要的發現之一**：真正的贏家往往「一路碰到 Day10 都還沒
真正回頭」，而 ROUND_TRIP_FAILURE 的特徵正是「中途就見頂，之後開始回落」。

## 1. ROUND_TRIP_FAILURE vs MATCHED_WINNER：Peak 對齊比較

| role | rel_offset | n | median 報酬 | median drawdown_from_max | median excess_3d | DETERIORATING% | AT_RISK+% | FAILED% |
|---|---|---|---|---|---|---|---|---|
| ROUND_TRIP_FAILURE | PEAK | 18 | +6.10% | 0.00% | +13.29% | 5.6% | 5.6% | 5.6% |
| **ROUND_TRIP_FAILURE** | **PEAK+1** | 18 | **-0.55%** | **-6.75%** | +4.50% | 22.2% | **66.7%** | 16.7% |
| ROUND_TRIP_FAILURE | PEAK+2 | 15 | -4.55% | -10.66% | -8.48% | 0.0% | 80.0% | 13.3% |
| ROUND_TRIP_FAILURE | PEAK+3 | 14 | -11.32% | -17.26% | -8.32% | 21.4% | 85.7% | 21.4% |
| MATCHED_WINNER | PEAK | 18 | +14.62% | 0.00% | +4.10% | 11.1% | 0.0% | 0.0% |
| MATCHED_WINNER | PEAK+1 | 4（僅剩少數有後續觀測） | +11.21% | -1.92% | -5.21% | 0.0% | 0.0% | 0.0% |

## 2. Earliest Divergence Point：**PEAK+1**（見頂後的下一個 observation）

**PEAK 當下兩組幾乎無法區分**（ROUND_TRIP AT_RISK+ 率 5.6% vs WINNER 0%，差距
僅 5.6pp）。**但只要多一個 observation（PEAK+1），差距立刻暴衝到 66.7pp**
（ROUND_TRIP 66.7% vs WINNER 0%）——這是本研究找到最清楚的「分岔點」：
**「見頂當下」看不出來，但「見頂後的下一次觀測」就已經非常清楚。**

## 3. PROFIT_PATH deterioration（drawdown_from_max<=-8%，沿用既有門檻未修改）

| role | rel_offset | 命中率 |
|---|---|---|
| ROUND_TRIP_FAILURE | PEAK | 0.0%（0/18） |
| **ROUND_TRIP_FAILURE** | **PEAK+1** | **44.4%（8/18）** |
| ROUND_TRIP_FAILURE | PEAK+2 | 80.0%（12/15） |
| ROUND_TRIP_FAILURE | PEAK+3 | 92.9%（13/14） |
| MATCHED_WINNER | PEAK / PEAK+1 | 0.0% |

**PEAK 當天 0% 命中（定義上必然如此，因為 drawdown 從自己的高點算，高點當天
drawdown=0）；PEAK+1 就已經有 44.4% 的 ROUND_TRIP_FAILURE 觸發 PROFIT_PATH
惡化訊號，PEAK+2 上升到 80%。MATCHED_WINNER 在僅有的觀測範圍內完全沒有觸發。**

## 4 個逐檔明細（first drawdown<=-8% 觸發時的殘餘報酬，見 CSV 完整版）

多數案例在 drawdown 觸及 -8% 門檻時，報酬已經轉負或接近轉負（例如 3021：peak
9.89%→day2 觸發，當時報酬 -1.10%；2409：peak 4.55%→day5 觸發，當時報酬
-4.55%）。只有 MFE 較高的 6770（peak 18.58%）在觸發時報酬仍接近 0%（-0.27%，
非明顯正報酬）——**誠實說明**：因為只用 7 個離散 observation（非逐日），實際
最早跨過 -8% drawdown 的確切時間點可能落在未觀測到的日期之間（例如 day5~day7
之間），本表的「觸發日」只是「觀測到的第一個超標日」，精度受限於取樣間隔。

## 回答 Round Trip Report 的 4 個問題

**1. ROUND_TRIP_FAILURE 最常在 Peak 後第幾天開始出現明顯 deterioration？**
**PEAK+1**（見頂後的下一次觀測）——AT_RISK+ 率從 PEAK 當下的 5.6% 跳升到
66.7%，PROFIT_PATH 惡化命中率從 0% 跳到 44.4%，是最清楚的轉折點。

**2. 當時的 median drawdown_from_max / median current_return 是多少？**
PEAK+1 時：**median drawdown_from_max = -6.75%**、**median current_return =
-0.55%**——換句話說，典型的 ROUND_TRIP_FAILURE 案例，在見頂後的下一次觀測，
報酬已經幾乎回到打平（接近 0%），但**還沒有跌到 -10%**，仍有提早辨識的空間。

**3. matched Winner 在相同階段是否通常能停止 drawdown 擴大 / 恢復相對強度 /
回到 HEALTHY？** 由於樣本限制（多數 WINNER 在整個觀測窗內都還在創高，沒有
「見頂後」的資料），**無法完整回答這個問題**——但從僅有的 4 檔 WINNER
PEAK+1 資料看，drawdown 只有 -1.92%（遠小於 ROUND_TRIP 的 -6.75%），
AT_RISK+ 率仍是 0%，方向支持「WINNER 即使短暫拉回，幅度也遠比 ROUND_TRIP_
FAILURE 溫和」的假設，但樣本太小（n=4）不能視為確定結論。

**4. 哪個 evidence 最能區分 ROUND_TRIP_FAILURE vs 正常 Winner Pullback？**
在本次 4 個 family 中，**CONTINUATION_STATE（AT_RISK+ 率）與 PROFIT_PATH
（drawdown<=-8% 命中率）在 PEAK+1 這個時間點的區分力最清楚**（66.7pp 與
44.4pp 的組間落差）；`excess_return_vs_market_3d`（RELATIVE_PERFORMANCE）
在 PEAK+1 也顯示 ROUND_TRIP 中位數 +4.50% vs WINNER -5.21%——方向不如預期
（ROUND_TRIP 這時相對大盤還沒轉弱），但 PEAK+2 起轉為 -8.48%，顯示
RELATIVE_PERFORMANCE 的訊號比 PROFIT_PATH/CONTINUATION_STATE 晚一拍才浮現；
`momentum_freshness`（MOMENTUM_STATE，用 DETERIORATING% 代表）本輪呈現不穩定
（PEAK+1 22.2%、PEAK+2 掉回 0%、PEAK+3 又回 21.4%），**區分力最弱、雜訊最大**。

**5. 是否值得下一步設計 research-only Exit Candidate State？**
**值得**——PEAK+1 這個時間點呈現高度一致、跨多個 family 同步出現的訊號
（AT_RISK+ 率暴衝、PROFIT_PATH 命中率暴衝），且觸發時報酬仍接近打平
（-0.55%）、遠早於最終 -10%+ 的損失。但受限於本輪 matched winner 樣本在
peak 之後幾乎沒有資料，「這訊號會不會也大量誤傷 WINNER 的正常回檔」這件事
**還沒有被充分驗證**——下一輪若要繼續，應該優先解決「找到真正經歷過 peak-
then-pullback 的 WINNER 案例」這個取樣問題（本輪 WINNER 池普遍還在創高，
天生就沒有足夠的『回檔後』案例可比較），而不是急著把 PEAK+1 訊號當成
Exit Candidate 定案。

## 本輪禁止事項確認

未修改任何 production 程式碼、`continuation_quality_state` 規則、既有門檻
（`drawdown_from_max<=-8%` 原樣沿用）、Hard Exclusion、LLM、candidate
selection；未新增第 5、6 個 evidence family；未產生任何 sell rule 或 exit
rule，僅提出上述觀察作為「Possible Exit Candidate Signal」的研究方向記錄。
