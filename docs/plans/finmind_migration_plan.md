# FinMind Migration Plan

這份文件說明：如果 `always-stock` 未來要「全面改用 FinMind 作為主資料源」，資料庫是否需要重建、建議如何遷移，以及目前能可靠確認的 FinMind 收費與權限分級資訊。

## TL;DR

- `不一定要整個 DB 全砍重建`
- `但幾乎一定要做 migration + 全量資料重灌 / backfill`
- 若只想低風險切換，可保留大部分既有 schema，先改 ETL
- 若想把財報、PE、分點、自然語言策略一起做好，建議順便重整 schema
- FinMind 確實有免費與付費分級，常見標示是 `backer`、`sponsor`
- 我這次能可靠確認「哪些資料需要哪個等級」，但無法可靠確認最新實際金額數字，因為公開 pricing 頁在目前可讀環境下沒有直接露出價格
- 依照你提供的最新 FinMind API 規格，若要維持現在這種「全市場每日 ETL」模式，實務上幾乎需要 `Backer` 或 `Sponsor`

## 0. 依據

本文件同時依據：

- 你在 2026-04-11 提供的 FinMind 完整 RESTful API / dataset 規格
- repo 目前既有 ETL / schema 設計

其中這次最關鍵的新增資訊有：

- Base URL：`https://api.finmindtrade.com/api/v4`
- Auth：`Authorization: Bearer {token}`
- Token rate limit：`600 req/hour`
- 未帶 token：`300 req/hour`
- 可用 `GET /data`、`GET /datalist`、`GET /translation`
- 可用 `GET https://api.web.finmindtrade.com/v2/user_info` 查 quota / usage

## 0.5 與目前專案的直接差異

你目前專案的 ETL 形狀是：

- 每日抓「全市場」
- 再把結果灌進自己的 row-based DB
- API 與前端都讀自己的 DB，不直接打資料源

這和 FinMind 是相容的，但有一個很重要的前提：

`你不能只看單檔 API 有沒有資料，還要看它能不能高效率支撐全市場每日同步。`

依照你貼的規格：

- `TaiwanStockPrice`：單檔免費；全市場單日整批抓取需 `Backer/Sponsor`
- `TaiwanStockInstitutionalInvestorsBuySell`：單檔免費；全市場單日整批抓取需 `Backer/Sponsor`
- `TaiwanStockMarginPurchaseShortSale`：單檔免費；全市場單日整批抓取需 `Backer/Sponsor`
- `TaiwanStockTradingDailyReport`：`Sponsor`

所以如果你真的要全面切 FinMind，而且不想把每日 ETL 拆成上千次逐股請求，會員等級幾乎不能只停在 free。

## 1. 如果全面切到 FinMind，DB 要不要重建？

答案是：

`不用一定整個重建，但很可能要做一次中型到大型 schema 調整。`

可以分成兩種做法。

### 方案 A：相容式遷移

做法：

- 保留現有主要表
- 改寫 ETL，讓資料改從 FinMind 進來
- 視需要新增少量欄位
- 跑全量 backfill 覆蓋舊資料

適合你如果：

- 想先穩定切資料源
- 不想同時動 API / 前端太多
- 希望盡快上線驗證

優點：

- 風險低
- 前端與既有 API 影響較小
- 測試修改量相對可控

缺點：

- schema 不一定最漂亮
- 會保留一些現在資料源時代的設計包袱

### 方案 B：重整式遷移

做法：

- 保留 DB，但重新設計主要表與新表
- 加入基本面、估值、來源資訊、更多市場欄位
- 跑 migration
- 重新 backfill 全量資料

適合你如果：

- 已經決定要把基本面也納進回測
- 未來會做自然語言策略與更多因子
- 願意多花一輪工程時間換乾淨結構

優點：

- 結構更清楚
- 後續擴充成本低很多
- 比較適合長期維護

缺點：

- 工程量大
- 回歸測試量大
- API/前端可能也要一起調整

## 2. 目前哪些表可以沿用？

### `stocks_master`

可以沿用，但建議擴充。

建議新增欄位：

- `market`：`twse` / `tpex` / `emerging`
- `industry_source`
- `source`
- `source_version`
- `updated_at`

原因：

- FinMind 涵蓋上市、上櫃、興櫃
- 你目前只存名稱與產業欄位，不夠表達市場別與資料來源

### `daily_price`

大致可沿用。

