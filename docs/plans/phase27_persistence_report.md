# Phase 2.7 PART A：Continuation Persistence Research 報告（2026-07-24）

> 純研究，不修改 production 程式碼、不修改 `continuation_quality_state` 原始
> 規則或門檻。純離線分析既有 132-stock（66 WINNER + 66 BIG_LOSER）7-observation
> （Day0/1/2/3/5/7/10）continuation quality 資料，未重新查資料庫。
>
> 腳本：`backend/analyze_phase27_persistence.py`
> 原始資料：`/tmp/phase27_persistence_132.csv` / `/tmp/phase27_persistence_raw.json`

## 核心問題

> 「單日 AT_RISK/FAILED 警報」跟「持續性的惡化」，兩者的區分力有沒有差異？

## 區分力總表（LOSER% - WINNER% = gap，數字越大代表區分力越強）

| 訊號 | BIG_LOSER | WINNER | gap (百分點) |
|---|---|---|---|
| ever AT_RISK+（單次） | 100.0% | 62.1% | 37.9 |
| AT_RISK+ persistence>=2 | 63.6% | 24.2% | 39.4 |
| **AT_RISK+ persistence>=3** | 59.1% | 9.1% | **50.0** |
| ever FAILED（單次） | 86.4% | 24.2% | 62.1 |
| FAILED persistence>=2 | 42.4% | 4.5% | 37.9 |
| **FAILED persistence>=3** | 37.9% | **0.0%** | 37.9 |
| **not_recovered_by_day10** | 63.6% | 13.6% | **50.0** |
| multiple risk episodes>=2 | 34.8% | 15.2% | 19.7 |
| recovered next observation | 33.3% | 31.8% | 1.5（無區分力） |
| recovered within 2 | 34.8% | 40.9% | -6.1（方向相反，無區分力） |

## 回答你的 8 個問題

**1. 單次 AT_RISK 的區分力多少？** 37.9 個百分點的 gap（100% vs 62.1%）——已經
有區分力，但 WINNER 組 62.1% 誤觸率偏高，單獨用來當決策依據太吵。

**2. AT_RISK persistence>=2 的區分力多少？** 39.4pp gap（63.6% vs 24.2%），
比單次略好，且兩組的絕對比例都大幅下降，代表大多數「單次 AT_RISK」是雜訊。

**3. AT_RISK persistence>=3 的區分力多少？** **50.0pp gap（59.1% vs 9.1%）**——
是本輪 AT_RISK 系列裡最強的，WINNER 組降到只有 9.1% 誤觸。

**4. 單次 FAILED 的區分力多少？** 62.1pp gap（86.4% vs 24.2%），是所有單一指標
中 gap 最大的，但 WINNER 組 24.2% 誤觸率仍不算低。

**5. FAILED persistence>=2/>=3 的區分力多少？** persistence>=2 是 37.9pp gap
（42.4% vs 4.5%）；**persistence>=3 在 WINNER 組是 0.0%（0/66）**——本輪 66 檔
WINNER 沒有一檔連續 3 次 observation 都停留在 FAILED，而 37.9% 的 BIG_LOSER
會出現這個型態。**這是本次找到的唯一「零誤觸」訊號。**

**6. WINNER 是否比 BIG_LOSER 更常快速恢復？** 「recovered_next」（33.3% vs
31.8%）跟「recovered_within_2」（34.8% vs 40.9%，方向甚至相反）都**沒有展現
區分力**——這代表「多快恢復」本身不是好的區分特徵（連 BIG_LOSER 也有 1/3 會在
下一個 observation 就短暫回到 HEALTHY/CAUTION，只是之後又再度惡化）。真正有
區分力的是「**最終**有沒有恢復」（`not_recovered_by_day10`：63.6% vs
13.6%，50.0pp gap），而不是「恢復的速度」。

**7. 最佳區分點是哪一個？** 沒有單一「最佳」，要看你要的是高召回還是高精準：
- **想要高召回（盡量不漏掉大虧股）**：用「ever FAILED」（單次即可），86.4% 捕獲率，代價是 24.2% 誤觸
- **想要零誤殺（絕不錯殺 winner）**：用「FAILED persistence>=3」，WINNER 組
  0% 誤觸，代價是捕獲率降到 37.9%
- **平衡點**：「AT_RISK+ persistence>=3」或「not_recovered_by_day10」，兩者
  gap 並列最高（50.0pp），捕獲率跟誤觸率都在中間

**8. 最佳點第一次出現時，BIG_LOSER 平均報酬是多少？是否仍早於 -10%？**
- AT_RISK+ persistence>=2 觸發時平均報酬 **-5.43%**，92.9%（39/42）發生在
  報酬跌破 -10% 之前
- AT_RISK+ persistence>=3 觸發時平均報酬 **-5.27%**，94.9%（37/39）發生在
  報酬跌破 -10% 之前
- **持續性門檻拉高（要求更多次確認）幾乎沒有拖慢偵測時機**（-5.43% →
  -5.27%，幾乎相同），代表「多等一兩個 observation 確認持續性」的代價很小，
  換來的是誤觸率大幅下降——這是本輪最重要的發現

## 小結

單日狀態（無論 AT_RISK 或 FAILED）都「有方向但雜訊大」；**要求持續性
（persistence>=2 或 >=3）或「最終沒有恢復」，能在幾乎不犧牲偵測時機的前提下
（觸發報酬仍維持在 -5% 左右，遠早於最終 -10%），大幅降低對 WINNER 的誤傷**，
FAILED persistence>=3 甚至做到零誤觸。這是「Possible Exit Candidate Signal」
的候選方向之一，**本報告不提出任何 production 門檻建議**，僅記錄此研究發現。
