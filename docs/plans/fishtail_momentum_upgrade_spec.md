# 魚尾選股程式升級 Spec（Momentum / Breadth / Regime / Tracking）

**狀態**：v2.1 已實作（2026-07-15，見下方進度）；v2.2 / v2.3 待開始  
**建立日期**：2026-07-15  
**目的**：把現有魚尾系統從「法人異常訊號主導」升級成「動能選股系統」，但保留既有 11 步流程骨架，讓工程師可以分階段實作。  

## 實作進度（2026-07-15）

### v2.1 ✅ 完成
- 新模組 [backend/app/signals/momentum.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/momentum.py)：全市場動能 frame（return 5/20/60d、RS market/industry percentile、rs_rank_improvement_5d、trend_efficiency、institution_buy_to_turnover_2d + 市場 percentile）+ `momentum_score`（30/25/20/15/10 權重 + 風險扣分）+ B/C 通道選股 + `build_signal_metrics`
- `candidate_pool.py`：候選池改四通道聯集（A 法人既有 / B 價格動能 / C 動能加速；D 基本面因缺 announcement_date 未上線，依 §6.1 D 限制）；每檔 merge frame 特徵 + momentum_score；截斷排序改用 momentum_score
- `classification.py`：LEADER / FOLLOWER / ROTATION_LAGGARD 依 §6.3 重寫（LAGGARD_CANDIDATE 改名，保留向後相容 alias）
- `filters.py`：§6.4 三條 gate 全上（RS<40 且未改善 hard exclusion / 震盪盤 score<60 / 退潮盤 RS<90）
- persistence：`SignalWatchHit.signal_metrics` JSON column（idempotent ALTER 已加進 signal_watch_schema.py）；pipeline 把 §9.2 欄位 deterministic 蓋回 watchlist item → snapshot + watch hit 都可回看
- 測試：`tests/test_signals_momentum.py` 新增 20 案例；classification 重寫 30 案例；filters +8；candidate_pool +2；archive +1。全 backend suite 無新增失敗（baseline 20 fail 不變）
- **工程決策（spec 未定部分）**：B 通道上限 40 / C 通道上限 20（避免 percentile 門檻灌爆 POOL_HARD_LIMIT）；percentile 樣本 guard（全市場 >=20、產業內 >=3、產業數 >=5，不足回 None）；market benchmark 用 universe 中位數（非 TAIEX，percentile 等價）；rolling high 用收盤價；LLM prompt / evidence card 未動（依 §10 Step 7 最後才改）

### v2.2 資料前置 ✅ 完成（2026-07-15 第二輪）
§4.2「要先補資料或 schema」的前兩項已解：

1. **monthly_revenue ETL 缺口修復 + 回補**：根因 = FinMind 把「N 月營收」全掛在
   「N+1 月 1 號」單一 date key + dataset-level fetch 只回 start_date 當日 →
   舊單日抓法永遠漏。已改逐「月 1 號」key 呼叫（daily 2 quota）；remote 已回補
   2026-01~06（每月 1072~1083 檔，yoy/mom 100%）
2. **`available_date` 規則（不加欄位、不改 schema）**：`momentum.revenue_available_date
   (revenue_month) = 次月 10 日`（法規公告截止日），frame 只吃 available <= target_date
   的月份 → **§6.1 D 基本面動能通道已上線**（yoy>15 且連兩月加速 / 轉正 / 產業內
   percentile>=80；上限 20 檔）+ momentum_score 基本面 10 分啟用
3. **發行股數 / 市值**：新表 `stock_shares_outstanding`（FinMind `TaiwanStockShareholding`
   的 NumberOfSharesIssued + 外資持股比；dataset-level 只回 start_date 當日，同 margin）；
   daily ETL step 8；remote 已回補 2026-07-01 起。momentum frame 新增 `shares_issued /
   market_cap / institution_buy_to_market_cap_2d`（**只出欄位，未進 score / 分類門檻**，
   等累積資料後再依 §6.1 A 決定啟用方式）

### v2.2 ⬜ 待開始
- market_breadth.py / breadth_score / NARROW_BULL / episode-aware hit_count / LLM facts-only
- `build_signal_metrics` 已預留 `breadth_score: None` 佔位

### v2.3 ⬜ 待開始
- 回測與持有管理（trade_status / ATR 停損 / 換股競爭）

