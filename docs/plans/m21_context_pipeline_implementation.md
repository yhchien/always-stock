# M21 Trade Quality Context — Implementation Plan

**狀態**：規劃中（2026-04-24 建立）
**與既有文件關係**：本檔只描述**落地細節與工作切分**；欄位語義、計算規則、門檻值的 canonical 來源仍是 [trade_quality_context_spec.md](trade_quality_context_spec.md)（附錄 A/B/C/D 不再重複）

---

## 1. 本次範圍（Phase A）

**做**：
- 新增 `backend/app/analysis/` 模組，包含 context builder 主函式與各 section 計算邏輯
- 門檻常數集中一檔（不可 env 覆寫）
- 新增 debug API endpoint：`GET /api/analysis/context`
- Unit tests：key-by-key 斷言為主 + 少量整體 snapshot

**不做（延後到另一份 spec / milestone）**：
- **M17 `routers/analysis.py` 整合**：現行 `_collect_context` / `_build_user_message` 維持原樣；`backend/app/prompts/trade_quality.md` **不改**（使用者明確要求保留）
- 新版 context-aware prompt 檔（例如 `trade_quality_v2.md`）
- Caching / LRU
- 新聞熱度（`industry_news_heat`）、展望（`guidance`）— DB 無來源，永遠 `null`

---

## 2. 檔案結構

```
backend/app/analysis/
├── __init__.py                    # re-export build_trade_quality_context
├── context_thresholds.py          # 所有 lookback / 門檻常數（附錄 B）
├── context_builder.py             # build_trade_quality_context() 主入口 + data_quality_notes 組裝
├── industry_signals.py            # PART 1：industry_summary
├── chip_signals.py                # PART 2：chip_summary
├── peer_rank.py                   # PART 3：peer_rank
├── fundamental_signals.py         # PART 4：fundamental
├── price_structure.py             # PART 5：price_structure
└── news_stub.py                   # PART 6：news_input_stub（純字串組裝）

backend/app/routers/
└── analysis.py                    # 新增 GET /api/analysis/context endpoint（既有 POST /trade-quality 不動）

backend/tests/
├── test_context_builder.py        # 主入口 + 整體 snapshot
├── test_industry_signals.py       # PART 1 key-by-key
├── test_chip_signals.py           # PART 2 key-by-key
├── test_peer_rank.py              # PART 3 key-by-key
├── test_price_structure.py        # PART 5 key-by-key
└── test_analysis_context_router.py  # API endpoint
```

fundamental_signals、news_stub 邏輯簡單，不開獨立測試檔，併入 `test_context_builder.py`。

---

## 3. 主入口簽章

```python
# backend/app/analysis/context_builder.py

def build_trade_quality_context(
    db: Session,
    stock_id: str,
    buy_date: date,
) -> dict:
    """
    Pre-aggregate DB raw data into conclusion-level signals for trade-quality AI.

    Deterministic, no hindsight: uses only data on or before buy_date.

    Returns schema documented in docs/plans/trade_quality_context_spec.md §REQUIRED OUTPUT SCHEMA.
    On missing data, field is null and a reason is appended to data_quality_notes.

    Raises:
        ValueError: if stock_id not found in stocks_master.
    """
```

**不** raise 的情況：
- `daily_price` 無近期資料 → 回 null + note，不 raise
- 同產業 peers 只有 1 檔 → peer_rank 全 null + note，不 raise
- `monthly_revenue` 無資料 → fundamental 全 null + note，不 raise

---

## 4. 模組職責（單向相依，無交叉 import）

```
context_builder
    ├── industry_signals   (用 db + stocks_master + daily_price + inst_stock_flow)
    ├── chip_signals       (用 db + daily_price + inst_stock_flow)
    ├── peer_rank          (用 db + stocks_master + daily_price + inst_stock_flow)
    ├── fundamental_signals(用 db + monthly_revenue)
    ├── price_structure    (用 db + daily_price)
    └── news_stub          (用 stocks_master info only)
```

每個 module 的 public 簽章都吃 `(db, stock_id, buy_date, industry_name)`（news_stub 只吃 stock info），回傳**該 section 的 dict + 該 section 的 notes list**。

