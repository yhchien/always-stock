# Natural Language Backtest Design

這份文件定義 `always-stock` 的自然語言回測 MVP 設計。

目標是讓使用者可以直接輸入策略描述、股票代號與日期區間，由系統將文字映射到一組「預先支援的策略條件」，再轉成結構化 JSON 並交給固定的回測引擎執行。

第一版重點不是讓 LLM 自由產生 Python 程式，而是讓 LLM 與規則 mapping 一起負責「理解使用者想表達什麼」，真正執行邏輯仍由 backend 的受控引擎完成。

## 設計目標

- 第一版就支援使用者輸入自然語言策略
- 後端只執行受控的條件集合，不執行 LLM 生成程式碼
- 若策略涉及未支援條件，明確回覆「目前不支援」
- 回測結果可重現、可測試、可觀測
- 後續可逐步擴充新的條件與指標

## 核心原則

### 1. LLM 負責判讀，不負責執行

使用者輸入自然語言後，系統可使用 LLM 幫忙理解語意，但 LLM 的輸出必須被限制在既定的條件字典內。

LLM 不直接輸出可執行 Python。

### 2. 策略條件必須來自預先定義的 catalog

所有可回測條件都必須先在 backend 定義，例如：

- `close_above_ma`
- `close_below_ma`
- `ma_golden_cross`
- `ma_dead_cross`
- `foreign_net_positive`
- `foreign_net_negative`
- `foreign_consecutive_buy`
- `trust_consecutive_buy`
- `dealer_consecutive_buy`
- `volume_above_ma`

使用者的自然語言只能被映射到這些條件。若無法映射，就回覆不支援。

### 3. 回測引擎只吃 JSON DSL

自然語言策略最終都要被轉成受控 JSON，再由固定的回測引擎執行。

### 4. Unsupported 要是產品能力，不是錯誤

若使用者提到目前資料庫沒有的條件，例如：

- 財報
- EPS
- 營收
- 本益比
- 新聞情緒
- 籌碼以外的法人明細衍生指標

系統應回覆：

- 哪一段有辨識成功
- 哪些條件目前不支援
- 建議使用者改寫成目前支援的條件

## MVP 使用者流程

```text
使用者輸入自然語言
    |
    v
API 驗證 stock_id / 日期區間
    |
    v
策略判讀層（mapping + LLM）
    |
    +--> 成功：輸出 Strategy JSON
    |
    +--> 部分成功：列出 unsupported_conditions
    |
    +--> 失敗：請使用者改寫
    |
    v
固定回測引擎執行
    |
    v
回傳績效、交易紀錄、equity curve、判讀說明
```

## 系統拆分

建議拆成 4 層。

### A. Input Layer

接收前端或 Telegram 的輸入：

- `stock_id`
- `start_date`
- `end_date`
- `strategy_text`

### B. Interpretation Layer

負責把自然語言轉成策略 JSON。

由兩段組成：

1. 規則 mapping
2. LLM 輔助判讀

這一層的責任不是回測，而是：

- 找出買進條件
- 找出賣出條件
- 找出條件參數
- 找出未支援條件
- 輸出受控 JSON

### C. Backtest Engine

只接受已驗證的 JSON DSL，負責：

- 載入價格與法人資料
- 計算技術指標
- 套用 entry / exit 規則
- 產生交易紀錄
- 計算報酬、勝率、最大回撤、Sharpe

### D. Explanation Layer

可選擇再用 LLM 把結果轉成易讀摘要，但這層不影響實際回測結果。

## 建議 API 設計

### `POST /api/backtest/interpret`

用途：將自然語言轉成策略 JSON，但不執行回測。

Request:

```json
{
  "stock_id": "2330",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "strategy_text": "股價站上20日均線而且外資連買3天就買，跌破20日均線或外資轉賣就賣"
}
```

Response:

```json
{
  "supported": true,
  "strategy": {
    "stock_id": "2330",
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "entry_rules": [
      {
        "indicator": "close_above_ma",
        "params": { "window": 20 }
      },
      {
        "indicator": "foreign_consecutive_buy",
        "params": { "days": 3 }
      }
    ],
    "exit_rules": [
      {
        "indicator": "close_below_ma",
        "params": { "window": 20 }
      },
      {
        "indicator": "foreign_net_negative",
        "params": {}
      }
    ]
  },
  "unsupported_conditions": [],
  "normalized_text": "買進：收盤價站上 MA20 且外資連買 3 天；賣出：收盤價跌破 MA20 或外資轉賣"
}
```

