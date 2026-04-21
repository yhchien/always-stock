# M21 Trade Quality Context 資料管線 Spec

**狀態**：規劃中（2026-04-21 建立）
**定位**：M17 交易質量分析的資料前處理層，與 M20 prompt 擴充平行互補
**最終產出**：`build_stock_analysis_input(stock_id: str, buy_date: str) -> dict`

---

## 目的

把 DB raw data 預先聚合成**結論層訊號**，餵給 LLM 進行質化分析。避免 AI 自己推 raw OHLC / 法人買賣超 → 推錯熱錢等級、推錯 leader/follower。

本層**只做訊號預處理**，不做交易決策、不做 LLM 呼叫。輸出必須 deterministic、可測試、no hindsight bias。

---

## Prompt（原始 spec，英文）

以下是落地這個 context 層時給 LLM / 工程師的 canonical prompt，保留英文原文（避免翻譯造成語義漂移）：

```
You are a senior quantitative/data engineer.

Goal:
Build a preprocessing layer for a Taiwan stock market LLM analysis system.

The purpose of this layer is NOT to make final trading decisions.
The purpose is to compute stable, repeatable structured signals for a single stock on a given buy_date, so that an LLM can later perform qualitative analysis, valuation, and narrative reasoning.

==================================================
OUTPUT GOAL
==================================================

Given:
- stock_id
- buy_date

Return a structured JSON object containing the following sections:

1. industry_summary
2. chip_summary
3. peer_rank
4. fundamental
5. price_structure
6. news_input_stub

This layer must use ONLY data on or before buy_date.
No hindsight data is allowed.

==================================================
AVAILABLE TABLES / ASSUMED SOURCES
==================================================

Use existing tables if available:

- stocks_master
- daily_price
- inst_stock_flow
- industry_daily_flow
- monthly_revenue

If some fields cannot be computed from DB, set them to null and explain clearly.

==================================================
REQUIRED OUTPUT SCHEMA
==================================================

{
  "stock_id": "2308",
  "buy_date": "2026-02-11",
  "industry_summary": {
    "industry_name": "AI_power",
    "industry_hot_score": 7,
    "industry_hot_level": "S",
    "industry_price_strength": "strong",
    "industry_volume_trend": "expanding_3d",
    "industry_institution_flow": "strong_buy",
    "industry_news_heat": null,
    "industry_capital_type": "re_rating_hot",
    "is_false_hot": false
  },
  "chip_summary": {
    "foreign_buy_days": 3,
    "investment_trust_buy_days": 2,
    "dealer_buy_days": 1,
    "volume_trend": "increasing",
    "price_trend": "uptrend",
    "is_accumulation": true,
    "chip_strength": "strong"
  },
  "peer_rank": {
    "industry_name": "AI_power",
    "return_5d_percentile": 0.12,
    "volume_percentile": 0.18,
    "institution_rank_percentile": 0.10,
    "leader_or_follower": "leader"
  },
  "fundamental": {
    "revenue_yoy": 25.3,
    "revenue_mom": 10.4,
    "guidance": null
  },
  "price_structure": {
    "trend": "uptrend",
    "is_breakout": true,
    "is_consolidation": false,
    "is_accelerating": false
  },
  "news_input_stub": {
    "query_stock": "2308 OR 台達電",
    "query_industry": "AI power OR server power OR power supply",
    "date_end": "2026-02-11"
  },
  "data_quality_notes": [
    "industry_news_heat is null because DB has no news source",
    "guidance is null because no earnings call / guidance table exists"
  ]
}

==================================================
GENERAL RULES
==================================================

1. All calculations must use only data on or before buy_date.
2. Prefer deterministic rules over fuzzy interpretation.
3. If a metric cannot be computed reliably, return null rather than guessing.
4. Add comments in code for every derived rule.
5. Separate raw data extraction from derived signal calculation.
6. Make the implementation easy to extend later.

==================================================
PART 1 — INDUSTRY SUMMARY
==================================================

We need to compute industry hot money signals for the stock's industry.

First identify the stock's industry from stocks_master.
Then compute the following over the lookback window before buy_date.

Lookback windows:
- price / hot money window: last 5 to 10 trading days
- volume expansion check: last 3 trading days compared with previous 5 trading days
- institution flow check: last 3 trading days
- false-hot check: last 1 to 2 trading days spike pattern

Fields to compute:

A. industry_price_strength
Definition:
- strong: at least 3 stocks in the same industry had positive cumulative return over the recent 5 trading days
- medium: exactly 2 stocks had positive cumulative return
- weak: otherwise

B. industry_volume_trend
Definition:
Compare industry aggregated average turnover/volume:
- expanding_3d: last 3 trading days average industry volume > previous 5 trading days average by meaningful threshold
- intermittent: some expansion but not consistent
- flat: no clear increase

Use a simple threshold and make it configurable, for example +15%.

C. industry_institution_flow
Use industry_daily_flow if available, otherwise aggregate from inst_stock_flow by industry.
Definition:
- strong_buy: multiple stocks in the industry show net institutional buying for >=2 of last 3 trading days
- mixed: some buying but not broad
- none: no clear institutional participation

D. industry_news_heat
Do not fabricate this from DB if no news source exists.
Return null for now.

E. industry_hot_score
Score 4 dimensions:
- price strength: strong=2, medium=1, weak=0
- volume trend: expanding_3d=2, intermittent=1, flat=0
- institution flow: strong_buy=2, mixed=1, none=0
- news heat: high=2, medium=1, low=0, null=0 for now

Total score = 0 to 8.

F. industry_hot_level
Map score:
- 7–8 => S
- 5–6 => A
- 3–4 => B
- 0–2 => C

G. industry_capital_type
Classify:
- trading_hot: sharp short-term move, weak institution support, likely one-day or two-day speculation
- re_rating_hot: gradual rise, institution support, broader industry participation

Suggested deterministic rule:
- if volume spike is sharp, price jump is concentrated in 1–2 days, and institution flow is weak => trading_hot
- if price strength is broad, volume trend is continuous, institution flow is strong_buy => re_rating_hot
- otherwise null

H. is_false_hot
Return true if ALL apply:
- only 1–2 trading days extreme volume spike
- sharp price jump instead of gradual rise
- weak/no continuous institutional buying
- no evidence of broad industry continuation

Otherwise false.

==================================================
PART 2 — CHIP SUMMARY
==================================================

Lookback windows:
- primary: 3–7 trading days
- secondary: 10–15 trading days for context only

Fields:

A. foreign_buy_days
Count consecutive foreign net buy days immediately before buy_date.

B. investment_trust_buy_days
Count consecutive investment trust net buy days immediately before buy_date.

C. dealer_buy_days
Same logic.

D. volume_trend
Classify:
- increasing
- spike
- flat
- declining

Use last 3 trading days volume vs previous 5.

E. price_trend
Classify:
- uptrend
- sideways
- downtrend

Use recent close structure:
- uptrend if higher highs / higher lows or positive slope over recent 5–10 days
- sideways if range-bound
- downtrend otherwise

F. is_accumulation
Return true if:
- price_trend = uptrend
- volume_trend = increasing
- price rise is gradual rather than one-day jump

Else false.

G. chip_strength
Classify:
- strong: foreign_buy_days or combined institutional support >=2 days and is_accumulation=true
- neutral: some support but incomplete
- weak: no support or suspicious spike

==================================================
PART 3 — PEER RANK
==================================================

Within the same industry, compare this stock against peers using only data on or before buy_date.

Fields:

A. return_5d_percentile
Percentile rank of 5-day return within same industry.
Lower percentile number means stronger rank is okay if documented consistently, or use top_percent_rank.
Be explicit in code and output.

B. volume_percentile
Percentile rank of recent volume expansion versus peers.

C. institution_rank_percentile
Percentile rank based on institutional buying intensity.

D. leader_or_follower
Leader if at least 2 of the following are true:
- recent 5–10 day return rank is top 30% of industry
- breakout / new high happened earlier than industry median
- institutional buying intensity >= industry average
- volume expansion > industry average

Otherwise follower.

==================================================
PART 4 — FUNDAMENTAL
==================================================

Use monthly_revenue.

Fields:
- revenue_yoy
- revenue_mom
- guidance

Guidance:
- if no structured source exists, return null

Use latest available monthly revenue on or before buy_date.

==================================================
PART 5 — PRICE STRUCTURE
==================================================

Fields:

- trend: uptrend / sideways / downtrend
- is_breakout: true if recent price broke above prior trading range
- is_consolidation: true if recent range is narrow / base-building
- is_accelerating: true if recent slope steepened meaningfully

These should be simple deterministic signals, not discretionary chartist language.

==================================================
PART 6 — IMPLEMENTATION REQUIREMENTS
==================================================

Deliver:

1. A short design summary
2. SQL or pseudo-SQL for raw extraction
3. Python or TypeScript code for derived signal calculation
4. Final function:

build_stock_analysis_input(stock_id: str, buy_date: str) -> dict

5. Explain assumptions and thresholds
6. Mark all thresholds as configurable constants

==================================================
IMPORTANT
==================================================

- Do not build the final LLM analysis prompt.
- Do not generate investment conclusions.
- Only build the signal/preprocessing layer.
- If a field is not reliably computable from current sources, return null and explain why.
- Keep the output deterministic and testable.
```

