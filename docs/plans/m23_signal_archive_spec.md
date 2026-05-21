# M23 訊號追蹤清單 Spec

> **Canonical spec**：若 README、CLAUDE.md、memory 與本文件衝突，以本文件為準。
> 2026-04-29 起草，屬於 M23 每日異常訊號清單的延伸功能。
> 依附既有 spec：[m23_daily_signals_spec.md](./m23_daily_signals_spec.md)
>
> **2026-05-21 更新**：retention 從 40 個交易日改為 **30 個交易日**（`ARCHIVE_RETENTION_TRADE_DAYS = 30`）。
> 本文件內所有「40 交易日」「40 日」字眼指原始設計值；目前實際行為為 30 個交易日。
> 同日全面同步 DB schema：DROP COLUMN `return_day_40_pct` + UPDATE `closure_reason` 字面值
> `completed_40_days` → `completed_30_days`（由 `main.py` lifespan 跑 idempotent migration）。

---

## 1. 目標

把每日異常訊號清單中「最終被納入 watchlist 的股票」做成一個可追蹤的 40 交易日名單，讓使用者可以回看：

1. 這檔股票是從哪一天開始被抓到
2. 在追蹤窗口內一共被抓到幾次
3. 目前已經進入追蹤第幾個交易日
4. 自基準日以來的報酬率
5. 每一次被抓到當天的報告內容
6. 當股票完成 40 交易日追蹤後，保留一份 completed archive 摘要，包含第 10 / 20 / 30 / 40 天報酬率

這不是使用者自選 watchlist，也不是回測系統；它是 **M23 訊號輸出的歷史追蹤視圖**。

---

## 2. 範圍與定義

### 2.1 追蹤對象

- 只追蹤 `signal_snapshots.watchlist`
- 不追蹤 `removed`
- 不追蹤 candidate pool / LLM intermediate 結果

### 2.2 共享模型

- 此功能沿用 M23 的「全站共享每日 snapshot」設計
- **不是 per-user 清單**
- 同一天若重新產生，仍以當天最後一份 snapshot 為準

### 2.3 40 交易日 retention

- 追蹤資料只保留最近 **40 個 distinct `snapshot_date`**
- 這裡的 `snapshot_date` 等同該日訊號使用的交易日
- 超出 40 個交易日的資料會從追蹤資料表中刪除
- `signal_snapshots` 既有歷史保留策略不因本功能改變

---

## 3. 使用者行為

### 3.1 寫入時機

當使用者按下「重新產生」後：

1. `POST /api/signals/regenerate` 只代表 job 建立成功，不代表追蹤資料已寫入
2. 只有在 pipeline 成功完成，且 `signal_generation_jobs.status = done` 時，才將該日 watchlist 寫入追蹤資料
3. 若 job `failed`，該次不寫入、不覆蓋，也不影響既有追蹤資料

### 3.2 同日重產覆蓋規則

- 同一個 `snapshot_date` 重新產生時：
  - 當日追蹤資料先刪除再重建
  - 只覆蓋 **當天**
  - 舊日期資料完全保留

### 3.3 命中次數

若某檔股票在 40 交易日內被抓到多次：

- 列表頁要顯示「被抓到幾次」
- 報告詳情要保留每次被抓到當天的 `reason`
- 呈現順序預設依日期排序（新到舊）

---

## 4. 資料模型

新增一張 normalized table，專門存 watchlist 命中紀錄。

### 4.1 `signal_watch_hits`

```python
class SignalWatchHit(Base):
    __tablename__ = "signal_watch_hits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    stock_id = Column(String, nullable=False, index=True)
    stock_name = Column(String, nullable=False)
    signal_type = Column(String(16), nullable=False)          # LEADER | FOLLOWER | LAGGARD
    industry_name = Column(String, nullable=True)
    sub_industry = Column(String, nullable=True)

    business_summary = Column(Text, nullable=True)
    reason = Column(Text, nullable=False)                     # watchlist.reason

    theme = Column(JSON, nullable=False, default={})
    group_info = Column(JSON, nullable=False, default={})
    leader_check = Column(JSON, nullable=False, default={})
    signals = Column(JSON, nullable=False, default={})

    snapshot_generated_at = Column(DateTime, nullable=True)
    job_id = Column(String(36), ForeignKey("signal_generation_jobs.job_id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "stock_id", name="uq_signal_watch_hit_date_stock"),
    )
```

### 4.2 為什麼不用直接讀 `signal_snapshots.watchlist`

原因：

1. `signal_snapshots` 是整包 JSON，做 stock-level 聚合與排序成本較高
2. 同日重產時，需要精準覆蓋 `(snapshot_date, stock_id)` 命中集合
3. 報告時間軸、hit count、retention 清理，都更適合 normalized table

