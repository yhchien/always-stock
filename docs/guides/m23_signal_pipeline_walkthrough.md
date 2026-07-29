# M23 訊號清單 Prompt 邏輯與實例走查

> **用途**：教學文件。帶讀者從 raw DB 走到最終的 WATCH 清單，理解每一層在做什麼、為什麼這樣設計、以及具體股票案例如何被處理。
>
> **目標讀者**：第一次接觸 M23 pipeline 的工程師 / 產品 / 利害關係人。
>
> **最後更新**：2026-05-26（含 Phase 1.1~1.3 再偵測閘門改動）

---

## 0. 一張圖看懂整個 Pipeline

```
                  [使用者點「重新產生」 / cron 觸發]
                              │
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │  Stage 1: Ingest      讀 60 個交易日的 DB 原始資料             │
   │  Stage 2: Rank        產業 3d 熱錢前 6 + 個股 3d 熱錢前 30     │
   │  Stage 3: Pool        組候選池（聯集 + 擴散，完整保留）        │
   │  Stage 4: Classify    預分類 LEADER / FOLLOWER / LAGGARD     │
   │  Stage 5: HardFilter  7 條 deterministic 硬閘門過濾           │
   │  Stage 6: SoftFilter  標 4 種 hint（不剔除）                  │
   │  Stage 7: LLM Market  上網查市場狀態（STRONG_BULL / RANGE...） │
   │  Stage 8: LLM Research上網查公司業務 / 題材 / 集團            │
   │  Stage 9: LLM Decide  WATCH / REMOVE 短決策                  │
   │  Stage 10: LLM Reason 只給 WATCH 寫 5 段 bullet 長理由        │
   │  Stage 11: Persist    寫進 signal_snapshots + watch_hits     │
   └────────────────────────────────────────────────────────┘
                              │
                              ▼
                  [使用者首頁看到「今日捕獲的大魚尾」]
```

**核心心法**：
- **Deterministic 層**（Stage 1~6）做嚴格紀律：規則 / 紅旗 / 過去績效
- **LLM 層**（Stage 7~10）做題材判讀 + 中文敘事
- **LLM 不做**：預測股價、目標價、買賣建議、嚴格數字紀律

---

## 1. LLM 在這個系統的角色

把 prompt（[watch-list-stock.md](../../backend/app/prompts/watch-list-stock.md)）核心原則拆開來看：

| LLM 做 | LLM 不做 |
|--------|---------|
| 上網查 VIX / 美股 / 台指期 → 判 market_state | 預測台股明日漲跌 |
| 上網查公司業務 + 產業鏈位置 | 自己判定股票該漲或該跌 |
| 解讀題材延續性（1-2 季 vs 短線事件） | 給目標價或預期報酬率 |
| 把 deterministic 訊號翻成繁體中文白話 | 修改 backend 算好的 prelim_type / tracking_status 數字 |
| 對 WATCH 名單寫 5 段 bullet 長理由 | 給 BUY / SELL 操作建議 |

**為什麼這樣分工**？因為 LLM 不擅長嚴格紀律（譬如「我上次推這檔，結果如何」這種需要記憶 + 數字判斷的事），但很擅長把資料寫成人類能讀的文字。把規則丟給 deterministic 層，把敘事丟給 LLM。

---

## 2. 候選池怎麼來：6 個來源

[`build_candidate_pool`](../../backend/app/signals/candidate_pool.py) 把以下 6 種來源做**聯集**：

| 來源 | 數量 | 為什麼放 |
|------|------|---------|
| 1. top_stocks_3d 前 30 | ~30 | 主訊號：3 日法人淨買超最多的股 |
| 2. top_industries_3d 前 6 的所有成分股 | ~80~150 | 抓**同產業 follower / laggard** |
| 3. top_stocks 前 6 即使不在熱門產業 | ~6 | 跨產業強勢股 |
| 4. 熱門產業龍頭股 | ~6 | 確保 leader_check 有對照 |
| 5. 熱門產業同供應鏈股 | ~10~20 | 抓題材擴散 |
| 6. top_stocks 前 6 的同集團股 | ~5~10 | 集團共振（鴻海系 / 國巨系 / 聯電系...）|