---

## 附錄 A：DB 欄位對照表（可行度）

對照 `always-stock` 現有 schema，每個欄位的資料來源與可行度。2026-04-21 評估。

| Section / 欄位 | DB 來源 | 可行度 | 備註 |
|---|---|---|---|
| **industry_summary** | | | |
| `industry_name` | `stocks_master.industry_name` | ✅ 現成 | 2026-04-21 起由 FinMind `TaiwanStockIndustryChain` 寫入 |
| `industry_price_strength` | `stocks_master` + `daily_price` 跨股 | ✅ | 同產業每檔算 5d 報酬，數 >0 家數 |
| `industry_volume_trend` | `daily_price` 跨股聚合 | ⚠️ | `industry_daily_flow` **沒有** volume 欄位，必須從 `daily_price` 自己 aggregate |
| `industry_institution_flow` | `inst_stock_flow` + `stocks_master` | ✅ | per-stock 判斷再跨產業統計（不用 `industry_daily_flow`） |
| `industry_news_heat` | — | 🚨 null | DB 無新聞來源，M14 輿情 ETL 完成後才能補 |
| `industry_hot_score` / `industry_hot_level` | 上面組合 | ✅ | 純計算 |
| `industry_capital_type` | 組合 heuristic | ✅ | |
| `is_false_hot` | 組合 heuristic | ✅ | |
| **chip_summary** | | | |
| `foreign_buy_days` / `investment_trust_buy_days` / `dealer_buy_days` | `inst_stock_flow` | ✅ | 連續計數；Python loop 往回數最清楚 |
| `volume_trend` / `price_trend` | `daily_price` | ✅ | 直接算 |
| `is_accumulation` / `chip_strength` | 組合 | ✅ | |
| **peer_rank** | | | |
| `return_5d_percentile` | `stocks_master` + `daily_price` | ✅ | `PERCENT_RANK() OVER (PARTITION BY industry_name)` |
| `volume_percentile` | 同上 | ✅ | |
| `institution_rank_percentile` | +`inst_stock_flow` | ✅ | |
| `leader_or_follower` | 組合 | ✅ | |
| **fundamental** | | | |
| `revenue_yoy` / `revenue_mom` | `monthly_revenue` | ✅ 現成 | 取 `buy_date` 前最新一筆 |
| `guidance` | — | 🚨 null | 無法說會 / 展望 DB，M14 接入前永遠 null |
| **price_structure** | | | |
| `trend` / `is_breakout` / `is_consolidation` / `is_accelerating` | `daily_price` | ✅ | 純 OHLC 計算 |
| **news_input_stub** | 字串組裝 | ✅ | 不查 DB，僅組成關鍵字 |

