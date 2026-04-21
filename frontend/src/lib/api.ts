const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
const REALTIME_BATCH_SIZE = 50
const BROKER_FETCH_RETRIES = 1
const BROKER_FETCH_RETRY_DELAY_MS = 400

export interface IndustryFlowItem {
  industry_name: string
  total_net_amount: number
  foreign_net_amount: number
  trust_net_amount: number
  dealer_net_amount: number
  total_buy_amount: number
  total_sell_amount: number
  streak: number // positive = consecutive buy days, negative = sell days
}

export interface StockFlowItem {
  stock_id: string
  stock_name: string
  industry_name: string
  sub_industry: string | null
  close_price: number | null
  prev_close_price: number | null
  price_change: number | null
  price_change_pct: number | null
  foreign_net_shares: number
  trust_net_shares: number
  dealer_net_shares: number
  foreign_net_amount: number
  trust_net_amount: number
  dealer_net_amount: number
}

export interface SubIndustrySummaryItem {
  sub_industry: string
  total_net_amount: number
  foreign_net_amount: number
  trust_net_amount: number
  dealer_net_amount: number
  streak: number
}

export interface StockHistoryItem {
  trade_date: string
  open_price: number | null
  high_price: number | null
  low_price: number | null
  close_price: number
  foreign_net_shares: number
  trust_net_shares: number
  dealer_net_shares: number
  foreign_cumulative: number
  trust_cumulative: number
  dealer_cumulative: number
}

export interface StockHistoryResponse {
  stock_id: string
  stock_name: string
  industry_name: string
  sub_industry: string | null
  history: StockHistoryItem[]
  earliest_date: string | null
  latest_date: string | null
}

export interface FetchOptions {
  signal?: AbortSignal
}

export interface BacktestTemplate {
  id: string
  name: string
  description: string
  entry_text?: string
  exit_text?: string
  stop_loss_pct?: number | null
  take_profit_pct?: number | null
  strategy_text: string
}

export interface BacktestCapabilityItem {
  id: string
  category?: string
  label: string
  aliases?: string[]
  examples: string[]
}

export interface BacktestCapabilityGroup {
  id: string
  label: string
  categories: string[]
}

export interface BacktestCapabilityCatalog {
  groups?: BacktestCapabilityGroup[]
  indicators: BacktestCapabilityItem[]
  risk_controls: BacktestCapabilityItem[]
  notes: string[]
}

export interface BacktestRunRequest {
  stock_id: string
  start_date: string
  end_date: string
  initial_capital: number
  entry_text?: string
  exit_text?: string
  stop_loss_pct?: number | null
  take_profit_pct?: number | null
  strategy_text?: string
}

export interface BacktestInterpretResponse {
  supported: boolean
  normalized_text: string
  strategy: Record<string, unknown>
  unsupported_conditions: string[]
  ai_mapped_conditions: string[]
  warnings: string[]
}

export interface BacktestMetricSummary {
  total_return_pct: number
  annual_return_pct: number
  win_rate_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  trade_count: number
  ending_equity: number
  benchmark_return_pct: number
  excess_return_pct: number
  avg_trade_return_pct: number
  avg_holding_days: number
  profit_factor: number | null
  avg_gain_pct: number | null
  avg_loss_pct: number | null
  max_consecutive_wins: number
  max_consecutive_losses: number
}

export interface BacktestEquityPoint {
  trade_date: string
  equity: number
  benchmark_equity: number
}

export interface BacktestTrade {
  entry_date: string
  exit_date: string
  entry_price: number
  exit_price: number
  holding_days: number
  return_pct: number
  pnl_amount: number
  exit_reason: string
}

export interface BacktestPeriodReturnItem {
  period: string
  return_pct: number
}

export interface BacktestRunResponse {
  supported: boolean
  normalized_text: string
  strategy: Record<string, unknown>
  unsupported_conditions: string[]
  ai_mapped_conditions: string[]
  metrics: BacktestMetricSummary
  equity_curve: BacktestEquityPoint[]
  period_returns: {
    monthly: BacktestPeriodReturnItem[]
    quarterly: BacktestPeriodReturnItem[]
    yearly: BacktestPeriodReturnItem[]
  }
  trades: BacktestTrade[]
  latest_recommendation: {
    latest_signal_date: string
    action: string
    reason: string
  }
  warnings: string[]
}

export interface BacktestAdviceRequest {
  stock_id: string
  strategy_text: string
  normalized_text: string
  metrics: BacktestMetricSummary
  trades: BacktestTrade[]
  latest_recommendation: {
    latest_signal_date: string
    action: string
    reason: string
  }
}

