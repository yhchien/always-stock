# M23 每日異常訊號清單 Spec

> **Canonical spec**：若 README、CLAUDE.md、memory 與本文件衝突，以本文件為準。
> 2026-04-25 定案，對應 milestone M23。
> 對應 LLM prompt：[backend/app/prompts/watch-list-stock.md](../../backend/app/prompts/watch-list-stock.md)

---

## 1. 需求總覽

每天台北凌晨 **03:00** 自動執行，產出「今日值得關注的台股清單」放在 L0 首頁的 **tab bar**。

- 不是報酬預測系統 / 不給目標價 / 不出 BUY / SELL 建議
- 只輸出 **WATCH / REMOVE**，每檔附 500–1000 字繁體中文 reason
- 同時涵蓋 **LEADER / FOLLOWER / LAGGARD** 三類，找熱錢主線正在擴散到哪裡
- 排除 **ETF / 金融股**

### 1.1 與舊版規劃的差異

| 項目 | 舊版（2026-04-24） | 新版（2026-04-25） |
|------|------------------|-------------------|
| 訊號分類 | 4 組（chip_bullish / margin_rotation / technical_breakout / industry_leader） | 3 類（LEADER / FOLLOWER / LAGGARD） |
| LLM 角色 | 翻譯 deterministic 訊號為中文 | 翻譯 + **上網查詢公司業務 / 題材 / 集團 / 龍頭比對** |
| Candidate pool | 純法人排行 Top N | Top N + **同產業擴散 + 集團股 + 同供應鏈 + 龍頭股** |
| Market gating | 無 | `STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK` 影響策略偏好 |
| 排程 | 07:00 | **03:00** |
| 觸發方式 | 僅排程 | 排程 + **使用者 L0 手動觸發**（背景多工） |
| 呈現 | 首頁新區塊 | L0 **tab bar**（pulse badge 通知 + 多工進度條） |

### 1.2 設計原則

1. **DB / 程式負責 deterministic filter**：候選池建立、Leader/Follower/Laggard 分類、籌碼判斷、技術面初篩
2. **LLM 負責外部資訊補齊與中文解釋**：market_state 判斷、產業題材延續性查詢、公司業務驗證、500–1000 字 reason
3. **拔掉 LLM 系統還能跑**：deterministic pipeline 仍能輸出 candidate pool + 籌碼分類；LLM 只是補上人類可讀的解釋與外部 context

---

## 2. 整體系統架構

```
┌──────────────────────────────────────────────────────────────────┐
│ 觸發層                                                              │
│  • GitHub Actions Cron（台北 03:00 週二~週六）                      │
│  • POST /api/signals/regenerate（L0 手動觸發，背景多工）            │
└──────────────────────────────────────────────────────────────────┘
                          │ 寫入 signal_generation_jobs（status=running）
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Pipeline（backend/app/signals/）                                   │
│ Step 1  Data Ingestion       讀 inst_stock_flow / daily_price /    │
│                              industry_daily_flow / monthly_revenue │
│                              / margin_trade（新增表）              │
│ Step 2  Industry Flow Rank   top_industries_3d（10 筆）            │
│ Step 3  Stock Flow Rank      top_stocks_3d（40 筆）                │
│ Step 4  Candidate Pool Build 套用 §6 規則組合候選池                │
│ Step 5  Peer / Group Expand  同產業 / 同供應鏈 / 同集團擴散         │
│ Step 6  Deterministic Filter 排除 ETF / 金融 / 籌碼轉弱 / 技術盤整  │
│         + 預分類 (LEADER / FOLLOWER / LAGGARD candidate)           │
│ Step 7  LLM Research Layer   一檔股票一次，prompt 引導上網查業務／  │
│                              產業鏈／集團／題材延續性                │
│ Step 8  LLM Explanation      500–1000 字 reason + WATCH / REMOVE   │
│         + market_state 判斷                                        │
│ Step 9  Persist Snapshot     寫 signal_snapshots（一日一筆）       │
│ Step 10 Update Job Status    job.status = done / failed            │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ 讀取層                                                              │
│  • GET /api/signals/latest                  最新 snapshot            │
│  • GET /api/signals/snapshot/{date}         歷史 snapshot            │
│  • GET /api/signals/jobs/latest             最新 job 進度            │
│  • POST /api/signals/regenerate             觸發背景重產（限頻）      │
└──────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                L0 首頁 tab bar（pulse badge + 進度條）
```