---

## 1. 結論先講

這份升級提案大部分都可以做，而且適合直接疊在現有 `backend/app/signals/` 架構上，不需要整套重寫。

目前 repo 已經有的基礎：

- 候選池、預分類、hard/soft filter、market regime gate 都已存在
- `monthly_revenue`、`margin_trade` 已存在，可支援基本面動能與散戶過熱判斷
- `SignalSnapshot` / `SignalWatchHit` / `archive.py` 已存在，可延伸追蹤與 episode
- 測試骨架完整，適合用 TDD 漸進改版

目前不能直接照提案原樣做、需要先調整的點：

- `institution_buy_to_market_cap` 目前缺少可靠的流通市值欄位
- 月營收目前只有 `revenue_month`，若要避免資料穿越，需要引入「公告可用日」
- `signal episode` 會碰到現有 `SignalWatchHit` / `archive.py` 的追蹤延續邏輯，不能只加欄位不改 carry 規則
- 市場廣度可從 `daily_price` 算，但要補一層 market breadth 聚合模組，不建議塞進現有 `market_regime.py` 直接硬算

因此建議採三階段實作，先做能立即提升選股品質、且對現有流程侵入最小的部分。

---

## 2. 本次升級的設計原則

1. 保留既有 11 步流程與 LLM 分工。
2. deterministic 規則仍是主體，LLM 只做研究與說明。
3. 先補「可排序的數值訊號」，再改分類與收斂，不先動 LLM prompt 主邏輯。
4. 所有新訊號都必須可回測、可落 snapshot、可測試，不能只存在執行中記憶體。
5. 嚴禁未來函數：任何月營收、episode、追蹤狀態都只能用當下可得資料。

---

## 3. 現有程式落點

本次改動主要會落在以下模組：

- [backend/app/signals/candidate_pool.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/candidate_pool.py)
- [backend/app/signals/classification.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/classification.py)
- [backend/app/signals/filters.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/filters.py)
- [backend/app/signals/market_regime.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/market_regime.py)
- [backend/app/signals/market_snapshot.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/market_snapshot.py)
- [backend/app/signals/pipeline.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/pipeline.py)
- [backend/app/signals/archive.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/signals/archive.py)
- [backend/app/models.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/app/models.py)

應同步擴充的測試：

- [backend/tests/test_signals_candidate_pool.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/tests/test_signals_candidate_pool.py)
- [backend/tests/test_signals_classification.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/tests/test_signals_classification.py)
- [backend/tests/test_signals_filters.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/tests/test_signals_filters.py)
- [backend/tests/test_signals_market_regime.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/tests/test_signals_market_regime.py)
- `archive` / `pipeline` 相關測試

---

## 4. Feasibility 盤點

### 4.1 可以直接做

- 新增價格動能候選
- 新增相對強度候選
- 新增動能加速候選
- 新增初版 `momentum_score`
- 用 `momentum_score` 重寫 LEADER / FOLLOWER / ROTATION_LAGGARD
- 在震盪盤 / 退潮盤收斂時加入分數門檻
- 新增市場廣度欄位與 `breadth_score`
- 把 `hit_count` 拆成 `consecutive_hit_count` / `independent_hit_count`
- 把 LLM 第九步改為「輸出事實欄位」，再由程式做 final decision

### 4.2 可以做，但要先補資料或 schema

- `institution_buy_to_market_cap`
  - 目前 repo 找不到可靠的流通市值或股本欄位
  - 建議第一版改成 `institution_buy_to_turnover` 必做，`buy_to_market_cap` 延後
- `revenue_yoy_acceleration` 的嚴格時間對齊
  - 目前 `monthly_revenue` 只有 `revenue_month`
  - 若直接以 `revenue_month <= target_date` 使用，會有公告前偷看資料風險
  - 建議新增 `announcement_date` 或 `available_date`
- `entry_score / hold_score / trade_status`
  - 現有 active tracking 邏輯是研究型追蹤，不是持倉引擎
  - 若要正式上線，需要額外持倉資料結構或獨立 strategy layer

### 4.3 不建議在第一版一起做

- 隔日成交模型、滑價、交易成本
- 投資組合層級的換股競爭
- ATR 驅動的實際出場模型全面替換現有 archive 規則

這些屬於回測 / 持有管理層，放在 v2.3 較合理。