```python
# 所有 section module 共同簽章
def compute_<section>(
    db: Session, stock_id: str, buy_date: date, industry_name: str,
) -> tuple[dict, list[str]]:
    """Returns (section_dict, notes)."""
```

`context_builder` 負責：
1. 先從 `stocks_master` 查 `industry_name`；若查無 → raise
2. 平行呼叫所有 section（Python 層 sequential 即可，SQLAlchemy session 非 thread-safe）
3. 合併 section dict + 展平 notes
4. 附加永遠存在的固定 notes（見 §7）
5. 回傳最終 dict

---

## 5. DB 存取策略

依決策 #4（peer_rank 走 Python 計算），所有百分位都在 Python 端算，避免 SQL `PERCENT_RANK` 造成 dialect 差異（SQLite 本地 dev / Postgres prod）。

**共用輔助函式**（置於 `context_builder.py` 或新檔 `_helpers.py`，若兩個以上 module 用到才抽出）：

```python
def recent_trading_dates(db, buy_date, n) -> list[date]:
    """Return N most recent trade_dates from daily_price ON/BEFORE buy_date."""

def peer_stock_ids(db, industry_name) -> list[str]:
    """Return all stock_id in the same industry from stocks_master (is_active=True)."""
```

**各 section 的 SQL 風格**：
- `SELECT trade_date, close_price, volume, turnover FROM daily_price WHERE stock_id=:sid AND trade_date<=:buy_date ORDER BY trade_date DESC LIMIT N` — 最常見 pattern
- Peer queries 一律 `WHERE stock_id IN (peer_ids) AND trade_date BETWEEN earliest AND buy_date`，Python 再 group by stock_id

**效能預估**：
- 單一檔 request 約 8~12 次 SQL query（每 section 1~3 次），每次 <50ms
- 全部 sequential < 500ms，第一版可接受；未來若 M17 同步呼叫變慢再優化

---

## 6. 常數門檻

依決策 #3 寫死於 [backend/app/analysis/context_thresholds.py](backend/app/analysis/context_thresholds.py)，內容對照 spec 附錄 B 全部搬進去。不吃 env。

測試檔直接 `from app.analysis.context_thresholds import INDUSTRY_VOLUME_EXPANDING_PCT` 引用，避免測試寫死神奇數字。

---

## 7. Null 處理與 data_quality_notes

依決策 #6（2026-04-24 更新為 **4b**），`data_quality_notes` **只在動態缺漏時追加**，永遠 null 的 `industry_news_heat` / `guidance` 不每次寫進去（避免雜訊）。

欄位值本身仍為 `null`（schema 不變），但 notes 只記錄當次真的有資料缺漏時才有意義的訊息：

- `"return_5d_percentile is null because industry '<name>' has only <N> active peers"`
- `"fundamental is null because no monthly_revenue row on/before <buy_date>"`
- `"price_structure is null because daily_price has <N> rows for <stock_id> (need >= <M>)"`

若當次完全沒缺漏 → `data_quality_notes: []`。

---

## 8. API Endpoint

依決策 #2，新增 debug endpoint。

```
GET /api/analysis/context?stock_id=<id>&buy_date=<YYYY-MM-DD>
```

**路徑**：併入既有 [backend/app/routers/analysis.py](backend/app/routers/analysis.py)，不新建 router 檔。

**認證**：
- 初版採 `Depends(require_user)` — 已登入即可用，admin-only 先不加（開發與 QA 都需要）
- 不做 rate limit（deterministic、不碰 OpenAI，無外部成本）
- 日後若要對外開放前端 debug 顯示再重估

**Response model**：直接回 `build_trade_quality_context` 的 dict；不定義 Pydantic schema（schema 在 spec 已明文，且 M21 尚在演進）。用 `JSONResponse(content=ctx)` 即可。

**buy_date 行為**（決策 #3 = **3b**）：與 M17 一致，未指定時 fallback 到 `get_latest_industry_trade_date(db)`（透過 `industry_flow_service`）。回應 JSON 的 `buy_date` 欄位一律回填 resolved 後的日期，方便 debug。