### 2.1 為什麼一天一份共享 snapshot 而不是 per-user

- LLM 呼叫成本：一次跑 30~60 檔候選股 × 500-1000 字 reason，估 OpenAI cost > $1 USD
- 使用者重複觸發會 burn cost 又重複算同一天的市場狀態
- **每日全站共享一份**：排程自動跑一次，使用者手動「重新產生」需 admin 權限或全站 cooldown（見 §11.4）

---

## 3. 資料來源設計

### 3.1 DB 提供的資料（deterministic）

| 來源 | 用途 | 新增？ |
|------|------|--------|
| `inst_stock_flow` | 個股三大法人 1d / 3d / 5d 累計 net_amount | 既有 |
| `industry_daily_flow` | 產業 3d 熱錢排行 | 既有 |
| `daily_price` | OHLC、漲跌幅、成交量、量比、5/10/20MA、突破檢測 | 既有 |
| `stocks_master` | industry / sub_industry / is_etf 判斷 | 既有 |
| `monthly_revenue` | 最近月營收 + YoY / MoM | 既有 |
| **`margin_trade`** | **融資融券每日餘額與變化** | **新增（見 §3.3）** |

#### 3.1.1 ETF / 金融股判斷

- ETF：`stock_id` 開頭符合 `^00\d{2,}` 或 `stocks_master.stock_name LIKE '%ETF%'`
- 金融股：`stocks_master.industry_name LIKE '%金融%'` OR `industry_name == '金融保險業'`
- 同時維護 `backend/app/signals/exclusions.py` 黑名單（金控旗下、複委託 ETF 等邊界 case）

### 3.2 LLM 上網查詢的資料（context）

由 LLM 在 Step 7 / Step 8 自行查詢（傳給 LLM 的 prompt 已要求查詢，見 watch-list-stock.md STEP 0 / STEP 2 / STEP 3 / STEP 4）：

- **market_state**：昨日加權 / 櫃買 / VIX / 美股 / 台指期 / USD-TWD
- **產業題材延續性**：近 2 週~1 個月新聞、報價、法說、政策、AI / 庫存週期
- **公司業務驗證**：實際主要產品、營收來源、產業鏈位置、是否真的受惠主線（不只是名字相關）
- **集團 / 龍頭股**：所屬集團、同集團其他上市櫃股票、產業 leader 是誰、leader 近 5–10 日表現

> **沒有 web search 工具的 fallback**：若部署模型不支援 web search（純 chat completion），LLM 仍可以用「訓練資料 + DB 提供的數字」推論；reason 中需註記「無外部即時資訊驗證」並降低 confidence。第一版以 `gpt-4o-search-preview` 或同等支援 web search 的模型為主。

### 3.3 新增 `margin_trade` 表

```python
class MarginTrade(Base):
    __tablename__ = "margin_trade"
    trade_date = Column(Date, primary_key=True)
    stock_id = Column(String(16), primary_key=True)
    margin_balance = Column(BigInteger)       # 融資餘額（張）
    margin_change = Column(BigInteger)        # 當日融資變化
    short_balance = Column(BigInteger)        # 融券餘額（張）
    short_change = Column(BigInteger)         # 當日融券變化
    source = Column(String(16), default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)
```

- ETL 模組：`backend/etl/finmind_margin_trade_sdk.py`
- 資料源：FinMind `TaiwanStockMarginPurchaseShortSale`
- 併入 `run_finmind_etl_sdk.py` 第 7 步（在 broker_trade_agg 之後）
- Backfill：3 年（M23 只用近 5 日窗口，3 年足夠且 FinMind 配額負擔小）

---

## 4. DB Schema：snapshot + job

### 4.1 `signal_snapshots`

一天一筆，存完整 LLM output JSON。

```python
class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, unique=True, index=True)
    market_context = Column(JSON, nullable=False)   # market_state / VIX / 加權 etc.
    watchlist = Column(JSON, nullable=False)        # List[StockSignal]
    removed = Column(JSON, nullable=False)          # List[RemovedItem]
    summary = Column(JSON, nullable=False)          # leader_count / follower_count / etc.
    candidate_pool_size = Column(Integer)           # filter 前候選數
    final_watchlist_size = Column(Integer)          # filter 後 WATCH 數
    llm_model = Column(String(64))                  # e.g. gpt-4o-search-preview
    llm_total_tokens = Column(Integer)              # cost tracking
    generated_at = Column(DateTime, default=datetime.utcnow)
    job_id = Column(String(36), ForeignKey("signal_generation_jobs.job_id"))
```