export interface BacktestAdviceResponse {
  summary: string
  strengths: string[]
  weaknesses: string[]
  rewrite_suggestions: string[]
  risk_notes: string[]
  source: "openai" | "heuristic"
}

async function buildErrorMessage(res: Response, fallbackPrefix: string): Promise<string> {
  let detail = ""

  try {
    const data = await res.json()
    if (typeof data?.detail === "string") {
      detail = data.detail
    }
  } catch {
    detail = ""
  }

  return detail ? `${fallbackPrefix}: ${detail}` : `${fallbackPrefix}: ${res.status}`
}

export function toDisplayError(error: unknown, fallback = "載入失敗"): string {
  if (error instanceof Error) {
    if (error.message.includes("503")) return "資料庫忙碌中，請稍後再試"
    if (error.message.includes("404")) return "此日期無資料，請選擇其他交易日"
    if (error.message.includes("start_date cannot be later than end_date")) return "開始日期不能晚於結束日期"
    if (error.message.includes("Strategy text cannot be blank")) return "策略文字不能空白"
    if (error.message.includes("Strategy text must contain one buy clause")) return "策略格式不完整，請確認買進與賣出條件之間有用分號分開"
    if (error.message.includes("Unsupported strategy conditions:")) {
      const detail = error.message.split("Unsupported strategy conditions:").at(-1)?.trim()
      return detail ? `目前不支援這些條件：${detail}` : "策略中包含目前不支援的條件"
    }
    if (error.message.includes("422")) return "策略格式或回測條件有問題，請調整後重試"
    return error.message
  }
  return fallback
}

export async function fetchIndustries(date: string, options?: FetchOptions): Promise<IndustryFlowItem[]> {
  const res = await fetch(`${API_BASE}/api/industries?date=${date}`, { signal: options?.signal })
  if (!res.ok) throw new Error(`Failed to fetch industries: ${res.status}`)
  return res.json()
}

export async function fetchIndustryStocks(
  industryName: string,
  date: string,
  options?: FetchOptions,
): Promise<StockFlowItem[]> {
  const res = await fetch(
    `${API_BASE}/api/industries/${encodeURIComponent(industryName)}/stocks?date=${date}`,
    { signal: options?.signal },
  )
  if (!res.ok) throw new Error(`Failed to fetch stocks: ${res.status}`)
  return res.json()
}

export async function fetchSubIndustrySummary(
  industryName: string,
  date: string,
  options?: FetchOptions,
): Promise<SubIndustrySummaryItem[]> {
  const res = await fetch(
    `${API_BASE}/api/industries/${encodeURIComponent(industryName)}/summary?date=${date}`,
    { signal: options?.signal },
  )
  if (!res.ok) throw new Error(`Failed to fetch summary: ${res.status}`)
  return res.json()
}

export async function fetchStockHistory(
  stockId: string,
  days = 60,
  endDate?: string,
  options?: FetchOptions & { startDate?: string },
): Promise<StockHistoryResponse> {
  const params = new URLSearchParams({ days: String(days) })
  if (endDate) params.set("end_date", endDate)
  if (options?.startDate) params.set("start_date", options.startDate)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/history?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch history"))
  return res.json()
}