---

## 5. 目標版本切分

### 5.1 v2.1：最先做，直接提升選股品質

範圍：

- 候選池加入價格動能與相對強度來源
- 新增 `momentum_score`
- 重寫 LEADER 條件，納入相對強度與 momentum score
- 在 deterministic gate 加入弱相對強度淘汰
- 震盪盤加入 `momentum_score < 60` 剔除
- 退潮盤加入相對強度門檻

預期效果：

- 解掉「法人有買但股票本身不強」的問題
- 候選池更像動能候選池，而不是純法人異常池

### 5.2 v2.2：把 regime 判斷升級成 index + breadth

範圍：

- 新增市場廣度模組與 `breadth_score`
- 市場狀態改成 `BROAD_BULL / NARROW_BULL / VOLATILE_RANGE / RISK_OFF`
- 第七步收斂規則全面改成 score-based
- `hit_count` 改成 episode-aware 統計
- LLM 改成 facts-only，再由程式做最終分數決策

預期效果：

- 解掉「指數很強但個股很弱」的誤判
- 降低震盪盤仍然追噴出股的機率

### 5.3 v2.3：回測與持有管理

範圍：

- 動能失效退出
- ATR / stop loss / drawdown 規則
- 新舊股票競爭
- 交易成本與滑價

預期效果：

- 讓系統從「研究型 watchlist」更接近「可驗證的策略系統」

---

## 6. v2.1 詳細需求

### 6.1 修改候選池：從單通道變多通道

現況：

- 候選池幾乎由法人買超與產業熱錢推進

目標：

- 改成四個來源聯集

#### A. 法人資金候選

保留現有：

- 近 2 日法人買超金額前 30
- 前 10 大非金融產業成分股
- 五大集團擴散

新增 ranking 指標：

- `institution_net_buy_amount_2d`
- `institution_buy_to_turnover_2d`

第一版不做：

- `institution_buy_to_market_cap_2d`

原因：

- 缺流通市值欄位

#### B. 價格動能候選

新增欄位：

- `return_5d`
- `return_20d`
- `return_60d`
- `relative_strength_market_20d`
- `relative_strength_industry_20d`
- `distance_to_20d_high`
- `distance_to_60d_high`
- `rs_market_percentile_20d`
- `rs_industry_percentile_20d`
- `return_percentile_60d`

候選條件：

- `rs_market_percentile_20d >= 85`
- 或 `rs_industry_percentile_20d >= 80`
- 或 `close == rolling_20d_high and volume_1d_to_20d_avg >= 1.2`
- 或 `return_percentile_60d >= 85 and return_5d > 0`

#### C. 動能加速候選

新增欄位：

- `rs_rank_20d_previous_5d`
- `rs_rank_20d_current`
- `rs_rank_improvement_5d`

候選條件：

- `rs_rank_improvement_5d >= 200`
- 且 `rs_market_percentile_20d >= 70`

#### D. 基本面動能候選

第一版只用月營收：

- `revenue_yoy`
- `revenue_mom`
- `revenue_yoy_acceleration`

候選條件：

- `revenue_yoy > 15 and yoy_acceleration positive for 2 consecutive months`
- 或 `revenue_yoy turns from negative to positive`
- 或 `industry revenue_yoy percentile >= 80`

限制：

- 若沒有 `announcement_date/available_date`，這一段先不上線到主流程，只先做欄位與測試草稿

### 6.2 新增 `momentum_score`

在 pipeline 上，建議把這一步放在：

- 候選池建立後
- 預分類前

建議新增模組：

- `backend/app/signals/momentum.py`

建議分數：

- 價格動能 30
- 相對強度 25
- 法人資金 20
- 量價品質 15
- 基本面動能 10
- 風險扣分

初版要求：

- 每個子分數都要是 deterministic function
- percentile-based，不直接用 raw return 當分數
- 最終輸出 `momentum_score = clamp(0, 100)`

### 6.3 重寫分類規則

#### LEADER

需同時符合：

- 產業 20 日相對強度位於全市場前 30%
- 個股產業內相對強度位於前 20%
- `momentum_score >= 70`
- 近 3 日法人至少 2 日買超，或 `institution_buy_to_turnover_2d` 位於前 20%
- `volume_5d_to_60d_ratio >= 1.3`
- 距離 20 日高點不超過 3%