- `unique(snapshot_date)`：同一天只保留最新一份。重新產生會 UPSERT（覆蓋）
- 歷史保留：所有日期都保留，給之後評估 filter 與 LLM 註解品質
- `watchlist` / `removed` JSON 結構嚴格對齊 §10.2 的 Output Schema

### 4.2 `signal_generation_jobs`

每次觸發（排程 / 手動）建一筆，給前端進度條讀。

```python
class SignalGenerationJob(Base):
    __tablename__ = "signal_generation_jobs"
    job_id = Column(String(36), primary_key=True)              # uuid4
    snapshot_date = Column(Date, nullable=False, index=True)   # 目標日期
    triggered_by = Column(String(64))                          # "cron" | "user:{id}" | "admin:{id}"
    status = Column(String(16), nullable=False)                # pending | running | done | failed
    current_stage = Column(String(64))                         # ingest | rank | candidate | filter | llm_research | llm_explain | persist
    progress_pct = Column(Integer, default=0)                  # 0~100
    progress_label = Column(String(255))                       # "正在分析第 12 / 45 檔"
    error_message = Column(Text)                               # 失敗時填
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
```

- **單一活躍 job 約束**：同一個 `snapshot_date` 不允許兩個 `running` job 並存，避免重複燒 LLM cost。Insert 前 SELECT lock 檢查（見 §11.4）
- 失敗時 `status = failed`、`error_message` 寫 traceback 摘要
- 保留 30 天歷史（可加 retention job）

---

## 5. 每日執行流程（step-by-step）

對應 watch-list-stock.md prompt 的 STEP 0–9。

### Step 0：取得市場環境（DB + LLM）
- **DB 先把昨日 / 近 5 日加權 / 櫃買收盤從 DB 讀出**（避免 LLM hallucinate 數字）
- LLM 上網查 VIX / 美股 / 台指期 / USD-TWD，組合判斷 `market_state`

### Step 1：讀 DB 熱錢產業前 10
```sql
SELECT industry_name, SUM(total_net_amount) AS net_3d
FROM industry_daily_flow
WHERE trade_date IN (last_3_trade_dates)
GROUP BY industry_name
ORDER BY net_3d DESC
LIMIT 10
```

### Step 2：讀 DB 熱錢個股前 40
- 沿用 `compute_hot_money(end_date, days=3, limit=40)`（M22 service）

### Step 3：建立初始候選池
- 把 Step 1 所有產業的成分股 + Step 2 的 Top 40 合併

### Step 4：候選池擴散
- 加入熱門產業的 **龍頭股**（產業內近 60 日成交量 / 市值 Top 1）
- 加入 Step 2 Top 10 個股的 **同集團股票**（需建立 `group_stocks` 對照表，第一版可從 watch-list-stock.md group_info 反推 + 維護白名單）
- 加入熱門產業的 **同供應鏈股票**（用 `stocks_master.sub_industry` 同 sub 的股票）

### Step 5：Leader / Follower / Laggard 預分類
詳細規則見 §7。

### Step 6：deterministic filter 排除
詳細規則見 §9。

### Step 7：LLM Research Layer
- 對 filter 後的每檔股票，呼叫 LLM（**支援 web search 的模型**），帶 §10.1 input JSON
- LLM 上網查公司業務、產業鏈位置、題材延續性、龍頭股 / 集團股表現
- 為了控制 cost / 時間：**一次 prompt 處理 5~10 檔（batch）**，而不是一檔一個 API call

### Step 8：LLM Explanation Layer
- 每檔產出 500–1000 字 reason、theme / theme_fit / signals / decision (WATCH / REMOVE)
- LLM 也輸出 market_context（market_state + market_state_reason）

### Step 9：寫 signal_snapshots（UPSERT by snapshot_date）

### Step 10：更新 signal_generation_jobs.status = done

---

## 6. Candidate Pool 設計

