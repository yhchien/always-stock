# L3 Manual Strategy Backtest Spec

> **實作狀態（2026-04-12 完工）**：MVP 與第一版支援條件全部落地。
> - Phase 1~5 全部完成，詳見各節「✅ 已完成」標記。
> - 策略輸入範例請見 `docs/guides/backtest_strategy_examples.md`。

這份文件定義 `always-stock` 新版回測功能的產品規格與技術設計。

目標是參考 [LazyBacktest](https://lazybacktest.netlify.app/) 的整體使用流程與結果呈現，但改成更適合本專案的版本：

- 保留「台股單檔、可直接回測、可看標準績效數值」的核心體驗
- 移除使用者不需要的功能：`import 策略`、手續費/交易稅自訂
- 把原本「策略組合器」改成「手動輸入策略」
- 回測完成後，額外串接 OpenAI 產出策略觀察與改寫建議

這份 SPEC 先以「先做對、再做滿」為原則，分成 MVP 與後續擴充。

## 1. 參考產品拆解

根據 2026-04-11 對公開頁面 `https://lazybacktest.netlify.app/` 的檢視，可觀察到其主要功能分區如下：

- 基本設定：股票代碼、日期區間、初始本金
- 交易設定：買賣時間點、手續費、交易稅
- 風險管理：部位大小、固定停損、固定停利
- 策略設定：做多進出場、做空進出場、組合式策略條件
- 策略管理：儲存/載入/刪除策略
- 執行結果：快速結果、淨值曲線、回測摘要、期間績效分析、交易記錄
- 延伸能力：參數優化、批量優化、今日建議

## 2. 我們版本的產品定位

`always-stock` 版回測不是要做成另一個完全通用的量化平台，而是做成「可直接利用現有台股資料庫、支援自然語意/手動策略輸入、可快速驗證單一股票策略」的 L3 功能。

產品定位如下：

- 使用範圍：單一台股個股/ETF 的日線回測
- 資料來源：直接從現有 DB 讀取價格、成交量、三大法人資料
- 互動入口：從個股頁進入 L3 回測工作區
- 策略輸入：使用者手動輸入策略文字，不再用 UI 勾選組合
- 執行方式：後端受控 DSL + 固定回測引擎，不執行 LLM 生成程式碼
- AI 能力：回測完成後，再用 OpenAI 對結果做摘要與建議

## 2.1 突出差異化

相較於參考站，我們這個版本最值得放大的不是「少一個組合器」，而是以下三個產品亮點：

- `策略改寫建議`：不只回傳績效，還能指出策略可能的問題，並給出具體改寫方向
- `台股籌碼模板`：提供幾組可以一鍵帶入的台股風格策略範本，降低新手上手門檻
- `回測結果連回研究頁`：讓使用者能把回測結果直接對照到 K 線、法人流向與個股研究上下文

這三點會讓產品從單純的回測工具，往「台股策略研究助手」靠攏。

## 3. 與參考站的功能對照

### 3.1 要保留的能力

- 輸入股票、測試區間、本金後執行回測
- 顯示核心績效數值
- 顯示淨值曲線
- 顯示交易記錄
- 顯示期間績效分析
- 顯示今日/最新交易日建議

### 3.2 要修改的能力

- 參考站的「買進/賣出條件組合器」改為「手動輸入策略」
- 參考站的多區塊參數設定，收斂成較簡潔的輸入表單
- 參考站策略管理改為後續功能，不列入第一版必做
- 參考站的結果頁，增加 AI 對策略本身的改寫建議
- 參考站沒有強調研究整合，我們版本要把交易點與現有研究頁內容串起來

### 3.3 明確不做

- `import 策略`
- 手續費/交易稅自訂
- 任意 Python 策略上傳或執行

### 3.4 第二階段再評估

- 做空交易
- 參數優化
- 批量優化
- 儲存/分享策略

## 4. 使用者流程

### 4.1 入口層級

建議定義：

- `L2`：現有個股頁
- `L3`：回測工作區

L3 的進入方式：

1. 使用者進入個股頁
2. 點擊「回測」按鈕
3. 展開或切換到 `L3 Backtest Workspace`
4. 在 L3 內輸入策略與測試區間
5. 執行回測並查看結果

### 4.2 MVP 流程

1. 系統帶入目前個股 `stock_id`
2. 使用者必填：
   - `start_date`
   - `end_date`
   - `strategy_text`
3. 系統提供一組預設策略文字
4. 使用者送出
5. 後端解析策略文字 -> 轉為受控 DSL
6. 後端從 DB 取資料並執行回測
7. 回傳：
   - 判讀後策略
   - 核心績效數值
   - 淨值曲線
   - 交易記錄
   - 期間績效
   - 最新交易日訊號
8. 前端再可選擇呼叫 AI 建議 API，顯示策略優化建議

### 4.3 回測後研究流程

回測不是最後一步，回測結果應能接回原本的研究路徑：

1. 使用者看到回測績效
2. 點擊任一筆交易或任一段淨值曲線區間
3. 畫面同步對應到該段 K 線與法人資料
4. 使用者查看：
   - 當時的價格走勢
   - 當時的三大法人買賣超狀態
   - 該交易是因為哪個 entry/exit rule 觸發
5. 使用者再決定是否改寫策略並重跑

## 5. MVP 範圍

### 5.1 必做

- 個股頁新增 L3 回測入口
- 回測表單支援：
  - 股票代碼
  - 開始日期
  - 結束日期
  - 初始本金
  - 策略文字
- `start_date`、`end_date`、`strategy_text` 為必填
- 系統提供 default strategy
- 後端策略判讀器
- 後端回測引擎
- 回測結果 API
- 前端結果頁
- OpenAI 回測後建議
- 台股策略模板帶入
- 交易結果連回研究頁

### 5.2 MVP 先不做

- 做空
- 交易成本自訂
- 策略匯入
- 參數優化/批量優化
- 策略儲存

### 5.3 MVP 加值亮點

這些功能建議列為第一版就盡量做到的差異化能力：

- 回測後 AI `策略改寫建議`
- 一鍵帶入的 `台股籌碼模板`
- 從 `Trade List` 或 `Equity Curve` 點回研究視角

如果開發時程需要切更細，可將它們列為 `MVP+`，在核心回測完成後立即補上。

## 6. 預設策略

建議第一版預設策略：

`收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出。`

這組策略符合目前資料庫能力，也利於展示價格 + 法人混合條件。

## 7. 支援的策略輸入模型

第一版雖然是「手動輸入策略」，但底層仍必須受控。

也就是說：

- 使用者輸入的是自然語言或半結構化文字
- 系統會把它轉成固定的 JSON DSL
- 回測引擎只接受 DSL

### 7.1 第一版支援條件（✅ 全部已完成）

#### 價格類

- ✅ 收盤價站上/跌破 N 日均線
- ✅ 短均線上穿/下穿長均線（黃金交叉 / 死亡交叉）
- ✅ 收盤價突破 N 日高點
- ✅ 收盤價跌破 N 日低點

#### 成交量類

- ✅ 成交量高於 N 日均量
- ✅ 成交量暴增至 N 日均量的 X 倍以上

#### 三大法人類

- ✅ 外資買超 / 賣超（`foreign_net_positive` / `foreign_net_negative`）
- ✅ 投信買超 / 賣超（`trust_net_positive` / `trust_net_negative`）
- ✅ 自營商買超 / 賣超（`dealer_net_positive` / `dealer_net_negative`）
- ✅ 外資連買 N 天 / 連賣 N 天
- ✅ 投信連買 N 天 / 連賣 N 天
- ✅ 自營商連買 N 天 / 連賣 N 天
- ✅ 三大法人合計買超 / 賣超（`all_inst_net_positive` / `all_inst_net_negative`）

#### 風險管理類

- ✅ 固定停損 %
- ✅ 固定停利 %
- 單次投入比例 %（DSL 欄位 `position_size_pct` 存在，但策略文字解析暫不支援）

### 7.2 第一版明確不支援

- EPS、營收、ROE、本益比等基本面
- 新聞/社群情緒
- 分點進階策略
- 多檔股票條件
- 盤中策略
- 自訂函數/程式碼

## 8. 策略 DSL 設計

```json
{
  "stock_id": "2330",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 1000000,
  "trade_timing": "next_open",
  "position_size_pct": 100,
  "stop_loss_pct": 8,
  "take_profit_pct": 20,
  "entry_logic": "all",
  "exit_logic": "any",
  "entry_rules": [
    { "indicator": "close_above_ma", "params": { "window": 20 } },
    { "indicator": "foreign_consecutive_buy", "params": { "days": 3 } }
  ],
  "exit_rules": [
    { "indicator": "close_below_ma", "params": { "window": 20 } },
    { "indicator": "foreign_net_negative", "params": {} }
  ]
}
```

### 8.1 設計原則

- `trade_timing` 第一版先固定為 `next_open`
- `position_size_pct` 預設 `100`
- 風控欄位可選填，未填即不啟用
- `entry_logic` / `exit_logic` 第一版先支援 `all` 與 `any`

## 9. 回測計算規則

第一版採用保守且可重現的計算方式：

- 資料頻率：日線
- 訊號生成：以當日收盤資料判斷
- 成交價格：次一交易日開盤價成交
- 持倉限制：同時間單一多單部位
- 現金模型：無槓桿、不可放空
- 成本模型：第一版固定為 `0`
- 缺資料處理：若指標所需 lookback 不足，該日不產生訊號

## 10. 標準輸出數值

你提到「參考的標準數值也想要」，因此我們的回測結果要有一組固定、可對齊的標準績效欄位。

### 10.1 Quick Result 卡片

- 總報酬率 `total_return_pct`
- 年化報酬率 `annual_return_pct`
- 勝率 `win_rate_pct`
- 最大回撤 `max_drawdown_pct`
- 夏普值 `sharpe_ratio`
- 交易次數 `trade_count`

### 10.2 Summary 區塊

- 期末資產 `ending_equity`
- Buy & Hold 報酬 `benchmark_return_pct`
- Alpha vs Buy & Hold `excess_return_pct`
- 平均每筆報酬 `avg_trade_return_pct`
- 平均持有天數 `avg_holding_days`
- 獲利因子 `profit_factor`
- 平均獲利 / 平均虧損

### 10.3 Performance Analysis 區塊

- 月度報酬
- 季度報酬
- 年度報酬
- 最大連續獲利次數
- 最大連續虧損次數

### 10.4 Trade List

每筆交易至少顯示：

- 進場日
- 出場日
- 進場價
- 出場價
- 持有天數
- 報酬率
- 報酬金額
- 出場原因

### 10.5 Equity Curve

- 策略淨值曲線
- Buy & Hold 對照線
- 最大回撤區段標示

## 11. 今日建議 / 最新交易日建議

參考站有「今日建議」概念；本專案資料多數為日資料，因此第一版定義為：

- 若最新交易日產生進場訊號且目前無持倉：`觀察買進`
- 若最新交易日產生出場訊號且目前有持倉：`觀察賣出`
- 若已有持倉且無出場訊號：`續抱`
- 若無訊號：`觀望`

回傳欄位建議：

```json
{
  "latest_signal_date": "2025-04-10",
  "action": "hold",
  "reason": "仍符合多頭持有條件，尚未觸發賣出訊號。"
}
```

## 12. OpenAI 建議層

這層只在回測完成後使用，不參與實際交易邏輯。

### 12.1 輸入給 OpenAI 的內容

- 原始策略文字
- 判讀後 DSL
- 核心績效數值
- 最近幾筆交易
- 最新交易日狀態

### 12.2 輸出內容

- 這個策略的風格摘要
- 結果亮點與弱點
- 可能的過度擬合風險
- 1 到 3 個可改寫方向

### 12.2.1 策略改寫建議範例

這個能力不是只生成泛泛而談的評論，而是要儘量輸出具體可操作的修改方向，例如：

- `這個策略交易次數過少，建議把外資連買 5 天放寬成 3 天，提高樣本數。`
- `這個策略過度依賴單一均線條件，建議加入固定停損或量能確認條件。`
- `策略在盤整區間回撤較大，可考慮加入趨勢過濾條件，例如季線走揚才允許進場。`

### 12.2.2 輸出限制

建議模型盡量引用回測結果中的具體事實，例如：

- 交易次數太少
- 勝率高但賺賠比差
- 最大回撤過大
- 報酬集中在少數交易

避免只輸出抽象建議。

### 12.3 限制

- AI 只做解釋與建議
- 不直接產生可執行策略碼
- 不修改回測結果

## 13. API 設計

## 13.1 `POST /api/backtest/interpret`

用途：

- 驗證輸入
- 把策略文字轉成 DSL
- 回傳支援與不支援條件

Request:

```json
{
  "stock_id": "2330",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 1000000,
  "strategy_text": "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出"
}
```

Response:

```json
{
  "supported": true,
  "normalized_text": "買進：收盤價站上 MA20 且外資連買 3 天；賣出：跌破 MA20 或外資賣超",
  "strategy": {
    "entry_logic": "all",
    "exit_logic": "any",
    "entry_rules": [
      { "indicator": "close_above_ma", "params": { "window": 20 } },
      { "indicator": "foreign_consecutive_buy", "params": { "days": 3 } }
    ],
    "exit_rules": [
      { "indicator": "close_below_ma", "params": { "window": 20 } },
      { "indicator": "foreign_net_negative", "params": {} }
    ]
  },
  "unsupported_conditions": [],
  "warnings": []
}
```

## 13.2 `POST /api/backtest/run`

用途：

- 解析策略
- 執行回測
- 回傳結果

Response 主要欄位：

```json
{
  "supported": true,
  "strategy": {},
  "metrics": {},
  "equity_curve": [],
  "period_returns": {
    "monthly": [],
    "quarterly": [],
    "yearly": []
  },
  "trades": [],
  "latest_recommendation": {},
  "warnings": []
}
```

## 13.3 `POST /api/backtest/advice`

用途：

- 將回測結果送去 OpenAI
- 取得策略摘要與調整建議

這支 API 可以由前端在回測成功後再另外呼叫，避免主回測 API 因 LLM 變慢。

建議回傳欄位：

```json
{
  "summary": "這是一個偏趨勢追蹤、結合法人確認的策略。",
  "strengths": [
    "能避開部分均線下彎期間的追價買進"
  ],
  "weaknesses": [
    "交易次數偏少，樣本數不足"
  ],
  "rewrite_suggestions": [
    "把外資連買 5 天調整成 3 天",
    "加入 8% 固定停損控制回撤"
  ],
  "risk_notes": [
    "此策略可能在盤整區間來回洗出"
  ]
}
```

## 13.4 `GET /api/backtest/templates`

用途：

- 讓前端讀取可一鍵帶入的策略模板
- 方便之後新增模板而不必硬編在前端

建議第一版模板：

- `外資連買突破型`
- `投信趨勢跟隨型`
- `量價突破型`
- `均線 + 籌碼共振型`

Response 範例：

```json
[
  {
    "id": "foreign_breakout",
    "name": "外資連買突破型",
    "description": "股價突破月線且外資連買時進場",
    "strategy_text": "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出"
  }
]
```

## 14. 後端模組設計

### 14.1 `backend/app/backtest_catalog.py`

責任：

- 定義支援的 indicator
- 定義中文同義詞 mapping
- 定義參數 schema

### 14.2 `backend/app/backtest_interpreter.py`

責任：

- 解析 `strategy_text`
- 先做規則比對
- 必要時再用 LLM 輔助
- 輸出受控 DSL

### 14.3 `backend/app/backtest_data.py`

責任：

- 從 `daily_price`、`inst_stock_flow` 載入回測資料
- 整理成 engine 需要的時序資料格式

### 14.4 `backend/app/backtest_engine.py`

責任：

- 計算技術指標
- 生成訊號
- 模擬持倉
- 計算交易記錄與績效

### 14.5 `backend/app/backtest_ai.py`

責任：

- 包裝 OpenAI 建議功能
- 與現有 `ai_analyst.py` 風格保持一致

### 14.6 `backend/app/routers/backtest.py`

責任：

- 提供 interpret / run / advice API

### 14.7 `backend/app/backtest_templates.py`

責任：

- 維護策略模板定義
- 提供模板 metadata 與預設 `strategy_text`
- 讓模板能被前端直接拉取與帶入

## 15. 前端設計

## 15.1 入口

現有個股頁已經有 `BacktestPanel` placeholder，因此第一版可沿用該區塊，但把它升級成真正的 L3 工作區。

建議 UI 結構：

- 頁首：`L3 策略回測`
- 表單區：
  - 股票代碼（唯讀或可切換）
  - 開始日期
  - 結束日期
  - 初始本金
  - 策略模板快捷按鈕或下拉選單
  - 策略輸入 textarea
  - 套用預設策略
  - 執行回測
- 判讀區：
  - 系統解析後策略
  - 不支援條件提示
- 結果區：
  - Quick Result 卡片
  - 淨值曲線
  - Summary
  - Performance Analysis
  - Trade List
  - AI 建議

## 15.1.1 台股籌碼模板

模板不是取代手動輸入，而是幫使用者快速起步。

建議第一版模板如下：

- `外資連買突破型`
  - 範例文字：`收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出`
- `投信趨勢跟隨型`
  - 範例文字：`5日均線上穿20日均線且投信買超就買進；5日均線跌破20日均線或投信轉賣就賣出`
- `量價突破型`
  - 範例文字：`收盤價突破60日高點且成交量大於20日均量1.5倍就買進；收盤價跌破20日均線就賣出`
- `均線 + 籌碼共振型`
  - 範例文字：`收盤價站上20日均線且外資買超且投信買超就買進；收盤價跌破20日均線或三大法人合計轉賣就賣出`

## 15.2 表單驗證

- `start_date` 必填
- `end_date` 必填
- `strategy_text` 必填
- `start_date <= end_date`
- 日期區間不可超出 DB 可用資料範圍

## 15.3 互動原則

- 若策略無法完整解析，不直接執行回測
- 先顯示 unsupported 條件與改寫建議
- 回測成功後才可請求 AI 建議

## 15.4 回測結果連回研究頁

這是本專案很重要的差異化能力。

建議互動如下：

- `Trade List` 每一筆交易都可點擊
- 點擊後：
  - K 線圖跳到該交易附近時間區間
  - 法人流向同步定位到同一段日期
  - 畫面顯示該筆交易的進出場原因
- `Equity Curve` 點擊某個回撤區段時，也可切換到該段研究視角

若第一版不做完全聯動，至少先做到：

- 每筆交易提供 `查看當時走勢`
- 導回個股頁對應日期區間

## 16. 資料需求與 DB 相容性

目前專案已具備的表：

- `daily_price`
- `inst_stock_flow`
- `stocks_master`

這已足夠支撐 MVP 的：

- 開高低收
- 成交量
- 三大法人買賣超
- 單檔股票 metadata

因此不需要新增外部資料源即可先做出第一版。

若要支援「哪些產業環境下特別有效」這類進一步分析，第一版可先不做完整產業 regime model，但可以預留：

- 交易當時的 `industry_name`
- 同期間產業資金流摘要
- 後續擴充為「策略在哪些產業/市場環境表現較好」

## 17. 非功能需求

### 17.1 可重現

- 相同輸入必須產生相同結果
- 不允許 LLM 直接參與交易邏輯

### 17.2 可測試

- 每個 indicator 都要可單測
- 固定資料集要有 golden result

### 17.3 可觀測

至少記錄：

- `stock_id`
- 日期區間
- 原始策略文字
- normalized strategy
- unsupported conditions
- 回測耗時
- 回測資料筆數

## 18. 驗證策略

### 18.1 單元測試

- 策略文字解析
- 指標計算
- 訊號生成
- 交易模擬
- metric 計算

### 18.2 API 測試

- interpret 成功案例
- interpret 部分支援案例
- run 成功案例
- stock 不存在
- 區間無資料
- strategy 空白

### 18.3 前端測試

- 必填欄位驗證
- 回測成功渲染
- unsupported 條件渲染
- AI 建議區 loading / error state

## 19. 建議開發順序

### Phase 1: Engine First ✅ 已完成

- ✅ 建立 catalog（`backtest_catalog.py`）
- ✅ 建立 interpreter / parser（`backtest_parser.py`）
- ✅ 建立 engine（`backtest_engine.py`）
- ✅ 建立測試

### Phase 2: API ✅ 已完成

- ✅ 新增 backtest router（`routers/backtest.py`）
- ✅ 串入 main app
- ✅ 建立 API tests（`tests/test_backtest_router.py`，20 個測試）

### Phase 3: Frontend L3 ✅ 已完成

- ✅ 升級 `BacktestPanel`（templates、interpret preview、equity curve、trade list）
- ✅ 顯示結果與交易表
- ✅ 顯示 equity curve（`BacktestEquityChart`）
- ✅ 加入模板帶入（下拉選單，7 個模板）
- ✅ 加入交易點回看入口（Trade List 每筆可點回研究頁）

### Phase 4: AI Advice ✅ 已完成

- ✅ 建立回測後建議 API（`backtest_advisor.py` + `/api/backtest/advice`）
- ✅ 前端接上 AI 建議卡片（OpenAI 優先，heuristic fallback）
- ✅ AI mapping（`backtest_ai_mapping.py`，unsupported 條件交 OpenAI 補充解析）

### Phase 5: Research Integration ✅ 已完成

- ✅ 交易記錄點回研究頁（Trade List 每筆連結到 `/stocks/:id?date=`）
- ✅ latest_recommendation 訊號日連回研究頁

## 20. 本次確認重點

這份 SPEC 先採用以下決策：

- 第一版只做單檔、多頭、日線回測
- 資料完全從既有 DB 撈取
- 入口放在個股頁，作為 L3 工作區
- 使用者必填測試區間與策略文字
- 系統提供 default strategy
- 不做 import 策略與手續費設定
- OpenAI 僅做回測後建議，不參與實際執行

若你確認方向沒問題，下一步我會依這份文件開始實作 Phase 1：

- 後端 DSL
- interpreter
- 回測引擎
- API
- 前端 L3 面板

## 21. TODO Checklist

以下 checklist 用來追蹤目前 L3 Manual Strategy Backtest 的實作進度。

### 21.1 已完成

- [x] 新增 backtest router 並掛入 FastAPI app
- [x] 提供 `GET /api/backtest/templates`
- [x] 提供 `POST /api/backtest/interpret`
- [x] 提供 `POST /api/backtest/run`
- [x] 提供 `POST /api/backtest/advice`
- [x] 建立第一版策略 catalog
- [x] 建立第一版中文策略 parser
- [x] 建立第一版回測引擎
- [x] 支援單檔、日線、long-only、收盤判斷訊號、次日開盤成交
- [x] 回傳 quick metrics
- [x] 回傳 summary metrics 的第一版欄位
- [x] 回傳 equity curve 與 Buy & Hold 對照資料
- [x] 回傳 trade list
- [x] 回傳 latest recommendation
- [x] 前端 `BacktestPanel` 改為真 API 串接
- [x] 前端支援策略模板帶入
- [x] 前端顯示 quick result
- [x] 前端顯示正式 equity curve chart
- [x] 前端顯示交易紀錄
- [x] 前端顯示策略建議卡片
- [x] 前端顯示回測 warnings
- [x] 交易紀錄 / 最新訊號可跳回研究頁日期
- [x] 補 backend router tests
- [x] 補 backend advisor tests
- [x] 補 frontend API tests
- [x] 補 frontend `BacktestPanel` component test

### 21.2 已做但仍屬第一版簡化

- [x] `advice` API 在有 `OPENAI_API_KEY` 時走 OpenAI，沒有 key 時 fallback 本地 heuristic 規則
- [x] parser 目前只支援固定句型與少數條件，不是完整自然語言理解
- [x] 成本模型目前固定為 `0`
- [x] 目前只支援單一部位，不支援分批進出

### 21.3 尚未完成

- [x] 前端先呼叫 `interpret` 做策略預覽 / 驗證，再決定是否允許執行回測
- [x] 前端顯示 `unsupported_conditions`
- [ ] 前端顯示完整 summary 區塊
- [ ] 前端顯示 monthly / quarterly / yearly performance analysis
- [ ] 前端顯示最大連續獲利 / 連續虧損等分析
- [ ] 前端顯示平均獲利 / 平均虧損 / profit factor 的完整說明
- [ ] 交易紀錄點擊後同步高亮對應 K 線位置
- [ ] 交易紀錄點擊後同步顯示當時觸發的 entry / exit rule 細節
- [ ] equity curve 點擊區段後同步回研究頁指定時間範圍
- [ ] advice 卡片加入手動重新產生按鈕
- [x] advice 卡片加入 loading skeleton / 更完整錯誤提示文案

### 21.4 尚未支援的策略條件

- [ ] 短均線上穿 / 下穿長均線
- [ ] 收盤價突破 N 日高點
- [ ] 收盤價跌破 N 日低點
- [ ] 成交量暴增至 N 日均量 X 倍以上
- [ ] 外資連賣 N 天
- [ ] 投信連買 N 天以外的更多投信條件
- [ ] 三大法人合計買超 / 賣超
- [ ] 固定停損 %
- [ ] 固定停利 %
- [ ] 單次投入比例 %
- [ ] `entry_logic = any` 的前端操作流程
- [ ] 更完整的中文同義詞與容錯解析

### 21.5 尚未補齊的驗證 / 邊界條件

- [x] `interpret` 的部分支援案例測試
- [x] `strategy_text` 空白 / 格式錯誤的 API 測試
- [x] `unsupported_conditions` 的測試
- [x] lookback 不足時的 warnings 測試
- [x] 開盤價缺失 fallback warnings 測試
- [x] `BacktestPanel` 的空白策略 validation test
- [ ] advice API 的 OpenAI 失敗 fallback 整合測試
- [x] `BacktestPanel` 的 loading / error state component tests（目前已涵蓋空白策略、partial support、細緻錯誤訊息）
- [ ] strategy template 下拉互動測試
- [ ] equity curve chart option 測試

### 21.6 可能漏掉、之後應補的產品細節

- [ ] 在 UI 上明確標示「回測不含手續費 / 交易稅 / 滑價」
- [ ] 在 UI 上明確標示「訊號用收盤判斷、次日開盤成交」
- [ ] 提供最少資料長度限制提示，例如 MA20 策略至少需要 20 個交易日以上
- [ ] 明確處理 stock 無資料、區間太短、條件無法解析時的 UX
- [x] 前端把 422 error detail 轉成更細的中文提示，不只顯示通用錯誤
- [ ] 規劃回測結果快取，避免同條件重跑浪費時間
- [ ] 規劃後端 metrics schema 的 typed model，避免目前 `Dict[str, Any]` 長期擴散
- [ ] 規劃把 `exit_reason` 從 indicator code 轉成更可讀的中文
- [ ] 規劃 strategy templates 後台化或可配置化，避免永久硬編在程式裡

### 21.7 自然語句策略與動態資料 TODO

這一段是之後一定要面對的核心問題：
使用者輸入的策略文字，很多條件不一定已經在目前 DB 裡有對應欄位或預先算好的指標。
因此不能只做「字串對照 DB 欄位」，還要補一層「判讀後決定要去哪裡找資料」的流程。

- [ ] 定義 `strategy interpretation outcome` 分類：
  - `直接可用現有 DB`
  - `可由現有 DB 現場計算`
  - `需即時向外部資料源抓取`
  - `需由 AI 幫忙判斷應查什麼資料`
  - `目前完全不支援`
- [ ] 定義每種 outcome 對應的 backend pipeline：
  - DB 直接查
  - DB 原始資料現場算 indicator
  - 現場補抓外部資料後再算
  - 先讓 AI 做欄位 / 指標 / 資料源 mapping，再進入受控 DSL
- [ ] 建立「資料需求規劃層」，不要讓 LLM 直接生成執行碼，而是只產生：
  - 想查的指標
  - 需要的資料集
  - 需要的 lookback
  - 是否可由本地 DB 解決
- [ ] 建立 `data capability catalog`
  - 哪些條件可由 `daily_price` 直接算
  - 哪些條件可由 `inst_stock_flow` 直接算
  - 哪些條件需要額外 ETL / runtime fetch
  - 哪些條件目前明確不支援
- [ ] 規劃 runtime fetch 的快取策略
  - 避免每次 user 改一句策略就重抓外部資料
  - 區分「短期 session cache」與「可落 DB 的持久化 cache」
- [ ] 明確定義外部資料抓取的 timeout / retry / fallback 規則
- [ ] 定義如果外部資料抓不到時，回測要怎麼降級：
  - 中止執行
  - 忽略該條件
  - 改成 partial support
  - 只回 strategy preview，不回測
- [ ] 定義 AI 在策略判讀中的角色：
  - 只做「資料需求理解」與「欄位映射建議」
  - 不直接產生可執行回測程式碼
  - 最終仍必須落到受控 DSL / catalog
- [ ] 為「需要動態抓資料」的策略建立等待 UX
- [ ] 為「AI 正在判斷這句策略需要查什麼」建立等待 UX
- [ ] 為「部分資料找得到、部分找不到」建立 partial support UX

### 21.8 等待文案 / 狀態文案 TODO

這些文案之後前端實作時應直接可用或微調，不要等到最後才補：

- [x] `正在解析策略文字，判斷這句話對應哪些指標...`
- [ ] `正在檢查目前資料庫是否已經有這些條件需要的資料...`
- [ ] `部分條件需要現場計算，正在整理可回測的版本...`
- [ ] `這個策略提到的資料目前不在本地資料庫，正在嘗試補抓...`
- [ ] `正在請 AI 協助判讀這句策略該對應哪些資料來源...`
- [ ] `已找到部分條件，但仍有幾個條件目前無法支援。`
- [ ] `目前只能先回測其中可支援的條件，是否要先查看預覽結果？`
- [ ] `這句策略需要的資料目前無法取得，暫時不能執行回測。`
- [ ] `這句策略可以判讀，但還需要額外資料處理，請稍候...`
- [ ] `若等待過久，請簡化策略條件後再試一次。`

### 21.10 Session Handoff

如果下一個 session 要直接接著做，建議優先順序如下：

1. 擴充 DSL 條件：
   - 均線交叉
   - 突破 N 日高低點
   - 固定停損 / 固定停利
2. 補前端完整 summary / period analysis 區塊
3. 規劃動態資料能力：
   - capability catalog
   - runtime fetch / AI mapping
   - partial support UX

目前已知狀態：

- 前端已是 `interpret -> preview -> run -> advice` 流程
- 若 `interpret.supported = false`，前端會停在 preview，不會直接跑回測
- 前端已能顯示：
  - normalized strategy
  - unsupported conditions
  - parser warnings
  - backtest warnings
  - translated 422 detail
- 最新一批前端測試：
  - `npm test -- --runInBand src/__tests__/lib/api.test.ts src/__tests__/components/BacktestPanel.test.tsx`
  - `34 passed`

### 21.9 需要明講的產品限制 TODO

- [ ] 在 spec 與 UI 上清楚區分：
  - `策略判讀成功`
  - `策略可完整回測`
  - `策略僅可部分回測`
  - `策略目前不可回測`
- [ ] 在 UI 上明確告知：
  - 哪些條件是用現有 DB 算的
  - 哪些條件是 runtime 補抓的
  - 哪些條件是 AI 協助映射後才支援
- [ ] 如果未來加入 runtime fetch，需在結果頁保留資料來源註記與抓取時間
