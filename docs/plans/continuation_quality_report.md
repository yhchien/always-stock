# Phase 2.6 Continuation / Hold Quality Research 報告（2026-07-24）

> 純研究，不修改 production 程式碼、不重跑 617 檔 full pipeline、不修改既有
> `tracking_state`。全程使用單股直接查詢（不重建全市場 momentum frame），
> 已知簡化見腳本 docstring：`momentum_freshness`/`entry_state`/`tracking_state`
> 缺少橫斷面排名（`rs_market_percentile_20d`/`rs_rank_improvement_5d`/
> `peer_rs_percentile_20d`），STRUCTURE_DAMAGED/REACCELERATING 兩態理論上不會
> 觸發。
>
> 腳本：`backend/analyze_phase26_continuation_quality.py`
> 原始資料：`/tmp/continuation_quality_matched40.csv`（280 列 = 40 檔 × 7 個
> offset）/ `/tmp/continuation_quality_raw.json`

## 核心問題

> 股票第一次被魚尾抓到之後，如何判斷它是正常續強、健康整理、開始惡化，
> 還是真正失敗？

沿用既有 40 檔 matched sample（20 WINNER `future_return_10d>=+10%` + 20
BIG_LOSER `future_return_10d<=-10%`）。新增 shadow-only 欄位
`continuation_quality_state`（HEALTHY/CAUTION/AT_RISK/FAILED），規則：

- **HEALTHY**：三個 deterioration family 都沒觸發
- **CAUTION**：剛好 1 個 family 觸發
- **AT_RISK**：至少 2 個 family 同時觸發
- **FAILED**：`hard_failure` 觸發（`tracking_state=INVALIDATED` / `entry_state=
  STRUCTURE_DAMAGED` / REVERSAL_FAILURE-like：法人反轉 + 相對大盤轉弱同時成立）

3 個 deterioration family（工程起始值，未為結果反覆調整）：
1. **PROFIT_PATH**：`max_return_so_far>=3% 且 drawdown_from_max<=-8%`（曾經賺錢
   後大幅吐回）或 `current_return<=-5% 且 max_return_so_far<=3%`（從未真正賺錢
   且已顯著虧損）
2. **RELATIVE_PERFORMANCE**：`excess_return_vs_market_1d<=-1.0%` 或
   `excess_return_vs_market_3d<=-2.0%`
3. **MOMENTUM_STATE**：`momentum_freshness∈{STALE,DETERIORATING}` 或
   `tracking_state∈{DETERIORATING,INVALIDATED}`

## 1. WINNER vs BIG_LOSER trajectory 對照（摘要，完整 280 列見 CSV）

BIG_LOSER 20 檔幾乎全數在 Day1~Day3 就進入 AT_RISK/FAILED，且**持續停留**在
FAILED/AT_RISK 直到 Day10（僅 2409、6770 有短暫回到 AT_RISK 但從未回到
HEALTHY）。WINNER 20 檔多數維持 HEALTHY，少數出現 CAUTION/AT_RISK/FAILED 波動
但**最終都恢復**。

## 2. BIG_LOSER：多早可以辨認？

| 指標 | 數值 |
|---|---|
| median 第一次 CAUTION+ 出現日 | **Day1** |
| median 第一次 AT_RISK+ 出現日 | **Day1** |
| median 第一次 FAILED 出現日 | **Day2.5** |
| 第一次 AT_RISK+ 時平均報酬 | **-3.57%** |
| 第一次 FAILED 時平均報酬 | **-5.46%** |
| **Early Detection Rate**（AT_RISK+ 出現時報酬仍 > -10%） | **20/20 = 100%** |

**全部 20 檔大虧股，都在最終 -10%~-19% 損失實現「之前」就已經進入 AT_RISK 或
FAILED，平均提前偵測時的報酬只有 -3.57%（AT_RISK）到 -5.46%（FAILED）**，遠早於
最終確認的 -10%+ 損失。

## 3. WINNER：會不會被太早嚇出去？

| 指標 | 數值 |
|---|---|
| 20 檔中曾經 AT_RISK 或更差 | **11/20 = 55%** |
| 20 檔中曾經 FAILED | **4/20 = 20%**（2486、6719、3028、4927） |
| 曾 FAILED 但最終恢復（未停留 FAILED） | **4/4 = 100%** |