聯集後 → 排除不在 stocks_master／人工黑名單，依 momentum score 與法人流排序；
P1 起不再因 raw union 總量超過 150 而截成 120。

---

## 3. 預分類：LEADER / FOLLOWER / LAGGARD

[`classify_stocks`](../../backend/app/signals/classification.py) 對候選池每檔股票做標籤。**這是 deterministic 規則，不是 LLM 判斷**。

### LEADER 必要條件（spec §7.1）
全部成立才算：
- `industry_rank_5d` 在該產業前 30%（用 `ceil(count × 0.3)`）
- `industry_rank_net_3d` 在該產業前 20%
- `consecutive_buy_days_3d >= 2`（近 3 日法人合計連買至少 2 天）
- `volume_5d_to_60d_ratio >= 1.5`（5 日均量比 60 日均量放大 1.5 倍以上）

### FOLLOWER 必要條件（spec §7.2）
- 同產業已有 LEADER
- `0 < price_change_5d < leader_gain × 0.7`（自己漲了但漲幅不及 leader）
- `total_institution_flow_3d > 0`

### LAGGARD_CANDIDATE 必要條件（spec §7.3）
- guard：同產業 LEADER 5 日漲幅 ≥ 5%
- 4 個次條件命中 ≥ 2 個（guard 自身已算 1 hit）：
  - 與 leader 漲幅落差 ≥ 5 個百分點
  - `total_institution_flow_1d > 0` OR `volume_1d_to_5d_ratio > 1.2`
  - 站上 5MA OR 10MA

**三類都不符 → 從候選池剔除**。

---

## 4. 七條 Deterministic Hard Filter（核心關卡）

[`_is_hard_excluded`](../../backend/app/signals/filters.py) 在候選池進 LLM 前，按順序檢查 7 條規則，任何一條命中就剔除。

| # | 規則名 | 條件 | 防什麼 |
|---|--------|------|--------|
| 1 | ETF / 金融 | `should_exclude(stock_id, name, industry)` | 系統定位排除 |
| 2 | 法人 5 日累計虧 | `flow_5d < 0` 且非 LAGGARD_CANDIDATE | 主訊號方向錯誤 |
| 3 | 3 日已過熱 | `price_change_3d > 15%` | 已追不上 |
| 4 | 流動性不足 | `avg_turnover_5d < 5,000 萬 TWD` | 拉抬風險 |
| **5** | **failed_follow_through**（2026-05-26 新增）| 1.1 算的 flag = True | **再偵測閘門** |
| **6** | **派發前兆**（2026-05-26 新增）| `price_10d > 25%` AND `flow_1d < 0` | **追高 + 法人轉賣** |
| **7** | **3d/1d 反轉確認**（2026-05-26 新增）| `flow_3d>0` AND `flow_1d<0` AND `price_1d<-1.5%` | **主力突然倒貨** |

---

## 5. tracking_status 注入（2026-05-26 新增）

[`_load_tracking_status`](../../backend/app/signals/candidate_pool.py) 在算完價量 / 法人 metrics 之後、進 hard filter 之前，去 `signal_watch_hits` 表查每檔候選股的**歷史驗證表現**，灌進 candidate dict。

### 注入的 7 個欄位

| 欄位 | 型別 | 來源 |
|------|------|------|
| `is_tracked` | bool | 該股是否曾被任何 snapshot_date 推進 watchlist |
| `first_seen_date` | date \| None | `MIN(snapshot_date)` |
| `days_since_first_seen` | int \| None | `daily_price.trade_date` 介於 (first_seen, target_date] 的交易日數 |
| `hit_count` | int \| None | DISTINCT snapshot_date 數 |
| `max_positive_return_pct` | float \| None | 該 cycle 累計最大正報酬（archive cron 每天更新）|
| `max_negative_return_pct` | float \| None | 該 cycle 累計最大負報酬 |
| `failed_follow_through` | bool | `days_since >= 3 AND max_pos < +3% AND max_neg < -6%` |

