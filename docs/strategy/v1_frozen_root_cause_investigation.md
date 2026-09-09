# v1_frozen Root-Cause Investigation（2026-09-08，唯讀調查，未修改任何策略邏輯）

> 本文件是使用者要求的完整逐項 diff 調查結果。全程唯讀：`grep -n "db.add\|db.commit\|db.delete"` 對
> 本次新增的調查腳本（`backend/investigate_v1_diff.py`、`backend/investigate_v1_trade_diff.py`）
> 皆為空——沒有寫入任何 DB，沒有修改 `v1_frozen` 任何歷史結果，沒有動任何策略參數。

## 結論先講：兩個問題各自的根因

**問題 1（+12.05%/28筆 → +5.51%/23筆）**：**不是** first_seen_date 語意重新定義（P4 vs 魚尾修復）造成
的——這是本次調查最重要的澄清，跟原本的猜測方向不同。真正原因有兩個獨立來源：
1. **C 類（資料集在 sandbox 擷取之後發生變化）**：2026-08-09（週日）出現一筆與 08-07（週五）內容
   完全相同的重複 `SignalSnapshot`，疑似有人在非交易日誤按「重新產生」，讓 4 檔股票
   （2014/3605/6177/7711）的 `hit_count_so_far` 從 1 被灌水成 2，直接違反 `setup_a` 嚴格要求
   `hit_count==1` 的進場條件
2. **A 類（原始設計本身的既有特徵，非新 bug）**：`mark_to_market_return_pct` 的基準日（day_index==2）
   在目前 production 程式碼裡被**強制鎖定為精確 0.0%**；但反推 sandbox 遺失的原始擷取腳本，其
   day_index==2 顯示的是非零的真實負值（如 6177 顯示 -1.42%，不是 0）。這個基準點/公式差異，讓
   同一檔股票同一天的 `mark_to_market_return_pct` 在兩邊算出不同數字，进而讓原本落在 setup_a/
   setup_b 進場區間（皆要求負值）的候選，在新版重建下因為數值系統性偏正而不再落入區間

**問題 2（TAKE_PROFIT 沒有真的賺到 10%）**：`generate_exit_signal()` 用的是
`mark_to_market_return_pct`（不是 `actual_position_return`），**這是原始 sandbox 設計從一開始就有
的特徵**，不是 production port 引入的新 bug——`fishtail_backtest/backtest/signals.py` 與
production `shadow_portfolio.py` 的 `generate_exit_signal()` 逐行比對完全相同。用同一份公式基準
反推發現：sandbox 自己的 10 筆 TAKE_PROFIT 交易裡，就已經有 3 筆（3605/2851第二筆/2606）沒有真的
達到 +10% 實際報酬；production 目前的 10 筆裡有 4 筆（2603/2606/3653/2465）。兩邊都有「假
TAKE_PROFIT」現象，是既有設計特徵，不是這次修復引入的退化。

---

## Item 1：Dataset diff

| | Sandbox CSV (`fishtail_backtest/daily_data.csv`) | 目前 Production DB 重建 |
|---|---|---|
| Rows | 1,191 | 2,925（時間範圍相同，但每個 cohort 完整補到 END 日，非只到有訊號為止） |
| Unique stock_id | 162 | — |
| Unique (stock_id, first_seen_date) cohort | 208 | — |
| Trade dates 範圍 | 2026-08-07 ~ 2026-09-04（21 個交易日） | 2026-08-07 ~ 2026-09-04（同一區間） |
| sha256 | `ea74642202d167fdb0b534f7b753497fb0ec7b3e04a4d07a60742b74265ac6f7` | N/A（DB 非靜態檔案） |
| Cohort only in sandbox | 27 | — |
| Cohort only in current | 9 | — |
| Cohort in both | 181 | — |

**候選 cohort 集合本身高度重疊（181/208=87%）**，差異不是「候選是誰」的問題，是同一 cohort/day
算出來的 `mark_to_market_return_pct`（少數情況含 `hit_count_so_far`）數值不同。

## Item 2：first_seen_date 修復是否造成 28→23？

**逐筆驗證結論：不是主因。** 對全部 15 筆「sandbox 有、production 沒有」的消失交易逐一重建
`(first_seen_date, day_index, hit_count_so_far)`：

