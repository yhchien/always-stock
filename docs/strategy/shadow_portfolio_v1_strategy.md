# 魚尾 Dual Engine v1（Paper Trading）說明

這份文件說明線上「魚尾模擬交易」頁面的 `v1_frozen` Dual Engine。它是 Paper Trading，
不會送出真實券商委託；每天收盤後讀取魚尾、P3/P4 與價格資料，產生下一交易日的模擬動作。

策略只在魚尾已追蹤的股票中找機會，ETF、資料品質可疑的標的排除。訊號日是 T 日，買賣在
下一個交易日執行；買進以當日最高價、賣出以當日最低價模擬，讓結果偏保守。

## 一、資金桶與固定參數

| 參數 | 現行值 | 意義 |
|---|---:|---|
| 起始本金 | 600,000 元 | 每個循環重新開始的虛擬本金 |
| 單位本金 | 100,000 元 | 每次 BUY/ADD 的標準資金單位 |
| Continuation 核心桶 | 300,000 元 | 強勢延續型股票，最多 3 個單位 |
| Pullback 桶 | 100,000 元 | 拉回後重新回穩型股票，最多 1 個單位 |
| Opportunity 桶 | 300,000 元 | Continuation 核心桶滿載後的額外機會，最多 3 個單位 |
| Continuation Starter | 100,000 元 | 新的延續型部位首次買進金額 |
| Confirmation 加碼 | 0 元 | 目前只確認狀態，不另外投入第二筆資金 |
| 循環長度 | 35 個交易日 | 到期全部結算，下一循環本金重設 |

Opportunity 是資金桶，不是獨立選股邏輯。符合哪一種進場 profile，先決定它屬於哪個
引擎與資金桶，再檢查容量與現金。容量滿、現金不足或已有同檔持股時，即使訊號很好也可能
不買。

## 二、Continuation 的七項證據

每個候選會建立七個 evidence family：

1. **Role**：是否為 LEADER / INDEPENDENT_LEADER / SECTOR_LEADER。
2. **Freshness**：動能是否為 FRESH_STRONG、FRESH_STABLE 或 STABLE。
3. **Watch quality**：魚尾品質是否為 READY。
4. **Relative strength**：相對大盤強度是否正向。
5. **Institutional flow**：法人流向與參與是否正向。
6. **Price structure**：價格結構是否健康，且不是 STRUCTURE_DAMAGED。
7. **Momentum**：動能分數是否至少 60。

可用證據至少 2 項，正向證據須達 `min(3, 可用證據數)`，且不能有 LAGGARD、P4_STOP、
結構損壞、流動性失敗或其他硬排除。這是「證據足夠」的共同門檻，不代表一定買進；
Continuation Starter 還要通過下方的 report profile。

## 三、買進條件與原因

### A. Continuation：抓正在延續或剛開始加速的強勢股

共同條件：

- 題材 `HIGH`。
- 報告類型是 LEADER，且不是 LAGGARD。
- P3 決策是 `RECOMMEND` 或 `BUY`。
- 七項證據通過共同門檻。
- 必須是魚尾週期的 Day 1，且今天確實被 P3 選中。
- 沒有公司行動或資料品質異常。

符合 profile 的 Continuation 部位可直接建立 100,000 元 Starter，歸入 Continuation 核心
桶；不是 Opportunity 追價單。不同 profile 的條件如下：

| Profile | 啟動條件 | 代表的買進理由 |
|---|---|---|
| **Early High Momentum** | 技術為 `early_turn` 或 `breakout`；動能 ≥85；市場 RS ≥90；產業 RS ≥90；20 日報酬 ≤10%；法人動能 `accelerating`；產業輪動為 `inflow` 或 `cooling`；leader 支持題材；資料信心 HIGH、覆蓋率 ≥90% | 三個強度維度已經同步，但價格尚未大幅反映，專門抓像 1560 的早期段落 |
| **Early Reaccel** | `early_turn`；動能 74–84；市場 RS ≥94；20 日報酬 ≥15%；RS 排名 5 日改善 ≥100；且產業或長期報酬強度足夠 | 回檔或整理後重新加速 |
| **Sustained Breakout** | `breakout`、`extended_chase`；動能 >84；獨立領導股；20 日報酬 ≥30%；市場/產業 RS ≥95；排名改善 0–20；均線距離 ≤25%；趨勢效率 ≥0.35；法人加速 | 已經形成持續突破，但仍有完整趨勢結構 |
| **Breakout Surge** | `breakout`、`extended_chase`；動能 76–84；5 日報酬 ≥10%；20 日報酬 ≥25%；市場 RS ≥96；產業 RS ≥93；法人加速 | 短線突破爆發，條件較窄，避免只因單一動能分數追價 |
| **Breakout Confirmed** | `breakout_confirmed`；高信心；動能 70–84；20 日報酬 ≥10%；市場 RS ≥93；產業 RS ≥95；法人加速 | 突破已確認，但尚未達到持續突破 profile |
| **Pullback Ride** | `pullback_setup` 或 `distribution`；動能 68–82；市場 RS ≥94；20 日報酬 ≥15%；距 20 日高點至少 -3%；產業流入或法人加速 | 強勢股整理後仍維持相對強度 |

