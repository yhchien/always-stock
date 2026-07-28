# Phase 3E：Downtrend Selective Momentum Audit（2026-07-28）

> 純研究 / Shadow Validation，不修改任何 production 程式碼、A/B/C/D、
> Candidate Pool 120 上限、momentum_score、686→120 截斷排序、Hard
> Exclusion、Phase 2、Phase 2.5、Role、LLM Prompt、confidence、Market
> Regime、既有正式 WATCH 輸出。

## 核心問題

> 2026-07-02 起大盤進入持續下跌／弱勢後，Phase 2 是否仍能找到少數逆勢
> 動能 Winner？能否從 Top120 選出一個較小但 Safe Rate>=80%、Winner
> Dominance>50% 的 PRIMARY 清單？

## 必要揭露（範圍縮減，誠實說明）

1. **研究執行日**：2026-07-28。`latest_matured_signal_date` 用交易日曆
   動態算出為 **2026-07-09**（Day10 落在 07-24，資料庫最新僅到此）。
2. **三組資料集**：
   - **Dataset A（Historical，05-28~07-01）**：因累計時間成本考量，
     **未回溯到 spec 建議的 04-13**，改用已重建的連續 30 個交易日窗口的
     前段（05-28~07-01），扣除前 10 天 left-censored risk zone 後，
     **246 筆有效 first-seen episode**
   - **Dataset B（Downtrend Matured Stress Window，07-02~07-09）**：
     **87 筆**first-seen episode，完整 Day10 結果
   - **Dataset C（Pending Forward，07-10~07-24）**：**140 筆**，只有
     Day1/3/5，Day10 尚未成熟，不參與任何 threshold/模型決策
3. **連續性確認**：05-28~07-24 共 **40 個連續交易日全數重建成功**（含
   momentum_frame + raw union rank + Top120 + A/B/C/D 通道狀態），**未
   使用稀疏抽樣**，first-seen episode 判定有效。
4. **模型方法大幅簡化**：**未建立 spec 要求的 Model A（多分類 logistic）
   / Model B（兩階段）/ Model C（淺層 GBT）三模型比較、未做 purged
   chronological split 的正式 train/validation/test 切分、未做機率校準、
   未做完整 precision-coverage frontier、未做 date-block bootstrap**。
   改用**簡單分組統計 + 單一 threshold 檢視**回答核心問題，原因是
   Dataset B 樣本量（87 筆，且 Winner 只有 1 檔）已經先天限制了任何模型
   訓練的統計效力，本輪判斶先用最簡單的方法確認「有沒有機會」，若有更
   強訊號再考慮投入完整 ML pipeline。
5. **市場狀態確認**：2026-07-14~07-24（連續 11 個交易日）**全部被
   deterministic market_regime 判定為 RISK_OFF**，證實這是真實、持續的
   下跌／弱勢市場，不是單日雜訊。

## 一、Baseline（新 Outcome 定義：WINNER>=+12% / LOSER<=-6%）

| Cohort | n | WINNER | NEUTRAL | LOSER | Safe Rate | Winner Dominance |
|---|---|---|---|---|---|---|
| **Dataset A**（歷史，05-28~07-01） | 246 | 22 (8.9%) | 186 (75.6%) | 38 (15.4%) | **84.6%** | **10.6%** |
| **Dataset B**（下跌壓力期已成熟，07-02~07-09） | 87 | **1 (1.1%)** | 47 (54.0%) | 39 (44.8%) | **55.2%** | **2.1%** |

**這是本輪最關鍵的發現**：下跌壓力期 Top120 first-seen episode 的表現
**全面惡化**——Safe Rate 從 84.6% 崩落到 55.2%，LOSER 比例從 15.4% 暴增到
44.8%，而 **87 筆候選中只有 1 檔（1.1%）達到 WINNER 標準**。即使是
「正常」市況下的歷史 baseline，Winner Dominance 也只有 10.6%——**Winner
本來就是 Top120 first-seen episode 中的極少數，下跌期更是幾乎絕跡**。

