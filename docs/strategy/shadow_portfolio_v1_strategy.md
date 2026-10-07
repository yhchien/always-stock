# 魚尾 Dual Engine（雙引擎）v1（Paper Trading／模擬交易）說明

這份文件說明線上「魚尾模擬交易」頁面的 `v1_frozen` Dual Engine（雙引擎）。它是 Paper Trading（模擬交易），
不會送出真實券商委託；每天收盤後讀取魚尾、P3/P4 與價格資料，產生下一交易日的模擬動作。

策略只在魚尾已追蹤的股票中找機會，ETF、資料品質可疑的標的排除。訊號日是 T 日，買賣在
下一個交易日執行；買進以當日最高價、賣出以當日最低價模擬，讓結果偏保守。

## 一、股票分類與固定參數

| 參數 | 現行值 | 意義 |
|---|---:|---|
| 起始本金 | 600,000 元 | 顯示與會計基準；不作為買進阻擋條件 |
| 單位本金 | 100,000 元 | 每一檔股票每次建立一筆獨立交易 |
| 同時持股上限 | 6 檔 | 只有名額全滿時才評估輪動換股 |
| 每檔最大單位 | 1 | 不做中間加碼 |
| 股票分類 | Continuation / Pullback / profile | 只描述選股類型，不對應資金桶 |
| 循環長度 | 5 個交易週 | 第 6 週第一個開盤日行政結算，下一循環本金重設 |

Continuation、Pullback 與各種 profile 是標籤，不是不同資金池。每筆交易以 100,000 元成本獨立計算；
中間現金餘額只保留為會計欄位，不會阻擋符合條件的新買進。週期結束時，將該週期所有已完成交易的
實現損益直接加總；例如 20 筆交易各損失 100,000 元，週期累計損益就是 -2,000,000 元。

週期邊界以市場週而不是固定交易日數計算。以 2026 年第一段為例：`8/1` 是週期標示的曆日，
實際起算日會對齊該週第一個開盤日 `8/3`；連續五週的最後交易日是 `9/4`，下一週第一個開盤日
`9/7` 使用當日最低價行政結算第一循環，並把 `9/7` 同時設為第二循環起算／訊號日，第二循環訊號
產生的交易則於 `9/8` 執行。

## 二、Continuation 的七項證據

每個候選會建立七個 evidence family：

1. **Role（角色）**：是否為 LEADER（領導股）/ INDEPENDENT_LEADER（獨立領導股）/ SECTOR_LEADER（產業領導股）。
2. **Freshness（動能新鮮度）**：動能是否為 FRESH_STRONG（新鮮強勢）、FRESH_STABLE（新鮮穩定）或 STABLE（穩定）。
3. **Watch quality（魚尾品質）**：魚尾品質是否為 READY（可進一步評估）。
4. **Relative strength（相對強度）**：相對大盤強度是否正向。
5. **Institutional flow（法人流向）**：法人流向與參與是否正向。
6. **Price structure（價格結構）**：價格結構是否健康，且不是 STRUCTURE_DAMAGED（結構損壞）。
7. **Momentum（動能）**：動能分數是否至少 60。

可用證據至少 2 項，正向證據須達 `min(3, 可用證據數)`，且不能有 LAGGARD（落後股）、P4_STOP（P4 停止觀察）、
結構損壞、流動性失敗或其他硬排除。這是「證據足夠」的共同門檻，不代表一定買進；
Continuation Starter 還要通過下方的 report profile。

## 三、買進條件與原因

### A. Continuation：抓正在延續或剛開始加速的強勢股

共同條件：

- 題材 `HIGH（高題材匹配）`。
- 報告類型是 LEADER（領導股），且不是 LAGGARD（落後股）。
- P3 決策是 `RECOMMEND（推薦）` 或 `BUY（買進）`。
- 七項證據通過共同門檻。
- 必須是魚尾週期的 Day 1，且今天確實被 P3 選中。
- 沒有公司行動或資料品質異常。

符合 profile 的 Continuation 部位可直接建立 100,000 元 Starter，歸入 Continuation 核心
桶；不是 Opportunity 追價單。不同 profile 的條件如下：