**誠實解讀**：如果把「曾經觸及 AT_RISK」當成觸發訊號，誤觸率偏高（55%）——這代表
`AT_RISK` 更適合當「提高關注」的訊號，不適合直接當退出觸發。但如果用更嚴格的
`FAILED`（本輪定義刻意與既有 hard-failure 概念一致），誤觸率降到 20%，且**這 4
檔全部後來都恢復**（沒有一檔真正卡在 FAILED 直到 Day10），符合 §16 的
「8039 類不能因短期震盪直接判死」的要求。

## 4. MFE / MAE 對照

| | BIG_LOSER (n=20) | WINNER (n=20) |
|---|---|---|
| median MFE（曾經最高賺多少） | **0.00%** | **24.60%** |
| median MAE（曾經最深虧多少） | **-13.93%** | **-0.14%** |

大虧股絕大多數**從未真正賺過錢**（median MFE=0%），贏家則普遍有可觀的正向
excursion（median MFE近25%）且中位數幾乎沒有真正意義上的虧損（median MAE 接近
0%，代表贏家組的「中位數」股票走勢相對順暢）。

## 5. NEVER_WORKED vs ROUND_TRIP_FAILURE

| 分類 | 數量 | 說明 |
|---|---|---|
| **NEVER_WORKED**（MFE<3%，從未真正成功） | **17/20 = 85%** | 8104/4755/8249/2493/1536/3051/1305/2481/2489/1735/5234/9921/1514/3013/1608/3257/6120 |
| **ROUND_TRIP_FAILURE**（曾經 MFE>=3%，後來吐回轉負） | **3/20 = 15%** | 2409（MFE 4.55%）、6805（MFE 7.0%）、6770（MFE 18.58%，最戲劇性——曾大漲後全數吐回） |

**大虧股絕大多數是「Day0 selection 本身就沒抓對方向」**，只有少數（15%）是
「一開始真的對，後來 Hold/Exit management 沒抓住獲利」的型態——這代表本輪要優化
的重點更偏向「盡早辨認 NEVER_WORKED」而非「管理已經賺錢部位的吐回」。

## 6. CLEAN_TREND_WINNER vs VOLATILE_WINNER

| 分類 | 數量 |
|---|---|
| **CLEAN_TREND_WINNER**（全程未觸及 AT_RISK/FAILED） | **9/20 = 45%**（006208/00991A/7750/3661/6415/00988A/6789/3209/2441） |
| **VOLATILE_WINNER**（曾觸及 AT_RISK 或 FAILED，最終仍是贏家） | **11/20 = 55%**（1727/2486/6719/2308/4720/3028/3518/1711/4722/3016/4927） |

**超過一半的贏家過程並不平順**——這正是 §12 提醒「避免把 VOLATILE_WINNER 錯當
failure」的核心關切，本輪的 4-family 規則設計（AT_RISK 需要至少 2 個獨立證據，
FAILED 需要真正 hard-failure 條件）某種程度上達成了這個目的（4 檔曾 FAILED 的
贏家全數恢復），但 55% 的 AT_RISK 觸及率仍偏高，若真要接 production 需要更嚴謹
校準。

## 7. 6505 / 8039 / 6414 / 1810 個案確認

**重要澄清**：這 4 檔是先前 LLM v6 真實 3 天驗證中出現在正式 WATCH 名單的股票
（用來確保 regression 不遺漏），**但用 10 日遠期報酬嚴格定義（>=+10% 才算
WINNER）重新檢查後，實際數值是**：

| stock | first_seen | future_return_10d | 是否符合 `>=+10%` WINNER 定義 |
|---|---|---|---|
| 6505 | 2026-06-24 | **+7.61%** | 否（接近但未達門檻） |
| 8039 | 2026-06-17 | **+1.37%** | 否（表現平庸） |
| 6414 | 2026-04-14 | **+9.35%** | 否（非常接近門檻） |
| 1810 | 2026-06-05 | **-5.53%** | **否，其實是輕微虧損** |

這 4 檔嚴格來說**都不是本輪 matched sample 定義下的「大贏家」**，只有 6414/6505
算是「還不錯但沒到 +10%」，1810 其實走勢偏弱。誠實揭露這點後，再看
continuation_quality 軌跡，反而更有意義——它與這 4 檔的實際相對表現排序一致：

- **6414**（+9.35%，本次 4 檔中表現最好）：`HEALTHY→CAUTION→HEALTHY→HEALTHY→
  HEALTHY→HEALTHY→CAUTION`，**全程無 AT_RISK/FAILED**，是最乾淨的軌跡
