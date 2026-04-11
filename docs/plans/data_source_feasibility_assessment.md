# Data Source Feasibility Assessment

這份文件評估 `always-stock` 未來若要擴充資料來源，是否適合從目前的 `TWSE + FinMind + 自行解析 TWSE BSR`，改為更多依賴 `FinMind`、`FinLab`，以及是否可利用 `TPEX 產業價值鏈資訊平台` 補強產業細分類。

評估重點是你目前明確想要的資料：

- 上市上櫃每檔股票的財報
- 每天每檔股票的券商分點買賣
- 籌碼面
- 本益比
- 產業細項 / 產業鏈分類

## TL;DR

最務實的結論是：

- `股價 / 籌碼 / 本益比 / 財報 / 分點` 最適合優先評估 `FinMind`
- `FinLab` 比較像研究型資料 SDK，不像你目前 ETL 架構習慣的 HTTP data API
- `TPEX 產業價值鏈資訊平台` 適合當「產業細分類補充來源」，不適合當主行情資料源
- 若要大幅提升資料覆蓋率與一致性，最值得做的是「讓 FinMind 成為主資料源，TWSE 作為備援或校驗」
- 若只談改動成本，`把股價 / 籌碼改抓 FinMind` 是中等改動；`把財報與 PE 接進專案` 是中到大；`把回測、前端、DSL 一起支援基本面` 是大改

## 目前專案現況

目前資料來源大致如下：

- `stocks_master`：FinMind `TaiwanStockInfo` + Fugle mapping
- `daily_price`：TWSE `MI_INDEX`
- `inst_stock_flow`：TWSE `T86`
- `broker_trade`：TWSE BSR HTML 解析

對應程式：

- [backend/etl/fetch_stock_master.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_stock_master.py)
- [backend/etl/fetch_daily_price.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_daily_price.py)
- [backend/etl/fetch_inst_flow.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_inst_flow.py)
- [backend/etl/fetch_broker_trade.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_broker_trade.py)

這代表你其實已經是「混合資料源」架構，不是完全綁死單一來源。

## 需求逐項評估

### 1. 上市上櫃每檔股票財報

#### FinMind

可行，而且是目前最適合接進你這個專案的候選。

官方文件顯示 FinMind 台股基本面有：

- `FinancialStatements`
- `BalanceSheet`
- `TaiwanCashFlowsStatement`
- `StockDividend`
- `StockDividendResult`
- `TaiwanStockMonthRevenue`

來源：