| Profile | 啟動條件 | 代表的買進理由 |
|---|---|---|
| **Early High Momentum（早期高動能）** | 技術為 `early_turn（早期轉折）` 或 `breakout（突破）`；動能 ≥85；市場 RS ≥90；產業 RS ≥90；20 日報酬 ≤10%；法人動能 `accelerating（加速）`；產業輪動為 `inflow（流入）` 或 `cooling（降溫但可接受）`；leader 支持題材；資料信心 HIGH（高）、覆蓋率 ≥90% | 三個強度維度已經同步，但價格尚未大幅反映，專門抓像 1560 的早期段落 |
| **Early Reaccel（早期重新加速）** | `early_turn（早期轉折）`；動能 74–84；市場 RS ≥94；20 日報酬 ≥15%；RS 排名 5 日改善 ≥100；且產業或長期報酬強度足夠 | 回檔或整理後重新加速 |
| **Sustained Breakout（持續突破）** | `breakout（突破）`、`extended_chase（延伸追價）`；動能 >84；獨立領導股；20 日報酬 ≥30%；市場/產業 RS ≥95；排名改善 0–20；均線距離 ≤25%；趨勢效率 ≥0.35；法人加速 | 已經形成持續突破，但仍有完整趨勢結構 |
| **Breakout Surge（突破爆發）** | `breakout（突破）`、`extended_chase（延伸追價）`；動能 76–84；5 日報酬 ≥10%；20 日報酬 ≥25%；市場 RS ≥96；產業 RS ≥93；法人加速 | 短線突破爆發，條件較窄，避免只因單一動能分數追價 |
| **Breakout Confirmed（突破確認）** | `breakout_confirmed（突破確認）`；高信心；動能 70–84；20 日報酬 ≥10%；市場 RS ≥93；產業 RS ≥95；法人加速 | 突破已確認，但尚未達到持續突破 profile（進場型態） |
| **Pullback Ride（強勢整理續抱）** | `pullback_setup（拉回設定）` 或 `distribution（整理／分配）`；動能 68–82；市場 RS ≥94；20 日報酬 ≥15%；距 20 日高點至少 -3%；產業流入或法人加速 | 強勢股整理後仍維持相對強度 |

Profile 優先序是 Early High Momentum / Early Reaccel，其次 Breakout 類，最後 Pullback Ride。
這個優先序只用當日資料，不讀未來報酬。

### B. Pullback Recovery（拉回回穩）：先觀察，回穩才買

Pullback 不會因為「跌很多」就直接買。先進入觀察狀態：

- 魚尾 Day 2–7（第 2–7 個交易日）。
- Episode 報酬（本輪價格路徑報酬）約 -15% 至 -4%。
- 動能至少 55。
- P4 尚未 STOP_OBSERVING（停止觀察）。

真正買進還要同時滿足：

- 相對觀察期間低點回升至少 3 個百分點。
- 今日收盤高於前一交易日。
- 動能至少 60，且沒有比前一天惡化超過 2 分。
- 最近 4 個交易日內曾有有效 Pullback Watch。
- P4 沒有停止觀察。

符合後以 100,000 元買進，使用 Pullback（拉回）桶。未回穩只記錄 WATCH（觀察），不是拒絕股票，而是
等待價格證明反轉成立。

### C. 六檔滿載時的 Rotation（輪動換股）

只有同時持有 6 檔時，才會評估輪動。候選還要符合：

- 報告 profile（進場型態）仍有效。
- Continuation 排名在前 10。
- 七項證據至少 6 項正向。
- 動能 70–84、相對大盤強度至少 90。
- 只有較弱、持有滿足最低天數且報酬未超過限制的部位，才可能被換掉。

如果未滿 6 檔，符合條件的候選直接使用空出的名額，不會因為 Continuation 或 Pullback 類型不同而
被另一個資金桶擋下。

## 四、不買的條件與原因

