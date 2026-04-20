---
name: always-stock-backend
description: always-stock 後端開發規範（FastAPI + SQLAlchemy + FinMind ETL + 回測引擎 + OpenAI prompts）。修改 backend/ 下的 Python 檔案時自動觸發，特別是 ETL 模組（etl/finmind_*_sdk.py、run_finmind_etl_sdk.py）、回測引擎（app/backtest_*.py）、API routers、DB schema（models.py）、prompts（app/prompts/）。
---

# always-stock Backend 開發規範

修改 `backend/` 下的程式碼時，先 check 以下規範，避免重踩過的坑。

## 1. FinMind ETL 欄位對照（canonical naming）

DB schema、API schema、前端、回測引擎**只能**使用內部 canonical 欄位名。ETL 層負責把 FinMind / TWSE 原始欄位映射成內部命名。

| FinMind 原始 | DB 欄位 | 易錯 |
|--------------|---------|------|
| `max` | `high_price` | **不是** `high` |
| `min` | `low_price` | **不是** `low` |
| `Trading_Volume` | `volume` | — |
| `Trading_money` | `turnover` | **不是** `money` |
| `open` | `open_price` | 一致 |
| `close` | `close_price` | 一致 |

**原則**：不要把外部資料源 naming 直接散落到全系統，未來 ETL 切換時避免 schema 混亂。

## 2. 三大法人 5→3 類型合併

FinMind 回傳 5 種法人：`Foreign_Investor`、`Foreign_Dealer_Self`、`Investment_Trust`、`Dealer_self`、`Dealer_Hedging`。

**必須**先 `groupby(["trade_date", "stock_id", "inst_type"]).agg(sum)` 合併成 3 種（`foreign` / `trust` / `dealer`）再 upsert，否則 unique constraint 違反。

映射表在 `etl/finmind_utils.py` 的 `FINMIND_INST_TYPES_MAPPING`。

## 3. Bulk Upsert 標準寫法

所有 ETL 模組統一使用 `sqlalchemy.text()` + `INSERT ON CONFLICT DO UPDATE`，batch size **1000**。

```python
# 速度：row-by-row ORM ~4 rec/sec → bulk ~1800 rec/sec（450x）
stmt = text("""
    INSERT INTO daily_price (trade_date, stock_id, ...)
    VALUES (:trade_date, :stock_id, ...)
    ON CONFLICT (trade_date, stock_id) DO UPDATE SET ...
""")
```

**FinMind SDK client gotcha**：內部屬性是 `self.api`，偵測用 `hasattr(client, "api")`，**不是** `self.client`。

## 4. FinMind 配額管理

| 項目 | 值 |
|------|---|
| Sponsor rate limit | 6000 req/hour |
| 每年 backfill（6 模組） | ~36 req |
| 全量 8 年 backfill | ~288 req |
| broker_trade_agg 資料起點 | **2021-06-30**（Sponsor 限定） |

`broker_trade_agg` 遇到 `start_date < 2021-06-30` 時自動調整；`start_date > end_date` 後跳過（回傳 `status: skipped`）。

## 5. 回測引擎規範

### normalized_text 必須從 AST 重建
- 從 `entry_rules` / `exit_rules` AST 呼叫 `backtest_parser._rule_to_text(rule)` 重建
- **禁用** naive `str.replace()` 修改原文
- 停損/停利附加在 exit 段尾端（不可混入 entry 段）

### None vs 0.0 語義
| 指標 | 無資料時 |
|------|---------|
| `profit_factor` | `None`（不是 `0.0`） |
| `avg_gain_pct` | `None` |
| `avg_loss_pct` | `None` |

TypeScript 對應 `number | null`。`0.0` 與「沒有資料」語義不同，誤導性強。

### Sharpe Ratio 係數
`_TRADING_DAYS_PER_YEAR = 252`（美股慣例，與 Zipline/Backtrader 對齊）。台股實際約 245 天，但改動影響跨系統可比性，**不要修改**。

### 4 欄位輸入優先序
`stop_loss_pct` / `take_profit_pct` 解析優先序：**顯式參數 > entry 文字 > exit 文字 > AI mapping**。

Parser 優先使用 `entry_text` / `exit_text`；未提供時 fallback 解析 `strategy_text`（向後相容）。

### 新增 indicator / pattern 必做
1. 更新 `backtest_parser.py` 解析邏輯
2. 更新 `backtest_parser._rule_to_text()`（normalized_text 依賴）
3. 更新 `backtest_catalog.groups`（前端分組依賴）
4. 跑 `tests/test_backtest_parser.py` 與 `tests/test_backtest_engine_normalized_text.py`

### K 棒 / 技術型態 guard 踩過的坑
`detect_head_shoulders_*` / `detect_double_*` / `detect_v_reversal` guard **必須**是：
```python
if i < lookback - 1:  # 正確
    continue
# 不可寫成 if i < lookback：會讓 N=lookback 天資料全判 False
```

peak/trough radius=3，測試資料須讓彼此相隔 ≥ 4 個 index，否則視窗互相 eclipse。

## 6. Prompt 管理（Trade Quality）

- **Canonical**：`backend/app/prompts/trade_quality.md`（production 讀取）
- **鏡像**：`docs/trade_quality_prompt.md`（repo 根，給人讀與編輯）
- 兩份需**同步**
- Render `rootDir=backend`，只有 `backend/` 下的檔案會進 production artifact

**原則**：分析邏輯放 prompt（資料檔），context 組裝 / API 契約放程式碼。改 A/B/C 門檻、target price 公式、輸出格式 **一律**改 md，不動 code。

- 5 階評級（`STRONG_BUY | BUY | NEUTRAL | WATCH | RUN`）由 prompt 直接輸出，**不要**在後端做 A/B/C → 5 階映射（避免 JSON 和 PART 2 不一致）

## 7. Daily Brief / Market router 模式

- `_resolve_trade_date()`：對 `IndustryDailyFlow` 找最近一個 `<= requested` 的實際交易日，非交易日也能正常呼叫
- `_top_industries_3d()`：用 `DISTINCT trade_date ORDER BY DESC LIMIT 3` 從 DB 找實際交易日，**不要**用曆法推算
- DailyBrief 手動觸發（前端不自動 fetch），避免不必要的 OpenAI 費用

## 8. GitHub Actions ETL 排程

| Workflow | 排程 | 說明 |
|----------|------|------|
| `daily_etl_update.yml` | 週一~五 21:00（台北） | `run_finmind_etl_sdk.py --date <台北當日>`，6 模組全跑 |
| `broker_trade_backfill.yml` | 每小時第 5 分 | 以**交易日**計數推進 `batch_days`（非 calendar days） |

exit code：`0=ok, 1=partial, 2=insufficient_quota, 3=error`。只有 `error` 會讓 workflow fail（partial / quota 視為 pass）。

## 9. DB 連線注意

本機 `localhost:8000` backend 進程的 `DATABASE_URL` 環境變數可能指向 **Render PostgreSQL**（非本地 SQLite）。開發/除錯前先確認連線目標，不要誤以為本地 SQLite 為資料來源。

`daily_valuation`、`monthly_revenue`、`financial_statement` 資料只在 Render，本地 SQLite 尚無。

## 10. L1 產業名稱 fallback

修改 `/api/industries/{name}/*` 時，需保留 fallback 對照：
- `水泥工業→水泥`、`鋼鐵工業→鋼鐵`、`食品工業→食品`、`金融科技→金融`、`數位雲端→雲端運算`
- Generic：剝離 `工業` / `業` 後綴

`太空衛星科技` 目前無安全對應，**不做硬映射**避免誤導。