### 為什麼用交易日而非曆日

`days_since_first_seen` 用 `daily_price.trade_date` distinct count 反推，**避開週末 / 春節長假**。若用曆日計算，春節後可能誤判「明明才放 3 天，怎麼變成 9 天了」。

### 為什麼用 max/min 聚合多筆 row

同一檔股票可能在 cycle 內被多個 snapshot_date 抓過（例：5/14, 5/18 都被抓）。每筆 row 都有 max_positive / max_negative 欄位，理論上 archive cron 每天會同步全部 row，但保守起見用 `MAX()`、`MIN()` 聚合，避免 partial update 殘留舊值。

---

## 6. LLM 研究、驗證與全體精選

### Stage 7: assemble_market_context（上網查市場）
- 模型：`gpt-4o-search-preview`（支援 web search tool）
- Prompt fragment：只含 STEP 0 + INPUT preamble + 重要限制
- 任務：上網查加權 / 櫃買 / VIX / 美股 / 台指期，判 `STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK`
- 4h cache：同一天連按重新產生不重打

### Stage 8: run_research_batch（上網查公司）
- 模型同上
- Batch size：8 檔一次 prompt
- 任務：對每檔上網查公司業務 / 主力產品 / 產業鏈位置 / 集團股 / 龍頭表現
- 輸出：每檔的 `business_summary` / `supply_chain_position` / `theme_fit` / `group_info` / `leader_check`
- **type 鎖死**：[backend 強制覆寫](../../backend/app/signals/llm_caller.py)，LLM 不能改 prelim_type

### Stage 9: run_explanation_batch（逐檔 assessment）
- 模型：由 `OPENAI_SIGNALS_DECISION_MODEL` 控制
- Batch size：4 檔一次
- 任務：只判 `ELIGIBLE` 或有嚴格前提的真實 `REMOVE`；ELIGIBLE 不是正式推薦

### Stage 10: Global Selector（全體比較）
- Prompt/version：`global-recommendation-selector-v1.md` / `p3_global_v1`
- 所有研究與 assessment 成功、且未真實 REMOVE 的候選各建立一張 compact card
- 一次完整比較，不分 batch、不做 Top-K/ratio/source/asset/cluster cap
- 輸出只有 `RECOMMEND` / `NOT_SELECTED`
- missing/duplicate/unknown/rank/reason schema 錯誤會原子失敗，不 fallback

### Stage 11: run_watch_reason_batch（長理由）
- 模型由 `OPENAI_SIGNALS_REASON_MODEL` 控制
- Batch size：4 檔
- 任務：**只對 Stage 10 的 RECOMMEND** 寫 5 段 bullet：
  - `theme_reason`（題材）
  - `capital_reason`（資金）← **若 tracking_status.is_tracked=true 且 days≥3 必須引用追蹤表現**
  - `chip_reason`（籌碼）
  - `margin_reason`（融資融券）
  - `technical_reason`（技術）
- 同時產出結構化 `margin_analysis` 物件（個股 + 大盤 3:7 權重）

---

## 7. 最終 watchlist 組裝

[`assemble_final_output`](../../backend/app/signals/llm_caller.py) 把 Stage 10 結果整理成 spec §10.2 schema：

```json
{
  "date": "2026-05-26",
  "market_context": { "market_state": "...", "margin_climate": {...} },
  "watchlist": [
    { "stock": "...", "decision": "RECOMMEND", "recommendation_rank": 1, ... }
  ],
  "not_selected": [
    { "stock": "...", "decision": "NOT_SELECTED", "selection_reason_code": "...", ... }
  ],
  "removed": [{ "stock": "...", "decision": "REMOVE", "veto_reason": "..." }],
  "summary": { "leader_count": 3, "follower_count": 5, "laggard_count": 2, ... },
  "candidate_pool_size": 95,
  "final_watchlist_size": 10
}
```