| 不買原因 | 意義 |
|---|---|
| 證據不足 | 七項 evidence（證據家族）沒有足夠可用或正向證據，不能只靠動能分數買進 |
| Profile 不符合 | 雖然可能是 RECOMMEND（推薦），但技術、報酬、RS（相對強度）、法人或資料信心組合不在任何進場 profile（進場型態） |
| 尚未回穩 | Pullback（拉回）仍在下跌，或尚未完成 3 個百分點反彈確認 |
| 非 Day 1 / 非當日 P3 選中 | Continuation Starter（延續首次進場）只接受新的魚尾 Day 1，避免在行情已走一段後補追 |
| P4_STOP / 硬排除 | 觀察系統失效、LAGGARD（落後股）、結構損壞、流動性或公司行動異常 |
| ETF 或不在魚尾 universe | Dual Engine 不自行擴大選股範圍；ETF（指數型基金）也排除 |
| 同時持股已滿 | 已有 6 檔持股，且候選沒有符合輪動條件的替代對象 |
| 已有持股 | 同一檔不重複建立第二個單位 |
| 資料品質可疑 | 相鄰交易日價格異常，先停判斷，避免把除權、分割或錯置資料當成訊號 |

## 五、賣出條件與優先序（Exit conditions／出場條件）

每天先處理持倉，再處理新買進。符合較前條件就直接出場，不繼續看後面條件。

### Continuation Starter / Profile 部位

- 一般 Starter（首次進場）在第一個完整確認日若沒有通過價格與動能延續確認，出場，原因是
  `CONTINUATION_NOT_CONFIRMED（未證明延續）`。
- 一般 Starter 實際持倉報酬 ≤ -5%，快速停損，原因是 `CONTINUATION_STARTER_FAST_FAIL（延續 Starter 快速停損）`。
- Profile（進場型態）直接進場部位的實際持倉報酬 ≤ -12%，停損；它不等待 P4 或官方週期結束，
  因為 profile 本身已是較強的直接進場類型。
- 一般確認後部位的實際持倉報酬 ≤ -8%，停損。
- 獲利達 +10% 後，若相對持有期間最高收盤回吐至少 6%，以 trailing exit（移動回吐出場）出場。

### Pullback Recovery 部位

- 實際持倉報酬 ≤ -8%，先停損。
- P4 判定 `STOP_OBSERVING（停止觀察）`，出場。
- 魚尾官方追蹤週期結束（`OFFICIAL_EXIT（官方結束）`），出場。
- Recovery 後在前 4 個交易日跌破觀察期間低點，視為假回穩，出場。

### 回落獲利出場（Profit Protection）

這一層是「獲利保護」，不是用來取代既有停損。每天先檢查既有硬性出場與移動回吐；都沒有觸發時，才檢查回落獲利規則。判斷使用**實際持倉報酬**：

```text
actual_profit_pct = 當日收盤價 ÷ 持股平均成本 − 1
```

#### 回落訊號與弱化分數

- 若目前實際報酬 **> +15%**，且報酬連續兩個交易日下降：`T-2 > T-1 > T`，記 1 個弱化訊號 `PROFIT_RETURN_DOWN_2D`。
- 若兩日回吐幅度大於 `T-2` 報酬的三分之一，這個訊號不會重複計分，而是升級成**強化弱化訊號**：

```text
(報酬[T-2] − 報酬[T]) ÷ 報酬[T-2] > 1/3
```

- 弱化分數是當日不同觀察項目的數量，不是百分比加總。現行可計入的項目包括 `P4_CAUTION`（P4 警戒）、`MOMENTUM_LOW`（動能偏低）、`MOMENTUM_DOWN_5PP`（動能下降至少 5 分）、`EVIDENCE_DOWN_2`（正向證據減少至少 2 項）、`TECHNICAL_WEAK`（舊技術狀態偏弱）、`TECHNICAL_ASSESSMENT_WEAKENING/BROKEN`（技術面轉弱／結構破壞）、`MOMENTUM_PHASE_WEAK`（動能階段轉弱）、`REPORT_NEGATIVE_DECISION`（研究決策偏負面）、`P3_NOT_SELECTED_2D`（連續兩日未被 P3 選中）、`EPISODE_RETURN_DOWN_2PP`（本輪價格路徑下降至少 2 個百分點）及上面的 `PROFIT_RETURN_DOWN_2D`。
- 同一個回落條件只算 1 分；「超過三分之一」只把它標成強訊號，不會多算 1 分，避免同一件事灌水。

