# Sector Mapping Validation Report（Phase 1，2026-07-21）

> 產生方式：`backend/run_classification_backfill.py`；原始統計見同目錄
> `validation_stats.json`。本報告是 spec §33 要求的人讀版本。

## A. Universe（總數統計）

| asset_type | 數量 | 說明 |
|---|---|---|
| COMMON_STOCK | 1210 | 一般上市普通股（含金融股，金融股不再另立 asset_type，由 `is_financial` + `primary_sector=FINANCIAL` 標記） |
| ETF | 264 | 上市 ETF |
| ETN | 28 | 上市 ETN（指數投資證券） |
| PREFERRED_STOCK | 36 | 特別股（沿用母公司 primary_sector） |
| DR | 36 | 存託憑證（TDR） |
| REIT | 8 | 不動產投資信託受益證券 |
| INDEX_BENCHMARK | 31 | **非真實證券**的指數佔位列（`Index`/`大盤`），不列入下方分類統計 |
| **總計** | **1613** | `stocks_master` 全表（含 is_active=false） |

## B. 普通股分類覆蓋（1290 檔，排除 ETF/ETN/INDEX_BENCHMARK）

| confidence | 數量 | 占比 |
|---|---|---|
| HIGH | 1127 | 87.4% |
| MEDIUM | 79 | 6.1% |
| LOW（= review_required） | 84 | 6.5% |

- `DIVERSIFIED_OTHER`（未能 remap 的 catch-all 殘留）：**59 檔**（4.6%），全部有明確 `classification_reason` 說明無法判斷的原因，完整清單見 `catch_all_remap_report.csv`
- 完整逐檔清單：`stock_sector_mapping.csv`
- 待人工複核清單（confidence=LOW）：`sector_mapping_manual_review.csv`

## C. Primary Sector 分布（Top 15，共 49 個 primary_sector 有命中）

| Primary Sector | 中文 | 數量 |
|---|---|---|
| COMPUTER_PERIPHERALS | 電腦及週邊設備 | 137 |
| SEMICONDUCTOR | 半導體 | 110 |
| FINANCIAL | 金融 | 77 |
| BUILDING_MATERIALS_CONSTRUCTION | 建材營造 | 62 |
| DIVERSIFIED_OTHER | 其他（待歸類） | 59 |
| ELECTRICAL_MACHINERY | 電機機械 | 58 |
| OPTOELECTRONICS | 光電（面板/觸控/光學） | 56 |
| TEXTILE_FIBER | 紡織與人纖 | 56 |
| COMMUNICATION_NETWORK | 通信網路 | 47 |
| PETROCHEMICAL | 石化 | 45 |
| PCB_ELECTRONIC_MATERIALS | 印刷電路板與電子材料 | 43 |
| RETAIL_TRADE | 貿易百貨與零售 | 40 |
| MEDICAL_DEVICE | 醫療器材 | 38 |
| STEEL | 鋼鐵 | 38 |
| AUTOMOTIVE | 汽車與零組件 | 37 |

## D. ETF / ETN Taxonomy（292 檔）

**Region 分布**：TAIWAN 175 / US 51 / CHINA 27 / JAPAN 16 / GLOBAL 11 / INDIA 3 / EUROPE 2 /
EMERGING_MARKETS 2 / ASIA 2 / HONG_KONG 1 / VIETNAM 1 / KOREA 1

**Strategy 分布**：MARKET_CAP 107 / THEMATIC 46 / HIGH_DIVIDEND 34 / ACTIVE 33 / LEVERAGED 27 /
INVERSE 27 / SECTOR 7 / ESG 7 / GROWTH 3 / LOW_VOLATILITY 1

**Confidence**：HIGH 20（人工確認的旗艦 ETF，見 `ETF_OVERRIDES`）／MEDIUM 272（規則引擎命中）

完整逐檔清單：`etf_classification.csv`

## E. Regression Cases（spec §20 驗證）

| 代號 | 名稱 | primary_sector | sub_sector | confidence |
|---|---|---|---|---|
| 2634 | 漢翔 | 航太國防 | 航空器製造 | HIGH |
| 1326 | 台化 | 石化 | 化學纖維原料 | HIGH |
| 8039 | 台虹 | 印刷電路板與電子材料 | 軟板/FCCL/電子材料 | HIGH |
| 2603 | 長榮 | 海運（貨櫃／散裝） | 貨櫃航運 | HIGH |
| 2646 | 星宇航空 | 航空 | 客運航空 | HIGH |