```
candidate_pool = unique(
    top_stocks_3d 前 40
  + top_industries_3d 前 10 的所有成分股
  + top_stocks_3d 前 10（即使不在熱門產業）
  + 每個熱門產業的龍頭股（成交量 60 日 Top 1）
  + 每個熱門產業的同供應鏈股票（sub_industry 同）
  + 每個 leader 的同集團股票（從 group_stocks 表）
  + 每個 leader 的可能 laggard（同產業、漲幅落後 leader 但籌碼開始轉強）
) - exclusions（ETF + 金融股）
```

### 6.1 期望規模
- 候選池目標：**60–120 檔**（過大 LLM cost 失控、過小可能漏掉 laggard）
- 若超過 150 檔，按「總法人 net_amount_3d 降冪」截斷至 120 檔

### 6.2 group_stocks 對照表

第一版**輕量處理**：
- 不建獨立 DB 表
- 維護一份 `backend/app/signals/group_stocks.json`（手動列大集團，例如鴻海集團 / 國巨集團 / 台塑集團）
- 之後若覺得不夠，再從公開資料（公開資訊觀測站轉投資 / FinMind？）補

---

## 7. Leader / Follower / Laggard 分類規則（deterministic）

### 7.1 LEADER 條件（**全部滿足**）
- 該產業中漲幅 5d 排名前 30%
- net_amount_3d 在該產業內排名前 20%
- 三大法人合計近 3 日連買（至少 2 / 3 天 net_amount > 0）
- 5d volume / 60d_avg_volume >= 1.5
- 無 LLM 即可判：deterministic

### 7.2 FOLLOWER 條件（**全部滿足**）
- 與 LEADER 同產業 OR 同 sub_industry
- price_change_5d > 0 但 < LEADER price_change_5d × 0.7
- 三大法人合計近 3 日 net_amount > 0
- 排除 LEADER 本身

### 7.3 LAGGARD 候選條件（**滿足以下 >=2 條**）
- 同產業有 LEADER 已漲（leader.price_change_5d >= 5%）
- 該股 price_change_5d 落後 LEADER 至少 5 個百分點
- 近 3 日法人或成交量開始轉強（net_amount_1d > 0 OR volume_1d / 5d_avg > 1.2）
- 技術面 early_turn（突破 5MA OR 站回 10MA）
- 公司業務題材相關（**這條由 LLM 在 Step 7 驗證**，deterministic 階段先標 candidate，最終由 LLM 確認）

### 7.4 標籤輸出
- 每檔候選股得到一個 `prelim_type ∈ {LEADER, FOLLOWER, LAGGARD_CANDIDATE}`
- LLM 在 Step 8 可以 override（例如 LAGGARD_CANDIDATE 業務不符 → REMOVE）

---

## 8. market_state Gating

由 LLM 在 Step 0 判斷 `market_state ∈ {STRONG_BULL, STRUCTURAL_BULL, RANGE, WEAK}`，影響 Step 8 LLM 過濾偏好（regulator function 寫進 prompt，已對齊 watch-list-stock.md STEP 8）：

| market_state | 策略偏好 |
|--------------|---------|
| STRONG_BULL | LEADER / FOLLOWER / LAGGARD 都可 WATCH |
| STRUCTURAL_BULL | 優先 LEADER 與高相關 LAGGARD；排除題材弱的 FOLLOWER |
| RANGE | 優先 LAGGARD；避免追高已急漲 LEADER |
| WEAK | 只保留 theme_fit=HIGH + chip_trend=accumulating + technical_status ∈ {breakout, steady_uptrend} |

---

## 9. Filter 排除規則

### 9.1 Hard Exclusions（deterministic, Step 6）
1. ETF（規則見 §3.1.1）
2. 金融股
3. 三大法人合計近 5 日 net_amount < 0 且無 LAGGARD 條件
4. price_change_3d > 15%（過熱避免追高）
5. 流動性不足（5d 平均成交金額 < 5,000 萬 TWD）

### 9.2 Soft Filters（deterministic + LLM 雙層）
- 前三日大買但昨日大賣（net_amount_3d > 0 且 net_amount_1d < -net_amount_3d × 0.5）→ 標 weakening，LLM 評估後可能 REMOVE
- 融資暴增但法人未買（margin_change_3d > +5% 且 net_amount_3d <= 0）→ retail_overheated
- 爆量不漲（volume_1d / 60d_avg > 2 且 price_change_1d <= 0）→ distribution
- 高檔長上影（high - close > (close - open) × 2 且 close < high × 0.97）→ distribution
- 盤整無突破（10d 高低差 < 5%）→ range_bound