**總可行度：~92%**（2/22 欄位必 null，符合 spec「return null rather than guessing」）

---

## 附錄 B：建議的常數門檻

集中於 `backend/app/analysis/context_thresholds.py`（新建）。未來調整門檻**只改這裡**。

```python
# === Lookback windows（都以交易日計，非 calendar days） ===
INDUSTRY_PRICE_LOOKBACK_DAYS = 5
INDUSTRY_VOLUME_RECENT_DAYS = 3
INDUSTRY_VOLUME_BASELINE_DAYS = 5
INDUSTRY_INSTITUTION_LOOKBACK_DAYS = 3
FALSE_HOT_SPIKE_DAYS = 2

CHIP_PRIMARY_LOOKBACK_DAYS = 7
CHIP_SECONDARY_LOOKBACK_DAYS = 15

PRICE_TREND_LOOKBACK_DAYS = 10
PRICE_BREAKOUT_BASELINE_DAYS = 20
PRICE_CONSOLIDATION_RANGE_PCT = 0.05  # 5% 區間內視為盤整

# === 產業門檻 ===
INDUSTRY_PRICE_STRONG_MIN_POSITIVE_STOCKS = 3  # >= 3 檔同步 → strong
INDUSTRY_PRICE_MEDIUM_POSITIVE_STOCKS = 2
INDUSTRY_VOLUME_EXPANDING_PCT = 0.15  # 近 3d avg > 前 5d avg × 1.15
INDUSTRY_INSTITUTION_STRONG_DAYS = 2  # 3 日裡 >=2 日有法人淨買的個股 >= N 檔

# === 個股籌碼 ===
CHIP_VOLUME_INCREASING_PCT = 0.10  # 近 3d avg > 前 5d avg × 1.10
CHIP_VOLUME_SPIKE_PCT = 0.50       # 單日 > 前 5d avg × 1.5 為 spike

# === Peer rank ===
LEADER_RETURN_PCT_TOP = 0.30       # top 30% 算 leader 條件之一
LEADER_REQUIRED_CRITERIA = 2       # 4 個條件滿足 >= 2 個即 leader
```