你目前已有：

- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `turnover`

這些與 FinMind `TaiwanStockPrice` 很接近，所以不一定要砍掉重建。

對應關係非常直接：

- `Trading_Volume` -> `volume`
- `Trading_money` -> `turnover`
- `open` -> `open_price`
- `max` -> `high_price`
- `min` -> `low_price`
- `close` -> `close_price`

建議新增欄位：

- `spread`
- `trading_turnover`
- `source`
- `ingested_at`

影響評估：

- `低到中`
- 現有 API 幾乎可不動
- 主要是 ETL 改寫與測試重寫

### `inst_stock_flow`

可以沿用基本概念，但建議重整。

原因：

- 你目前只有 `foreign / trust / dealer`
- 如果未來你想接更多籌碼欄位，現在這張表會開始吃緊

依照你貼的 `TaiwanStockInstitutionalInvestorsBuySell` 規格，欄位是：

- `date`
- `stock_id`
- `buy`
- `name`
- `sell`

這表示 FinMind 會把不同法人類別以 `name` 區分列出來，而不是像 TWSE T86 那樣固定欄位展開。

所以 ETL 需要做的事是：

- 把 FinMind `name` 映射成目前系統用的 `foreign / trust / dealer`
- 以 `buy - sell` 算 `net_shares`
- 仍可用 `daily_price.close_price` 去估算 `buy_amount_est / sell_amount_est / net_amount_est`

最少可做法：

- 保持 `foreign / trust / dealer`
- 改 ETL 來源為 FinMind

較佳做法：

- 保留 `inst_stock_flow`
- 新增其他籌碼表，例如 `stock_margin_short`、`stock_shareholding`, `stock_securities_lending`

### `broker_trade`

邏輯上可沿用，但我會建議至少局部重整。

你目前存的是券商彙總結果：

- `trade_date`
- `stock_id`
- `broker_id`
- `broker_name`
- `buy_shares`
- `sell_shares`
- `net_shares`

這張表還能用，但 FinMind 的原始分點資料其實有逐價資訊：

- `price`
- `buy`
- `sell`
- `securities_trader_id`

你目前前端 BrokerPanel 與 `/api/stocks/{stock_id}/brokers` 實際需要的是：

- 指定股票
- 指定日期或區間
- 依券商聚合後的買進 / 賣出 / 淨買超

而 FinMind 直接提供兩種很有用的資料：

- `TaiwanStockTradingDailyReport`
  - 原始逐價分點資料
- `TaiwanStockTradingDailyReportSecIdAgg`
  - 當日券商聚合統計

這意味著：

- `是的，關鍵券商這塊理論上可以直接抓，不必再自己 parser TWSE BSR HTML`
- 若只想支撐目前 BrokerPanel，`TaiwanStockTradingDailyReportSecIdAgg` 很適合直接接
- 若未來想做逐價分析，保留 `TaiwanStockTradingDailyReport` 會更完整

但有三個限制要注意：

- 需要 `Sponsor`
- 歷史起點是 `2021-06-30`
- 單次請求仍是單日資料

所以我的建議是：

- UI / API 第一階段先用 `TaiwanStockTradingDailyReportSecIdAgg`
- 若之後要做更深的分點分析，再額外同步 `TaiwanStockTradingDailyReport`
- 在歷史覆蓋不完整前，先不要立刻刪掉舊 parser 與既有快取資料

所以更好的做法是拆成兩層：

- `broker_trade_raw`
- `broker_trade_daily_agg`

這樣未來你要做更細的分點分析會比較方便。

## 3. 哪些表建議新增？

如果你要全面切 FinMind，且希望之後能支援基本面與自然語言回測，建議新增這些表。

### `daily_valuation`

用途：

- 存 `PER`
- `PBR`
- `dividend_yield`

建議欄位：

- `trade_date`
- `stock_id`
- `per`
- `pbr`
- `dividend_yield`
- `source`

對應 FinMind：

- `TaiwanStockPER`

### `monthly_revenue`

用途：

- 月營收
- YoY / MoM 可由 ETL 或 query 時計算

建議欄位：

- `revenue_month`
- `stock_id`
- `revenue`
- `source`

對應 FinMind：

- `TaiwanStockMonthRevenue`

### `financial_statement_item`

用途：

- 存綜合損益表欄位

建議欄位：

- `report_date`
- `stock_id`
- `type`
- `value`
- `origin_name`
- `source`