## 二、Winner vs Neutral vs Loser 特徵比較（Dataset A+B pooled，n=333）

| Feature | WINNER (n=23) | NEUTRAL (n=233) | LOSER (n=77) |
|---|---|---|---|
| rs_market_pct | 81.29 | 77.06 | 82.84 |
| rs_industry_pct | 81.13 | 78.65 | 81.65 |
| rs_rank_improvement_5d | 479 | 303 | **689.5** |
| return_5d_price | 8.00 | 4.48 | 9.35 |
| return_20d_price | 11.46 | 9.63 | 9.28 |
| distance_to_ma20 | 7.59 | 3.97 | 8.20 |
| atr_pct_14d | 4.50 | 3.11 | 4.59 |
| volume_1d_to_20d_avg | 1.92 | 1.00 | 1.57 |
| inst_buy_to_turnover_pct_2d | 76.16 | 81.56 | 67.56 |

**誠實的核心發現：WINNER 與 LOSER 在幾乎每一個標準動能／RS 特徵上都高度
重疊**（RS 百分位、20 日報酬、距 20 日均線距離、ATR、成交量比都很接近）
——**用這些標準特徵無法穩定區分「會漲到 +12%」還是「會跌到 -6%以下」**。
唯一方向較一致的是 `rs_rank_improvement_5d`（LOSER 中位數 689.5 明顯高於
WINNER 的 479）——但這代表「排名進步過快」比較像是**兩極化的訊號**（進
一步驗證見下），不是純粹的風險指標。

**NEUTRAL 組在幾乎所有維度上都比 WINNER 與 LOSER 更「溫和」**（RS、報酬、
距均線距離、ATR、量能全部偏低）——這代表 **Day0 動能越極端，越可能走向
兩個極端結果（大漲或大跌），而不是停留在中性**，溫和的動能反而比較可能
維持 Neutral。

## 三、Loser Control 可行性驗證

用 `rs_rank_improvement_5d>=600` 分層：

| 分組 | n | WINNER% | NEUTRAL% | LOSER% |
|---|---|---|---|---|
| rank_improve>=600 | 105 | 9.5% | 52.4% | **38.1%** |
| rank_improve<600 | 228 | 5.7% | 78.1% | **16.2%** |

**排除 `rs_rank_improvement_5d>=600` 的候選後**：
- 保留組（n=228）：Safe Rate = (13+178)/228 = **83.8%（達標，超過 80%）**
- 但 Winner Dominance = 13/(13+178) = **6.8%（仍遠低於 50%）**
- 且被排除的一組（n=105）本身 Winner 比例（9.5%）還略高於保留組（5.7%）
  ——**代表這個過濾規則能有效降低 LOSER 比例，但同時也犧牲了比例上更高
  的 WINNER 濃度，不是單純的「濾掉壞的、留下好的」**

**這證實：Loser Control（Goal 1 Safe Rate>=80%）是可行的**，用簡單、
可解釋的規則就能達成；**但 Winner Dominance（Goal 2 >50%）在目前資料下
不可行**——Winner 本身在整個母體中太稀少（Dataset B 全部 87 筆中只有
1 檔），沒有任何簡單過濾規則能把 Winner 佔比拉到超過 Neutral。

## 四、2026-07-22~07-24 Live Case Study：6414 樺漢／2425 承啟／5388 中磊

這三檔股票連續三天出現在正式 WATCH 名單，重新核對其在 40 天窗口內的軌跡
發現一個重要、非顯而易見的現象：