export async function fetchBacktestTemplates(options?: FetchOptions): Promise<BacktestTemplate[]> {
  const res = await fetch(`${API_BASE}/api/backtest/templates`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch backtest templates"))
  return res.json()
}

export async function fetchBacktestCapabilities(options?: FetchOptions): Promise<BacktestCapabilityCatalog> {
  const res = await fetch(`${API_BASE}/api/backtest/capabilities`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch backtest capabilities"))
  return res.json()
}

export async function interpretBacktest(
  payload: BacktestRunRequest,
  options?: FetchOptions,
): Promise<BacktestInterpretResponse> {
  const res = await fetch(`${API_BASE}/api/backtest/interpret`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to interpret backtest"))
  return res.json()
}

export async function runBacktest(
  payload: BacktestRunRequest,
  options?: FetchOptions,
): Promise<BacktestRunResponse> {
  const res = await fetch(`${API_BASE}/api/backtest/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to run backtest"))
  return res.json()
}

export async function fetchBacktestAdvice(
  payload: BacktestAdviceRequest,
  options?: FetchOptions,
): Promise<BacktestAdviceResponse> {
  const res = await fetch(`${API_BASE}/api/backtest/advice`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch backtest advice"))
  return res.json()
}

export interface RealtimeQuote {
  stock_id: string
  stock_name: string
  price: number | null
  prev_close: number
  change: number | null
  change_pct: number | null
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  trade_time: string | null
}

export async function fetchRealtimeQuotes(stockIds: string[]): Promise<RealtimeQuote[]> {
  if (stockIds.length === 0) return []
  const batches: string[][] = []
  for (let i = 0; i < stockIds.length; i += REALTIME_BATCH_SIZE) {
    batches.push(stockIds.slice(i, i + REALTIME_BATCH_SIZE))
  }

  const responses = await Promise.all(
    batches.map(async (batch) => {
      const ids = batch.join(",")
      const res = await fetch(`${API_BASE}/api/realtime/quotes?stock_ids=${ids}`)
      if (!res.ok) return []
      return res.json() as Promise<RealtimeQuote[]>
    })
  )

  return responses.flat()
}

// ── Broker trades (M13) ─────────────────────────────────────────────────────

export type BrokerCategory = "day_trade" | "next_day" | "short_term" | "swing"

export interface BrokerTradeItem {
  broker_id: string
  broker_name: string
  display_name: string
  buy_shares: number
  sell_shares: number
  net_shares: number
  categories: string[]
}

export interface BrokerRankedResponse {
  stock_id: string
  trade_date: string
  is_refreshing?: boolean
  buy_top: BrokerTradeItem[]
  sell_top: BrokerTradeItem[]
}

export interface BrokerTradeResponse {
  stock_id: string
  trade_date: string
  category: string
  category_label: string
  is_refreshing?: boolean
  brokers: BrokerTradeItem[]
}

export interface BrokerTradeFetchOptions {
  signal?: AbortSignal
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

function isRetryableNetworkError(error: unknown): boolean {
  return error instanceof TypeError && !isAbortError(error)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export interface BrokerDailyItem {
  trade_date: string
  buy_shares: number
  sell_shares: number
  net_shares: number
}

export interface BrokerHistoryResponse {
  stock_id: string
  broker_id: string
  broker_name: string
  display_name: string
  history: BrokerDailyItem[]
}

export async function fetchBrokerHistory(
  stockId: string,
  brokerId: string,
  startDate: string,
  endDate: string,
  options?: FetchOptions,
): Promise<BrokerHistoryResponse> {
  const params = new URLSearchParams({ start: startDate, end: endDate })
  const res = await fetch(
    `${API_BASE}/api/stocks/${stockId}/brokers/${encodeURIComponent(brokerId)}/history?${params}`,
    { signal: options?.signal },
  )
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch broker history"))
  return res.json()
}

export async function fetchBrokerRanked(
  stockId: string,
  date?: string,
  days = 1,
  options?: BrokerTradeFetchOptions,
): Promise<BrokerRankedResponse> {
  const params = new URLSearchParams({ days: String(days) })
  if (date) params.set("date", date)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/brokers/ranked?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(`Failed to fetch broker ranked: ${res.status}`)
  return res.json()
}

export async function fetchBrokerTrades(
  stockId: string,
  category: BrokerCategory = "day_trade",
  date?: string,
  days = 1,
  options?: BrokerTradeFetchOptions,
): Promise<BrokerTradeResponse> {
  const params = new URLSearchParams({ category, days: String(days) })
  if (date) params.set("date", date)
  let lastError: unknown

  for (let attempt = 0; attempt <= BROKER_FETCH_RETRIES; attempt += 1) {
    try {
      const res = await fetch(`${API_BASE}/api/stocks/${stockId}/brokers?${params}`, {
        signal: options?.signal,
      })
      if (!res.ok) throw new Error(`Failed to fetch broker trades: ${res.status}`)
      return res.json()
    } catch (error) {
      if (isAbortError(error)) throw error
      lastError = error
      if (!isRetryableNetworkError(error) || attempt === BROKER_FETCH_RETRIES) {
        throw error
      }
      await sleep(BROKER_FETCH_RETRY_DELAY_MS)
    }
  }

  throw lastError instanceof Error ? lastError : new Error("Failed to fetch broker trades")
}

/** Format shares in 張 (lots of 1000 shares) */
export function fmtLots(shares: number): string {
  const lots = shares / 1000
  if (Math.abs(lots) >= 10000) {
    return (lots >= 0 ? "+" : "") + (lots / 10000).toFixed(1) + "萬張"
  }
  return (lots >= 0 ? "+" : "") + lots.toFixed(0) + "張"
}

/** Format NT$ amount (raw value is NT$1) → display in 億元 */
export function fmtAmount(val: number): string {
  const yi = val / 1e8
  return (yi >= 0 ? "+" : "") + yi.toFixed(2) + "億"
}

/** Format shares in 萬股 */
export function fmtShares(val: number): string {
  const wan = val / 1e4
  return (wan >= 0 ? "+" : "") + wan.toFixed(0) + "萬"
}

/** Format streak: +5 → "連買5天", -3 → "連賣3天", 0 → "-" */
export function fmtStreak(streak: number): string {
  if (streak === 0) return "-"
  const abs = Math.abs(streak)
  const days = abs >= 31 ? "30+" : `${abs}`
  return streak > 0 ? `連買${days}天` : `連賣${days}天`
}

// ── Financials (M8) ───────────────────────────────────────────────────────

export interface ValuationItem {
  trade_date: string
  per: number | null
  pbr: number | null
  dividend_yield: number | null
}

export interface ValuationResponse {
  stock_id: string
  stock_name: string
  history: ValuationItem[]
}

export interface RevenueItem {
  revenue_month: string
  revenue: number | null
  yoy_pct: number | null
  mom_pct: number | null
}

export interface RevenueResponse {
  stock_id: string
  stock_name: string
  history: RevenueItem[]
}

export interface FinancialItem {
  report_date: string
  item_name: string
  item_code: string | null
  value: number | null
  period_type: string
  unit: string | null
}

export interface FinancialResponse {
  stock_id: string
  stock_name: string
  items: FinancialItem[]
}

export async function fetchValuation(
  stockId: string,
  startDate?: string,
  endDate?: string,
  options?: FetchOptions,
): Promise<ValuationResponse> {
  const params = new URLSearchParams()
  if (startDate) params.set("start_date", startDate)
  if (endDate) params.set("end_date", endDate)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/valuation?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "估值資料載入失敗"))
  return res.json()
}

export async function fetchRevenue(
  stockId: string,
  months = 24,
  options?: FetchOptions,
): Promise<RevenueResponse> {
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/revenue?months=${months}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "月營收載入失敗"))
  return res.json()
}

export async function fetchFinancials(
  stockId: string,
  quarters = 8,
  itemNames?: string,
  options?: FetchOptions,
): Promise<FinancialResponse> {
  const params = new URLSearchParams({ quarters: String(quarters) })
  if (itemNames) params.set("item_names", itemNames)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/financials?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "財報資料載入失敗"))
  return res.json()
}

// ── Daily brief (market LLM analysis) ──────────────────────────────────────

export interface DailyBriefResponse {
  trade_date: string
  content: string
  source: "openai" | "unavailable"
}

export async function fetchDailyBrief(date: string, options?: FetchOptions): Promise<DailyBriefResponse> {
  const res = await fetch(`${API_BASE}/api/market/daily-brief?date=${date}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "盤前摘要載入失敗"))
  return res.json()
}

// ── Trade quality analysis ───────────────────────────────────────────────

export interface StockSearchItem {
  stock_id: string
  stock_name: string
  industry_name: string
}

export async function searchStocks(q: string, options?: FetchOptions): Promise<StockSearchItem[]> {
  const params = new URLSearchParams({ q, limit: "20" })
  const res = await fetch(`${API_BASE}/api/stocks/search?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "股票搜尋失敗"))
  return res.json()
}

export async function fetchLatestTradeDate(options?: FetchOptions): Promise<string | null> {
  const res = await fetch(`${API_BASE}/api/market/latest-trade-date`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "最新交易日載入失敗"))
  const data: { trade_date: string | null } = await res.json()
  return data.trade_date
}

export type TradeQualityRating = "STRONG_BUY" | "BUY" | "NEUTRAL" | "WATCH" | "RUN"

export interface TradeQualityResponse {
  stock_id: string
  stock_name: string
  buy_date: string
  rating: TradeQualityRating
  rating_label: string
  classification?: string | null
  market_state?: string | null
  quadrant?: string | null
  expectation_gap?: string | null
  action?: string | null
  summary: string
  core_logic?: string | null
  risk_level?: string | null
  target_price_low?: number | null
  target_price_high?: number | null
  time_horizon_days?: number | null
  exit_price_low?: number | null
  exit_price_high?: number | null
  max_holding_days?: number | null
  report_markdown: string
  warnings: string[]
  source: "openai" | "unavailable" | "market_not_open"
}

export async function analyzeTradeQuality(
  payload: { stock_id: string; buy_date?: string | null },
  options?: FetchOptions,
): Promise<TradeQualityResponse> {
  const body: Record<string, unknown> = { stock_id: payload.stock_id }
  if (payload.buy_date) body.buy_date = payload.buy_date
  const res = await fetch(`${API_BASE}/api/analysis/trade-quality`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "交易質量分析失敗"))
  return res.json()
}