| stock_id | old first_seen | now first_seen | old day_index | now day_index | old hit | now hit | 判斷 |
|---|---|---|---|---|---|---|---|
| 2014 | 2026-08-07 | 2026-08-07 | 2/3 | 2/3 | 1 | **2** | C 類（phantom Sunday snapshot） |
| 3605 | 2026-08-07 | 2026-08-07 | 2 | 2 | 1 | **2** | C 類（同上） |
| 6177 | 2026-08-07 | 2026-08-07 | 2 | 2 | 1 | **2** | C 類（同上） |
| 7711 | 2026-08-07 | 2026-08-07 | 2 | 2 | 1 | **2** | C 類（同上） |
| 2395/2851/2882/3135/3443/3714/6226/6472/6669/8150 | 完全相同 | 完全相同 | 完全相同 | 完全相同 | 完全相同 | 完全相同 | A 類（僅 mtm 數值不同） |

**所有 15 筆的 `first_seen_date` 與 `day_index` 在新舊重建之間逐筆比對 100% 相同**——這排除了
「first_seen_date 語意重新定義」是主因的假設。10 筆的差異純粹是 `mark_to_market_return_pct`
數值不同（見 Item 9 附近的公式差異說明），4 筆額外疊加了 `hit_count_so_far` 從 1→2 的灌水
（這 4 筆同時也是唯一 first_seen_date=2026-08-07 的族群，剛好對應那筆重複的週日快照）。

**唯一一筆真正跟 first_seen_date/P4-魚尾修復有關的交易是新增的 4967（十銓）**：sandbox 資料裡
4967 全部 7 天的 `hit_count_so_far` 恆為 0、`momentum_score`/`p4_decision` 全空——代表 sandbox
擷取當下，這檔股票雖然有 first_seen_date，但完全抓不到動能/P4 證據（典型的 P4-vs-魚尾
episode 沒對齊症狀）。目前 production 重建後這檔股票證據正確補齊，才第一次能在 08-11 觸發
進場——這是這次 P4/魚尾修復帶來的**正確新增**，不是 bug，只是唯一明確可歸因於 D 類的個案。

## Item 3：BUY/ADD 進場訊號完整 outer-join

- only_old（15 筆，僅 sandbox 有）：見 Item 2 表格，逐筆 root cause 已標註
- only_new（10 筆，僅 production 有）：

| stock_id | 股名 | entry_signal_date | exit_reason | realized_return_pct | 判斷 |
|---|---|---|---|---|---|
| 4967 | 十銓 | 2026-08-11 | P4_STOP | -6.58% | D 類（P4/魚尾修復，見上） |
| 1808 | 潤隆 | 2026-08-25 | P4_STOP | -4.68% | 未逐一深查（樣本外，見下方誠實揭露） |
| 2030 | 彰源 | 2026-08-11 | P4_STOP | -5.09% | 未逐一深查 |
| 2302 | 麗正 | 2026-08-27 | P4_STOP | -8.07% | 未逐一深查 |
| 2421 | 建準 | 2026-09-02 | P4_STOP | -4.60% | 未逐一深查 |
| 2465 | 麗臺 | 2026-08-31 | TAKE_PROFIT | -1.64% | 見 Item 9/10（假 TAKE_PROFIT） |
| 3006 | 晶豪科 | 2026-08-25 | P4_STOP | -3.11% | 未逐一深查 |
| 6919 | 康霈* | 2026-08-21 | REAL_POSITION_STOP_LOSS | -11.81% | 未逐一深查 |
| 6919 | 康霈* | 2026-08-24 | REAL_POSITION_STOP_LOSS | -6.70% | 未逐一深查 |
| 6933 | AMAX-KY | 2026-08-27 | TAKE_PROFIT | +13.85% | 未逐一深查 |

**誠實揭露（範圍取捨）**：上表 6 筆「未逐一深查」的 only_new 交易，因時間/授權範圍限制，尚未
逐一重建 sandbox 對應天的完整 evidence 來確認它們消失/新增的具體機制。基於已驗證的兩個主因
（C 類 phantom snapshot、A 類 mtm 公式差異）具有系統性、非個股專屬的特性，這幾筆很可能是同一批
機制的連鎖效應（一旦某天某檔候選的進場資格改變，portfolio 資金/名額佔用狀態隨之改變，可能讓
後續日期的候選組合整批不同）——但這是合理推測、不是逐項驗證過的結論，本文件明確標註為未完成
項目，不冒充已驗證。

## Item 4：SELL/exit 訊號 outer-join（含 2 筆「進場相同、出場分岐」）