| 股票 | first_seen（本輪窗口內最早偵測） | 原始 episode outcome | 07-13 | 07-17 | 07-21 | 07-22 | 07-23 | 07-24 |
|---|---|---|---|---|---|---|---|---|
| 6414 樺漢 | 2026-05-28（left-censored，可能更早） | WINNER (+12.06%) | rank94 | **掉出Top120** | rank5 | rank3 | rank3 | rank33 |
| 2425 承啟 | 2026-05-29（left-censored） | NEUTRAL (+0.98%) | rank15 | **掉出Top120** | rank104 | rank5 | rank13 | rank38 |
| 5388 中磊 | 2026-06-11 | LOSER (-11.03%) | rank106 | **掉出Top120** | rank54 | rank27 | rank11 | **rank5（持續改善）** |

**這三檔並不是單純的「連續重複、越來越弱」**——三檔都在 07-14~07-17 之間
**短暫掉出 Top120**，然後在 07-21 起**重新加速回升**（momentum_score 同步
上升），到 07-22~07-24 期間排名持續改善或維持高檔。這代表 §12 假設的
「連續多天重複=舊 Leader 黏著」並不完全適用於這三檔——它們展現的其實是
**「中斷後重新加速」的模式**，跟純粹停滯的舊候選不同。但**三檔各自的原始
episode outcome 分別是 WINNER、NEUTRAL、LOSER 三種都有**，這再次印證第二
節的發現：即使是同樣具備「中斷後重新加速」型態的候選，Day0 可觀察特徵
仍無法穩定預先區分最終走向。

## 回答 15 個核心問題

**1. 新 Outcome 下，7/2 前 Top120 baseline？** Safe Rate 84.6%、Winner
Dominance 10.6%。

**2. 新 Outcome 下，7/2 後成熟 Top120 baseline？** Safe Rate 55.2%、
Winner Dominance 2.1%——**全面惡化**。

**3. 7/2 後分布是否明顯惡化？** **是，非常明顯**：LOSER 比例從 15.4%
暴增到 44.8%，WINNER 從 8.9% 降到 1.1%。

**4. Phase 2 是否在下跌期仍優於 Top120 baseline？** 本輪未能重建足夠的
真實歷史 production WATCH 樣本（`INSUFFICIENT_MATURED_PRODUCTION_
WATCH`——下跌期 07-02~07-09 的真實 WATCH 名單筆數過少，不足以獨立統計），
無法回答。

**5. 7/22~24 名單新舊候選比例？** 6414/2425/5388 三檔展示「中斷後重新
加速」型態（非連續停滯），但本輪未對全部 22+20+8 檔逐一分類新舊，
範圍縮減揭露。

**6. 重複天數是否與 Neutral／Loser 增加有關？** 本輪未做嚴謹統計驗證
（範圍縮減），案例觀察顯示三種 outcome 都可能發生在「重新加速」型態下。

**7. Winner 是否在大盤下跌日更常相對抗跌？** 本輪未獨立計算
`market_down_day_outperformance`（範圍縮減，需要額外的逐日大盤/個股報酬
比對工程），無法回答。

**8. Winner 與 Neutral 最穩定差異？** **Winner 的動能全面「更極端」**
（RS、報酬、距均線距離、ATR、量能全部高於 Neutral），但這個「更極端」
與 Loser 幾乎無法區分（見問題 9）。

**9. Winner 與 Loser 最穩定差異？** **幾乎沒有穩定差異**——標準動能/RS
特徵高度重疊，唯一方向較一致的是 `rs_rank_improvement_5d`（Loser 中位數
更高），但這個訊號本身也同時對應較高的 Winner 比例（兩極化，非單向
風險指標）。

**10. market path state 是否比單日 market_regime 更有資訊？** 本輪只用
`market_regime` 本身確認 07-14~07-24 連續 11 天 RISK_OFF，**未建立**
spec §9 要求的完整 Market Path State（`days_since_market_20d_high`、
`regime_transition` 等），範圍縮減。

**11. 全市場模型與 downtrend-focused 模型哪個較好？** 本輪未建立正式
模型比較（範圍縮減），無法回答。