Profile 優先序是 Early High Momentum / Early Reaccel，其次 Breakout 類，最後 Pullback Ride。
這個優先序只用當日資料，不讀未來報酬。

### B. Pullback Recovery：先觀察，回穩才買

Pullback 不會因為「跌很多」就直接買。先進入觀察狀態：

- 魚尾 Day 2–7。
- Episode 報酬約 -15% 至 -4%。
- 動能至少 55。
- P4 尚未 STOP_OBSERVING。

真正買進還要同時滿足：

- 相對觀察期間低點回升至少 3 個百分點。
- 今日收盤高於前一交易日。
- 動能至少 60，且沒有比前一天惡化超過 2 分。
- 最近 4 個交易日內曾有有效 Pullback Watch。
- P4 沒有停止觀察。

符合後以 100,000 元買進，使用 Pullback 桶。未回穩只記錄 WATCH，不是拒絕股票，而是
等待價格證明反轉成立。

### C. 桶子滿載時的 Opportunity / Rotation

Continuation 核心桶滿載後，不是所有高分股票都能進 Opportunity。候選還要符合：

- 報告 profile 仍有效。
- Continuation 排名在前 10。
- 七項證據至少 6 項正向。
- 動能 70–84、相對大盤強度至少 90。
- 可用現金與 Opportunity 容量足夠。
- 只有較弱、持有滿足最低天數且報酬未超過限制的部位，才可能被換掉。

因此新加入的 Early High Momentum（動能至少 85）主要使用核心桶；核心桶滿時，可能因
Opportunity 的即時輪動上限而不買。這是刻意避免把「剛開始的強勢股」變成無限制追價。

## 四、不買的條件與原因

| 不買原因 | 意義 |
|---|---|
| 證據不足 | 七項 evidence 沒有足夠可用或正向證據，不能只靠動能分數買進 |
| Profile 不符合 | 雖然可能是 RECOMMEND，但技術、報酬、RS、法人或資料信心組合不在任何進場 profile |
| 尚未回穩 | Pullback 仍在下跌，或尚未完成 3 個百分點反彈確認 |
| 非 Day 1 / 非當日 P3 選中 | Continuation Starter 只接受新的魚尾 Day 1，避免在行情已走一段後補追 |
| P4_STOP / 硬排除 | 觀察系統失效、LAGGARD、結構損壞、流動性或公司行動異常 |
| ETF 或不在魚尾 universe | Dual Engine 不自行擴大選股範圍 |
| 資金桶滿 | 核心桶、Pullback 桶或 Opportunity 桶已達上限 |
| 現金不足或已有持股 | 保留現金預約與單檔既有持倉，不重複建立第三個單位 |
| 資料品質可疑 | 相鄰交易日價格異常，先停判斷，避免把除權、分割或錯置資料當成訊號 |

## 五、賣出條件與優先序

每天先處理持倉，再處理新買進。符合較前條件就直接出場，不繼續看後面條件。

### Continuation Starter / Profile 部位

- 一般 Starter 在第一個完整確認日若沒有通過價格與動能延續確認，出場，原因是
  `CONTINUATION_NO_FOLLOW_THROUGH`。
- 一般 Starter 實際持倉報酬 ≤ -5%，快速停損，原因是 `CONTINUATION_STARTER_FAST_FAIL`。
- Profile 直接進場部位的實際持倉報酬 ≤ -12%，停損；它不等待 P4 或官方週期結束，
  因為 profile 本身已是較強的直接進場類型。
- 一般確認後部位的實際持倉報酬 ≤ -8%，停損。
- 獲利達 +10% 後，若相對持有期間最高收盤回吐至少 6%，以 trailing exit 出場。

### Pullback Recovery 部位

- 實際持倉報酬 ≤ -8%，先停損。
- P4 判定 `STOP_OBSERVING`，出場。
- 魚尾官方追蹤週期結束，出場。
- Recovery 後在前 4 個交易日跌破觀察期間低點，視為假回穩，出場。

### 資料與週期事件

- 公司行動或價格資料可疑時，當天不新增判斷，避免誤賣；資料恢復後再評估。
- 35 個交易日循環結束時，全部持倉按期末模擬價格結算，並重設 600,000 元本金。

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