### 9.3 LLM 排除（Step 8）
- theme_fit = LOW / NONE
- 公司業務與熱門題材不符（LLM 上網查證後決定）
- 短線消息股（題材延續性 < 1Q）

---

## 10. Input / Output JSON Schema

完全對齊 [backend/app/prompts/watch-list-stock.md](../../backend/app/prompts/watch-list-stock.md)。

### 10.1 LLM Input Schema

```jsonc
{
  "date": "YYYY-MM-DD",
  "top_industries_3d": [
    {
      "industry": "半導體業",
      "sub_industry": "IC 設計",
      "rank": 1,
      "net_flow": 12000000000,
      "net_flow_unit": "TWD",
      "stock_count": 35
    }
  ],
  "top_stocks_3d": [
    {
      "stock": "2330",
      "name": "台積電",
      "industry": "半導體業",
      "sub_industry": "IC 製造",
      "rank": 1,
      "net_flow": 5000000000,
      "net_flow_unit": "TWD",
      "price_change_3d": 4.5
    }
  ],
  "stock_pool": [
    {
      "stock": "2330",
      "name": "台積電",
      "industry": "半導體業",
      "sub_industry": "IC 製造",
      "is_etf": false,
      "is_financial": false,
      "price_change_1d": 1.5,
      "price_change_3d": 4.5,
      "price_change_5d": 6.2,
      "price_change_10d": 8.1,
      "volume_change_3d": 1.35,
      "volume_change_5d": 1.20,
      "foreign_flow_1d": 1200000000,
      "foreign_flow_3d": 4000000000,
      "investment_trust_flow_1d": 300000000,
      "investment_trust_flow_3d": 800000000,
      "dealer_flow_1d": -100000000,
      "dealer_flow_3d": 200000000,
      "total_institution_flow_1d": 1400000000,
      "total_institution_flow_3d": 5000000000,
      "margin_change_1d": 0.012,
      "margin_change_3d": 0.045,
      "short_change_1d": -0.005,
      "short_change_3d": -0.020,
      "group_name": null,
      "peer_group": ["2317", "2454"]
    }
  ]
}
```

### 10.2 LLM Output Schema

```jsonc
{
  "date": "YYYY-MM-DD",
  "market_context": {
    "market_state": "STRUCTURAL_BULL",
    "taiex_change_pct": 0.8,
    "otc_change_pct": -0.2,
    "vix_status": "neutral",
    "futures_bias": "LONG",
    "market_state_reason": "加權收盤 +0.8%、櫃買 -0.2%，主升集中權值；VIX 小升但仍在 16 以下，期貨偏多。"
  },
  "watchlist": [
    {
      "stock": "2330",
      "name": "台積電",
      "type": "LEADER",
      "industry": "半導體業",
      "sub_industry": "IC 製造",
      "business_summary": "全球最大晶圓代工廠……",
      "supply_chain_position": "midstream",
      "theme_fit": "HIGH",
      "theme": {
        "main_theme": "AI 加速器代工",
        "theme_duration": "2Q_plus",
        "theme_score": 3,
        "theme_reason": "Blackwell 量產持續，CoWoS 供需吃緊"
      },
      "group_info": {
        "is_group_stock": false,
        "group_name": null,
        "related_group_stocks": [],
        "group_price_sync": "none"
      },
      "leader_check": {
        "industry_leader": "2330 台積電",
        "leader_price_trend": "strong_up",
        "leader_supports_theme": true
      },
      "signals": {
        "capital_flow": "strong",
        "chip_trend": "accumulating",
        "margin_short_signal": "neutral",
        "technical_status": "steady_uptrend"
      },
      "decision": "WATCH",
      "reason": "（500–1000 字繁體中文，包含 13 點強制要點，見 watch-list-stock.md「reason 寫作規則」）"
    }
  ],
  "removed": [
    {
      "stock": "2603",
      "name": "長榮",
      "remove_reason": "貨櫃航運近期題材延續性 < 1Q；融資 3d +12% 散戶過熱、法人未承接，符合 retail_overheated。"
    }
  ],
  "summary": {
    "main_hot_industries": ["半導體業", "AI 伺服器", "光通訊"],
    "leader_count": 5,
    "follower_count": 8,
    "laggard_count": 4,
    "risk_note": "VIX 小升、櫃買偏弱，注意短線追高風險。"
  }
}
```