#### FOLLOWER

需同時符合：

- 同產業存在 LEADER
- `momentum_score` 介於 55~69
- 近 5 日漲幅低於 LEADER
- `rs_rank_improvement_5d > 0`
- 近 3 日法人買超為正
- 無爆量長上影

#### LAGGARD

建議改名：

- `ROTATION_LAGGARD`

需同時符合：

- 同產業存在 LEADER
- 產業仍為強勢產業
- 個股 20 日報酬落後產業至少 5%
- 近 5 日相對強度改善
- 法人由賣轉買或量能轉強
- 站回 10 日線或突破整理
- `momentum_score >= 50`

### 6.4 修改 deterministic gate

新增 hard exclusion：

- 若 `rs_market_percentile_20d < 40` 且 `rs_rank_improvement_5d <= 0`，直接排除

震盪盤新增：

- `momentum_score < 60` 直接排除

退潮盤新增：

- `rs_market_percentile_20d < 90` 直接排除

---

## 7. v2.2 詳細需求

### 7.1 市場廣度

新增 daily breadth metrics：

- `pct_above_ma20`
- `pct_above_ma60`
- `advance_decline_ratio`
- `new_high_20d_count`
- `new_low_20d_count`
- `median_stock_return_5d`
- `strong_industry_ratio`

建議新增模組：

- `backend/app/signals/market_breadth.py`

資料來源：

- 全市場 `daily_price`
- `stocks_master`

注意：

- 金融、ETF 是否排除，要與 candidate pool 規則一致
- breadth 計算需有 universe 定義，不可每天股票池漂移過大

### 7.2 新 market regime

將現有三態：

- `BULL_TREND`
- `VOLATILE_RANGE`
- `RISK_OFF`

升級成四態：

- `BROAD_BULL`
- `NARROW_BULL`
- `VOLATILE_RANGE`
- `RISK_OFF`

判斷方式：

- 先保留現有 index trend / volatility 邏輯
- 再疊 `breadth_score`

### 7.3 第七步收斂改寫

#### BROAD_BULL

- 保留 `momentum_score >= 50`
- 高信心：`momentum_score >= 75 and independent_hit_count >= 2`
- 中信心：`momentum_score >= 60 or LEADER`

#### NARROW_BULL

- 保留 `LEADER and momentum_score >= 65`
- 或 `momentum_score >= 70 and no distribution hint`

#### VOLATILE_RANGE

直接排除：

- distribution
- 單日急拉 > 5% 且量比 > 2
- `momentum_score < 60`
- 趨勢效率過低
- 相對強度近 5 日惡化
- 單次命中且非 LEADER

#### RISK_OFF

保留條件：

- LEADER
- `momentum_score >= 75`
- 對大盤 20 日相對強度前 10%
- 近 5 日法人買超為正
- 無出貨訊號
- 收盤站上 20 日線
- 波動率不在全市場最差 20%

### 7.4 Signal episode

將現有單一 `hit_count` 拆成：

- `consecutive_hit_count`
- `independent_hit_count`

新增欄位建議：

- `signal_episode_id`
- `episode_start_date`
- `episode_last_hit_date`
- `consecutive_hit_count`
- `independent_hit_count`

episode 判定規則：

- 命中間隔 <= 3 個交易日，視為同一 episode
- 至少 5 個交易日未命中，才算新的獨立 episode

進階重置條件可放第二步：

- 跌破 10 日線後重新站回
- 法人由賣轉連買
- 新突破區間

### 7.5 限縮 LLM 決策權

把第九步從 `WATCH / REMOVE` 改為 facts-only：

- `theme_verified`
- `theme_name`
- `business_relevance`
- `theme_horizon`
- `positive_catalyst`
- `negative_event`
- `evidence_quality`
- `reason`

由程式算：

- `theme_score`
- `risk_penalty`
- `final_score`
- `final_decision`

建議決策：

- `final_score >= 70 -> WATCH_HIGH`
- `60~69 -> WATCH`
- `50~59 -> REVIEW`
- `< 50 -> REMOVE`

---

## 8. v2.3 詳細需求

### 8.1 研究追蹤 vs 交易追蹤分離

現有 `archive.py` 比較偏研究追蹤。

新增欄位建議：

- `research_status`
- `trade_status`
- `exit_reason`

