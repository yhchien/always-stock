# Phase 2.9：Profit Retention Event-Based Validation（2026-07-24）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、Candidate Selection、
> Phase 2、Phase 2.5、`continuation_quality_state`、`tracking_state`、
> `momentum_freshness`、Hard Exclusion、LLM、既有 threshold。
>
> **Event-Based（非 outcome-first）**：對既有 617-dedup replay cohort **全部
> 617 檔股票**（非預先分 WINNER/LOSER 再配對）逐日重建 feature-only trajectory，
> 掃描 FIRST_MEANINGFUL_PULLBACK_EVENT（running_peak_return>=+3% 之後首次
> drawdown_from_running_max<=-5%，只用事件當下以前的資料，無 look-ahead）。
>
> 腳本：`backend/analyze_phase29_profit_retention.py`
> 輸出：`/tmp/phase29_pullback_events.csv`（483 筆事件）、
> `/tmp/phase29_event_stratification.csv`、`/tmp/phase29_retention_persistence_matrix.csv`

## 核心問題

> 在「曾經漲得差不多」且「現在都發生類似程度回檔」的股票中，
> `profit_retention_ratio` 是否仍然能預測後續修復 vs 持續惡化，
> 還是只是 Peak Return 大小的替代品（Phase 2.8 發現的 Confounding）？

## 一、必要揭露（§32）

**1. 總事件數**：617 檔 cohort 中，**483 檔（78.3%）**至少一次觸發
FIRST_MEANINGFUL_PULLBACK_EVENT（曾漲到 +3% 以上、之後首次回檔 -5% 以上）。
一檔股票只取第一次事件。

**2. 各 Peak Layer 樣本數**（分層在看 outcome 前依 sample size 決定，未依結果
調整）：

| Peak Layer | n | 佔比 |
|---|---|---|
| 3-8% | 146 | 30.2% |
| 8-15% | 123 | 25.5% |
| 15-25% | 97 | 20.1% |
| >=25% | 117 | 24.2% |

四層樣本數分布均衡（不像 Phase 2.8 outcome-first 抽樣完全沒有重疊），**Common
Support 良好**——這正是 Event-Based 方法論相對 outcome-first matching 的優勢。

**3. RECOVERED / FAILED / UNRESOLVED 分布（Event+10）**：394/396（有效資料）
`{'FAILED': 35, 'RECOVERED': 194, 'UNRESOLVED': 254}`。

**4. 資料完整率**：Event+5 為 456/483（94.4%），Event+10 為 396/483
（82.0%，主要是近期才觸發事件的股票，forward 資料還不夠 10 個交易日）。

**5. 關鍵誠實揭露——各 Peak Layer 的 FAILED 樣本數差異巨大**：

| Peak Layer | n | RECOVERED | FAILED | UNRESOLVED | FAILED 佔比(resolved) |
|---|---|---|---|---|---|
| 3-8% | 146 | 59 | **20** | 67 | 25.3% |
| 8-15% | 123 | 48 | **11** | 64 | 18.6% |
| 15-25% | 97 | 44 | **3**（<5，僅 descriptive） | 50 | 6.4% |
| >=25% | 117 | 43 | **1**（<5，僅 descriptive） | 73 | 2.3% |

**FAILED 比例隨 Peak Layer 遞增而急遽下降**（25.3% → 18.6% → 6.4% → 2.3%）
——這本身就是本輪 event-based、無 outcome-first 偏誤下得到的乾淨發現：**Peak
Return 大小本身就是失敗率的強力預測因子**，與 retention ratio 無關。也因此，
**15-25% 與 >=25% 兩層的 FAILED 樣本太少（3、1 檔），依 §20 規則只能做
descriptive，不能下強結論**——只有 3-8% 與 8-15% 兩層有足夠的 FAILED 樣本
可以真正比較 RECOVERED vs FAILED。

## 二、核心輸出表 2：各 Peak Layer 內 RECOVERED vs FAILED 的 profit_retention_ratio

| Peak Layer | 組別 | n | retention median (p25/p75) | drawdown median (p25/p75) |
|---|---|---|---|---|
| 3-8% | RECOVERED | 59 | **-0.244** (-0.606/0.064) | -6.54 (-7.85/-5.56) |
| 3-8% | FAILED | 20 | **-0.197** (-0.942/0.062) | -6.48 (-7.57/-5.82) |
| 8-15% | RECOVERED | 48 | **0.330** (0.193/0.425) | -7.00 (-8.78/-6.00) |
| 8-15% | FAILED | 11 | **0.243** (0.148/0.335) | -7.50 (-9.81/-6.26) |
| 15-25%（樣本不足）| RECOVERED | 44 | 0.664 | -6.21 |
| 15-25%（樣本不足）| FAILED | 3 | 0.325 | -11.55 |
| >=25%（樣本不足）| RECOVERED | 43 | 0.766 | -9.27 |
| >=25%（樣本不足）| FAILED | 1 | 0.596 | -13.21 |