---

## 附錄 C：SQL 範例片段

### C.1 找個股的產業名稱

```sql
SELECT industry_name
FROM stocks_master
WHERE stock_id = :stock_id
LIMIT 1;
```

### C.2 同產業最近 5 個交易日報酬（給 industry_price_strength / peer_rank）

```sql
WITH peer_stocks AS (
    SELECT stock_id FROM stocks_master WHERE industry_name = :industry
),
peer_prices AS (
    SELECT dp.stock_id, dp.trade_date, dp.close_price,
           ROW_NUMBER() OVER (PARTITION BY dp.stock_id ORDER BY dp.trade_date DESC) AS rn
    FROM daily_price dp
    JOIN peer_stocks ps USING (stock_id)
    WHERE dp.trade_date <= :buy_date
)
SELECT stock_id,
       MAX(CASE WHEN rn = 1 THEN close_price END) AS close_latest,
       MAX(CASE WHEN rn = 5 THEN close_price END) AS close_5d_ago,
       (MAX(CASE WHEN rn = 1 THEN close_price END) /
        NULLIF(MAX(CASE WHEN rn = 5 THEN close_price END), 0) - 1) AS ret_5d
FROM peer_prices
WHERE rn <= 5
GROUP BY stock_id;
```

### C.3 同產業 peer_rank 5d 報酬 percentile

```sql
WITH peer_returns AS (
    -- 接 C.2 的 ret_5d 結果
    ...
)
SELECT stock_id, ret_5d,
       PERCENT_RANK() OVER (ORDER BY ret_5d DESC) AS rank_top_pct
FROM peer_returns;
-- 主股的 rank_top_pct 即為 return_5d_percentile
```

### C.4 產業聚合 volume（補 `industry_daily_flow` 沒有 volume 的坑）