### 4.3 `signal_watch_completed_archives`

當某檔股票完成一個 40 交易日追蹤 cycle 後，額外寫入一張 completed archive table：

- `stock_id`
- `stock_name`
- `industry_name`
- `sub_industry`
- `first_seen_date`
- `latest_hit_date`
- `hit_count`
- `latest_signal_type`
- `baseline_trade_date`
- `baseline_price`
- `return_day_10_pct`
- `return_day_20_pct`
- `return_day_30_pct`
- `completed_trade_date`

同一檔股票如果在之後某個時間點已經移出舊 cycle，且未來重新被抓到，應以新的 `first_seen_date` 再新增一筆 completed archive row，而不是覆蓋舊 row。

---

## 5. 寫入流程

在既有 M23 pipeline `Persist Snapshot` 後，新增一個 archive persist step。

### 5.1 寫入順序

1. `signal_snapshots` UPSERT 完成
2. 清除 `signal_watch_hits` 中 `snapshot_date = target_date` 的舊資料
3. 逐筆寫入該日 `payload.watchlist`
4. 寫入完後執行 retention：只保留最近 40 個 distinct `snapshot_date`

### 5.2 冪等性

- 同一 job retry 時結果應可重跑，不可重複插入
- `(snapshot_date, stock_id)` unique constraint 保證單日單股只有一筆

### 5.3 寫入來源欄位對應

| `signal_watch_hits` | 來源 |
|---|---|
| `snapshot_date` | pipeline `target_date` |
| `stock_id` | `watchlist[].stock` |
| `stock_name` | `watchlist[].name` |
| `signal_type` | `watchlist[].type` |
| `industry_name` | `watchlist[].industry` |
| `sub_industry` | `watchlist[].sub_industry` |
| `business_summary` | `watchlist[].business_summary` |
| `reason` | `watchlist[].reason` |
| `theme` | `watchlist[].theme` |
| `group_info` | `watchlist[].group_info` |
| `leader_check` | `watchlist[].leader_check` |
| `signals` | `watchlist[].signals` |
| `snapshot_generated_at` | `SignalSnapshot.generated_at` |
| `job_id` | pipeline `job_id` |

---

## 6. 查詢模型

前端列表頁顯示的是 **aggregate summary**，不是單筆 hit raw rows。

### 6.1 Summary row 欄位

每列至少包含：

- `stock_id`
- `stock_name`
- `industry_name`
- `sub_industry`
- `first_seen_date`
- `latest_hit_date`
- `tracking_day_index`
- `hit_count`
- `latest_signal_type`
- `return_pct`
- `baseline_trade_date`
- `baseline_price`
- `latest_eval_trade_date`
- `latest_eval_price`

### 6.2 欄位語義

#### `first_seen_date`
- 該股在最近 40 交易日追蹤窗口中第一次被抓到的 `snapshot_date`

#### `latest_hit_date`
- 該股最近一次被抓到的 `snapshot_date`

#### `tracking_day_index`
- 從 `first_seen_date` 到目前評估日之間，經過了第幾個交易日
- 以「交易日」計，不是 calendar day
- 例：4/24 首次抓到，4/29 為最新評估日，中間交易日為 4/24、4/25、4/28、4/29，則顯示第 4 天

#### `hit_count`
- 在最近 40 交易日窗口內，該股被抓到的次數

#### `latest_signal_type`
- 最近一次被抓到時的類型（LEADER / FOLLOWER / LAGGARD）

---

## 7. 報酬率規則

### 7.1 核心原則

報酬率不是用「訊號當天收盤」當基準，而是：

1. **訊號當天**
   - 顯示 `--`
2. **下一個交易日的 19:00 後**
   - 用該日 `(open_price + close_price) / 2` 作為基準價
   - 若此時最新可用評估日就是基準日，報酬率顯示 `0.00%`
3. **從基準日的下一個交易日開始**
   - 用每個交易日的當天 `close_price` 與基準價比較

### 7.2 正式定義

對某檔股票：

- `first_seen_date` = 最近 40 交易日窗口內首次命中的 `snapshot_date`
- `baseline_trade_date` = `first_seen_date` 之後的下一個交易日
- `baseline_price` = `(open_price + close_price) / 2` on `baseline_trade_date`
- `as_of_trade_date` = 頁面載入當下依 signals 同一套規則解析出的最新可評估交易日：
  - 若當日有交易且台北時間 >= 19:00，可用今天
  - 否則回退到前一個可用交易日
