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

## 10. Render Python 版本 / 語法相容

Render 的 Docker image（Python runtime）目前跑 **Python 3.9**。撰寫 `backend/` 下的 Python 時：

- **禁用 PEP 604 union 語法** `X | Y` / `X | None`（Python 3.10+ 才支援）
  - 一律用 `Optional[X]` / `Union[X, Y]` 搭配 `from typing import Optional, Union`
  - 本機 Python 3.11+ 不會報錯，但 Render build 成功後 import 時會掛 `TypeError: unsupported operand type(s) for |`
- 其他 3.10+ only 功能也要避開：structural pattern matching (`match/case`)、`type` 語句別名、`ExceptionGroup` 等
- 本機開發前可以跑 `python3.9 -c "import ast; ast.parse(open('app/main.py').read())"` 驗證（但光 AST 過不代表 runtime 過，type annotation 是 runtime 解析的）

**Dockerfile 沒 pin Python 版本時，Render 預設給 3.9。** 若要升版需要在 `backend/Dockerfile` 改 base image 並重測所有相依。

## 11. L1 產業名稱 fallback（已於 2026-04-21 全面移除）

舊設計的 `INDUSTRY_NAME_FALLBACKS` 硬映射 + 後綴剝離在產業分類切 FinMind 後已全部刪除。`industry_daily_flow.industry_name` 與 `stocks_master.industry_name` 皆由 FinMind 寫入，名稱一致。

**不要**再引入任何 Fugle CSV / TWSE mapping / 硬寫死的產業名稱對照表。

## 12. Phase 3 規劃（M18/M19/M20）

### M18 使用者註冊系統（⬜ 待開始）
- 認證：Gmail OAuth（第一階段唯一）+ Admin local auth（帳號 / 密碼由 `ADMIN_EMAIL` / `ADMIN_PASSWORD` env 設定）
- Gating：未登入僅開放首頁 M17（`POST /api/analysis/trade-quality`），其他 endpoint 全部要求登入
- Telegram Bot：chat_id 需綁定已註冊帳號才能用任何指令
- 新增表：`users`、`user_telegram_bindings`（+ session/JWT）
- 新增 API：`POST /api/auth/google/callback`、`POST /api/auth/admin-login`、`POST /api/auth/logout`、`GET /api/auth/me`
- Admin 密碼即使寫死也**必須 hash**（bcrypt / argon2），不可 plaintext 存 DB

### M19 關注買進清單（⬜ 待開始，M18 完成後）
- 必須綁 `user_id`，存 Render Postgres（不走 localStorage）
- 新增表：`user_watchlist`（user_id / stock_id / buy_date / avg_price / created_at）
- 對應 API：`POST /api/watchlist`、`GET /api/watchlist`、`DELETE /api/watchlist/{id}`
- 持股卡片觸發「交易分析」時，需把 `avg_price` 加進 `/api/analysis/trade-quality` 的 context（為 M20 鋪路）

### M20 M17 交易分析擴充（⬜ 待開始，M19 完成後）
- 改 `backend/app/prompts/trade_quality.md`（canonical）+ 同步 `docs/trade_quality_prompt.md`
- 新增分析段落：「如何操作以達 45% 預期報酬率」含加碼點位 + 停損停利
- **寫死參數**：目標報酬 45%、風報比 1:1.75
- JSON schema 視需要新增 `if_strong.add_position_levels: [{price, reason}, ...]`
- 程式碼只改 context 組裝（加 avg_price），API 契約與分析邏輯改 md 不改 code

### M21 Trade Quality Context 資料管線（⬜ 待開始，可與 M20 平行）
- 完整 spec：`docs/plans/trade_quality_context_spec.md`
- 主入口：`backend/app/analysis/context_builder.py::build_stock_analysis_input(stock_id, buy_date) -> dict`
- 產出 6 區塊結論層 JSON（industry_summary / chip_summary / peer_rank / fundamental / price_structure / news_input_stub）
- **必 null 欄位**：`industry_news_heat`（DB 無新聞源）、`guidance`（DB 無法說會/展望）。每個 null 必須寫進 `data_quality_notes`
- **必踩坑**：
  - `industry_daily_flow` **沒有** volume，`industry_volume_trend` 要從 `daily_price` + `stocks_master` 跨股聚合
  - 連續買超天數用 Python loop（SQL window function 可讀性差）
  - Lookback 單位一律交易日（`ORDER BY trade_date DESC LIMIT N`），非 calendar days
  - Peer rank 用 `PERCENT_RANK() OVER (PARTITION BY industry_name)` 即時算，不預聚合
- **No hindsight bias 強制**：所有 SQL / Python 計算只能用 `trade_date <= buy_date` 資料
- **門檻集中**：`backend/app/analysis/context_thresholds.py`（改門檻只改這檔）
- **檔案結構**：`industry_signals.py` / `chip_signals.py` / `peer_rank.py` / `fundamental_signals.py` / `price_structure.py` / `news_stub.py` 拆檔
- **測試**：固定 `(stock_id, buy_date)` snapshot 測試；新上市 / 孤兒產業 null 處理測試；deterministic 測試