### `POST /api/backtest`

用途：直接用自然語言進行完整回測。

Request:

```json
{
  "stock_id": "2330",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "strategy_text": "股價站上20日均線而且外資連買3天就買，跌破20日均線或外資轉賣就賣"
}
```

Response:

```json
{
  "supported": true,
  "strategy": {
    "entry_rules": [
      { "indicator": "close_above_ma", "params": { "window": 20 } },
      { "indicator": "foreign_consecutive_buy", "params": { "days": 3 } }
    ],
    "exit_rules": [
      { "indicator": "close_below_ma", "params": { "window": 20 } },
      { "indicator": "foreign_net_negative", "params": {} }
    ]
  },
  "metrics": {
    "total_return_pct": 24.7,
    "win_rate_pct": 58.3,
    "max_drawdown_pct": -12.1,
    "sharpe": 1.42,
    "trades": 24
  },
  "trades": [],
  "equity_curve": [],
  "unsupported_conditions": [],
  "warnings": []
}
```

### 部分支援的回應格式

若使用者輸入：

`EPS 年增轉正而且股價站上季線就買`

Response:

```json
{
  "supported": false,
  "strategy": null,
  "unsupported_conditions": [
    {
      "token": "EPS 年增轉正",
      "reason": "financial_fundamental_not_supported"
    }
  ],
  "message": "目前不支援財報或 EPS 條件，請改用價格、均線、成交量、法人買賣超條件。"
}
```

## 策略 DSL 設計

第一版建議維持簡單且可測試。

```json
{
  "stock_id": "2330",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "entry_logic": "all",
  "exit_logic": "any",
  "entry_rules": [
    {
      "indicator": "close_above_ma",
      "params": { "window": 20 }
    },
    {
      "indicator": "foreign_consecutive_buy",
      "params": { "days": 3 }
    }
  ],
  "exit_rules": [
    {
      "indicator": "close_below_ma",
      "params": { "window": 20 }
    }
  ]
}
```

欄位說明：

- `entry_logic`: `all` 或 `any`
- `exit_logic`: `all` 或 `any`
- `indicator`: 條件識別碼
- `params`: 條件參數，例如均線天數、連買天數

## 第一版支援條件建議

### 價格類

- `close_above_ma`
- `close_below_ma`
- `ma_golden_cross`
- `ma_dead_cross`
- `close_above_value`
- `close_below_value`
- `high_break_n_day`
- `low_break_n_day`

### 成交量類

- `volume_above_ma`
- `volume_below_ma`
- `volume_above_value`

### 法人類

- `foreign_net_positive`
- `foreign_net_negative`
- `trust_net_positive`
- `trust_net_negative`
- `dealer_net_positive`
- `dealer_net_negative`
- `foreign_consecutive_buy`
- `foreign_consecutive_sell`
- `trust_consecutive_buy`
- `dealer_consecutive_buy`

### 後續可擴充

- `foreign_cumulative_above`
- `inst_total_net_positive`
- `price_change_pct_above`
- `drawdown_from_recent_high`

## Mapping 設計

### 1. 關鍵字同義詞表

建立一份可維護的 mapping dictionary，例如：

```text
"站上20日均線" -> close_above_ma(window=20)
"突破月線" -> close_above_ma(window=20)
"站上月線" -> close_above_ma(window=20)
"跌破季線" -> close_below_ma(window=60)
"黃金交叉" -> ma_golden_cross
"死亡交叉" -> ma_dead_cross
"外資買超" -> foreign_net_positive
"外資賣超" -> foreign_net_negative
"外資連買3天" -> foreign_consecutive_buy(days=3)
```

這份 dictionary 應由程式碼管理，不放在 prompt 裡硬編。

### 2. 規則優先，LLM 補語意

建議流程：

1. 先跑 regex / mapping
2. 若能完整辨識，直接輸出 JSON
3. 若有模糊語句，再呼叫 LLM
4. LLM 只能從允許的 `indicator` 清單中挑選
5. 最後做 schema validation

這樣可以降低成本，也能讓常見策略有穩定結果。

### 3. LLM 輸出格式強限制

Prompt 要明確要求：

- 只能輸出 JSON
- 只能使用允許的 indicator 名稱
- 若遇到不支援條件，要放進 `unsupported_conditions`
- 不可自行發明欄位