### 10.3 對外 API Response Schema（包 snapshot meta）

```jsonc
{
  "snapshot_date": "2026-04-25",
  "generated_at": "2026-04-25T03:12:45+08:00",
  "llm_model": "gpt-4o-search-preview",
  "data": { /* 上面的 LLM Output JSON */ }
}
```

---

## 11. Backend API

### 11.1 `GET /api/signals/latest`
- 取最新一筆 `signal_snapshots`
- 權限：**公開**（與 `/api/market/hot-money` 同層級）
- Response: §10.3
- 若 DB 無任何 snapshot → `404 No snapshot yet`

### 11.2 `GET /api/signals/snapshot/{snapshot_date}`
- 取指定日期 snapshot
- 權限：公開
- Response: §10.3

### 11.3 `GET /api/signals/jobs/latest`
- 取最新 job（不論 status），給前端進度條 polling
- 權限：公開
- Response:
```jsonc
{
  "job_id": "uuid",
  "snapshot_date": "2026-04-25",
  "status": "running",
  "current_stage": "llm_explain",
  "progress_pct": 65,
  "progress_label": "正在分析第 28 / 45 檔",
  "started_at": "2026-04-25T03:00:01+08:00",
  "finished_at": null,
  "error_message": null
}
```

### 11.4 `POST /api/signals/regenerate`
- 觸發背景重新產生
- 權限：**登入即可**，但有以下限制：
  - **同一個 `snapshot_date` 已有 `running` job → 回 `409 Conflict`**（前端引導讀進度而不是再觸發）
  - **同一日 user 限頻 10 次** → 回 `429 Too Many Requests`（rate limit by user_id + snapshot_date，2026-04-27 從 1 → 10 放寬）
  - **全站同一日累計 10 次** → 回 `429`（避免成本失控，2026-04-27 從 5 → 10 放寬）
- 行為：
  1. 建立 `SignalGenerationJob`（status=pending, triggered_by=`user:{id}`）
  2. 啟動 `BackgroundTasks` 跑 pipeline
  3. **立刻回 `202 Accepted`** + `{ "job_id": "...", "snapshot_date": "..." }`
- 失敗：背景 task 內 catch exception → `job.status=failed`、`error_message=traceback`

### 11.5 背景任務實作

- 使用 FastAPI `BackgroundTasks` + `asyncio.create_task`
- Pipeline runner：`backend/app/signals/pipeline.py::run_signal_pipeline(job_id, snapshot_date, db_session_factory)`
- **重要**：不能用 request session（請求結束會 close）；要用 `SessionLocal()` 自建一個獨立 session
- 每個 stage 結束後 update `job.current_stage / progress_pct / progress_label`
- LLM batch（5~10 檔一次）後也 update progress
- 結束時：
  - 成功 → `job.status=done, finished_at=now()`，UPSERT `signal_snapshots`
  - 失敗 → `job.status=failed, error_message=traceback[:2000]`

### 11.6 排程觸發 entrypoint

`backend/run_daily_signals.py`：

```python
"""每日異常訊號清單排程入口（GitHub Actions 03:00 台北）"""
import sys, asyncio, uuid
from datetime import date, timedelta
from app.database import SessionLocal
from app.signals.pipeline import run_signal_pipeline_sync

def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else _resolve_target_date()
    job_id = str(uuid.uuid4())
    with SessionLocal() as db:
        # 建 job 紀錄（triggered_by="cron"）
        ...
    # Inline run（cron 環境不需要 background task，直接跑完 exit）
    run_signal_pipeline_sync(job_id, target_date)

def _resolve_target_date() -> date:
    # 03:00 台北跑時，target = 「昨天」（昨天的收盤資料）
    # 用 4h offset：03:00 - 4h = 23:00 → date 給昨天
    import datetime as dt, zoneinfo
    now_tpe = dt.datetime.now(zoneinfo.ZoneInfo("Asia/Taipei"))
    return (now_tpe - dt.timedelta(hours=4)).date()
```

Exit code：`0=ok / 1=no_data / 2=llm_error / 3=db_error`

---

## 12. 排程（GitHub Actions）

