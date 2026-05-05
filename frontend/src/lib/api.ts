const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
const REALTIME_BATCH_SIZE = 50
const BROKER_FETCH_RETRIES = 1
const BROKER_FETCH_RETRY_DELAY_MS = 400
const LATEST_TRADE_DATE_CACHE_TTL_MS = 5 * 60 * 1000
const INDUSTRIES_CACHE_TTL_MS = 5 * 60 * 1000
const HOT_MONEY_CACHE_TTL_MS = 5 * 60 * 1000
const SIGNAL_SNAPSHOT_CACHE_TTL_MS = 60 * 1000
const CLIENT_CACHE_PREFIX = "always-stock:client-cache:"
const memoryCache = new Map<string, { expiresAt: number; value: unknown }>()
const pendingCache = new Map<string, Promise<unknown>>()

export function resetClientCacheForTests(): void {
  memoryCache.clear()
  pendingCache.clear()
  if (!canUseClientCache()) return
  try {
    const keysToRemove: string[] = []
    for (let i = 0; i < window.sessionStorage.length; i += 1) {
      const key = window.sessionStorage.key(i)
      if (key?.startsWith(CLIENT_CACHE_PREFIX)) keysToRemove.push(key)
    }
    keysToRemove.forEach((key) => window.sessionStorage.removeItem(key))
  } catch {
    // ignore
  }
}

/**
 * 統一的 fetch wrapper，帶上 session cookie（M18）。
 * 後端的 httpOnly session cookie 需要 credentials: 'include' 才會被帶上。
 */
export function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: "include" })
}

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
  bypassCache?: boolean
}

function canUseClientCache(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined"
}

function readClientCache<T>(key: string): T | null {
  const now = Date.now()
  const memory = memoryCache.get(key)
  if (memory) {
    if (memory.expiresAt > now) return memory.value as T
    memoryCache.delete(key)
  }

  if (!canUseClientCache()) return null
  try {
    const raw = window.sessionStorage.getItem(`${CLIENT_CACHE_PREFIX}${key}`)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { expiresAt: number; value: T }
    if (parsed.expiresAt <= now) {
      window.sessionStorage.removeItem(`${CLIENT_CACHE_PREFIX}${key}`)
      return null
    }
    memoryCache.set(key, { expiresAt: parsed.expiresAt, value: parsed.value })
    return parsed.value
  } catch {
    return null
  }
}

function writeClientCache<T>(key: string, value: T, ttlMs: number): void {
  const expiresAt = Date.now() + ttlMs
  memoryCache.set(key, { expiresAt, value })
  if (!canUseClientCache()) return
  try {
    window.sessionStorage.setItem(
      `${CLIENT_CACHE_PREFIX}${key}`,
      JSON.stringify({ expiresAt, value }),
    )
  } catch {
    // ignore storage quota / serialization issues
  }
}

async function fetchWithClientCache<T>(
  key: string,
  ttlMs: number,
  loader: () => Promise<T>,
): Promise<T> {
  const cached = readClientCache<T>(key)
  if (cached !== null) return cached

  const pending = pendingCache.get(key)
  if (pending) return pending as Promise<T>

  const request = loader()
    .then((value) => {
      writeClientCache(key, value, ttlMs)
      pendingCache.delete(key)
      return value
    })
    .catch((error) => {
      pendingCache.delete(key)
      throw error
    })

  pendingCache.set(key, request as Promise<unknown>)
  return request
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
  if (options?.bypassCache) {
    const res = await apiFetch(`${API_BASE}/api/industries?date=${date}`, { signal: options.signal })
    if (!res.ok) throw new Error(`Failed to fetch industries: ${res.status}`)
    return res.json()
  }
  return fetchWithClientCache(`industries:${date}`, INDUSTRIES_CACHE_TTL_MS, async () => {
    const res = await apiFetch(`${API_BASE}/api/industries?date=${date}`, { signal: options?.signal })
    if (!res.ok) throw new Error(`Failed to fetch industries: ${res.status}`)
    return res.json()
  })
}