#### 兩種獲利保護動作

| 動作 | 現行門檻 | 觸發後處理 |
|---|---|---|
| **停利出場** `PROFIT_PROTECTION_EXIT` | 小幅獲利區間 **+10%～+11%**：弱化分數至少 4，且需有強化弱化訊號與主要弱化訊號；或獲利至少 **+25%**：弱化分數至少 3，且同樣需有強化與主要弱化訊號 | 直接建立賣出訊號，不等待新候選 |
| **停利換股** `PROFIT_PROTECTION_ROTATION` | 實際獲利至少 **+25%**、弱化分數至少 2、具主要弱化訊號；並且當日必須有通過既有 Continuation rotation gate 的新候選 | 只有找到合格新股才賣舊股並買新股，沒有新股就保留原持股 |

「主要弱化訊號」目前指 `P4_CAUTION`、`TECHNICAL_WEAK`、`TECHNICAL_ASSESSMENT_BROKEN`、`MOMENTUM_PHASE_WEAK` 或 `REPORT_NEGATIVE_DECISION` 其中至少一項。技術面 `WEAKENING` 是風險證據，但不會在這個 primary signal 清單中單獨取代 `BROKEN`；這能避免只因單一指標轉弱就過早賣出。

**例子：**某股三天實際報酬為 `+30% → +24% → +18%`。目前仍高於 +15%，且連跌兩天，因此有 1 分 `PROFIT_RETURN_DOWN_2D`；回吐 `(30−18)/30 = 40%`，超過三分之一，所以升級為強化訊號。若同時有 `TECHNICAL_ASSESSMENT_BROKEN` 與 `P4_CAUTION`，弱化分數達 3，會符合 +25% 以上的停利出場；若只有分數 2，則只能在有合格新候選時走停利換股。

這個順序保留了「先砍明確風險、再保護已實現的獲利、最後才做容量換股」的優先級；輪動不會把仍然符合反彈保護條件的健康持股強行換掉。

### 資料與週期事件

- 公司行動或價格資料可疑時，當天不新增判斷，避免誤賣；資料恢復後再評估。
- 5 個交易週結束後，於下一週第一個開盤日使用該日最低價行政結算全部持倉，並重設 600,000 元本金；
  週期的交易區間結束日仍是前一交易週最後交易日，行政結算日則是下一週第一個開盤日。週期累計損益則是
  每筆已完成交易的 realized P&L 總和，與中間現金餘額無關。

## 六、如何讀線上頁面

- 「下一交易日動作」是收盤後產生的待執行訊號，不是已成交。
- BUY/ADD 的成交日期與成交價，要看「目前持倉」或歷史日明細。
- 「未實現損益」以最新收盤價計算；「歷史交易區間報酬」則是已完成循環的結算結果。
- 每筆持倉的「首次買進」是第一筆實際成交日，不是魚尾第一次出現日；魚尾 cohort 日期
  與實際成交日期是兩個不同欄位。

## 相關程式

- 策略邏輯：[`backend/app/signals/shadow_portfolio.py`](../../backend/app/signals/shadow_portfolio.py)
- 線上頁面：[`frontend/src/app/signals/(product)/shadow-portfolio/page.tsx`](<../../frontend/src/app/signals/(product)/shadow-portfolio/page.tsx>)
- 每日排程：[`.github/workflows/shadow_portfolio.yml`](../../.github/workflows/shadow_portfolio.yml)
- 歷史重播：[`backend/backfill_shadow_portfolio_replay.py`](../../backend/backfill_shadow_portfolio_replay.py)
- 技術面條件說明：[`technical_assessment.html`](../technical_assessment.html)
- 魚尾選股與換股流程：[`fishtail_selection_and_rotation.html`](../fishtail_selection_and_rotation.html)