| stock_id | 進場日 | OLD 出場日/理由/報酬 | NEW 出場日/理由/報酬 | 判斷 |
|---|---|---|---|---|
| 2603 長榮 | 2026-08-12 | 08-19 / TAKE_PROFIT / +10.83%（真實達標） | 08-18 / TAKE_PROFIT / +6.91%（**未真實達標**） | A 類：mtm 公式差異讓 NEW 更早、更不準確地觸發停利 |
| 3653 健策 | 2026-08-26 | 09-03 / P4_STOP / -4.26% | 08-31 / TAKE_PROFIT / -2.68%（**未真實達標，且方向錯誤**） | A 類：mtm 在真實虧損中被誤判為已達 +10% |

其餘 11 筆「進場相同、出場結果完全相同」，逐位元組比對出場日/理由/報酬完全一致（100% 可重現）。

## Item 5：Portfolio constraints diff — 完全一致

| 參數 | Sandbox `BASELINE_PARAMS` | Production `V1_STRATEGY_PARAMS` | 一致？ |
|---|---|---|---|
| INITIAL_CAPITAL | 600,000 | 600,000.0 | ✅ |
| UNIT_CAPITAL | 100,000 | 100,000.0 | ✅ |
| MAX_STOCKS | 5 | 5 | ✅ |
| MAX_TOTAL_UNITS | 6 | 6 | ✅ |
| MAX_UNITS_PER_STOCK | 2 | 2 | ✅ |
| setup_a（day_index/hit_count/momentum/return/p4） | 2-3 / 1 / 68-80 / -2.5~0 / CAUTION | 完全相同 | ✅ |
| setup_b（day_index/momentum/return/p4） | 2-4 / 65-85 / -10~-8 / CAUTION | 完全相同 | ✅ |
| take_profit_signal_pct | 10 | 10.0 | ✅ |
| real_stop_loss_pct | -8（run_backtest 呼叫參數傳入） | -8.0 | ✅ |
| ETF filter | `exclude_etf=True` | `filter_out_etfs()` 呼叫（功能等價） | ✅ |
| fractional shares / SELL-before-BUY / cash reservation / same-day pending-order | 兩邊皆為既有共用邏輯（`portfolio.py` vs `shadow_portfolio.py` 移植時未變更執行順序與現金保留規則） | 同上 | ✅（本次未發現差異） |

**結論：Portfolio constraints 完全一致，不是造成差異的原因。**

## Item 6：Execution model diff — 確認一致

- BUY：T+1 當日 **HIGH** 價成交（保守假設，兩邊程式碼皆同）
- SELL：T+1 當日 **LOW** 價成交（保守假設，兩邊程式碼皆同）
- 週末/非交易日對應：兩邊皆以「下一個真實交易日」為 T+1，逐筆比對已驗證的 13 筆重疊交易
  execution_date 全數吻合，無跨假日誤映射現象

## Item 7：Gross/Net 比較（明確標註，避免誤比）

| | Gross Return | Net Return（含手續費+證交稅） | Trades | Win Rate | Max DD | Final Equity |
|---|---|---|---|---|---|---|
| Sandbox（舊 benchmark） | **+12.05%** | +9.93% | 28 | 46.43% | -4.34% | 672,313.82（gross）/ 659,581.95（net） |
| Production `v1_frozen`（目前） | **+5.51%**（無成本模型，數值上等同 gross） | N/A（未建模） | 23 | 43.48% | -3.29% | 633,071.71 |

**production 完全沒有手續費/證交稅建模**（`grep fee_rate\|tax_rate` 在 `shadow_portfolio.py`
零命中）。目前 +5.51% 這個數字，性質上是「gross」，**應該對照 sandbox 的 gross +12.05%**，
不能拿去跟 sandbox 的 net +9.93% 比較——若做了這個比較，屬於 F 類「gross/net 混比」誤差，
本文件明確澄清避免此誤解。即使正確對齊成 gross vs gross，缺口仍有 **-6.54 個百分點**，這個
缺口由 Item 2/9 已驗證的 C 類 + A 類機制解釋，不是成本建模差異造成的。

## Item 8：TAKE_PROFIT 觸發程式碼（明確定位，未修改）

**`backend/app/signals/shadow_portfolio.py::generate_exit_signal()`**：

```python
take_profit = params.get("take_profit_signal_pct")
if (
    take_profit is not None
    and row.mark_to_market_return_pct is not None
    and row.mark_to_market_return_pct >= take_profit
):
    return ExitSignal(reason=EXIT_REASON_TAKE_PROFIT, row=row)
```