- [FinMind 基本面總覽](https://finmind.github.io/v3/tutor/TaiwanMarket/Fundamental/)
- [FinMind 台灣市場資料總覽](https://finmind.github.io/tutor/TaiwanMarket/DataList/)

優點：

- 有明確的 API 文件
- 有 token 與 HTTP API
- 和你現在 backend ETL 的寫法相容
- 可抓月營收、三大報表、股利

注意：

- 文件頁面使用的是 v3 / v4 混合示例，實作時要統一版本
- 財報資料是「報表欄位型」資料，清洗與欄位映射成本不低
- 要注意公告時點與回測資料洩漏

結論：

- `非常可行`
- 建議先從 `TaiwanStockMonthRevenue`、`FinancialStatements`、`BalanceSheet` 開始

#### FinLab

也可行，但比較適合策略研究，不太像你現在後端服務的資料主源。

官方文件顯示：

- `data.get('price:收盤價')`
- `data.get('price_earning_ratio:本益比')`
- `data.get('monthly_revenue:當月營收')`
- `data.get('financial_statement:每股盈餘')`

來源：

- [FinLab data.get 文件](https://www.finlab.finance/docs/en/reference/data/)
- [FinLab 數據說明](https://www.finlab.finance/docs/details/get_data/)

優點：

- 已經幫你整理成對齊好的 DataFrame
- 對研究與回測很方便
- 對財報欄位命名與使用者體驗相對友善

缺點：

- 主要介面是 Python package，不是你現在最順手的 REST ETL 形態
- 更偏整批 DataFrame 下載，不是 per-stock API 設計
- 若要進後端服務，需自行拆回 row-based schema
- 文件顯示免費版資料範圍較短、最新資料需 VIP

來源：

- [FinLab FAQ](https://www.finlab.finance/docs/faq/)

結論：

- `可行，但不建議拿來當 backend 主資料 ETL`
- `更適合研究、驗證策略、快速原型`

### 2. 每天每檔股票的券商分點買賣

#### FinMind

這項對你目前專案很有吸引力。

官方文件顯示 FinMind 有：

- `TaiwanStockTradingDailyReport`，可用股票代碼查
- `TaiwanStockTradingDailyReport`，可用券商代碼查
- `TaiwanSecuritiesTraderInfo`
- `TaiwanStockTradingDailyReportSecIdAgg`

而且明確寫到：

- 提供上市、上櫃、興櫃分點資訊
- 分點資料區間為 `2021-06-30 ~ now`
- 單次請求只提供一天資料
- `TaiwanStockTradingDailyReport` 只限 sponsor 會員使用
- 有部分缺資料日期

來源：

- [FinMind 籌碼面文件](https://finmind.github.io/tutor/TaiwanMarket/Chip/)

優點：

- 直接 API 化，不用自己解析 ASP.NET HTML
- 支援上市上櫃興櫃
- 比你目前的 TWSE BSR parser 穩定很多
- 有券商代碼與券商名稱

缺點：

- 只到 `2021-06-30` 以後
- sponsor 會員限制
- 仍要處理大量請求與同步策略
- 單次一天資料，若要 backfill 很重

結論：

- `很可行`
- 如果你願意付費，這是最值得替換的部分之一

#### TWSE BSR 現行方案

你現在已經能抓，但維護成本偏高。

優點：

- 不一定要付費
- 目前已有可用程式

缺點：

- HTML 結構脆弱
- session / viewstate 流程麻煩
- 只有上市，對上櫃支援有限
- on-demand 抓取容易慢

結論：

- `短期可維持`
- `中期若採付費資料源，最建議先替換成 FinMind`

### 3. 籌碼面

#### FinMind

可行性高。

官方文件顯示有：

- `TaiwanStockInstitutionalInvestorsBuySell`
- `TaiwanStockShareholding`
- `TaiwanStockHoldingSharesPer`
- `TaiwanStockSecuritiesLending`
- `TaiwanStockMarginPurchaseShortSale`

其中：

- 法人買賣資料區間為 `2005-01-01 ~ now`
- 部分整批抓單日全市場資料需要 `backer / sponsor`

來源：

- [FinMind 籌碼面文件](https://finmind.github.io/tutor/TaiwanMarket/Chip/)

優點：

- 可直接涵蓋更多籌碼資料，不只三大法人
- 上市上櫃興櫃覆蓋較完整
- API 介面清楚

缺點：

- 若要全市場每日更新，可能需要較高方案
- 你的現有 schema 只存三大法人，若要擴充要加表

結論：

- `非常可行`
- `比目前單抓 TWSE T86 更有擴充性`

#### FinLab

可做研究，但不建議當正式 ETL 主來源。

文件示例顯示可抓：

- `institutional_investors_trading_summary:投信買賣超股數`
- `etl:外資持股比例`

來源：

- [FinLab data 參考](https://www.finlab.finance/docs/en/reference/data/)

優點：

- 研究速度快
- 已對齊成表格形式

缺點：

- 不適合 row-based API service ingestion
- 不容易直接知道底層原始欄位與限制

結論：

- `適合研究，不適合當主資料 ETL`

### 4. 本益比 PE

#### FinMind

可行，而且很直接。

官方文件顯示：

- `TaiwanStockPER`
- 資料包含 `PER`、`PBR`、`dividend_yield`
- 資料區間 `2005-10-01 ~ now`

來源：

- [FinMind 技術面文件](https://finmind.github.io/tutor/TaiwanMarket/Technical/)

優點：

- 直接有每日 PE / PBR
- 不用你自己先做 TTM EPS 對齊
- 和目前 ETL 型態相容

缺點：

- 仍要確認定義是否符合你回測想用的口徑
- 若做嚴格回測，還是要注意資料發布時點與可用時點

結論：

- `非常可行`
- `是你最適合優先加進專案的基本面欄位之一`

#### FinLab

也可行。

文件示例顯示：

- `data.get('price_earning_ratio:本益比')`

來源：

- [FinLab data 參考](https://www.finlab.finance/docs/en/reference/data/)

優點：

- 用起來很簡單

缺點：

- 一樣較偏研究用途

結論：

- `可用，但較適合分析層，不是資料層主來源`

### 5. 產業細項 / 產業鏈分類

#### TPEX 產業價值鏈資訊平台

很適合拿來做補充分類，但不適合拿來當主要交易資料源。

網站顯示：

- 依產業鏈提供上市、上櫃、興櫃公司分類
- 平台由櫃買中心與證交所維運
- 公司面資料由各公司輸入

來源：

- [TPEX 產業價值鏈資訊平台首頁](https://ic.tpex.org.tw/index.php)
- [半導體產業鏈範例頁](https://ic.tpex.org.tw/introduce.php?ic=D000&stk_code=6770)

優點：

- 對你要做自然語言策略、產業 drill-down 很有幫助
- 比一般粗分類更細
- 可補上市與上櫃

缺點：

- 我目前沒有查到公開 API 文件
- 很可能要走爬蟲 / 匯入流程
- 分類是平台觀點，不一定能直接當量化欄位使用
- 資料更新頻率、歷史版本、異動追蹤都不一定完整

結論：

- `適合當靜態或低頻更新的 mapping source`
- `不適合當價格 / 籌碼 / 財報主來源`

## 來源比較

### FinMind

適合程度：`最高`

理由：

- 有官方 HTTP API
- 有 token 與配額機制
- 資料集覆蓋你要的價格、籌碼、PE、財報、分點
- 與現有 ETL 程式風格最接近

風險：

- 部分高價值資料需付費等級
- 分點資料有歷史起點限制與缺日
- 仍需自行做資料表設計與清洗

### FinLab

適合程度：`中`

理由：

- 對研究與回測原型非常方便
- PE、營收、EPS、財報欄位看起來都能取

風險：

- 主要是 Python SDK + DataFrame 介面
- 不像你現在 backend ETL 的 row-based ingestion
- 免費資料範圍有限，最新資料需 VIP

### TWSE / TPEX 官方

適合程度：`混合`

理由：

- 官方、免費、可驗證

風險：

- 介面不一致
- BSR 類資料維護成本高
- 產業細分類通常沒有現成標準 API

## 對專案改動規模評估

### A. 把股價從 TWSE 改成 FinMind

改動規模：`中`

影響：

- 改寫 [fetch_daily_price.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_daily_price.py)
- schema 大致可維持不變
- 測試案例要重寫
- backfill 流程可能更穩

預期收益：

- 可支援上市上櫃興櫃
- 來源一致性更好

### B. 把三大法人籌碼從 TWSE 改成 FinMind

改動規模：`中`

影響：

- 改寫 [fetch_inst_flow.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_inst_flow.py)
- `inst_type` 映射邏輯要重整
- 可考慮擴充更多法人欄位

預期收益：

- 可擴充更多籌碼資料
- 能減少對單一 TWSE 表格格式的依賴

### C. 把分點從 TWSE BSR parser 改成 FinMind sponsor

改動規模：`中`

影響：

- 改寫 [fetch_broker_trade.py](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/etl/fetch_broker_trade.py)
- 新增 token / 會員等級設定
- 既有 DB schema 大致可以沿用，但最好保留 `price` 明細或新增原始表

預期收益：

- 維護成本大幅下降
- 可補上櫃與興櫃
- 比 HTML 解析穩定

限制：

- 歷史只到 2021-06-30

### D. 新增財報與月營收

改動規模：`中到大`

影響：

- 新增 ORM model
- 新增 ETL 模組
- 新增 migration
- 新增回測層因子轉換
- 新增 API schema

建議新增資料表：

- `monthly_revenue`
- `financial_statement_item`
- `balance_sheet_item`
- `cash_flow_item`
- `daily_valuation`

預期收益：

- 之後自然語言策略可支援 EPS / 營收 / PE
- 可做基本面濾網與因子研究

### E. 導入 TPEX 產業價值鏈細分類

改動規模：`小到中`

影響：

- 新增一個低頻更新的 mapping ETL
- 擴充 `stocks_master` 或新增 `stock_industry_mapping` 表
- 前端 L0 / L1 可改用更細分類

預期收益：

- 產業分類品質提升
- 更適合未來自然語言查詢與產業策略

## 我對你這個專案的建議路線

### 路線 1：最務實

1. 保留 `stock_master` 目前架構
2. 將 `daily_price` 改為優先支援 FinMind
3. 將 `inst_stock_flow` 改為優先支援 FinMind
4. 將 `broker_trade` 優先改用 FinMind sponsor
5. 新增 `daily_valuation`，先存 `PER / PBR / dividend_yield`
6. 新增 `monthly_revenue`
7. 最後再擴充完整財報三表

這條路最符合你目前系統的形狀。

### 路線 2：研究優先

1. 先用 FinLab 快速驗證哪些基本面欄位你真的會拿來做策略
2. 確定後再落地到自己的 DB schema
3. 正式產品仍用 FinMind / 官方資料 ETL 實作

這條路適合你想快速試策略，但不想一開始就做大量資料工程。

## 付費可行性

### FinMind

我可以確認的是：

- 有 token 制
- 有 sponsor / backer 會員層級
- 分點資料明確標示 sponsor 會員才能用

來源：

- [FinMind 快速開始](https://finmind.github.io/quickstart/)
- [FinMind 籌碼面文件](https://finmind.github.io/tutor/TaiwanMarket/Chip/)

我目前沒有從公開可讀頁面驗證到最新的明確價格數字；FinMind 官網有「贊助方案」入口，但該頁面在目前可讀環境下是 JavaScript 載入，無法確認最新金額：

- [FinMind 官網](https://finmindtrade.com/)

### FinLab

我可以確認的是：

- 有 API token
- 有 VIP 會員限制
- 免費版只能存取較舊資料，最新資料需 VIP

來源：

- [FinLab 數據說明](https://www.finlab.finance/docs/details/get_data/)
- [FinLab FAQ](https://www.finlab.finance/docs/faq/)

同樣地，我目前無法從公開可讀頁面可靠驗證最新 VIP 價格，因為 pricing 頁面在目前可讀環境只顯示載入狀態：

- [FinLab pricing](http://ai.finlab.tw/pricing/)

所以這裡的結論是：

- `有付費方案`
- `有資料權限分級`
- `但本次無法可靠驗證最新實際價格數字`

## 最終建議

如果你問我這個專案下一步最值得做什麼，我會建議：

### 第一優先

- 用 `FinMind` 接 `daily_valuation(PER/PBR/dividend_yield)`
- 用 `FinMind` 評估取代現有 `broker_trade` parser

因為這兩塊收益最大，而且改動相對集中。

### 第二優先

- 新增 `monthly_revenue`
- 新增基本面最小表集，先不要一次把所有財報欄位都做完

### 第三優先

- 以 `TPEX 產業價值鏈平台` 做產業細分類 mapping

### 不建議直接做

- 直接把整個資料層完全改成 FinLab

因為它比較像研究工具，不像你的正式後端資料底座。

## 來源

- [FinMind 快速開始](https://finmind.github.io/quickstart/)
- [FinMind 台灣市場總覽](https://finmind.github.io/tutor/TaiwanMarket/DataList/)
- [FinMind 技術面](https://finmind.github.io/tutor/TaiwanMarket/Technical/)
- [FinMind 籌碼面](https://finmind.github.io/tutor/TaiwanMarket/Chip/)
- [FinMind 基本面](https://finmind.github.io/v3/tutor/TaiwanMarket/Fundamental/)
- [FinMind 官網](https://finmindtrade.com/)
- [FinLab 數據說明](https://www.finlab.finance/docs/details/get_data/)
- [FinLab data 參考](https://www.finlab.finance/docs/en/reference/data/)
- [FinLab FAQ](https://www.finlab.finance/docs/faq/)
- [TPEX 產業價值鏈資訊平台](https://ic.tpex.org.tw/index.php)
- [TPEX 半導體產業鏈範例](https://ic.tpex.org.tw/introduce.php?ic=D000&stk_code=6770)