最後寫進 DB：
- `signal_snapshots`：完整 JSON（一日一筆 UPSERT）
- `signal_watch_hits`：只有 RECOMMEND 拆成多筆 row；NOT_SELECTED/REMOVE 不新增 hit，
  也不停止較早日期既有 observation

---

## 8. Dry-run 實例（真實 prod 資料）

下面 4 個案例都是從 prod DB（`signal_watch_hits` 表，截至 2026-05-25 snapshot）的真實紀錄。

### Case A：6515 穎崴（**failed_follow_through 命中**）

**歷史**（DB 原始紀錄）：

| snapshot_date | type | baseline_date | baseline_price | latest_price (5/25) | return_pct | max_pos | max_neg |
|---|---|---|---|---|---|---|---|
| 2026-05-14 | FOLLOWER | 2026-05-15 | 10140 | 8850 | -12.72% | +0.99% | -14.20% |
| 2026-05-18 | FOLLOWER | 2026-05-15 | 10140 | 8850 | -12.72% | +0.99% | -14.20% |

**假設今天 2026-05-26 又跑 pipeline，6515 又出現在原始候選池**：

```
Stage 1~3 (Pool)     6515 因仍在 hot_stocks_3d 而進入候選池
                              │
                              ▼
Stage 3.5 (1.1)      _load_tracking_status 撈到：
                       - first_seen_date = 2026-05-14
                       - days_since = 8（5/15~5/26 共 8 個交易日）
                       - hit_count = 2
                       - max_pos = +0.99% < +3.0%  ←
                       - max_neg = -14.20% < -6.0% ←
                       → failed_follow_through = True ✗
                              │
                              ▼
Stage 4 (Classify)   原本可能被預分類為 FOLLOWER
                              │
                              ▼
Stage 5 (HardFilter) 規則 5 命中（failed_follow_through=True）
                       → 6515 從候選池剔除
                              │
                              ▼
Stage 8~10 (LLM)     LLM 看不到 6515
                              │
                              ▼
[使用者首頁] 不會再看到 6515 → 避免重複推薦失敗股
```

**為什麼這是改進**：原本 5/14 推完後系統還會在 5/18 再推一次（DB 已驗證）。改動後從 5/26 起任何下次跑都會自動剔除。

---

### Case B：2327 國巨\*（**健康路徑，繼續保留**）

**歷史**（DB 原始紀錄）：

| snapshot_date | type | baseline | latest | return | max_pos | max_neg |
|---|---|---|---|---|---|---|
| 2026-05-08 | LEADER | 393 (5/11) | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-12 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-13 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-15 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-19 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-20 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |
| 2026-05-21 | LEADER | 393 | 691 | +75.83% | +75.83% | 0.00% |

**假設 5/26 又跑 pipeline，2327 又進候選池**：

```
Stage 3.5 (1.1)      tracking_status：
                       - days_since = 11
                       - max_pos = +75.83%  > +3.0% ✓
                       - max_neg = 0%       > -6.0% ✓
                       → failed_follow_through = False ✓
                              │
                              ▼
Stage 5 (HardFilter) 規則 5 不命中（flag=False）
                     規則 6 假設 price_10d > 25% 但今日法人仍買 → 不命中
                     → 通過所有 hard filter
                              │
                              ▼
Stage 8 (Research)   LLM 看到 evidence + tracking_status：
                       "tracking_status": {
                         "is_tracked": true,
                         "days_since_first_seen": 11,
                         "max_positive_return_pct": 75.83,
                         "max_negative_return_pct": 0.0
                       }
                              │
                              ▼
Stage 10 (Reason)    LLM 寫 capital_reason 時必須引用：
                       "已追蹤 11 個交易日，最高 +75.83% / 最低 0%，
                        主升段已明確驗證，國巨集團共振強..."
```

**為什麼這個 case 重要**：證明 tracking_status 不是只用來砍股票，**也用來強化敘事**。LLM 對表現已驗證的股票可以給更篤定的描述。

---

### Case C：假想的派發前兆股（**規則 6 命中**）

