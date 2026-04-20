---
name: always-stock-frontend
description: always-stock 前端開發規範（Next.js + Tailwind + shadcn/ui + ECharts）。修改 frontend/ 下的檔案時自動觸發，特別是 app/ 路由頁面、components/ 元件（StockChart、BrokerPanel、FinancialsPanel、BacktestPanel、DailyBrief、TradeQualityAnalysis）、圖表設定與 API client。
---

# always-stock Frontend 開發規範

修改 `frontend/` 下的程式碼時，先 check 以下規範，避免重踩過的坑。

## 1. 日期與時區（重要）

### 預設日期一律用台北時區
```typescript
// ✅ 正確
const today = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Taipei",
}).format(new Date());
```

```typescript
// ❌ 禁用：台灣凌晨會落到前一天
const today = new Date().toISOString().slice(0, 10);
```

### L2 頁面跨元件共用 date
L2 個股頁的 `StockChart`、`BrokerPanel`、`FinancialsPanel` **必須**共用同一個 `date` query param，避免同頁不同日期資料混用。

### 回測預設 startDate
用台北時區一年前，不可寫死日期字串。

## 2. Panel Toggle 規範

### localStorage key 命名
```
always-stock:show-<feature>-panel
```
例：`always-stock:show-financials-panel`、`always-stock:show-broker-panel`（已隱藏）

### 關閉的 panel 不 render、不觸發 API
使用條件渲染，**不要**只是 CSS hidden；否則背景仍會 fetch，浪費 API 配額。

### Retry 上限模式（參考 BrokerPanel）
當 API 回傳 `is_refreshing: true` 但資料為空時，內部計數器 +1，**超過 3 次**後停止 auto-refresh polling 並顯示「此日期無紀錄」。切換股票/日期時計數器歸零。

## 3. 圖表顯示規範

### PER / YoY null 處理
- `PER <= 0` 視為 N/A（FinMind 回 0 代表 EPS 為負），傳給 ECharts 時用 `null` 不畫線
- 全期間 PER 皆 N/A 時顯示「此期間 EPS 為負值或不適用，本益比無法顯示」
- 月營收 `yoy_pct` 無資料時不顯示 YoY 線與圖例，提示「目前僅顯示月營收」

### Null 顯示
```typescript
// ✅ 正確
value === null ? "—" : value.toFixed(2)

// ❌ 禁用：null 上呼叫 toFixed 會炸
value.toFixed(2)
```

### 配色
- 2026-04 已從 `zinc` 改為 `slate` 調（深色藍調）
- body: `bg-slate-*`、卡片: 卡片加透明度
- K 線圖深色主題為主

### ECharts 尺寸
- `StockChart`: `70vh / min 500px`
- `BacktestEquityChart`: `height: 380`（含回撤副圖）

### BacktestEquityChart
- Y 軸用**報酬率 %**（不是絕對金額）
- 有回撤副圖（drawdown %）與主圖 X 軸聯動
- 進出場標記：買入 ▲ 紅色三角、賣出 pin（獲利黃 / 虧損綠）
- tooltip 整合策略報酬、Buy & Hold、回撤三項

## 4. API 邊界

### realtime/quotes batch 上限
`/api/realtime/quotes` 單次最多 **50 檔**。前端查整個產業時必須自動分 batch，**不可**假設所有股票可一次取回。

### 產業聚合不在前端算
`industry_daily_flow` 仍是 L0 主查詢來源。**不要**把產業聚合搬回 API 臨時計算或前端計算。

## 5. L2 / L3 頁面結構

### 垂直 sidebar 導覽
L2 個股頁用 `SIDEBAR_ITEMS` 定義可切換 section。目前 `broker` entry 已移除（L2 券商面板在 2026-04-19 主動隱藏，元件程式碼保留）。

### chartDays 向下傳遞
`FinancialsPanel` 的三個子元件隨 K 線天數連動：
| 子元件 | days 轉換 |
|--------|----------|
| 估值 | 直接用 `chartDays` |
| 月營收 | `chartDays ÷ 30`（clamp 6~120 月） |
| 財報 | `chartDays ÷ 90`（clamp 4~20 季） |

### L3 回測 4 欄位
- 輸入：`entry_text` / `exit_text` textarea + `stop_loss_pct` / `take_profit_pct` 數字輸入
- `strategy_text` 保留向後相容（後端優先使用 4 欄位）
- 「查看可用條件列表」為可收合區塊，依後端 `CapabilityCatalog.groups` 渲染

### 策略文字預設值
由後端 `/api/backtest/templates` 第一筆決定，**前端不另存常數**。

## 6. Toggle 列（L2）

K 線圖下方為緊湊 pill 列（`ToggleChip` 元件），不要另闢 section：
- `回測程式 →`（連結）
- `財報`（toggle，localStorage: `always-stock:show-financials-panel`）
- 關鍵券商已隱藏（程式碼保留）

兩個 toggle 分別存 localStorage。

## 7. 首頁結構

`page.tsx` 載入順序：
1. `<DailyBrief>` — AI 盤前摘要（手動觸發）
2. `<TradeQualityAnalysis>` — 交易質量 AI 分析
3. `<IndustryDashboard>` — 產業排行

## 8. TradeQuality 輸入規範

- 股票代號 / 名稱 autocomplete（user 輸入即時 filter 下拉選單）
- 買進日期空白時 fallback 到 `/api/market/latest-trade-date`
- 5 階評級顏色：`STRONG_BUY`（深綠）/ `BUY`（綠）/ `NEUTRAL`（黃）/ `WATCH`（橘）/ `RUN`（紅）
- meta 列顯示 `market_state`(Hot/Cold)、`quadrant`(AA/AB/BA/BB)、`expectation_gap`(High/Medium/Low/Negative)
- 「詳細」按鈕 → 展開 PART 2 完整中文分析

## 9. 元件命名 / 跳轉

- 從 BacktestPanel 交易紀錄 / 最新訊號可跳回 L2 研究頁
- StockChart 有 `onDaysChange` prop，讓 L2 頁追蹤當前 K 線時間範圍（給 BrokerBarChart / FinancialsPanel 用）