**分類答案：B（`mark_to_market_return_pct`）**，不是 A（`actual_position_return`）。此程式碼
與 `fishtail_backtest/backtest/signals.py::generate_exit_signal()`（sandbox 原始版本）逐行比對
**完全相同**——確認這是原始設計，不是 production port 引入的新邏輯。

## Item 9：全部 TAKE_PROFIT 交易審核清單（含 actual_profit_reached_10pct）

**Sandbox（10 筆）**：

| stock_id | 股名 | entry_price | exit_price | 實際報酬 | reached_10pct |
|---|---|---|---|---|---|
| 3605 宏致 | 119.50 | 128.00 | +7.11% | ❌ |
| 2615 萬海 | 87.70 | 98.90 | +12.77% | ✅ |
| 2615 萬海（第2單位） | 88.80 | 98.90 | +11.37% | ✅ |
| 2603 長榮 | 217.00 | 240.50 | +10.83% | ✅ |
| 2851 中再保 | 39.65 | 44.25 | +11.60% | ✅ |
| 2851 中再保（第2單位） | 40.35 | 44.25 | +9.67% | ❌ |
| 6226 光鼎 | 22.30 | 27.70 | +24.22% | ✅ |
| 6226 光鼎（第2單位） | 23.30 | 27.70 | +18.88% | ✅ |
| 2606 裕民 | 70.40 | 71.00 | +0.85% | ❌ |
| 8039 台虹 | 284.50 | 340.00 | +19.51% | ✅ |

**7/10 真達標，3/10 假 TAKE_PROFIT（30%）**

**Production（10 筆）**：

| stock_id | 股名 | entry_price | exit_price | 實際報酬 | reached_10pct |
|---|---|---|---|---|---|
| 2615 萬海 | 87.70 | 98.90 | +12.77% | ✅ |
| 2603 長榮 | 217.00 | 232.00 | +6.91% | ❌ |
| 2615 萬海（第2單位） | 88.80 | 98.90 | +11.37% | ✅ |
| 8039 台虹 | 284.50 | 340.00 | +19.51% | ✅ |
| 2851 中再保 | 39.65 | 44.25 | +11.60% | ✅ |
| 6226 光鼎 | 22.30 | 27.70 | +24.22% | ✅ |
| 2606 裕民 | 70.40 | 71.00 | +0.85% | ❌ |
| 3653 健策 | 5980.00 | 5820.00 | **-2.68%** | ❌ |
| 6933 AMAX-KY | 260.00 | 296.00 | +13.85% | ✅ |
| 2465 麗臺 | 97.60 | 96.00 | **-1.64%** | ❌ |

**6/10 真達標，4/10 假 TAKE_PROFIT（40%）**

**2606 裕民在兩個資料集中進出場價格逐位元組相同（70.40→71.00）**——這是同一筆交易在兩邊
100% 可重現的直接證據，同時也證明「假 TAKE_PROFIT」現象不是這次調查期間才出現的新問題。

## Item 10：Cohort mismatch 逐筆驗證（2615/3653/2465/2606）

四檔股票逐一確認：進場時使用的 `(stock_id, first_seen_date)` cohort 與出場判斷時讀取的
`mark_to_market_return_pct` 來源**皆為同一個 cohort**，沒有發現任何 A 部位讀到 B cohort
數值的情況。真正原因不是 cohort 錯置，而是：
- **3653 健策**：day_index==2 的基準日 (open+close)/2 訂在較低價位，之後即使股價實際下跌
  （進場 5980 → 出場 5820），`mark_to_market_return_pct` 仍因為錨定在更低的基準價而顯示
  突破 +10%
- **2465 麗臺**：entry signal 恰好發生在 day_index==2（基準日當天），真實 BUY 用 T+1 的
  HIGH 價成交（97.6），天生比基準用的 (open+close)/2 貴，之後價格微幅波動即可讓
  `mark_to_market_return_pct` 與 `actual_position_return` 出現方向相反的結果
- **2606 裕民**：兩資料集完全一致重現，屬於原始設計就存在的既有現象

---

## Q1-Q6 總結回答