假設某檔股票 X：
- 過去 10 個交易日漲幅 +28.5%
- 今日（target_date）三大法人合計淨賣 -3.2 億
- 過去 3 日法人合計仍是淨買 +1.5 億（看起來主訊號還在）
- price_change_3d = +6%（未超過規則 3 的 15% 過熱門檻）

```
Stage 4 (Classify)   industry_rank_5d=2/10（前 20%）
                     consecutive_buy_days_3d=2
                     vol_5d_to_60d=1.6
                     → 預分類 LEADER
                              │
                              ▼
Stage 5 (HardFilter) 規則 1~4 全不命中
                     規則 5 不命中（無 tracking 歷史）
                     規則 6 檢查：
                       price_change_10d=+28.5% > 25.0% ✗
                       total_institution_flow_1d=-3.2e8 < 0 ✗
                       → 兩條件同時成立 → 規則 6 命中
                     → 剔除（即使預分類是 LEADER 也砍）
```

**為什麼不放給 LLM 判斷**：因為「漲 28% 但法人開始賣」對 LLM 來說很可能被合理化成「主升段中段的洗盤」。Deterministic 規則直接砍，避免 LLM 被引導去找正當理由。

---

### Case D：全新候選股（**tracking_status 全 null**）

假設某檔股票 Y 第一次進候選池：

```
Stage 3.5 (1.1)      _load_tracking_status 查不到任何紀錄
                       → 灌入 _empty_tracking_status()：
                         is_tracked=False, 其他全 None
                         failed_follow_through=False
                              │
                              ▼
Stage 5 (HardFilter) 規則 5 不命中（False）
                     其他規則照常檢查
                              │
                              ▼
Stage 8 (Research)   LLM 看到 evidence：
                       "tracking_status": {
                         "is_tracked": false,
                         "first_seen_date": null,
                         "days_since_first_seen": null,
                         ...
                       }
                     → LLM 知道這是新候選，沒有歷史包袱
                              │
                              ▼
Stage 10 (Reason)    capital_reason 不必引用追蹤表現（is_tracked=false）
                     按一般 LEADER/FOLLOWER 敘事即可
```

**為什麼這個 case 重要**：證明系統對新標的不會誤殺。tracking_status 全 null 是合法狀態，不會 raise / crash / 誤觸發 failed flag。

---

## 9. 真實 failed_follow_through 命中清單（截至 2026-05-25）

以下是 prod DB 內 max_pos < 3% 且 max_neg < -6% 的 8 檔，**若這些股票在 5/26 後再次進入原始候選池，全部會被規則 5 剔除**：

| stock_id | name | first_seen | max_pos | max_neg | hits |
|---|---|---|---|---|---|
| 7711 | 永擎 | 2026-05-06 | +1.97% | -23.58% | 2 |
| 2337 | 旺宏 | 2026-05-06 | +0.45% | -16.20% | 2 |
| 2382 | 廣達 | 2026-05-07 | +0.44% | -15.20% | 5 |
| **6515** | **穎崴** | **2026-05-14** | **+0.99%** | **-14.20%** | **2** |
| 8103 | 瀚荃 | 2026-05-11 | +0.64% | -13.01% | 3 |
| 6117 | 迎廣 | 2026-04-28 | +2.28% | -12.30% | 3 |
| 4583 | 台灣精銳 | 2026-04-30 | +1.49% | -10.95% | 2 |
| 3022 | 威強電 | 2026-05-05 | +0.07% | -10.56% | 2 |

對比同期表現最好的 4 檔（**全部會繼續通過 hard filter**）：

| stock_id | name | type | first_seen | max_pos | max_neg |
|---|---|---|---|---|---|
| 3048 | 益登 | FOLLOWER | 2026-05-06 | +76.64% | 0% |
| **2327** | **國巨\*** | **LEADER** | **2026-05-08** | **+75.83%** | **0%** |
| 6449 | 鈺邦 | FOLLOWER | 2026-05-08 | +74.62% | 0% |
| 6166 | 凌華 | FOLLOWER | 2026-04-28 | +67.00% | -1.38% |

---

## 10. 限制與已知缺口