**在唯一樣本充足、可信的 3-8% 層（FAILED n=20，本輪 FAILED 樣本最多的一層）
中，RECOVERED 與 FAILED 的 profit_retention_ratio 中位數幾乎完全相同
（-0.244 vs -0.197），甚至方向還輕微反過來（FAILED 的 retention 中位數還
略高於 RECOVERED）**，兩組 IQR 也高度重疊。8-15% 層（FAILED n=11）雖然方向
正確（RECOVERED 0.330 > FAILED 0.243）但差距不大、IQR 同樣大幅重疊。

## 三、核心輸出表 3：Tertile Gradient（不一致）

| Peak Layer | 指標 | LOW→MID→HIGH Recovery（Event+10） | LOW→MID→HIGH Failure（Event+10） |
|---|---|---|---|
| 3-8% | | 33.3%→44.9%→42.9%（**非單調**） | 18.8%→8.2%→14.3%（**非單調**） |
| 8-15% | | 34.1%→51.2%→31.7%（**非單調，MID 最高**） | 12.2%→9.8%→4.9%（單調） |
| 15-25%（樣本不足）| | 34.4%→46.9%→54.5%（單調） | 6.2%→3.1%→0.0%（單調，但基數僅 3 檔） |
| >=25%（樣本不足）| | 30.8%→43.6%→35.9%（**非單調**） | 2.6%→0.0%→0.0%（基數僅 1 檔） |

**Recovery rate 的 gradient 在 4 層中有 3 層不是單調的**（3-8%、8-15%、
>=25% 都是 MID 或某中間 tertile 表現最好，不是乾淨的「越高越好」）。只有
15-25% 層呈現乾淨單調，但這層 FAILED 只有 3 檔，不可靠。

## 四、核心輸出表 4：Raw Drawdown vs Profit Retention Ratio——幾乎給出相同資訊

| Peak Layer | retention gradient | raw_drawdown gradient |
|---|---|---|
| 3-8% | 33.3%→44.9%→42.9% | 35.4%→38.8%→46.9% |
| 8-15% | 34.1%→51.2%→31.7% | 36.6%→48.8%→31.7% |
| 15-25% | 34.4%→46.9%→54.5% | 37.5%→43.8%→54.5% |
| >=25% | 30.8%→43.6%→35.9% | 41.0%→51.3%→17.9% |

**在每一層，`profit_retention_ratio` 與最原始的 `drawdown_from_peak_at_event`
呈現幾乎一樣的 gradient 型態**（8-15% 層兩者數字近乎相同：34.1/51.2/31.7 vs
36.6/48.8/31.7）。**這代表在同一 Peak Layer 內，用 peak_return 正規化過的
retention ratio，並沒有比最原始、未正規化的回撤百分點提供更多資訊**——正規化
這一步本身沒有創造額外價值，因為「已經按 Peak Layer 分層」這件事本身就已經
把 peak_return 的效應控制掉了，剩下的原始回撤幅度跟 retention ratio 自然
高度相關、資訊重疊。

## 五、核心輸出表 5：Retention × Persistence 四象限（全體 events，**未依 Peak
Layer 控制，需誠實揭露此表可能仍受 Peak 大小混雜**）

| 象限 | n | RECOVERED | FAILED |
|---|---|---|---|
| HIGH retention / 無 persistent risk | 145 | 42.1% | 2.1% |
| LOW retention / 無 persistent risk | 142 | 49.3% | 6.3% |
| HIGH retention / persistent risk | 97 | 35.1% | 1.0% |
| **LOW retention / persistent risk** | 99 | 29.3% | **22.2%** |

**唯一有意義的組合效應**：LOW retention + AT_RISK persistence>=3 的 FAILED
率（22.2%）遠高於其他 3 個象限（1.0%~6.3%），是「LOW retention 單獨」
（6.3%）的 3.5 倍，也遠高於粗略估算的「persistence>=3 單獨」（不分 retention
高低，加權約 11.7%）。**這暗示 retention 與 persistence 疊加時確實有濃縮
failure 的效果**，但本表未依 Peak Layer 控制，考量到本輪已經證實 Peak
Layer 本身與 retention 分布相關（層 3-8% 的 retention 中位數是負值、層
>=25% 是 +0.766），**這個組合效應仍可能部分是 Peak Layer 混雜**，本報告
誠實標記為「觀察到但未完全排除混雜」，不當作已驗證結論。

## 六、時間價值（§18）：FAILED 案例在 Event Day 當下的殘餘空間

35 檔最終判定 FAILED（Event+10）的股票，在 **EVENT_DAY 當下**：

- median current_return_at_event：**+0.49%**（幾乎打平，接近成本）
- median peak_return_at_event：7.17%
- median drawdown_from_peak_at_event：-6.94%
- median profit_retention_ratio：0.071（接近 0，但非深度負值）
- **Event Day 當下已經 <=-10% 的案例：0/35（0%）**