## Unsupported 條件處理策略

以下類型在第一版直接視為不支援：

- 財報
- EPS
- 營收
- ROE
- 本益比
- 籌碼分點進階統計
- 新聞、社群、情緒分析
- 多股票條件
- 盤中策略

建議回傳結構：

```json
{
  "unsupported_conditions": [
    {
      "token": "EPS 年增轉正",
      "reason": "financial_fundamental_not_supported"
    }
  ]
}
```

前端應顯示為明確的人話，例如：

`目前不支援財報類條件（EPS、營收、ROE、本益比），請改用均線、價格、成交量或法人買賣超條件。`

## Backend 模組建議

建議新增以下模組。

### `backend/app/backtest_catalog.py`

責任：

- 維護所有支援的 indicator 定義
- 提供同義詞 mapping
- 提供參數規格

### `backend/app/backtest_interpreter.py`

責任：

- 將自然語言轉成策略 JSON
- 先做 rule-based mapping
- 需要時才呼叫 LLM
- 做 JSON schema 驗證

### `backend/app/backtest_engine.py`

責任：

- 根據 DSL 執行回測
- 計算交易點
- 計算 metrics

### `backend/app/routers/backtest.py`

責任：

- 提供 `/api/backtest/interpret`
- 提供 `/api/backtest`

## Frontend 設計

沿用現有的 [BacktestPanel.tsx](/Users/brian.yh.chien/.gstack/projects/always-stock/frontend/src/components/BacktestPanel.tsx)。

建議將「買進條件 / 賣出條件」兩欄改成「單一自然語言策略輸入」：

- `strategy_text` textarea
- `start_date`
- `end_date`
- `Run backtest`

執行後顯示：

- 系統判讀出的結構化策略
- unsupported 條件提示
- 績效卡片
- trade list
- equity curve

若是部分支援，畫面應先顯示：

- 哪些句子有成功判讀
- 哪些句子不支援
- 可直接複製的改寫建議

## 驗證與測試

第一版至少要有以下測試。

### 單元測試

- mapping 能正確辨識常見均線詞
- mapping 能正確辨識外資連買天數
- 不支援條件能被正確標記
- JSON schema 驗證能擋掉非法 indicator
- backtest engine 對固定資料集輸出穩定結果

### API 測試

- `/api/backtest/interpret` 成功案例
- `/api/backtest/interpret` 部分支援案例
- `/api/backtest` 成功案例
- stock 不存在
- 日期無資料

### Prompt 回歸測試

保留一組固定自然語言案例，確保模型升級後不會破壞既有 mapping。

## 安全性

這個方案的核心安全點是：

- 不執行 LLM 生成程式碼
- 不讓 LLM 直接接觸任意 SQL
- LLM 只能在有限 indicator catalog 內做選擇
- 所有結果都要過 schema validation

## 可觀測性

建議記錄以下欄位：

- 原始 `strategy_text`
- normalized text
- interpreter 是否有用到 LLM
- unsupported_conditions
- final strategy JSON
- backtest 執行時間
- 回測資料筆數

這樣之後才有辦法優化 mapping 與 prompt。

## 推薦分階段實作

### Phase 1

- 建立 `backtest_catalog.py`
- 建立 `backtest_engine.py`
- 建立 `POST /api/backtest`
- 先只用 rule-based mapping

### Phase 2

- 建立 `POST /api/backtest/interpret`
- 對模糊語句加上 LLM 輔助
- 補 unsupported 條件提示

### Phase 3

- 前端 BacktestPanel 改為自然語言輸入
- 顯示 normalized strategy 與 unsupported 提示

## 推薦第一版成功標準

- 使用者可輸入自然語言策略並成功回測
- 至少支援均線、價格、成交量、法人淨買賣、法人連買連賣
- 不支援的財報條件能被清楚拒絕
- 相同輸入可得到可重現結果
- 回測結果能回傳 metrics 與基本交易紀錄

## 結論

你的方向是對的，第一版就做自然語言輸入是可行的，但實作方式應該是：

`自然語言 -> mapping / LLM 判讀 -> 受控 JSON DSL -> 固定回測引擎`

而不是：

`自然語言 -> LLM 自由產生程式碼 -> 執行`

這樣可以同時保留：

- 使用者體驗上的自由輸入
- 系統安全性
- 回測可重現性
- 後續擴充性