對應 FinMind：

- `TaiwanStockFinancialStatements`

### `balance_sheet_item`

對應 FinMind：

- `TaiwanStockBalanceSheet`

### `cash_flow_item`

對應 FinMind：

- `TaiwanStockCashFlowsStatement`

### `stock_shareholding`

用途：

- 外資持股 / 股權分散

### `stock_margin_short`

用途：

- 融資融券

### `stock_securities_lending`

用途：

- 借券相關

## 4. 我建議的 schema 調整方向

如果是我來設計，我會保留目前架構精神，但加上兩個概念。

### A. 所有主資料表加 `source`

例如：

- `daily_price.source`
- `inst_stock_flow.source`
- `broker_trade.source`
- `stocks_master.source`

可選值：

- `twse`
- `tpex`
- `finmind`
- `manual_mapping`

好處：

- 未來比較資料源差異方便
- 遷移期能共存

### B. 所有 ETL 加 `ingested_at`

好處：

- 除錯容易
- 可觀測性好
- 出錯時知道哪批資料寫入

## 5. 我建議的遷移策略

### Phase 1：先做相容層

1. 新增 `source` / `ingested_at` / 必要市場別欄位
2. 保留既有 API schema
3. 新寫一組 FinMind ETL
4. 不先刪舊資料

### Phase 2：雙寫或影子驗證

做法：

- 同日期同股票，TWSE 與 FinMind 各跑一份
- 比對：
  - price row count
  - close price
  - inst flow
  - broker row count

這一階段的目標不是正式切換，而是確認口徑。

### Phase 3：全量回補

做法：

- 決定正式切換後，使用 FinMind 全量 backfill
- 覆蓋現有歷史資料
- 建立驗證報告

### Phase 4：切正式讀取

做法：

- API 改為只讀 FinMind 來源資料
- 舊 ETL 保留一段時間作備援
- 最後再淘汰不再使用的程式

## 6. 會不會需要「全新 DB」？

通常不需要。

除非你想一次做到這些事情：

- 完整重新設計所有表
- 清掉舊資料源口徑
- 不想保留任何歷史包袱
- 願意重跑所有 backfill

這種情況才比較像「開新 DB 重建」。

但以你這個專案現況，我不建議一開始就這樣做。

更穩的方式是：

`同一個 DB 做 migration -> 新增欄位/新表 -> 全量回補 -> 驗證 -> 切換`

## 7. FinMind 怎麼收費？

### 這次能可靠確認到的資訊

FinMind 目前可以可靠確認的是：

- 有免費可使用的資料集與 API
- 有 token 機制
- 有分級會員權限
- 常見分級名稱是 `backer`、`sponsor`
- 依你提供的最新規格，token 狀態下 rate limit 為 `600 req/hour`
- 未帶 token 為 `300 req/hour`

來源：