- **6505**（+7.61%）：`HEALTHY×4→FAILED（day5,-2.9%）→HEALTHY→HEALTHY`，
  中間一天觸及 FAILED 但立刻恢復，最終收在 +7.61%——符合「短期震盪不代表失敗」
- **8039**（+1.37%，表現平庸）：`CAUTION→CAUTION→HEALTHY→FAILED→CAUTION→
  AT_RISK→HEALTHY`，軌跡明顯比 6414/6505 更反覆不定，跟它「表現平庸、走勢
  膠著」的實際狀況吻合
- **1810**（-5.53%，實際偏弱）：`HEALTHY→HEALTHY→CAUTION→FAILED→FAILED→
  FAILED→FAILED`，**從 Day3 起持續 FAILED 直到 Day10**（Day10 報酬從 Day7 的
  -12.17% 回升到 -5.53%，但仍全程停在 FAILED 狀態）——這是本輪 4 檔中唯一
  「持續性 FAILED」的案例，也正是實際表現最差的一檔

**這個排序（6414 > 6505 > 8039 > 1810）與 continuation_quality 軌跡的「乾淨
程度」完全一致**，是本輪最有力的個案驗證：系統沒有把「短期震盪」誤判成失敗
（6505 一天 FAILED 立刻恢復），但確實抓住了「真正走弱」的 1810。

## 回答你的 4 個核心問題

**1. 未來大虧股雖然 Day0 分不出來，但通常在 Day+幾開始可以辨認？**
Median 在 **Day1** 就出現 AT_RISK 等級的證據，Day2.5 左右升級為 FAILED 等級。

**2. 第一次可辨認時，通常已經跌多少？**
AT_RISK 平均 **-3.57%**、FAILED 平均 **-5.46%**——遠早於最終確認的 -10%~-19%，
是介於 -2%~-8% 這個區間，符合你原本設想的「-2%/-5%」量級，而不是要等到 -10%。

**3. 用 Continuation Quality 動態監控，能否減少 left-tail，而不大量錯殺 winner？**
100% 的大虧股能提早偵測到，且用 FAILED（而非 AT_RISK）當門檻時，贏家組的誤判率
只有 20%，且這些誤判**全數會自我恢復**（不會一路錯到底）。用較寬鬆的 AT_RISK
當門檻則誤判率上升到 55%——這代表**若真的要接 shadow production，門檻應該設在
FAILED 這一層，而非 AT_RISK**，AT_RISK 比較適合當「提高關注、暫緩加碼」的訊號，
不適合當「減碼/退出」的觸發點。

**4. 8039 這種「中間很醜但最後是 winner」，跟真正一路失敗的股票，trajectory 上
到底差在哪？** 兩個關鍵差異：(a) **NEVER_WORKED vs 有過正向 excursion**——
真正失敗股 85% 從未真正賺過錢（median MFE=0%），而 8039/6505 這類都曾經有正報酬
時刻；(b) **FAILED 是否「停留」**——8039/6505 的 FAILED/AT_RISK 都是單日或短暫
出現隨即恢復，真正失敗股（如 1810）一旦進入 FAILED 就持續停留到最後。**「是否
持續停留在惡化狀態」比「是否曾經觸及惡化狀態」更能區分兩者**。

## 是否值得擴大到 80~120 stocks？

**建議：值得擴大確認。** 本輪結果全面達成（甚至超越）§16 設定的成功標準：
- Early detection rate 100%（原本期待 10~14/20 即可）
- 8039 類 volatile winner 沒有被單次 FAILED 判死（4/4 全部恢復）
- 排序驗證與具名案例的實際表現完全吻合

需要在 80~120 stocks 上確認的重點：
1. 100% early detection rate 是否在更大樣本上保持，還是本輪 20 檔恰好都是
   「乾淨」的失敗案例
2. AT_RISK 55% 誤觸率是否隨樣本數增加而收斂或惡化
3. 「持續停留 FAILED」vs「單次觸及後恢復」這個區分（本報告問題 4 的核心發現）
   是否能量化成一個明確的 descriptive 指標（例如「FAILED 天數佔比」），而不是
   目前這種質化描述

**本輪全程沒有修改任何 production 程式碼、沒有動 momentum_score/RS threshold/
Hard Exclusion/LLM/WATCH_QUALITY_MODE，continuation_quality_state 純粹是
shadow research 欄位。**