**Error 對應**：
- stock 找不到 → 404
- DB 完全無交易日資料 → 404（與 M17 `POST /trade-quality` 行為一致）

---

## 9. 測試計畫

依決策 #5，key-by-key 為主 + 少量 snapshot。

**Fixtures**（`backend/tests/conftest.py` 或新檔 `fixtures_context.py`）：
- Session 用 SQLite in-memory，塞 3~5 檔虛擬股票資料
- 固定產業「AI 伺服器」含 4 檔 peers，方便測 peer_rank
- 至少一檔「無 monthly_revenue」驗證 fundamental null 路徑
- 至少一檔「歷史不足 5 天」驗證 price_structure null 路徑

**測試分類**：

| 測試檔 | 重點 |
|---|---|
| `test_industry_signals.py` | price_strength / volume_trend / institution_flow 個別 case；hot_score 分數映射；is_false_hot heuristic |
| `test_chip_signals.py` | 外資連買天數（3 正 1 負 → 3）；volume_trend 四分類；is_accumulation 組合條件 |
| `test_peer_rank.py` | 4 檔 peers 時 percentile 值正確；peers < 2 → 全 null；leader 條件 4 取 2 |
| `test_price_structure.py` | trend 三分類；breakout 定義；consolidation 5% 區間；accelerating slope |
| `test_context_builder.py` | 主入口整體 shape；data_quality_notes 固定+動態；unknown stock 404；integration snapshot（1 個 happy case） |
| `test_analysis_context_router.py` | 200 / 400 (no buy_date) / 401 (未登入) / 404 (unknown stock) |

**No-hindsight 測試**：每個 section 至少 1 個 case 把 `buy_date` 往前選，驗證之後的資料不影響結果（插入 `buy_date + 1` 的假資料，expect output 不變）。

---

## 10. 工作切分（建議逐 PR / commit）

依序 commit，每步可獨立驗證：

1. `context_thresholds.py` + `__init__.py`（含 constants 測試）
2. `price_structure.py` + `test_price_structure.py`（OHLC-only，最單純，先做）
3. `chip_signals.py` + `test_chip_signals.py`（inst_stock_flow）
4. `fundamental_signals.py`（monthly_revenue，邏輯最簡）
5. `news_stub.py`（純字串）
6. `industry_signals.py` + `test_industry_signals.py`（需跨 peers，較複雜）
7. `peer_rank.py` + `test_peer_rank.py`（Python percentile）
8. `context_builder.py` + `test_context_builder.py`（組裝 + notes）
9. `GET /api/analysis/context` endpoint + `test_analysis_context_router.py`
10. README / CLAUDE.md / memory 更新

---

## 11. 驗收條件

- [ ] `backend/app/analysis/` 各模組完成
- [ ] `build_trade_quality_context(db, "2330", date(2026, 4, 20))` 回傳 spec §REQUIRED OUTPUT SCHEMA 定義的 6 sections
- [ ] `data_quality_notes` 只在資料缺漏時有內容（完整 happy case 時為 `[]`）
- [ ] 所有 section module 有獨立測試，pass
- [ ] `GET /api/analysis/context?stock_id=2330&buy_date=2026-04-20` 回 200
- [ ] M17 既有 `POST /api/analysis/trade-quality` 行為**不變**（回歸測試通過）
- [ ] CLAUDE.md 新增 M21 完工段落、memory 新增記憶檔、README milestones 更新為已完工

---

## 12. 不在本次範圍內的延伸（備忘）

以下延伸獨立開 issue / milestone，不影響 M21 驗收：

- **M17 context-aware 整合**：新版 prompt 檔（保留 `trade_quality.md` 不動）+ 改 `_build_user_message` 吃 context JSON
- **M14 輿情 ETL**：補 `industry_news_heat`、`guidance` 真實值
- **Caching**：若 M17 / 其他呼叫點效能不足再加 in-memory LRU
- **M23 訊號清單**：可重用本層的 `industry_signals` / `chip_signals`