- `eval_price`：
  - 若 `as_of_trade_date < baseline_trade_date` → `null`
  - 若 `as_of_trade_date == baseline_trade_date` → `baseline_price`
  - 若 `as_of_trade_date > baseline_trade_date` → `close_price(as_of_trade_date)`

`return_pct = (eval_price - baseline_price) / baseline_price * 100`

### 7.3 缺資料處理

以下任一情況，`return_pct = null`，前端顯示 `--`：

1. 還沒有下一個交易日
2. 基準日缺 `open_price` 或 `close_price`
3. `eval_price` 缺資料

---

## 8. API 設計

### 8.1 `GET /api/signals/archive`

用途：取得 40 交易日追蹤清單 summary。

Query params：

- `sort_by`：
  - `tracking_days_desc`（預設）
  - `return_desc`
  - `return_asc`
  - `hit_count_desc`
  - `latest_hit_desc`
  - `stock_id_asc`
- `type`（optional）：`LEADER | FOLLOWER | LAGGARD`
- `limit`（optional，預設 200）

Response:

```json
{
  "as_of_trade_date": "2026-04-29",
  "retention_trade_days": 40,
  "items": [
    {
      "stock_id": "2330",
      "stock_name": "台積電",
      "industry_name": "半導體業",
      "sub_industry": "晶圓代工",
      "first_seen_date": "2026-04-24",
      "latest_hit_date": "2026-04-29",
      "tracking_day_index": 4,
      "hit_count": 3,
      "latest_signal_type": "LEADER",
      "baseline_trade_date": "2026-04-25",
      "baseline_price": 881.5,
      "latest_eval_trade_date": "2026-04-29",
      "latest_eval_price": 892.0,
      "return_pct": 1.19
    }
  ]
}
```

### 8.2 `GET /api/signals/archive/{stock_id}`

用途：取得單一股票追蹤詳情與報告時間軸。

Response:

```json
{
  "stock_id": "2330",
  "stock_name": "台積電",
  "industry_name": "半導體業",
  "sub_industry": "晶圓代工",
  "first_seen_date": "2026-04-24",
  "latest_hit_date": "2026-04-29",
  "tracking_day_index": 4,
  "hit_count": 3,
  "return_pct": 1.19,
  "reports": [
    {
      "snapshot_date": "2026-04-29",
      "signal_type": "LEADER",
      "reason": "..."
    },
    {
      "snapshot_date": "2026-04-28",
      "signal_type": "FOLLOWER",
      "reason": "..."
    }
  ]
}
```

---

## 9. 前端設計

### 9.1 入口位置

入口放在首頁 `DailySignalsPanel` header，和「重新產生」同一排，命名：

- `40日追蹤`

原因：

1. 與訊號來源最貼近
2. 使用者剛看完當日訊號時，最容易想追「這檔最近出現幾次」
3. 不必污染全域 Navbar

### 9.2 頁面路由

- `GET /signals/archive`（Next.js page）

### 9.3 列表欄位

欄位顯示：

1. 股票基本資料
2. 首次抓到日期
3. 最近一次抓到日期
4. 目前追蹤第幾天
5. 在這幾天中被抓到幾次
6. 報酬率
7. `看 K 線圖`
8. `看報告`

### 9.4 報酬率樣式

沿用台股習慣：

- 正報酬：紅字
- 負報酬：綠字
- `null`：灰色 `--`

### 9.5 排序

預設排序：

- `tracking_day_index DESC`

使用者可切換：

- 依報酬率
- 依命中次數
- 依最近抓到日期
- 依股票代號

### 9.6 報告檢視

點 `看報告` 後：

- 以 drawer 或 modal 顯示單股詳情
- 依 `snapshot_date DESC` 排列
- 每筆顯示：
  - 日期
  - 類型
  - 當天 reason

---

## 10. 實作備註

### 10.1 與既有 signals target date 對齊

archive 報酬率的 `as_of_trade_date` 必須與 `signals` 現有規則一致：

- 有開盤日且 19:00 後 → 可用當日
- 其他情況 → 用前一個可用交易日

不可另寫一套日期規則，避免頁面間報酬率口徑不一致。

### 10.2 首次抓到當天不顯示報酬

這是產品需求，不是缺資料 bug。

前端需明確顯示 `--`，不要顯示 `0.00%`。

### 10.3 pipeline fail 不得污染 archive

若 job `failed`：

- 不寫入 `signal_watch_hits`
- 不刪除當日既有 hits
- 不執行 retention prune

避免同日重產失敗時把原本成功資料清掉。

---

## 11. 交付順序

1. schema + model
2. pipeline persist hook
3. archive summary/detail API
4. frontend `/signals/archive` 列表頁
5. `DailySignalsPanel` header 入口
6. tests（router / pipeline / return calculation / sorting）