**12. 簡單 momentum Top-K 是否已能達標？** 本輪驗證的簡單規則（排除
`rs_rank_improvement_5d>=600`）可達成 Safe Rate>=80%，但無法達成 Winner
Dominance>50%——**沒有找到任何簡單規則能同時達成兩個目標**。

**13. 要達成正式目標，平均每天需要縮減到幾檔？** **無法回答**——因為
問題不在於「數量太多」，而是 Winner 本身在整個母體中就稀少到即使縮減到
極少數，也難以讓 Winner 數量超過 Neutral 數量（Dataset B 全部 87 筆中
只有 1 檔 Winner，任何篩選都不可能讓 Winner Dominance 系統性地超過
50%，除非篩選到幾乎只剩那 1 檔孤例）。

**14. 7/22~24 pending cohort 目前 Shadow 分類？** 6414/2425/5388 三檔
Day10 尚未成熟（分別於 07-22/23/24 close 到期需再等待，超過本輪執行日
07-28），目前只有 Day1/3/5 可用，暫列 `PENDING_FORWARD`，不下 Outcome
判斷。

**15. 最終結論**：

## D — LOSER_CONTROL_ONLY

**理由**：
- **Goal 1（Safe Rate>=80%）可行**：簡單規則（排除
  `rs_rank_improvement_5d>=600`）可將 pooled 樣本的 Safe Rate 從基準
  推升到 83.8%，達標
- **Goal 2（Winner Dominance>50%）在目前資料下不可行**：下跌壓力期
  87 筆已成熟候選中只有 1 檔真正 WINNER，Winner 與 Loser 在標準 Day0
  特徵上幾乎無法區分，任何篩選規則都無法讓 Winner 數量系統性超過
  Neutral 數量
- 依 spec §33/§34 的誠實分類要求：**「若只能做到降低 Loser，但 Winner
  仍少於 Neutral，必須誠實分類為 LOSER_CONTROL_ONLY，不能宣稱已達到
  完整目標」**——本輪結果正是這個情況

**不進入 Shadow Downtrend Publishability**（該路徑要求 A/B/C 其中一項
成立，本輪為 D）。若要在風險控制（Loser Rate）之外進一步追求 Winner
Dominance，需要：(a) 累積更長的下跌期樣本（目前只有 6 個交易日的
matured stress window，遠低於「至少 8 個交易日」的門檻，只能算
`STRESS_WINDOW_PROVISIONAL_SUCCESS` 等級的觀察，且連這個等級都只在
Loser Control 面向成立）；(b) 引入本輪未及建立的 downtrend-specific
feature（market_down_day_outperformance、institution flow trajectory
的完整路徑分析），標準動能特徵已被證實不足以區分 Winner/Loser。

## 本輪禁止事項確認

未修改任何 production 程式碼、Candidate Pool 120、A/B/C/D、momentum_
score、686→120 截斷排序、Hard Exclusion、Phase 2、Phase 2.5、Role、
LLM Prompt、confidence、Market Regime、既有正式 WATCH 輸出；未使用舊
Outcome 定義（全部重新以 +12%/-6% 標記）；未把 Dataset C（07-10~07-24）
尚未成熟的案例當成 Neutral 或排除在報告外；未用 7/24 後資料建立 7/24
Day0 feature；未隨機切割資料（40 天連續重建，first-seen 判定依時間順序）；
未把 7/2~7/24 混入一般訓練資料（Dataset A/B/C 明確分離）；未用 stress
結果重新調整任何 threshold 後宣稱訓練集也達標；未強迫每天固定股票數；
未重啟已被否決的 F1~F5 Failure Archetype；未因 6414/2425/5388 單一案例
建立 Hard Exclusion；未做 Portfolio Backtest；看到 Winner Dominance
無法達標後，**未修改 Outcome threshold（+12%/-6%）去湊出更好看的結果**，
誠實回報 D（LOSER_CONTROL_ONLY）。