### 8.2 新退出規則

研究追蹤仍可保留 30 交易日，但交易規則改為：

- 連 2 日跌破 10MA
- 跌破 20MA 且法人近 3 日轉賣
- 相對強度跌出前 40%
- 產業退出強勢前 30%
- `momentum_score < 45`
- 從最高點回落超過 `2.5 ATR`

### 8.3 硬停損

初版規則：

- `max(-10%, entry_price - 2.5 * ATR)`

### 8.4 新舊股票競爭

新增：

- `entry_score`
- `hold_score`

僅當：

- `new_entry_score >= hold_score + 10`

才允許換股。

---

## 9. Schema / Persistence 建議

### 9.1 不要把所有欄位都塞進主表 column

建議分兩層：

- 需要查詢 / 排序 / 回測 join 的欄位：正式 DB columns
- 僅供 snapshot 說明與 audit 的欄位：JSON payload

### 9.2 第一批一定要落地的欄位

建議先放在 `SignalWatchHit.signals` 或新增 `signal_metrics` JSON，並同步寫入 snapshot：

- `return_5d`
- `return_20d`
- `return_60d`
- `rs_market_percentile_20d`
- `rs_industry_percentile_20d`
- `rs_rank_improvement_5d`
- `institution_buy_to_turnover_2d`
- `trend_efficiency_20d`
- `distance_to_high_20d`
- `distance_to_ma20`
- `momentum_score`
- `market_regime_detail`
- `breadth_score`

### 9.3 第二批再升成實體欄位

若後續要做 dashboard / 篩選器 / 回測排序，再考慮新增實體欄位或獨立表：

- `signal_feature_daily`
- `market_breadth_daily`
- `signal_episode`

這會比一直擴 `SignalWatchHit` 更乾淨。

### 9.4 月營收可用時間修正

若 v2.1 就要啟用基本面動能，必須補其一：

- `announcement_date`
- 或 `available_date`

否則先不要把 revenue feature 接到主決策。

---

## 10. 建議實作順序

### Step 1

建立 feature 計算層：

- `momentum.py`
- `market_breadth.py`

### Step 2

擴充 `candidate_pool.py`：

- 新候選來源
- 相對強度
- 價格動能
- ranking improvement

### Step 3

擴充 `classification.py`：

- LEADER / FOLLOWER / ROTATION_LAGGARD 新規則

### Step 4

擴充 `filters.py`：

- score-based gate
- regime-specific min score

### Step 5

擴充 `market_regime.py` / `market_snapshot.py`：

- breadth score
- `NARROW_BULL`

### Step 6

擴充 persistence：

- snapshot / watch hit 加 feature payload
- episode 狀態 carry

### Step 7

最後才改 LLM prompt / output schema。

---

## 11. 驗收標準

### v2.1 驗收

- 候選池可由法人、價格、RS、加速四路進池
- 每檔候選都有 `momentum_score`
- LEADER 條件明顯變嚴，不再只靠產業排行
- 震盪盤中 `momentum_score < 60` 不會送進 LLM
- snapshot / watch hit 可回看每檔的 `momentum_score` 與 RS 指標

### v2.2 驗收

- 市場狀態能區分 `BROAD_BULL` 與 `NARROW_BULL`
- breadth 指標可被單元測試固定驗證
- 同一檔股票可正確累積 `consecutive_hit_count` 與 `independent_hit_count`
- final decision 由程式決定，不由 LLM 直接輸出 WATCH/REMOVE

### v2.3 驗收

- 研究追蹤與交易追蹤分離
- 新退出規則可回測
- 換股邏輯不會每天因清單波動而頻繁切換

---

## 12. 建議派工方式

建議拆成 4 個工程任務：

1. `feature engine`
   - 相對強度、momentum score、trend efficiency、breadth score
2. `signals pipeline`
   - candidate pool、classification、filters、market regime
3. `persistence & tracking`
   - snapshot payload、episode、archive carry
4. `prompt & output schema`
   - LLM facts-only output、final decision 接線

---

## 13. 本次建議採納版本

若要最小改動但最值得先做，建議先實作這 3 個欄位並接上主流程：

- `rs_market_percentile_20d`
- `rs_industry_percentile_20d`
- `momentum_score`

這三個欄位完成後，系統才算真正開始從「法人異常系統」轉成「動能選股系統」。

