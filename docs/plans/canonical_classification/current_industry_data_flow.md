# 現有產業分類資料流（Phase 1 前置調查，2026-07-21）

## 資料流總覽

```text
FinMind TaiwanStockInfo + TaiwanStockIndustryChain
        ↓
backend/etl/fetch_stock_master.py（ETL，寫 industry_name / sub_industry / chain=NULL）
        ↓
stocks_master（PostgreSQL，source of truth，1613 檔 is_active）
        ↓
Backend routers（industries.py / stocks.py / signals.py / watchlist.py / market.py / analysis.py）
        ↓
Frontend（IndustryDashboard / StockList / StockChart / DailySignalsPanel / StockSignalSummaryPanel /
          TradeQualityAnalysis / WatchlistTradeQualityCards / signals/archive page / Navbar）
        ↓
魚尾 Selection Pipeline（backend/app/signals/*）— industry_name 只用於 top_industries 排行 +
          exclusions.is_financial() 字串比對；不吃 sub_industry 做選股分類
```

## 1. Source of truth：`stocks_master`

```python
class StockMaster(Base):
    __tablename__ = "stocks_master"
    stock_id = Column(String, primary_key=True)
    stock_name = Column(String, nullable=False)
    market = Column(String, default="twse")
    industry_name = Column(String, nullable=False)   # FinMind TaiwanStockIndustryChain.industry
    chain = Column(String, nullable=True)             # 永遠 NULL（Fugle 遺留欄位，2026-04-21 已捨棄）
    sub_industry = Column(String, nullable=True)       # FinMind TaiwanStockIndustryChain.sub_industry
    is_active = Column(Boolean, default=True)
    source = Column(String, default="fugle")           # 現況全部 "finmind"
```

`industry_name` / `sub_industry` 由 `backend/etl/fetch_stock_master.py` 從 FinMind
`TaiwanStockIndustryChain` 直接寫入，**沒有任何自建 mapping 層**。`chain` 欄位是 Fugle 時代
遺留（上游/中游/下游三層供應鏈），2026-04-21 全面切 FinMind 後永久寫 NULL，未做 migration
刪除。

## 2. 現況資料分布（active=true，2026-07-21 快照）

- 總計 1613 檔（含 ETF / ETN / 特別股 / TDR / REIT 受益證券 / index 佔位列）
- `industry_name` 87 個 distinct 值，且**同義詞重複**：例如 `半導體`(87) vs `半導體業`(12)、
  `電腦及週邊設備`(106) vs `電腦及週邊設備業`(8)、`通信網路`(40) vs `通信網路業`(4)、
  `食品`(26) vs `食品生技`(8)、`化學工業`(4) vs `化學生技醫療`(4)、`紡織`(49) vs `紡織纖維`(2)、
  `汽車`(30) vs `汽車工業`(8)、`電子零組件業`(2)。這是 FinMind 不同時期/不同抓取批次 industry
  enum 命名不一致造成，**目前系統沒有任何 normalize 層**（`app/industry_names.py` 的
  `normalize_industry_name` 只處理少數已知別名，不是全面 canonical 化）
- `其他` 117 檔（最大單一 catch-all）
- `sub_industry` 覆蓋率 1082/1613（67%）；**已有值時通常品質不錯**（例如 `半導體` 的
  sub_industry 直接是 `IC設計` / `晶圓製造` / `IC封裝測試` 等，已接近 spec 要的
  peer-group 顆粒度）；`其他` bucket 內部分股票的 `sub_industry` 其實有訊號（例如
  `環保潔能服務產業`、`農業科技業`、`其他電子產品及電子服務產業`），只是 `industry_name`
  沒吃到這層
- 非「一般上市公司產業」列：`ETF`(264) / `ETN`(28) / `Index`(30，**非真實證券，是指數
  benchmark 佔位列**，`stock_id` 如 `Automobile`/`BiotechnologyMedicalCare`) / `存託憑證`(36,
  TDR 陸資/外資企業) / `受益證券`(8，不動產投資信託 REIT beneficiary certificate) /
  `創新版股票`(6，實際是一般公司只是掛創新板) / `大盤`(1，`TAIEX` 加權指數本身)
- 特別股（股票代號 `^\d{4}[A-Z]$`）36 檔，`industry_name` 目前**沿用母公司產業**
  （例如 `2881A 富邦特` → `金融`），語意上算合理但目前系統沒有明確 `asset_type` 欄位標示
  它是特別股

## 3. Backend 使用面

| 檔案 | 用途 |
|---|---|
| `app/routers/industries.py` | L0/L1 產業儀表板；`industry_name` 當 group-by key，`normalize_industry_name` 處理少數命名差異 |
| `app/routers/stocks.py` | L2 個股頁 `StockHistoryResponse.industry_name/sub_industry` 直接回傳 DB 原值 |
| `app/routers/signals.py` | 魚尾快照 `industry_name/sub_industry` 隨 candidate 原樣輸出 |
| `app/routers/watchlist.py` | watchlist item 帶 `industry_name` |
| `app/routers/analysis.py` | M17 trade quality context 把 `industry_name/sub_industry` 塞進 LLM prompt |
| `app/routers/market.py` | Daily brief 用 `industry_name` 做熱門產業彙總 |
| `app/signals/exclusions.py` | `is_financial(industry_name)` 純字串關鍵字比對（`金融/銀行/保險/證券`）；`is_etf()` 用 `stock_id` regex + 名稱關鍵字，**完全不查 `industry_name`** |
| `app/signals/candidate_pool.py` / `classification.py` / `momentum.py` | 選股 pipeline 用 `industry_name` 算「產業 20 日 RS」、「同產業有無 LEADER」等——**這是本次 Phase 1 明確禁止觸碰的策略邏輯** |

## 4. Frontend 使用面

`industry_name` / `sub_industry` 目前顯示於：`IndustryDashboard.tsx`、`StockList.tsx`、
`StockChart.tsx` / `StockChartDialog.tsx`、`DailySignalsPanel.tsx`、
`StockSignalSummaryPanel.tsx`、`TradeQualityAnalysis.tsx`、`WatchlistTradeQualityCards.tsx`、
`signals/archive/page.tsx`、`Navbar.tsx`（面包屑）。全部直接顯示 API 回傳的原始
`industry_name`/`sub_industry` 字串，無任何前端 remap 層。

## 5. 結論：Phase 1 要解決的具體問題

1. `industry_name` 有同義詞重複、部分 catch-all（`其他`）過於粗糙、`Index`/`大盤` 是非證券
   佔位列混在 `stocks_master` 裡
2. `sub_industry` 品質其實不差，但覆蓋率只有 67%，且沒有跟 `industry_name` 一起被
   consolidate 成穩定 taxonomy
3. ETF / ETN / TDR / REIT / 特別股完全沒有專屬分類欄位，全部借用（或誤用）公司產業欄位
4. 沒有 `asset_type` 欄位可以明確區分「一般個股 / 金融股 / ETF / 特別股 / TDR / 非證券佔位列」
5. 沒有 confidence / mapping_version / theme_clusters 等 Phase 1 spec 要求的欄位

以上即為 Phase 1 要新建 `security_classification` + `etf_classification` 兩張表、
canonical taxonomy、以及 API/前端顯示層的完整動機。