export async function fetchIndustryStocks(
  industryName: string,
  date: string,
  options?: FetchOptions,
): Promise<StockFlowItem[]> {
  const res = await apiFetch(
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
  const res = await apiFetch(
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
  const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/history?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch history"))
  return res.json()
}

export async function fetchBacktestTemplates(options?: FetchOptions): Promise<BacktestTemplate[]> {
  const res = await apiFetch(`${API_BASE}/api/backtest/templates`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch backtest templates"))
  return res.json()
}

export async function fetchBacktestCapabilities(options?: FetchOptions): Promise<BacktestCapabilityCatalog> {
  const res = await apiFetch(`${API_BASE}/api/backtest/capabilities`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Failed to fetch backtest capabilities"))
  return res.json()
}

export async function interpretBacktest(
  payload: BacktestRunRequest,
  options?: FetchOptions,
): Promise<BacktestInterpretResponse> {
  const res = await apiFetch(`${API_BASE}/api/backtest/interpret`, {
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
  const res = await apiFetch(`${API_BASE}/api/backtest/run`, {
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
  const res = await apiFetch(`${API_BASE}/api/backtest/advice`, {
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
      const res = await apiFetch(`${API_BASE}/api/realtime/quotes?stock_ids=${ids}`)
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
  const res = await apiFetch(
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
  const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/brokers/ranked?${params}`, {
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
      const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/brokers?${params}`, {
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
  const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/valuation?${params}`, {
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
  const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/revenue?months=${months}`, {
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
  const res = await apiFetch(`${API_BASE}/api/stocks/${stockId}/financials?${params}`, {
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
  const res = await apiFetch(`${API_BASE}/api/market/daily-brief?date=${date}`, {
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
  const res = await apiFetch(`${API_BASE}/api/stocks/search?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "股票搜尋失敗"))
  return res.json()
}

export async function fetchLatestTradeDate(options?: FetchOptions): Promise<string | null> {
  if (options?.bypassCache) {
    const res = await apiFetch(`${API_BASE}/api/market/latest-trade-date`, {
      signal: options.signal,
    })
    if (!res.ok) throw new Error(await buildErrorMessage(res, "最新交易日載入失敗"))
    const data: { trade_date: string | null } = await res.json()
    return data.trade_date
  }
  return fetchWithClientCache("latest-trade-date", LATEST_TRADE_DATE_CACHE_TTL_MS, async () => {
    const res = await apiFetch(`${API_BASE}/api/market/latest-trade-date`, {
      signal: options?.signal,
    })
    if (!res.ok) throw new Error(await buildErrorMessage(res, "最新交易日載入失敗"))
    const data: { trade_date: string | null } = await res.json()
    return data.trade_date
  })
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
  key_factors?: KeyFactor[] | null
  warnings: string[]
  source: "openai" | "unavailable" | "market_not_open" | "cache"
}

// M25 條列指標 + 燈號用
export type KeyFactorCategory =
  | "industry"
  | "industry_heat"
  | "return"
  | "chip"
  | "technical"
  | "fundamental"

export type KeyFactorLevel = "A" | "B" | "C"
export type KeyFactorTrend = "improving" | "stable" | "weakening" | "deteriorating"

export interface KeyFactor {
  category: KeyFactorCategory | string
  level: KeyFactorLevel | string
  trend: KeyFactorTrend | string
  note: string
}

export type TradeQualityStreamStage =
  | "collect_raw"
  | "build_context"
  | "openai_call"
  | "done"
  | "error"

export interface TradeQualityStreamEvent {
  stage: TradeQualityStreamStage
  label: string
  payload?: TradeQualityResponse | { detail: string }
}

/**
 * Stream trade-quality analysis as NDJSON。每行是一個 event。
 * onEvent 會被依序呼叫；最後一個 event 一定是 stage="done"（成功）或 "error"（失敗）。
 * 成功時 done event 的 payload 為 TradeQualityResponse；失敗時為 { detail: string }。
 */
export async function streamTradeQuality(
  payload: { stock_id: string; buy_date?: string | null },
  onEvent: (event: TradeQualityStreamEvent) => void,
  options?: FetchOptions,
): Promise<TradeQualityResponse> {
  const body: Record<string, unknown> = { stock_id: payload.stock_id }
  if (payload.buy_date) body.buy_date = payload.buy_date
  const res = await apiFetch(`${API_BASE}/api/analysis/trade-quality/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "交易質量分析失敗"))
  if (!res.body) throw new Error("交易質量分析回傳缺少 body")

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  // 注意：TS CFA 不追 closure 內的 mutation，若用 `let x: T | null = null` 然後在
  // `handleLine` 內賦值，迴圈外 TS 會把它收斂成 `never`。`as` 顯式留住 union 型別。
  let lastDonePayload = null as TradeQualityResponse | null
  let lastError = null as { detail: string } | null

  const handleLine = (line: string) => {
    if (!line.trim()) return
    let event: TradeQualityStreamEvent
    try {
      event = JSON.parse(line) as TradeQualityStreamEvent
    } catch {
      return
    }
    onEvent(event)
    if (event.stage === "done" && event.payload && "stock_id" in event.payload) {
      lastDonePayload = event.payload as TradeQualityResponse
    } else if (event.stage === "error") {
      lastError = (event.payload as { detail: string } | undefined) ?? {
        detail: event.label || "分析過程發生錯誤",
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) handleLine(line)
  }
  // flush 最後一段（若 stream 沒以 \n 結尾）
  if (buffer.trim()) handleLine(buffer)

  if (lastError) throw new Error(lastError.detail)
  if (!lastDonePayload) throw new Error("交易質量分析未回傳結果")
  return lastDonePayload
}

// ── Auth (M18) ──────────────────────────────────────────────────────────────

export interface AuthUser {
  id: number
  email: string
  name: string | null
  is_admin: boolean
}

async function parseAuthError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data?.detail === "string") return data.detail
  } catch {
    // ignore
  }
  return fallback
}

export async function fetchCurrentUser(options?: FetchOptions): Promise<AuthUser | null> {
  const res = await apiFetch(`${API_BASE}/api/auth/me`, { signal: options?.signal })
  if (res.status === 401) return null
  if (!res.ok) throw new Error(await parseAuthError(res, "無法取得使用者資訊"))
  return res.json()
}

export async function registerUser(payload: {
  email: string
  password: string
  name?: string
}): Promise<AuthUser> {
  const res = await apiFetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await parseAuthError(res, "註冊失敗"))
  return res.json()
}

export async function loginUser(payload: {
  email: string
  password: string
}): Promise<AuthUser> {
  const res = await apiFetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await parseAuthError(res, "登入失敗"))
  return res.json()
}

export async function logoutUser(): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/auth/logout`, { method: "POST" })
  if (!res.ok) throw new Error(await parseAuthError(res, "登出失敗"))
}

// ── Hot money (M22) ────────────────────────────────────────────────────────

export interface HotMoneyStockItem {
  rank: number
  stock_id: string
  stock_name: string
  industry_name: string
  sub_industry: string | null
  start_close_price: number | null
  end_close_price: number | null
  price_change_pct: number | null
  foreign_net_amount: number
  trust_net_amount: number
  dealer_net_amount: number
  total_net_amount: number
}

export interface HotMoneyResponse {
  start_date: string | null
  end_date: string | null
  trade_dates: string[]
  items: HotMoneyStockItem[]
}

export async function fetchMarketHotMoney(
  date: string,
  days = 3,
  limit = 20,
  options?: FetchOptions,
): Promise<HotMoneyResponse> {
  if (options?.bypassCache) {
    const params = new URLSearchParams({
      date,
      days: String(days),
      limit: String(limit),
    })
    const res = await apiFetch(`${API_BASE}/api/market/hot-money?${params}`, {
      signal: options.signal,
    })
    if (!res.ok) throw new Error(await buildErrorMessage(res, "熱錢排行載入失敗"))
    return res.json()
  }
  return fetchWithClientCache(`market-hot-money:${date}:${days}:${limit}`, HOT_MONEY_CACHE_TTL_MS, async () => {
    const params = new URLSearchParams({
      date,
      days: String(days),
      limit: String(limit),
    })
    const res = await apiFetch(`${API_BASE}/api/market/hot-money?${params}`, {
      signal: options?.signal,
    })
    if (!res.ok) throw new Error(await buildErrorMessage(res, "熱錢排行載入失敗"))
    return res.json()
  })
}

// ── Watchlist (M19) ────────────────────────────────────────────────────────

export interface WatchlistItem {
  id: number
  stock_id: string
  stock_name: string
  industry_name: string | null
  latest_close: number | null
  latest_trade_date: string | null
}

export interface WatchlistResponse {
  items: WatchlistItem[]
  total: number
  capacity: number
}

export interface WatchlistCreateRequest {
  stock_id: string
}

export async function fetchWatchlist(options?: FetchOptions): Promise<WatchlistResponse> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, { signal: options?.signal })
  if (res.status === 401) {
    return { items: [], total: 0, capacity: 20 }
  }
  if (!res.ok) throw new Error(await buildErrorMessage(res, "清單載入失敗"))
  return res.json()
}

export async function addWatchlistEntry(payload: WatchlistCreateRequest): Promise<WatchlistItem> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "加入清單失敗"))
  return res.json()
}

export async function removeWatchlistEntry(entryId: number): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/watchlist/${entryId}`, { method: "DELETE" })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "移除失敗"))
}

export async function clearWatchlistEntries(): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/watchlist`, { method: "DELETE" })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "清空清單失敗"))
}

// ---------------------------------------------------------------------------
// M25 自選清單 trade quality 快照表

export interface WatchlistSnapshotPayload {
  snapshot_trade_date: string
  rating: TradeQualityRating | null
  rating_label: string | null
  classification: string | null
  market_state: string | null
  action: string | null
  summary: string | null
  report_markdown: string | null
  key_factors: KeyFactor[] | null
  status: "ok" | "failed"
  is_stale: boolean
  generated_at: string
}

export interface WatchlistFactorSnapshot {
  snapshot_trade_date: string
  key_factors: KeyFactor[] | null
}

export interface WatchlistTradeQualityItem {
  stock_id: string
  stock_name: string
  industry_name: string | null
  latest_close: number | null
  latest_trade_date: string | null
  change_pct: number | null
  latest: WatchlistSnapshotPayload | null
  previous: WatchlistSnapshotPayload | null
  /** 最近 N 個 ok 快照（snapshot_trade_date 倒序，最新在前），給 KeyFactorsTimeline 多日趨勢用 */
  recent_factors: WatchlistFactorSnapshot[]
}

export interface WatchlistTradeQualityResponse {
  items: WatchlistTradeQualityItem[]
  total: number
  snapshot_trade_date: string | null
}

export async function fetchWatchlistTradeQuality(
  options?: FetchOptions,
): Promise<WatchlistTradeQualityResponse> {
  const res = await apiFetch(`${API_BASE}/api/watchlist/trade-quality`, {
    signal: options?.signal,
  })
  if (res.status === 401) {
    return { items: [], total: 0, snapshot_trade_date: null }
  }
  if (!res.ok) throw new Error(await buildErrorMessage(res, "自選清單交易質量載入失敗"))
  return res.json()
}

export async function refreshWatchlistTradeQuality(
  stockId: string,
  options?: FetchOptions,
): Promise<WatchlistTradeQualityItem> {
  const res = await apiFetch(`${API_BASE}/api/watchlist/trade-quality/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stock_id: stockId }),
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "重新分析失敗"))
  return res.json()
}

export async function fetchIndustryHotMoney(
  industryName: string,
  date: string,
  opts: { days?: number; limit?: number; subIndustry?: string | null } = {},
  fetchOptions?: FetchOptions,
): Promise<HotMoneyResponse> {
  const params = new URLSearchParams({
    date,
    days: String(opts.days ?? 3),
    limit: String(opts.limit ?? 10),
  })
  if (opts.subIndustry) params.set("sub_industry", opts.subIndustry)
  const res = await apiFetch(
    `${API_BASE}/api/industries/${encodeURIComponent(industryName)}/hot-money?${params}`,
    { signal: fetchOptions?.signal },
  )
  if (!res.ok) throw new Error(await buildErrorMessage(res, "熱錢排行載入失敗"))
  return res.json()
}

// ---------------------------------------------------------------------------
// M23 每日異常訊號清單（slice 8）
// ---------------------------------------------------------------------------

export type SignalDecisionType = "LEADER" | "FOLLOWER" | "LAGGARD"

export interface SignalWatchlistItem {
  stock: string
  name?: string | null
  type?: SignalDecisionType | null
  industry?: string | null
  sub_industry?: string | null
  business_summary?: string | null
  supply_chain_position?: string | null
  theme_fit?: string | null
  theme?: {
    main_theme?: string | null
    theme_duration?: string | null
    theme_score?: number | null
    theme_reason?: string | null
  } | null
  group_info?: {
    is_group_stock?: boolean | null
    group_name?: string | null
    related_group_stocks?: string[] | null
    group_price_sync?: string | null
  } | null
  leader_check?: {
    industry_leader?: string | null
    leader_price_trend?: string | null
    leader_supports_theme?: boolean | null
  } | null
  signals?: {
    capital_flow?: string | null
    chip_trend?: string | null
    margin_short_signal?: string | null
    technical_status?: string | null
  } | null
  decision?: string | null
  reason?: string | null
}

export interface SignalSummary {
  main_hot_industries?: string[] | null
  leader_count?: number | null
  follower_count?: number | null
  laggard_count?: number | null
  risk_note?: string | null
}

export interface SignalMarketContext {
  market_state?: string | null
  taiex_change_pct?: number | null
  otc_change_pct?: number | null
  vix_status?: string | null
  futures_bias?: string | null
  market_state_reason?: string | null
}

export interface SignalSnapshotData {
  market_context: SignalMarketContext
  watchlist: SignalWatchlistItem[]
  summary: SignalSummary
  candidate_pool_size: number | null
  final_watchlist_size: number | null
}

export interface SignalSnapshotResponse {
  snapshot_date: string
  generated_at: string
  llm_model: string | null
  data: SignalSnapshotData
}

export type SignalJobStatus = "pending" | "running" | "done" | "failed"

export interface SignalJobResponse {
  job_id: string
  snapshot_date: string
  status: SignalJobStatus
  current_stage: string | null
  progress_pct: number
  progress_label: string | null
  started_at: string
  finished_at: string | null
  error_message: string | null
}

export interface SignalRegenerateResponse {
  job_id: string
  snapshot_date: string
}

export interface SignalRegenerateQuotaResponse {
  snapshot_date: string
  daily_limit: number
  used_count: number
  remaining_count: number
  disabled: boolean
}

export type SignalArchiveSortBy =
  | "tracking_days_desc"
  | "return_desc"
  | "return_asc"
  | "hit_count_desc"
  | "latest_hit_desc"
  | "stock_id_asc"

export interface SignalArchiveSummaryItem {
  stock_id: string
  stock_name: string
  industry_name: string | null
  sub_industry: string | null
  first_seen_date: string
  latest_hit_date: string
  tracking_day_index: number
  hit_count: number
  latest_signal_type: string
  baseline_trade_date: string | null
  baseline_price: number | null
  latest_eval_trade_date: string | null
  latest_eval_price: number | null
  return_pct: number | null
  max_positive_return_pct: number | null
  max_positive_return_trade_date: string | null
  max_negative_return_pct: number | null
  max_negative_return_trade_date: string | null
}

export interface SignalArchiveSummaryResponse {
  as_of_trade_date: string | null
  retention_trade_days: number
  items: SignalArchiveSummaryItem[]
}

export interface SignalArchiveCompletedItem {
  stock_id: string
  stock_name: string
  industry_name: string | null
  sub_industry: string | null
  first_seen_date: string
  latest_hit_date: string
  hit_count: number
  latest_signal_type: string
  baseline_trade_date: string | null
  baseline_price: number | null
  return_day_10_pct: number | null
  return_day_20_pct: number | null
  return_day_30_pct: number | null
  return_day_40_pct: number | null
  max_positive_return_pct: number | null
  max_positive_return_trade_date: string | null
  max_negative_return_pct: number | null
  max_negative_return_trade_date: string | null
  completed_trade_date: string
}

export interface SignalArchiveCompletedResponse {
  items: SignalArchiveCompletedItem[]
}

export interface SignalArchiveReportItem {
  snapshot_date: string
  signal_type: string
  reason: string
  business_summary: string | null
  snapshot_generated_at: string | null
}

export interface SignalArchiveDetailResponse extends SignalArchiveSummaryItem {
  reports: SignalArchiveReportItem[]
}

export async function fetchLatestSignalSnapshot(
  options?: FetchOptions,
): Promise<SignalSnapshotResponse | null> {
  if (options?.bypassCache) {
    const res = await apiFetch(`${API_BASE}/api/signals/latest`, {
      signal: options.signal,
    })
    if (res.status === 404) return null
    if (!res.ok) throw new Error(await buildErrorMessage(res, "訊號清單載入失敗"))
    return res.json()
  }
  return fetchWithClientCache("signals:latest-snapshot", SIGNAL_SNAPSHOT_CACHE_TTL_MS, async () => {
    const res = await apiFetch(`${API_BASE}/api/signals/latest`, {
      signal: options?.signal,
    })
    if (res.status === 404) return null
    if (!res.ok) throw new Error(await buildErrorMessage(res, "訊號清單載入失敗"))
    return res.json()
  })
}

export async function fetchSignalSnapshotByDate(
  snapshotDate: string,
  options?: FetchOptions,
): Promise<SignalSnapshotResponse | null> {
  const res = await apiFetch(
    `${API_BASE}/api/signals/snapshot/${encodeURIComponent(snapshotDate)}`,
    { signal: options?.signal },
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(await buildErrorMessage(res, "訊號快照載入失敗"))
  return res.json()
}

export async function fetchLatestSignalJob(
  options?: FetchOptions,
): Promise<SignalJobResponse | null> {
  const res = await apiFetch(`${API_BASE}/api/signals/jobs/latest`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Job 狀態載入失敗"))
  return res.json()
}

export async function fetchSignalRegenerateQuota(
  options?: FetchOptions,
): Promise<SignalRegenerateQuotaResponse> {
  const res = await apiFetch(`${API_BASE}/api/signals/quota`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "重產額度載入失敗"))
  return res.json()
}

export async function fetchSignalArchive(
  params?: {
    sort_by?: SignalArchiveSortBy
    type?: string
    limit?: number
  },
  options?: FetchOptions,
): Promise<SignalArchiveSummaryResponse> {
  const qs = new URLSearchParams()
  if (params?.sort_by) qs.set("sort_by", params.sort_by)
  if (params?.type) qs.set("type", params.type)
  if (params?.limit != null) qs.set("limit", String(params.limit))
  const url = `${API_BASE}/api/signals/archive${qs.toString() ? `?${qs.toString()}` : ""}`
  const res = await apiFetch(url, { signal: options?.signal })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "訊號追蹤清單載入失敗"))
  return res.json()
}

export async function fetchSignalArchiveDetail(
  stockId: string,
  options?: FetchOptions,
): Promise<SignalArchiveDetailResponse | null> {
  const res = await apiFetch(`${API_BASE}/api/signals/archive/${encodeURIComponent(stockId)}`, {
    signal: options?.signal,
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(await buildErrorMessage(res, "訊號追蹤詳情載入失敗"))
  return res.json()
}

export async function fetchCompletedSignalArchive(
  params?: {
    limit?: number
  },
  options?: FetchOptions,
): Promise<SignalArchiveCompletedResponse> {
  const qs = new URLSearchParams()
  if (params?.limit != null) qs.set("limit", String(params.limit))
  const url = `${API_BASE}/api/signals/archive/completed${qs.toString() ? `?${qs.toString()}` : ""}`
  const res = await apiFetch(url, { signal: options?.signal })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "40日移出紀錄載入失敗"))
  return res.json()
}

export async function regenerateSignals(): Promise<SignalRegenerateResponse> {
  const res = await apiFetch(`${API_BASE}/api/signals/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "重新產生失敗"))
  memoryCache.delete("signals:latest-snapshot")
  pendingCache.delete("signals:latest-snapshot")
  if (canUseClientCache()) {
    window.sessionStorage.removeItem(`${CLIENT_CACHE_PREFIX}signals:latest-snapshot`)
  }
  return res.json()
}