### 12.1 新增 `.github/workflows/daily_signals.yml`

```yaml
name: Daily Signals Generation

on:
  schedule:
    # 台北 03:00 週二~週六（即週一~週五收盤後的隔天凌晨）
    # UTC = 03:00 - 8h = 19:00（前一日）
    # 所以 cron 寫 `0 19 * * 1-5` → 週一~週五 UTC 19:00 → 週二~週六 台北 03:00
    - cron: '0 19 * * 1-5'
  workflow_dispatch:
    inputs:
      target_date:
        description: '目標分析日期 (YYYY-MM-DD)，預設為昨天'
        required: false

jobs:
  run_signals:
    runs-on: ubuntu-latest
    timeout-minutes: 90  # LLM 跑 60~120 檔大概 30~60 min

    env:
      TZ: Asia/Taipei

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install --quiet -r backend/requirements.txt

      - name: Resolve target date
        id: resolve
        env:
          INPUT_DATE: ${{ github.event.inputs.target_date }}
        run: |
          if [ -n "$INPUT_DATE" ]; then
            TARGET_DATE="$INPUT_DATE"
          else
            # 03:00 跑往前推 4h = 前一日 23:00 → date 給前一日（昨日收盤資料）
            TARGET_DATE=$(date -d '4 hours ago' +%F)
          fi
          echo "target_date=$TARGET_DATE" >> "$GITHUB_OUTPUT"

      - name: Run signal pipeline
        working-directory: backend
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENAI_MODEL: ${{ secrets.OPENAI_SIGNALS_MODEL || 'gpt-4o-search-preview' }}
        run: python3 run_daily_signals.py ${{ steps.resolve.outputs.target_date }}
```

### 12.2 排程關鍵 gotcha

- GitHub Actions cron 常延遲 10~90 分鐘 → `4h offset` 確保即使延遲到 04:00 仍 resolve 為昨日
- ETL workflow 已經在台北 23:00 跑（cron `0 15 * * 1-5`），signals 03:00 跑時昨日 ETL 已完成（給足 4 小時 buffer）
- 排程跑時先 SELECT job 檢查是否已有 `running` 同日 job（理論上不會，但避免人工觸發 + cron 撞期）

---

## 13. 前端 L0 Tab Bar UX

### 13.1 整體版位調整

L0 首頁 ([frontend/src/app/page.tsx](../../frontend/src/app/page.tsx)) 目前順序：
1. TradeQualityAnalysis
2. HotMoneyList
3. IndustryDashboard

**新版**改成：
1. TradeQualityAnalysis（不動）
2. **`<DailySignalsPanel />`（新增 tab bar，預設 collapse 不展開）**
3. HotMoneyList
4. IndustryDashboard

### 13.2 `DailySignalsPanel` 結構

```tsx
<section className="rounded-lg border border-zinc-700 bg-zinc-700/50">
  <header className="flex items-center justify-between px-4 py-3">
    <h2 className="text-base font-semibold text-slate-100">
      今日異常訊號清單
      {hasNewSignals && (
        <span className="ml-2 inline-flex items-center gap-1">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          <span className="text-xs text-emerald-400">新</span>
        </span>
      )}
    </h2>
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-400">
        產生於 {formatTpeDateTime(snapshot.generated_at)}
      </span>
      <button onClick={handleRegenerate} disabled={!canRegenerate}>
        重新產生
      </button>
    </div>
  </header>

  {jobStatus === "running" && (
    <ProgressBar
      pct={job.progress_pct}
      label={job.progress_label}
    />
  )}

  <Tabs defaultValue="leader">
    <TabsList>
      <TabsTrigger value="leader">LEADER ({summary.leader_count})</TabsTrigger>
      <TabsTrigger value="follower">FOLLOWER ({summary.follower_count})</TabsTrigger>
      <TabsTrigger value="laggard">LAGGARD ({summary.laggard_count})</TabsTrigger>
      <TabsTrigger value="removed">REMOVED ({removed.length})</TabsTrigger>
    </TabsList>
    <TabsContent value="leader">
      <SignalCardList items={watchlist.filter(s => s.type === "LEADER")} />
    </TabsContent>
    {/* ... */}
  </Tabs>
</section>
```

### 13.3 跳跳跳（pulse）通知邏輯