```sql
WITH peer_stocks AS (
    SELECT stock_id FROM stocks_master WHERE industry_name = :industry
)
SELECT dp.trade_date, SUM(dp.turnover) AS industry_turnover
FROM daily_price dp
JOIN peer_stocks USING (stock_id)
WHERE dp.trade_date <= :buy_date
  AND dp.trade_date > :buy_date - INTERVAL '30 days'
GROUP BY dp.trade_date
ORDER BY dp.trade_date DESC
LIMIT 8;  -- 近 3d + 前 5d
```

### C.5 個股連續外資買超天數

不用 SQL，直接 Python：
```python
rows = session.execute(text("""
    SELECT trade_date, net_shares
    FROM inst_stock_flow
    WHERE stock_id = :stock_id AND inst_type = 'foreign' AND trade_date <= :buy_date
    ORDER BY trade_date DESC LIMIT 15
"""), {"stock_id": stock_id, "buy_date": buy_date}).fetchall()

foreign_buy_days = 0
for row in rows:
    if row.net_shares > 0:
        foreign_buy_days += 1
    else:
        break
```

---

## 附錄 D：Null 處理政策

**永遠 null 的欄位（DB 無來源）**：
- `industry_summary.industry_news_heat`
- `fundamental.guidance`

**資料缺漏時應 null（非錯誤）**：
- `monthly_revenue` 無 `buy_date` 前資料 → `revenue_yoy` / `revenue_mom` 皆 null
- 假日 / 新股掛牌未滿 N 交易日 → 報酬 / 量能相關欄位可能 null
- 同產業 peers 少於 3 檔 → `peer_rank.*` 可能不具代表性，建議 null + `data_quality_notes` 註明

**必須在 `data_quality_notes` array 中列出所有 null 的原因**：
```json
{
  "data_quality_notes": [
    "industry_news_heat is null because DB has no news source (M14 pending)",
    "guidance is null because no earnings call / guidance table exists",
    "return_5d_percentile is null because only 2 peers in industry"
  ]
}
```

---

## 附錄 E：建議檔案結構

```
backend/app/analysis/
├── __init__.py
├── context_thresholds.py          # 所有 lookback / 門檻常數
├── context_builder.py             # build_stock_analysis_input(stock_id, buy_date) 主入口
├── industry_signals.py            # PART 1 計算邏輯
├── chip_signals.py                # PART 2
├── peer_rank.py                   # PART 3
├── fundamental_signals.py         # PART 4
├── price_structure.py             # PART 5
└── news_stub.py                   # PART 6（純字串組裝）

backend/tests/
└── test_context_builder.py        # 固定 buy_date + 固定 stock_id 的 snapshot 測試
```

---

## 附錄 F：測試策略

1. **固定 fixture 測試**：選幾檔典型股票（如 2308 台達電、2330 台積電、2603 長榮）+ 多個歷史日期，比對 snapshot JSON
2. **Null 處理測試**：
   - 新上市股票（歷史 < 20 天）→ price_structure 應優雅 null
   - 同產業只有 1~2 檔 → peer_rank 應 null
3. **No hindsight 測試**：用 `buy_date = 2024-01-01` 計算，結果不能依賴 2024-01-02 之後任何資料
4. **Deterministic 測試**：同樣 input 連呼叫 10 次，output 必須完全相同

---

## 與其他 Milestone 的關係

- **M17**（✅ 已完成）：目前直接用 raw DB 查詢 + prompt 讓 AI 自己判斷，準確度受限
- **M20**（⬜ 規劃中）：改 prompt 加 45% 報酬加碼建議。**會和 M21 互補**：M20 指示 AI 怎麼分析，M21 提供可靠的結論型輸入
- **M21**（本 spec）：context 資料管線，獨立於 M20 可以先做
- **M14**（⬜ 未開始）：LLM 輿情分析。M14 完成後可補 `industry_news_heat`
- **M17 整合路徑**：
  1. `POST /api/analysis/trade-quality` 收到 request
  2. 呼叫 `build_stock_analysis_input(stock_id, buy_date)` → 得到 6 區塊 JSON
  3. 把 JSON 塞進 OpenAI user message（取代目前 raw data context）
  4. System prompt = `backend/app/prompts/trade_quality.md`（M20 擴充版）
  5. AI 回傳 PART 1 JSON + PART 2 長文