**這是本輪最一致正面的發現**：即使 profit_retention_ratio 本身區分力有限，
「回檔到 -5% 這個事件本身」在時機上仍然夠早——沒有一檔最終失敗的股票在
EVENT_DAY 當下就已經跌破 -10%，median 殘餘報酬還接近打平，代表這個事件觸發
點本身仍保有相當的提前處理空間（即使還不知道「這次回檔會不會修復」）。

## 回答 8 個核心問題（§33）

**1. 控制 Peak Return 後，profit_retention_ratio 是否仍能區分 RECOVERED vs
FAILED？** **否，在樣本最充足的層幾乎完全消失**——3-8% 層（FAILED n=20，
本輪最大 FAILED 樣本）RECOVERED 與 FAILED 的 retention 中位數幾乎相同
（-0.244 vs -0.197），方向甚至輕微反轉。8-15% 層（FAILED n=11）方向正確
但差距小、IQR 高度重疊。15-25%/>=25% 層 FAILED 樣本太少無法下結論。

**2. 是否在不同 Peak Layer 都呈現一致方向？** **否**——Recovery rate 的
tertile gradient 在 4 層中有 3 層不是單調的（3-8%、8-15%、>=25% 皆非單調，
且 8-15% 是 MID tertile 表現最好而非 HIGH）。

**3. Profit Retention 是否真的比 Raw Drawdown 更有資訊？** **否**——4 個
Peak Layer 中，retention 與 raw drawdown 呈現幾乎相同的 gradient 型態
（尤其 8-15% 層兩者數字近乎一致），**用 peak_return 正規化沒有創造額外價值**。

**4. 哪個 Peak Layer 中區分力最強？最弱？** 最弱（也最可信，因為 FAILED
樣本最多 n=20）：**3-8% 層，區分力幾乎為零甚至方向反轉**。8-15% 層
（FAILED n=11）有微弱但方向正確的區分力，是本輪唯一稱得上「有一點點區分
力」的層。15-25%/>=25% 因 FAILED 樣本太少（3、1 檔）無法評估，未為了讓
結果好看調整 threshold 或分層方式。

**5. Event Day 當下是否已存在有用 Early Signal？還是必須等待 Persistence
才能確認？** **必須等待 Persistence**——profit_retention_ratio 作為 Early
Signal（事件當下即可知道）本身區分力薄弱（見 Q1），真正有效的訊號仍是
Phase 2.7 已驗證的 Persistence（Confirmation Signal，需要事件之後的觀察）。

**6. Profit Retention × AT_RISK persistence>=3 是否比單獨使用任一指標更有
辨識力？** **觀察到有（LOW retention + persist 的 FAILED 率 22.2%，遠高於
其他象限），但未依 Peak Layer 控制，不能排除仍是 Peak Layer 混雜的一部分
結果**——這是本輪唯一保留、值得未來用 Peak-Layer-controlled 方式重新驗證的
線索，但不足以現在就當作已驗證的獨立訊號。

**7. 當 Failure 可以被辨認時，通常還有多少時間/報酬空間？** **很多**——
EVENT_DAY 當下 median 殘餘報酬 +0.49%（接近打平），**0/35（0%）已經跌破
-10%**——事件觸發的時機本身很早，即使還無法單靠 retention 本身判斷會不會
修復。

**8. 最終結論**：**REJECTED**（profit_retention_ratio 作為獨立新 feature）。
理由：Stop Condition #1（控制 Peak Layer 後，樣本最充足層的 RECOVERED vs
FAILED 差異幾乎完全消失）與 Stop Condition #3（Raw Drawdown 表現與 Profit
Retention 幾乎相同，無需新增這個正規化 feature）**兩個都成立**。依 §27
指示應停止把 `profit_retention_ratio` 當作核心 feature 繼續發展；依 §30
指示：**不繼續發明新的 Profit Feature，直接接受目前只有 Persistence 具有
較穩健的證據**——下一階段若仍要研究 Exit，應只基於 NEVER_WORKED /
persistent deterioration（已驗證的 Persistence）做較保守的 Exit Candidate，
**ROUND_TRIP_FAILURE 這條路徑暫不自動處理**（因為本輪找不到能在早期、獨立
於 Peak Layer 之外真正區分它與正常回檔的訊號）。

**不進入 Phase 3 Exit Candidate State Design**（依 §28，只有 VALIDATED 才
進下一步；本輪結論是 REJECTED）。

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate Selection、Phase 2、Phase 2.5、
`continuation_quality_state`/`tracking_state`/`momentum_freshness`、Hard
Exclusion、LLM、既有 threshold；未新增 technical indicator / institution
model / 新 Evidence Family；未重跑 617 檔 full pipeline（僅用單股 DB 查詢
重建 feature-only trajectory）；未用未來最高點建立 Event（running peak 僅用
事件當下以前資料）；未為結果漂亮調整 Peak Layer 分層或 retention threshold
（分層依 sample size 決定，且在看 outcome 前就決定）；未 hardcode stock_id
邏輯；未做正式 SELL rule 或 portfolio backtest；未設計 production Exit
Candidate（依驗證結果為 REJECTED，依 §28 規則不進入 Phase 3 設計）。