金融/ETF 對照：

| 代號 | 名稱 | primary_sector | sub_sector |
|---|---|---|---|
| 2881 | 富邦金 | 金融 | 金融控股 |
| 5876 | 上海商銀 | 金融 | 銀行 |
| 2855 | 統一證 | 金融 | 證券期貨 |
| 0050 | 元大台灣50 | asset_type=ETF | region=TAIWAN / strategy=MARKET_CAP |

全部驗證通過，5 個 regression case + 3 個金融子類 + 1 個 ETF 皆符合 spec §20 預期。

## F. 系統性 bug 修正記錄（dry-run 階段發現）

1. **ETF 判斷 regex 遺漏字母後綴**：舊 `_ETF_ID_PATTERN` 只比對純數字（`^00\d{2,}$`），
   2023 年後新掛牌的主動式（`00400A`）/槓桿反向（`00631L`/`00632R`）/平衡型（`00981T`）
   ETF 一律漏判為 COMMON_STOCK，影響約 130 檔。已改為 `^00\d{2,6}[A-Za-z]?$`，修正後
   ETF+ETN 從 186 檔回升到正確的 292 檔。
2. **兩個 industry_name 完全遺漏對照**：`智慧電網`（19 檔）與 `再生醫療`（4 檔）在第一版
   `industry_mapping.py` 中沒有任何 entry，導致這 23 檔誤入 `DIVERSIFIED_OTHER`。已補上
   對照規則（智慧電網→電器電纜與重電，再生醫療→生技醫療）+ 5 檔業務差異大者個別 override
   （台達電/致茂/大同/洋華/熙特爾-創）。
3. **`其他電子業`（3367 英華達）遺漏個股 override**：已補上（COMPUTER_PERIPHERALS，
   NB/伺服器代工，HIGH confidence）。

修正後 dry-run 重跑，`catch_all_remap_report.csv` 內已無「無系統性映射也無個股 override」
的非預期殘留，全部 59 個 `DIVERSIFIED_OTHER` 都是刻意標記的 LOW confidence 個案。

## G. Backend / API Changes

- 新增 DB 表：`security_classification`、`etf_classification`（`backend/app/models.py`）
- 新增 router：`backend/app/routers/classification.py`
  - `GET /api/classification/{stock_id}`
  - `GET /api/classification?stock_ids=...`
- Additive 欄位：
  - `GET /api/stocks/{stock_id}/history` → `StockHistoryResponse.canonical`
  - `GET /api/signals/latest` / `GET /api/signals/snapshot/{date}` → 每筆
    watchlist/removed item 多一個 `canonical` key
- 舊 `industry_name` / `sub_industry` 契約完全不動

## H. Frontend Changes

- 新元件 `CanonicalSectorTag.tsx`（+ `classificationLabels.ts` 中文字典）
- 掛載位置：
  - `StockChartDialog.tsx`（K 線圖 popup header，L2 個股頁 K 線入口）
  - `DailySignalsPanel.tsx`（魚尾卡片 subtitle + 詳情 popup header）
- ETF/ETN 顯示 region/strategy/主題，**不**顯示公司產業（語意上不適用）
- `canonical` 為 null（舊資料或查無分類）時該 tag 不 render，向後相容

## I. Strategy Confirmation（spec §37 J 項）

以下確認 **完全未修改**：
`app/signals/candidate_pool.py` / `classification.py` / `filters.py` / `momentum.py` /
`market_regime.py` / `deterministic_signals.py` / `llm_caller.py` / `pipeline.py` /
`market_breadth.py` / `exclusions.py` / prompt 檔案 / `industries.py` 的 L0/L1 產業排行邏輯 /
`industry_daily_flow` 聚合。

驗證方式：`git diff --stat` 只涉及新增檔案（`app/classification/*`、
`app/routers/classification.py`、`run_classification_backfill.py`、前端新元件）+
四處 additive 修改（`models.py` 新增兩個 class、`main.py` 新增一個 lifespan 呼叫、
`stocks.py`/`signals.py` 各自新增一個 optional 欄位）。`app/signals/` 目錄零改動。