### 10.1 規則 5（failed_follow_through）的盲點
- ❌ **首次抓到當天無法判斷**：必須等 3 個交易日才有 tracking 資料
- ❌ **archive 早退結算後失效**：若該股已被 archive.py 早退（hits 表 row 被刪除），下次再被抓進池時 `_load_tracking_status` 拿不到資料 → flag 不會觸發
- ❌ **邊界 case 漏網**：max_pos=+3.5% / max_neg=-9% 過不了門檻，但實質上也是弱訊號

### 10.2 規則 6 / 7 的盲點
- ❌ **派發模式只覆蓋兩種**：慢洩漏 6 週、量價背離但未跌、創高後縮量盤整...等其他派發 pattern 仍會放行
- ❌ **門檻是經驗值**：25% / -1.5% / 1.5% 沒 backtest 過，可能太嚴（誤殺）或太鬆（漏抓）

### 10.3 LLM 層的限制
- ❌ LLM 看到「池子已過濾」的提示可能反而**過度樂觀**，把邊緣股全推 WATCH
- ❌ LLM 對「題材延續性」的判斷依賴 web search 結果，內容質量視當天搜尋環境而定
- ❌ 同一檔股票 LLM 不同次跑可能給不同 reason 文字（temperature=0 但 search snippet 會變）

### 10.4 驗證效果的工具尚未建立
目前沒有 dashboard 量化「規則上線後命中率是否提升」、「誤殺率多少」。**這是 Phase 1.5 應該補的監測層**。建議手動 sample check：
1. 4 週後跑 archive 撈出所有「曾被 hard-excluded 的股票」
2. 看後續 10 日表現是否真的偏弱
3. 若大多數真的跌 → 規則有效；若大多數其實漲 → 門檻太嚴需放寬

---

## 11. 相關檔案索引

| 檔案 | 角色 |
|------|------|
| [backend/app/signals/candidate_pool.py](../../backend/app/signals/candidate_pool.py) | Stage 1~3 + tracking_status 注入 |
| [backend/app/signals/classification.py](../../backend/app/signals/classification.py) | Stage 4 預分類 |
| [backend/app/signals/filters.py](../../backend/app/signals/filters.py) | Stage 5~6 hard/soft filter |
| [backend/app/signals/llm_caller.py](../../backend/app/signals/llm_caller.py) | Stage 7~10 LLM 呼叫 + evidence card |
| [backend/app/signals/pipeline.py](../../backend/app/signals/pipeline.py) | 整條 pipeline orchestration + job 狀態管理 |
| [backend/app/signals/archive.py](../../backend/app/signals/archive.py) | 30 日追蹤、early-exit 結算、return 更新 |
| [backend/app/prompts/watch-list-stock.md](../../backend/app/prompts/watch-list-stock.md) | LLM system prompt（spec §10 完整 I/O contract）|
| [docs/plans/m23_daily_signals_spec.md](../plans/m23_daily_signals_spec.md) | 原始設計 spec |
| [docs/plans/m23_signal_archive_spec.md](../plans/m23_signal_archive_spec.md) | 30 日追蹤 spec |

---

## 12. 一句話總結每層在做什麼

- **Stage 1~3**：「今天有誰拿到了不少法人錢？把產業熱錢 + 同產業同集團的全撈進來。」
- **Stage 4**：「他們之中誰是領頭羊、誰是跟漲、誰還在後段補漲？」
- **Stage 5**：「之前已經被市場驗證失敗的、漲過頭法人開始賣的、主力倒貨的，全砍。」
- **Stage 7**：「現在大盤環境是強多、結構多、區間、還是偏弱？」
- **Stage 8**：「這幾家公司實際在做什麼？產業鏈在哪？集團共振嗎？」
- **Stage 9~10**：「最後留下的是哪幾檔，為什麼值得追？用 5 段繁體中文白話寫給使用者。」

---

> **教學提醒**：講解時可以從 Case A（6515）切入，因為這是改動的核心動機。先讓聽眾理解「為什麼系統會重複推一檔已知失敗的股票」，再回頭講整條 pipeline 怎麼接住這個問題。