- [FinMind 官網](https://finmindtrade.com/)
- [FinMind 文件首頁](https://finmind.github.io/)

### 從文件可確認的權限差異

文件中多次明確標示：

- 某些資料是 `只限 backer、sponsor 會員使用`
- 某些資料是 `只限 sponsor 會員使用`

例如：

- `TaiwanStockTradingDailyReport` 分點資料：`只限 sponsor 會員使用`
- 基本面中的「一次拿特定日期所有資料」：常見為 `只限 backer、sponsor`
- `TaiwanStockPriceAdj`、週 K、月 K、部分技術資料：常見為 `只限 backer、sponsor`

來源：

- [FinMind 技術面](https://finmind.github.io/tutor/TaiwanMarket/Technical/)
- [FinMind 基本面](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)
- [FinMind 籌碼面](https://finmind.github.io/tutor/TaiwanMarket/Chip/)

### 目前無法可靠確認的資訊

我這次無法可靠確認：

- `backer` 最新月費 / 年費
- `sponsor` 最新月費 / 年費
- 是否有企業方案價格

原因是：

- FinMind 官網的「贊助方案」頁面在目前可讀環境下是 JavaScript 載入
- 公開文件可讀到權限分級，但沒有清楚列出最新金額數字

因此，這份文件不能負責任地寫出確定價格數字。

### 現階段你可以怎麼理解 FinMind 收費

可以先把它理解成三層：

1. `free`
   - 可用部分資料
   - 適合試用與小量研究

2. `backer`
   - 可拿到更多整批資料與進階資料
   - 適合大量 ETL / 研究

3. `sponsor`
   - 權限最高
   - 像分點這類高價值資料通常在這層

### 對你這個專案最實際的收費判斷

如果你真的要「全面改 FinMind」，那判斷其實很簡單：

- 若只做單檔研究或少量策略驗證，`free` 勉強可用
- 若要維持現在這種每日全市場 ETL，至少要評估 `Backer`
- 若要正式接手 `broker_trade` 分點資料，幾乎就是 `Sponsor`

也就是說：

`全面切 FinMind` 和 `只用 free tier` 這兩件事在你的專案型態上幾乎不相容。

## 7.5 對目前 API / 前端的具體影響

### `/api/stocks/{stock_id}/history`

影響：`低`

原因：

- 目前只需要價格 + 三大法人
- `daily_price` 與 `inst_stock_flow` schema 可延續

### `/api/stocks/{stock_id}/brokers`

影響：`中`

原因：

- API 介面可以大致維持
- 但資料填充方式會從：
  - `cache miss -> parser TWSE HTML -> 寫入 DB`
  變成
  - `cache miss -> call FinMind sponsor API -> 寫入 DB`

這會讓 code 更乾淨，但會牽涉到：

- token handling
- API quota handling
- 單日查詢快取策略

### `aggregate_industry_flow`

影響：`低`

原因：

- 只要上游 `stocks_master`、`daily_price`、`inst_stock_flow` 仍提供相同語義欄位
- 聚合邏輯基本不必重寫

## 7.6 對資料庫的最終判斷

如果只回答一句：

`資料庫大部分可以不必重做，但不代表可以完全不調整。`

更精準地說：

- `stocks_master`：保留，建議擴欄位
- `daily_price`：保留，建議擴欄位
- `inst_stock_flow`：保留，ETL 映射改寫
- `industry_daily_flow`：保留
- `broker_trade`：可保留，但建議新增 raw / agg 拆層或至少補 source 欄位
- `daily_valuation` / `monthly_revenue` / `financial_statement_*`：新增

所以不是：

- `重建整顆 DB`

而是：

- `保留主幹表 + 做 migration + 新增基本面表 + 全量重灌`

如果你真的準備正式導入，建議在採購前直接到 FinMind 官網或聯繫官方確認最新價格與商用條款。

## 8. 如果你真的要全面切 FinMind，我的建議

### 最小可行路線

1. `stocks_master` 加 `market` / `source`
2. `daily_price` 改用 FinMind `TaiwanStockPrice`
3. `inst_stock_flow` 改用 FinMind `TaiwanStockInstitutionalInvestorsBuySell`
4. `broker_trade` 改用 FinMind `TaiwanStockTradingDailyReportSecIdAgg`，必要時同步 `TaiwanStockTradingDailyReport`
5. 新增 `daily_valuation`，接 `TaiwanStockPER`

這樣你就已經能得到：

- 上市上櫃興櫃價格
- 三大法人與更多籌碼擴充空間
- 分點
- PE / PBR / 殖利率

### 完整升級路線

在上面基礎上再加：

6. `monthly_revenue`
7. `financial_statement_item`
8. `balance_sheet_item`
9. `cash_flow_item`
10. 回測層支援基本面 DSL

## 9. 結論

如果你要全面切 FinMind：

- `不需要第一天就整個 DB 全部重建`
- `但需要有計畫地做 schema 調整與全量資料重灌`

如果你希望風險低：

- 先走相容式遷移

如果你希望之後策略、基本面、自然語言都能做得漂亮：

- 建議趁這次做結構重整

而就資料源選擇來說，FinMind 仍然是目前最適合你這個專案全面接手的候選。

這次基於你提供的完整 API 規格，我會把判斷再收斂成三句：

1. `broker parser 可以被 FinMind sponsor API 實質取代`
2. `DB 不需要整顆重建，但需要 migration 與新增基本面表`
3. `若要維持全市場 ETL + 分點，實務上應直接規劃 Backer / Sponsor，而不是 free tier`

## 來源

- [FinMind 官網](https://finmindtrade.com/)
- [FinMind 文件首頁](https://finmind.github.io/)
- [FinMind 技術面](https://finmind.github.io/tutor/TaiwanMarket/Technical/)
- [FinMind 基本面](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)
- [FinMind 籌碼面](https://finmind.github.io/tutor/TaiwanMarket/Chip/)