- localStorage key：`always-stock:signals:last_seen_snapshot_date`
- mount 時 fetch `/api/signals/latest`，比對 `snapshot_date > last_seen_date` → `hasNewSignals = true`
- 使用者展開 tab（任一 tab 被點擊）→ 寫入當前 snapshot_date 到 localStorage → `hasNewSignals = false`
- 動畫：Tailwind `animate-ping` + 綠點（範例見 §13.2 程式碼）

### 13.4 多工進度條 + 離開頁面後回來繼續看

```tsx
function useSignalJobPolling() {
  const [job, setJob] = useState<SignalJob | null>(null)
  useEffect(() => {
    let timer: any
    async function poll() {
      const j = await fetchLatestSignalJob()
      setJob(j)
      if (j?.status === "running" || j?.status === "pending") {
        timer = setTimeout(poll, 3000)  // 3 秒一次
      }
    }
    poll()
    return () => timer && clearTimeout(timer)
  }, [])
  return job
}
```

- 進入頁面時先打一次 `/api/signals/jobs/latest`：
  - status=running → 顯示進度條 + 開始 polling
  - status=done → 顯示報告（讀 `/api/signals/latest`）
  - status=failed → 顯示錯誤訊息 + 「重新產生」按鈕
- 使用者點「重新產生」→ POST `/api/signals/regenerate` → 拿 job_id → 立刻開始 polling（不需要 SSE，因為 polling 已經夠用且支援多 client、離開回來也接得上）

### 13.5 「重新產生」按鈕狀態

| 條件 | 按鈕狀態 | 顯示文字 |
|------|---------|---------|
| 未登入 | disabled | 重新產生（需登入） |
| 有 running job | disabled | 產生中… |
| 全站今日累計 >= 10 次 | disabled | 今日已達上限 |
| 我今日已觸發 | disabled | 你今日已觸發 |
| 可觸發 | enabled | 重新產生 |

### 13.6 離開頁面繼續多工

- React State 即使 unmount 也不影響 server 背景 task（`BackgroundTasks` 在 server 側獨立跑）
- 使用者跳到 `/stocks/2330` 或 `/watchlist` → server task 繼續跑 → 回到 `/` 時重新 mount `<DailySignalsPanel />` → polling 接上最新進度
- 全程不需要持有任何 long-lived connection

---

## 14. 實作順序（commit 切片）

1. **本 spec 文件** + 同步 README / CLAUDE.md / memory
2. `margin_trade` 表 + `etl/finmind_margin_trade_sdk.py` + 併入 `run_finmind_etl_sdk.py` + 3 年 backfill
3. DB schema：`SignalSnapshot` / `SignalGenerationJob` model + `Base.metadata.create_all` 自動建表
4. `backend/app/signals/` 模組骨架（`pipeline.py` / `candidate_pool.py` / `classification.py` / `filters.py` / `llm_caller.py` / `exclusions.py` / `group_stocks.json`）
5. Deterministic 部分 + 單元測試（不動 LLM，產出純 candidate pool + LEADER/FOLLOWER/LAGGARD candidate）
6. LLM batch caller + prompt assembly（讀 `backend/app/prompts/watch-list-stock.md`）
7. API endpoints：`/latest` / `/snapshot/{date}` / `/jobs/latest` / `/regenerate` + 測試
8. 前端 `DailySignalsPanel` + `useSignalJobPolling` + tab bar + pulse badge + 進度條
9. GitHub Actions workflow `daily_signals.yml` 上線
10. 跑一次手動觸發驗證 prod，沒問題後等 cron 03:00 自動跑

---

## 15. 開放問題（待 phase 2 解決）

1. **group_stocks 對照表自動化**：第一版手動維護 JSON，未來考慮接公開資訊觀測站
2. **回測評估**：snapshot 留下後，未來需要 backtest module 驗證「LLM WATCH 後 5/10/20 日表現」與「REMOVE 表現」是否有差異
3. **LLM 模型成本控制**：若 `gpt-4o-search-preview` cost 過高，未來改用 `gpt-4o-mini-search` 或自建 web search tool + `gpt-4.1-mini`
4. **per-user 通知偏好**：第一版全站共享 snapshot；未來若加入「使用者自訂關注產業」可在 LLM prompt 加 user context
5. **Telegram 推送**：早上 03:00 報告產生後可同步推 Telegram bot 訂閱者（M15 連動）