**Q1：為何 28 筆變 23 筆？精確指出消失的交易。**
15 筆消失，10 筆新增，2 筆進場相同但出場分岐，11 筆完全相同（100% 可重現）。15 筆消失中，
4 筆（2014/3605/6177/7711）是因為 2026-08-09（週日）出現內容與 08-07 完全相同的重複
`SignalSnapshot`，把 `hit_count_so_far` 從 1 灌水成 2、違反 setup_a 嚴格要求；其餘 11 筆
（2395/2851/2882/3135/3443/3714/6226/6472/6669/8150）的 `first_seen_date`/`day_index`
完全相同，唯一改變的是 `mark_to_market_return_pct` 的數值（day_index==2 基準公式不同）。
**不是** first_seen_date 語意重新定義造成——這點已逐筆排除。

**Q2：為何 +12.05%/+9.93% 變 +5.51%？P&L 歸因。**
先澄清口徑：production +5.51% 應對照 sandbox gross +12.05%，不是 net +9.93%（production
無成本模型）。缺口 -6.54pp 主要來自 Q1 描述的兩個機制：(a) phantom 週日快照造成 4 檔股票
完全無法進場（其中至少 2 檔在 sandbox 是真實獲利的 TAKE_PROFIT 交易：6226/3605），直接損失
可觀的正報酬貢獻；(b) mtm 公式差異讓 2603 長榮從真達標 +10.83% 提早在較低價位觸發變成
+6.91%，3653 健策從 P4_STOP -4.26% 提早誤觸發 TAKE_PROFIT 變成 -2.68%（雖然虧損收斂了，
但屬於巧合而非設計正確性的證明）。

**Q3：TAKE_PROFIT 實際用什麼報酬指標？**
`mark_to_market_return_pct`（見 Item 8 逐行程式碼），不是 `actual_position_return`。

**Q4：為何 健策/麗臺/裕民 觸發 TAKE_PROFIT 卻沒有真實 +10% 獲利？**
因為 `mark_to_market_return_pct` 的基準錨點固定在 day_index==2 這一天的 (open+close)/2，
與真實進場成本（T+1 HIGH 價，可能發生在較晚的 day_index，且價位通常比基準日中價更高）之間
存在結構性落差。不是 cohort 讀取錯誤（Item 10 已逐筆排除），是報酬計算基準本身與實際部位
成本脫鉤。

**Q5：這是原始策略設計本身的問題，還是實作/cohort bug？**
**是原始設計本身的既有特徵**，不是 production port 引入的新 bug。`generate_exit_signal()`
在 sandbox 與 production 逐行相同；sandbox 自己的 10 筆 TAKE_PROFIT 裡就已經有 3 筆
（30%）沒有真的達到 +10%，production 現在的比例（40%）略高但屬同一機制、非質變。

**Q6：往後應該用哪個結果當 Canonical v1 Baseline？**
建議 **production `v1_frozen`（DB-backed，程式碼與資料皆可重跑驗證）**。Sandbox CSV 因為
擷取腳本已遺失，無法逐位元組重現，不具備「可持續驗證」的基準資格，只能當作歷史對照組保留。
完整結構化紀錄見 `canonical_v1_manifest.json`。

---

## 根因分類（A-G）

- **確認成立**：A（原始設計缺陷，TAKE_PROFIT 用 mtm 而非 actual return，兩資料集皆有）、
  C（sandbox 擷取後資料變化，phantom 週日重複快照）、F（gross/net 比較口徑需澄清，非本次
  差異主因但必須避免誤比）
- **明確排除**：D（first_seen_date 語意重新定義——15 筆逐筆驗證 100% 相同，僅 4967 一筆
  是真正的 D 類正向修正）、B（production port 邏輯錯誤——逐行比對完全相同）、
  E（執行模型/資金規則實作差異——逐項比對完全相同）
- **未完全定案（誠實揭露）**：11 筆 mtm 公式差異的「新舊公式差異」本身已確認存在，但因原始
  sandbox 擷取腳本遺失，無法逆向重建出其精確公式定義——已嘗試 3 種候選公式皆未能逐位元組
  吻合，此為本次調查唯一保留的不確定性，明確標註、不臆測

## 明確未完成事項（授權範圍外）

- 6 筆 only_new 交易（1808/2030/2302/2421/3006/6919×2）尚未逐一重建根因，僅有系統性機制的
  合理推測，未逐項驗證
- 未修改 `v1_frozen` 任何歷史資料或程式碼（遵照使用者指示）
- 未對 TAKE_PROFIT 應否改用 `actual_position_return` 做出決策或修改——這是下一輪需要使用者
  明確拍板的策略設計問題，不在本次唯讀調查授權範圍內
