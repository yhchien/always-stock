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

/**
 * Phase 1 Canonical Market Classification（2026-07-21，display-only）。
 * 這是「顯示層」補充資訊，不代表魚尾選股 pipeline 已改用這套分類——選股仍依
 * `industry_name` / `sub_industry`（source_industry 的來源）運作。
 */
export type CanonicalAssetType =
  | "COMMON_STOCK"
  | "ETF"
  | "ETN"
  | "PREFERRED_STOCK"
  | "DR"
  | "REIT"
  | "INDEX_BENCHMARK"
  | "OTHER"

export interface CanonicalEtfClassification {
  asset_class: string
  region: string
  strategy: string
  themes: string[]
  tracking_index: string | null
  is_leveraged: boolean
  is_inverse: boolean
  is_active: boolean
  confidence: string
}

export interface CanonicalClassification {
  stock_id: string
  asset_type: CanonicalAssetType
  source_industry: string | null
  primary_sector: string | null
  primary_sector_label: string | null
  sub_sector: string | null
  secondary_sectors: string[]
  theme_clusters: string[]
  is_financial: boolean
  confidence: string | null
  review_required: boolean
  mapping_version: string
  etf: CanonicalEtfClassification | null
}

export interface StockHistoryResponse {
  stock_id: string
  stock_name: string
  industry_name: string
  sub_industry: string | null
  history: StockHistoryItem[]
  earliest_date: string | null
  latest_date: string | null
  /** Phase 1（2026-07-21）：canonical primary/sub sector，display-only、additive。 */
  canonical?: CanonicalClassification | null
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
      // no-store：這是輪詢端點，同一組 stock_ids 每次呼叫都要拿當下最新報價；
      // 沒有明確關掉快取的話，瀏覽器/中間層有機會把重複的 GET URL 當成可快取
      // 回應重複使用，造成畫面卡在某一次抓到的舊漲跌幅（2026-08-11 使用者回報
      // 「漲幅看起來像昨天的」）。
      const res = await apiFetch(`${API_BASE}/api/realtime/quotes?stock_ids=${ids}`, {
        cache: "no-store",
      })
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
  // M3 結構化 6 panel + 一句話總結
  action_one_liner?: string | null
  industry_section?: string[] | null
  chip_section?: string[] | null
  fundamental_section?: string[] | null
  technical_section?: string[] | null
  peer_section?: string[] | null
  news_section?: string[] | null
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
  // M3 結構化 6 panel + 一句話總結
  action_one_liner?: string | null
  industry_section?: string[] | null
  chip_section?: string[] | null
  fundamental_section?: string[] | null
  technical_section?: string[] | null
  peer_section?: string[] | null
  news_section?: string[] | null
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
  /** P2：商品類型只控制 evidence applicability，不是 eligibility/rank。舊快照可缺。 */
  asset_type?: "COMMON_STOCK" | "FINANCIAL" | "ETF" | string | null
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
  /** 舊版單一字串 reason，向後相容保留；新 prompt 會把 5 段 bullet 組成 markdown 也塞回這欄。 */
  reason?: string | null
  /** M2（2026-05-24）：5 段 bullet array — 題材 / 資金 / 籌碼 / 融券 / 技術。 */
  theme_reason?: string[] | null
  capital_reason?: string[] | null
  chip_reason?: string[] | null
  margin_reason?: string[] | null
  technical_reason?: string[] | null
  /** 2026-05-25：融資融券專屬結構化分析，比重 大盤 30% / 個股 70%。 */
  margin_analysis?: SignalMarginAnalysis | null
  /** 2026-06-26：產生這檔的 prompt 版本（v1 / v2 …）；舊快照無 → 視為 v1。 */
  prompt_version?: string | null
  /** 2026-06-26 M27：deterministic regime 信心度（high/medium/low）；舊快照無 → null。 */
  conviction?: SignalConviction | null
  /** 2026-06-26 M27：產生當下的大盤 regime；舊快照無 → null。 */
  regime?: SignalRegime | null
  /** 2026-06-26 M27：deterministic 觀察積極度（aggressive/normal/cautious）；舊快照無 → null。 */
  watch_intensity?: SignalWatchIntensity | null
  /** 2026-07-16 v5：LLM 回填的動能區塊（含 4 條 momentum_reason bullet）；v5 之前的快照無 → null。 */
  momentum?: SignalMomentumBlock | null
  /** 2026-07-16 v2.2：backend deterministic 動能特徵快照（pipeline 蓋回，不依賴 LLM）。 */
  signal_metrics?: SignalMetrics | null
  /** 2026-07-21 Phase 1：canonical primary/sub sector，display-only、additive。 */
  canonical?: CanonicalClassification | null
  /** P3: only RECOMMEND rows appear in the main watchlist. */
  selection_status?: "RECOMMEND" | "NOT_SELECTED" | "REMOVE" | string | null
  selection_version?: string | null
  recommendation_rank?: number | null
  backend_priority_rank?: number | null
  backend_priority_total?: number | null
  backend_priority_percentile?: number | null
  recommendation_thesis?: string | null
  relative_advantage?: string | null
  recommendation_basis?: string[] | null
  rank_override?: boolean | null
  rank_override_reason?: string | null
  theme_cluster?: string | null
  selection_reason_code?: string | null
  selection_reason?: string | null
  overlap_with?: string[] | null
  overlap_reason?: string | null
  veto_reason?: string | null
  short_reason?: string | null
}

export type SignalMomentumGrade = "A" | "B" | "C" | "D"
export type SignalMomentumPhase =
  | "emerging"
  | "accelerating"
  | "trending"
  | "extended"
  | "weakening"

/** v5 prompt 輸出的 momentum 區塊（LLM 原樣回填 backend 數字 + 4 條解讀 bullet）。 */
export interface SignalMomentumBlock {
  momentum_score?: number | null
  momentum_grade?: SignalMomentumGrade | string | null
  momentum_phase?: SignalMomentumPhase | string | null
  return_20d?: number | null
  return_60d?: number | null
  rs_market_percentile_20d?: number | null
  rs_industry_percentile_20d?: number | null
  rs_rank_change_5d?: number | null
  trend_efficiency_20d?: number | null
  distance_to_high_20d_pct?: number | null
  atr_pct_14d?: number | null
  momentum_reason?: string[] | null
}

/** v2.2 spec §9.2：pipeline deterministic 蓋回 watchlist item 的動能特徵（audit / 歸因用）。 */
export interface SignalMetrics {
  momentum_score?: number | null
  momentum_grade?: SignalMomentumGrade | string | null
  momentum_phase?: SignalMomentumPhase | string | null
  return_5d?: number | null
  return_20d?: number | null
  return_60d?: number | null
  rs_market_percentile_20d?: number | null
  rs_industry_percentile_20d?: number | null
  rs_rank_improvement_5d?: number | null
  institution_buy_to_turnover_2d?: number | null
  trend_efficiency_20d?: number | null
  distance_to_high_20d?: number | null
  distance_to_ma20?: number | null
  breadth_score?: number | null
  consecutive_hit_count?: number | null
  independent_hit_count?: number | null
  revenue_yoy?: number | null
  momentum_score_version?: string | null
  feature_coverage?: number | null
  score_confidence?: string | null
  applicable_score_weight?: number | null
  missing_score_weight?: number | null
  not_applicable_score_weight?: number | null
  score_before_penalty?: number | null
  risk_penalty_total?: number | null
  fundamental_applicability?: "AVAILABLE" | "MISSING" | "NOT_APPLICABLE" | string | null
  selection_status?: "RECOMMEND" | string | null
  selection_version?: string | null
  recommendation_rank?: number | null
  backend_priority_rank?: number | null
  backend_priority_total?: number | null
  backend_priority_percentile?: number | null
  initial_recommendation_date?: string | null
  initial_recommendation_rank?: number | null
  initial_backend_priority_rank?: number | null
  initial_phase2_role?: string | null
  initial_entry_state?: string | null
  initial_momentum_freshness?: string | null
  initial_watch_quality_state?: string | null
  initial_quality_evidence?: Record<string, boolean> | null
  initial_theme_cluster?: string | null
  initial_recommendation_thesis?: string | null
  initial_relative_advantage?: string | null
  initial_instrument_validation?: string | null
  initial_theme_validation?: string | null
  initial_catalyst_summary?: string | null
  momentum_score_detail?: {
    price?: number | null
    relative_strength?: number | null
    institution?: number | null
    volume_quality?: number | null
    fundamental?: number | null
    fundamental_applicability?: "AVAILABLE" | "MISSING" | "NOT_APPLICABLE" | string | null
    applicable_score_weight?: number | null
    missing_score_weight?: number | null
    not_applicable_score_weight?: number | null
    score_before_penalty?: number | null
    risk_penalty?: number | null
    risk_penalty_total?: number | null
    penalty_reasons?: string[] | null
  } | null
}

export interface SignalMarginAnalysisTable {
  close_price: number | null
  margin_balance_shares: number | null
  margin_change_shares: number | null
  short_balance_shares: number | null
  short_change_shares: number | null
  margin_short_ratio_pct: number | null
}

export interface SignalMarginAnalysis {
  stock_table: SignalMarginAnalysisTable
  stock_interpretation: string
  stock_conclusion: string
  market_summary: string
  risk_note: string
  weight_ratio: string
}

export interface SignalSummary {
  main_hot_industries?: string[] | null
  leader_count?: number | null
  follower_count?: number | null
  laggard_count?: number | null
  risk_note?: string | null
  /** P1 optional pipeline audit metadata; absent on historical snapshots. */
  processing_summary?: SignalProcessingSummary | null
  selection_summary?: {
    phase2_eligible_count?: number
    research_completed_count?: number
    veto_removed_count?: number
    global_eligible_count?: number
    recommended_count?: number
    not_selected_count?: number
    technical_failure_count?: number
    selection_complete?: boolean
    selection_version?: string
    selection_rationale?: string
    status?: "COMPLETED" | "FAILED" | string
  } | null
  /** P4 additive daily observation result; absent on historical snapshots. */
  tracking_summary?: SignalTrackingSummary | null
}

export interface SignalProcessingSummary {
  raw_union_count?: number
  phase2_pool_count?: number
  hard_exclusion_count?: number
  base_eligibility_survivor_count?: number
  regime_survivor_count?: number
  llm_eligible_count?: number
  research_requested_count?: number
  research_completed_count?: number
  research_failed_count?: number
  decision_requested_count?: number
  decision_completed_count?: number
  decision_failed_count?: number
  global_selection_eligible_count?: number
  global_selection_recommended_count?: number
  global_selection_not_selected_count?: number
  global_selection_status?: "NOT_STARTED" | "RUNNING" | "COMPLETED" | "FAILED" | string
  selection_complete?: boolean
  long_reason_requested_count?: number
  long_reason_completed_count?: number
  final_watch_count?: number
  final_remove_count?: number
  unprocessed_count?: number
  technical_failure_count?: number
  capacity_truncated_count?: number
  momentum_score_version?: string
  momentum_score_mode?: string
  prompt_family_version?: string
  shared_policy_version?: string | null
  prompt_sha256?: Partial<Record<
    "research" | "assessment" | "global_selector" | "reason" | "tracking",
    string
  >>
  research_prompt_version?: string
  assessment_prompt_version?: string
  global_selector_version?: string
  reason_prompt_version?: string
  tracking_prompt_version?: string
  tracking_state_machine_version?: string
  tracking_review_count?: number
  tracking_review_failed_count?: number
  tracking_conflict_count?: number
  selection_candidate_count?: number
  selection_serialized_bytes?: number
  selection_estimated_input_tokens?: number
  selection_output_token_reserve?: number
  selection_model_context_limit_tokens?: number
  is_complete?: boolean
  /** 2026-08-12：這次 run 各 LLM stage 的實際 token 用量（去重後）。 */
  token_usage?: SignalTokenUsageSummary
}

export interface SignalStageTokenUsage {
  call_count: number
  total_tokens: number
}

/** 2026-08-12：整次 pipeline run 的 token 用量彙整，見 pipeline.py
 * `_summarize_pipeline_token_usage`。同一次 API 呼叫（同一批候選共用）只計一次。 */
export interface SignalTokenUsageSummary {
  by_stage: {
    market: SignalStageTokenUsage
    research: SignalStageTokenUsage
    decision: SignalStageTokenUsage
    global_selection: SignalStageTokenUsage
    reason: SignalStageTokenUsage
    tracking: SignalStageTokenUsage
  }
  total_tokens: number
  total_call_count: number
}

export interface SignalMarketMarginToday {
  margin_balance_shares: number | null
  margin_change_shares: number | null
  short_balance_shares: number | null
  short_change_shares: number | null
  margin_short_ratio_pct: number | null
  stock_count: number | null
}

export interface SignalMarketMarginTrend {
  baseline_date: string | null
  margin_change_pct: number | null
  short_change_pct: number | null
  margin_short_ratio_pct_change: number | null
}

export interface SignalMarketMarginClimate {
  target_date: string
  data_available: boolean
  today?: SignalMarketMarginToday | null
  trend_5d?: SignalMarketMarginTrend | null
  climate_label: "expansive" | "neutral" | "contractive" | "unknown"
  climate_reason: string
}

export type SignalRegime = "BULL_TREND" | "VOLATILE_RANGE" | "RISK_OFF"
export type SignalConviction = "high" | "medium" | "low"
export type SignalWatchIntensity = "aggressive" | "normal" | "cautious"

export interface SignalMarketContext {
  market_state?: string | null
  /** 2026-06-26 M27：backend deterministic 大盤狀態 gate（全市場一個，攤平成字串）。 */
  market_regime?: SignalRegime | null
  market_regime_label?: string | null
  market_regime_reason?: string | null
  taiex_change_pct?: number | null
  otc_change_pct?: number | null
  vix_status?: string | null
  futures_bias?: string | null
  market_state_reason?: string | null
  /** 2026-05-25：大盤融資融券盤勢，從 backend market_margin.py 注入。 */
  margin_climate?: SignalMarketMarginClimate | null
  /** 2026-07-16 v2.2：市場廣度 0~100（全市場 MA20/60 上方比例、漲跌家數等聚合）。 */
  breadth_score?: number | null
  /** 2026-07-16 v2.2：4 態 regime detail（BROAD_BULL/NARROW_BULL/…，僅觀察用；gate 已在 backend 做）。 */
  market_regime_detail?: string | null
}

export interface SignalSnapshotData {
  market_context: SignalMarketContext
  watchlist: SignalWatchlistItem[]
  /** P3 additive bucket; absent on historical snapshots. */
  not_selected?: SignalWatchlistItem[]
  /** True backend/validated vetoes only. */
  removed?: SignalWatchlistItem[]
  /** Research/assessment/global/reason technical failures, never market decisions. */
  technical_failures?: Array<{
    stock?: string | null
    stock_id?: string | null
    stage?: string | null
    status?: string | null
    processing_status?: string | null
    error_code?: string | null
    error_summary?: string | null
  }>
  summary: SignalSummary
  candidate_pool_size: number | null
  final_watchlist_size: number | null
  /** 2026-08-12：這次 run 實際花費的 token 總量（market+research+decision+
   * global_selection+reason+tracking 六段加總，同一次 API 呼叫的用量只計一次）；
   * 舊快照（本次改動之前產生的）沒有這個資料，會是 null。 */
  llm_total_tokens?: number | null
}

export interface SignalSnapshotResponse {
  snapshot_date: string
  generated_at: string
  llm_model: string | null
  /** 2026-06-26：產生這份快照的 prompt 版本（v1 / v2 …）。 */
  prompt_version?: string | null
  data: SignalSnapshotData
}

export type SignalJobStatus = "pending" | "running" | "done" | "partial_failure" | "failed"

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
  /** 2026-06-26：產生這檔的 prompt 版本（取最新一次命中）；舊資料 = v1。 */
  prompt_version?: string | null
  // M26：對應 (stock_id, first_seen_date) 的 expectation price 預測；舊資料 = null
  conservative_price?: number | null
  dream_price?: number | null
  /** 2026-07-13：卡片極簡化 UI 用的 as_of 收盤價 + 當日漲跌幅（個股當日停牌 = null）。 */
  latest_close_price?: number | null
  daily_change_pct?: number | null
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
  max_positive_return_pct: number | null
  max_positive_return_trade_date: string | null
  max_negative_return_pct: number | null
  max_negative_return_trade_date: string | null
  completed_trade_date: string
  closure_reason: SignalClosureReason
  /** 2026-06-26：產生此 cycle 的 prompt 版本（取最新一次命中）；舊資料 = v1。 */
  prompt_version?: string | null
  // M26：對應 (stock_id, first_seen_date) 的 expectation price 預測；舊資料 = null
  conservative_price?: number | null
  dream_price?: number | null
}

export type SignalClosureReason =
  | "completed_30_days"
  | "early_exit_stop_loss"
  | "early_exit_drawdown_from_peak"
  | "manual_reset"
  | "p4_stopped"

export interface SignalArchiveCompletedPeriod {
  period_start: string
  period_end: string
  count: number
}

export interface SignalArchiveCompletedResponse {
  items: SignalArchiveCompletedItem[]
  periods: SignalArchiveCompletedPeriod[]
  selected_period_start: string | null
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
  // 2026-08-11：正式推薦頁併入魚尾單一入口，取最新一筆命中的補充欄位；舊資料 = null
  recommendation_thesis?: string | null
  relative_advantage?: string | null
  margin_analysis?: SignalMarginAnalysis | null
}

// ── P4 Observation Lifecycle ───────────────────────────────────────────────

export type SignalObservationStatus = "OBSERVING" | "CAUTION" | "STOPPED"
export type SignalObservationDecision =
  | "CONTINUE"
  | "CAUTION"
  | "STOP_OBSERVING"
  | "REVIEW_FAILED"

export interface SignalObservationItem {
  id: number
  stock: string
  name: string
  asset_type: string
  episode_id: string
  status: SignalObservationStatus
  started_at: string
  started_signal_date: string
  last_review_date: string | null
  latest_decision: SignalObservationDecision | null
  consecutive_caution_count: number
  latest_reason_codes: string[]
  latest_reason: string | null
  latest_review_technical_status: string | null
  stopped_at: string | null
  stop_reason_code: string | null
  stop_reason: string | null
  baseline_quality: string
  selection_version: string | null
  /** P3 formal recommendation today; intentionally separate from P4 status. */
  recommended_today: boolean
}

export interface SignalObservationReview {
  review_date: string
  previous_status?: SignalObservationStatus | null
  decision: SignalObservationDecision
  reason_codes: string[]
  reason: string | null
  caution_dimensions: string[]
  failed_dimensions: string[]
  backend_evidence: Record<string, unknown>
  external_assessment: Record<string, unknown> | null
  market_context: Record<string, unknown>
  persistence_warning: Record<string, unknown>
  technical_status: string | null
  tracking_prompt_version: string
  tracking_state_machine_version: string
  prompt_family_version?: string | null
  shared_policy_version?: string | null
  assembled_prompt_sha256?: string | null
}

export interface SignalObservationListResponse {
  as_of_date: string | null
  observations: SignalObservationItem[]
}

export interface SignalObservationDetail extends SignalObservationItem {
  as_of_date: string | null
  initial_observation: Record<string, unknown>
  latest_snapshot: Record<string, unknown>
  review_timeline: SignalObservationReview[]
  recommendation_history: Array<{
    date: string
    signal_type: string
    prompt_version: string | null
  }>
  episode_history?: Array<{
    id: number
    episode_id: string
    status: SignalObservationStatus
    started_signal_date: string
    stopped_at: string | null
    initial_thesis: string | null
    stop_reason_code: string | null
    stop_reason: string | null
    is_current: boolean
  }>
}

export interface SignalTrackingSummary {
  review_date: string | null
  active_before_review: number
  continue_count: number
  caution_count: number
  stopped_count: number
  review_failed_count: number
  conflict_count: number
  review_complete: boolean
  tracking_prompt_version: string
  tracking_state_machine_version: string
}

export interface SignalTrackingSummaryResponse {
  tracking_summary: SignalTrackingSummary
}

// ── P6 Product UI / Outcome Analytics ─────────────────────────────────────

export type SignalOutcomeLabel =
  | "WINNER"
  | "NEUTRAL"
  | "BIG_LOSER"
  | "IMMATURE"
  | "OUTCOME_DATA_MISSING"

export interface SignalOutcomeSummary {
  date_range: {
    requested_start: string | null
    requested_end: string | null
    actual_start: string | null
    actual_end: string | null
  }
  sample: {
    total: number
    matured: number
    immature: number
    missing: number
  }
  recommendation: {
    winner_count: number
    neutral_count: number
    big_loser_count: number
    winner_rate: number
    neutral_rate: number
    big_loser_rate: number
    acceptable_rate: number
    acceptable_target_met: boolean
    winner_greater_than_neutral: boolean
    average_recommend_count: number
  }
  selection: {
    winner_recall: number
    not_selected_winner_count: number
    not_selected_winner_rate: number
    not_selected_winner_by_reason: Record<string, number>
    average_compression_rate: number
    average_phase2_eligible_count: number
    average_global_eligible_count: number
    average_recommended_count: number
    rank_override_count: number
    rank_override_big_loser_count: number
    backend_rank_distribution: Record<string, Record<string, number>>
  }
  observation: {
    caution_recovery_rate: number
    caution_event_recovery_rate: number
    caution_episode_recovery_rate: number
    premature_stop_candidate_count: number
    stop_before_big_loss_rate: number
    average_trading_days_to_stop: number
    rerecommended_episode_count: number
  }
  versions: Record<string, string[]>
  definitions: Record<string, string>
}

export interface SignalOutcomeTimeseriesItem {
  date: string
  eligible: number
  phase2_eligible: number
  recommended: number
  not_selected: number
  removed: number
  technical_failure: number
  winner: number
  neutral: number
  big_loser: number
  matured_sample: number
  acceptable_rate: number
  winner_recall: number
}

export interface SignalOutcomeTimeseriesResponse {
  outcome_definition_version: string
  items: SignalOutcomeTimeseriesItem[]
}

export interface SignalOutcomeItem {
  id: number
  signal_date: string
  stock: string
  name: string
  asset_type: string
  backend_priority_rank: number | null
  recommendation_rank: number | null
  p3_decision: "RECOMMEND" | "NOT_SELECTED"
  selection_reason_code: string | null
  selection_reason: string | null
  theme_cluster: string | null
  observation_status: SignalObservationStatus | null
  stop_date: string | null
  stop_reason: string | null
  day10_return: number | null
  outcome_label: SignalOutcomeLabel
  entry_trade_date: string | null
  entry_price: number | null
  exit_trade_date: string | null
  exit_price: number | null
  selection_version: string | null
  prompt_family_version: string | null
  momentum_score_version: string | null
  research_prompt_version: string | null
  assessment_prompt_version: string | null
  global_selector_version: string | null
  reason_prompt_version: string | null
  tracking_prompt_version: string | null
  tracking_state_machine_version: string | null
  outcome_definition_version: string
  rank_override: boolean
  rank_override_reason: string | null
  metadata: Record<string, unknown>
}

export interface SignalOutcomeItemsResponse {
  page: number
  page_size: number
  total: number
  pages: number
  items: SignalOutcomeItem[]
}

export interface SignalObservationAnalyticsResponse {
  summary: SignalOutcomeSummary["observation"]
  definitions: Record<string, string>
  premature_stop_candidates: Array<Record<string, unknown>>
  stopped_before_big_loss: Record<string, unknown>
  average_days_to_stop: Record<string, number>
  rerecommended_episodes: Array<Record<string, unknown>>
}

export interface SignalOutcomeReviewItem {
  id: number
  source_type: string
  category: string
  stock: string
  signal_date: string | null
  observation_id: number | null
  review_status: "UNREVIEWED" | "REVIEWED"
  review_note: string | null
  reviewed_at: string | null
  reviewed_by: string | null
}

export interface SignalOutcomeReviewQueueResponse {
  page: number
  page_size: number
  total: number
  items: SignalOutcomeReviewItem[]
}

export interface SignalOutcomeFilters {
  start_date?: string
  end_date?: string
  prompt_family?: string
  selection_version?: string
  asset_type?: string
  theme_cluster?: string
  outcome_label?: SignalOutcomeLabel
  p3_decision?: "RECOMMEND" | "NOT_SELECTED"
  selection_reason_code?: string
  observation_status?: SignalObservationStatus
  momentum_score_version?: string
  research_prompt_version?: string
  assessment_prompt_version?: string
  global_selector_version?: string
  reason_prompt_version?: string
  tracking_prompt_version?: string
  tracking_state_machine_version?: string
}

// ============================================================================
// Expectation Price（一個月內資金行情可期待價格區間）
// ============================================================================

export type ExpectationValuationMode =
  | "PE_VALUATION"
  | "THEME_RE_RATING"
  | "MOMENTUM_MARKUP"
  | "EXTREME_MOMENTUM_MARKUP"
  | "FAILED_FOLLOW_THROUGH"

export type ExpectationPricePosition =
  | "undervalued_to_theme"
  | "fair"
  | "optimistic"
  | "overextended"
  | "failed_follow_through"

export type ExpectationChaseRisk = "low" | "medium" | "high"
export type ExpectationConfidence = "high" | "medium" | "low"

export interface ExpectationScorecard {
  theme_score_calc?: number
  fundamental_score?: number
  institution_score?: number
  margin_short_score?: number
  technical_score?: number
  sentiment_score?: number
  total_score?: number
}

export interface ExpectationValuationDetail {
  eps_used?: number | null
  eps_type?: string
  conservative_pe?: number | null
  dream_pe?: number | null
  detected_day_high_multiplier_conservative?: number | null
  detected_day_high_multiplier_dream?: number | null
  pe_reason?: string
}

export interface ExpectationClassification {
  stage?: string
  role?: string
  follower_subtype?: string | null
  mainstream_theme?: boolean
  institution_status?: string
  technical_state?: string
  margin_status?: string
  follow_through_status?: string
}

export interface ExpectationPriceItem {
  stock_id: string
  stock_name: string
  first_detected_date: string
  latest_detected_date: string | null
  detected_type: string | null
  industry_name: string | null
  sub_industry: string | null
  conservative_price: number | null
  dream_price: number | null
  valuation_mode: ExpectationValuationMode | null
  valuation_basis: string | null
  current_price_position: ExpectationPricePosition | null
  chase_risk: ExpectationChaseRisk | null
  confidence: ExpectationConfidence | null
  detected_day_high: number | null
  detected_day_close: number | null
  current_price: number | null
  hit_conservative_at: string | null
  hit_dream_at: string | null
  scorecard: ExpectationScorecard | null
  classification: ExpectationClassification | null
  valuation_detail: ExpectationValuationDetail | null
  reason_50_words: string | null
  risk_note_30_words: string | null
  source: "cron" | "manual"
  status: "ok" | "failed"
  error_message: string | null
  generated_at: string
  updated_at: string
}

export interface ExpectationPriceListResponse {
  snapshot_date: string | null
  items: ExpectationPriceItem[]
}

export interface ExpectationQuotaResponse {
  daily_limit: number
  used_count: number
  remaining_count: number
  disabled: boolean
}

export interface ExpectationRegenerateAcceptedResponse {
  stock_id: string
  status: string
}

export async function fetchExpectationPrices(
  snapshotDate?: string | null,
  options?: FetchOptions,
): Promise<ExpectationPriceListResponse> {
  const qs = new URLSearchParams()
  if (snapshotDate) qs.set("snapshot_date", snapshotDate)
  const url = `${API_BASE}/api/signals/expectation-prices${
    qs.toString() ? `?${qs.toString()}` : ""
  }`
  const res = await apiFetch(url, { signal: options?.signal })
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "預測價載入失敗"))
  return res.json()
}

export async function fetchExpectationPrice(
  stockId: string,
  options?: FetchOptions,
): Promise<ExpectationPriceItem | null> {
  const res = await apiFetch(
    `${API_BASE}/api/signals/expectation-prices/${encodeURIComponent(stockId)}`,
    { signal: options?.signal },
  )
  if (res.status === 404) return null
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "個股預測價載入失敗"))
  return res.json()
}

export async function fetchExpectationQuota(
  options?: FetchOptions,
): Promise<ExpectationQuotaResponse> {
  const res = await apiFetch(`${API_BASE}/api/signals/expectation-prices/quota`, {
    signal: options?.signal,
  })
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "預測額度載入失敗"))
  return res.json()
}

export async function regenerateExpectationPrice(
  stockId: string,
): Promise<ExpectationRegenerateAcceptedResponse> {
  const res = await apiFetch(`${API_BASE}/api/signals/expectation-prices/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stock_id: stockId }),
  })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "重新預測失敗"))
  return res.json()
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

function appendDefinedParams<T extends object>(
  qs: URLSearchParams,
  params?: T,
): void {
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") qs.set(key, String(value))
  })
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

export async function fetchSignalObservations(
  params?: {
    status?: SignalObservationStatus
    limit?: number
    asOfDate?: string
  },
  options?: FetchOptions,
): Promise<SignalObservationListResponse> {
  const qs = new URLSearchParams()
  if (params?.status) qs.set("status", params.status)
  if (params?.limit != null) qs.set("limit", String(params.limit))
  if (params?.asOfDate) qs.set("as_of_date", params.asOfDate)
  const url = `${API_BASE}/api/signals/observations${
    qs.toString() ? `?${qs.toString()}` : ""
  }`
  const res = await apiFetch(url, { signal: options?.signal })
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "每日觀察清單載入失敗"))
  return res.json()
}

export async function fetchSignalObservationDetail(
  observationId: number,
  options?: FetchOptions,
): Promise<SignalObservationDetail | null> {
  const res = await apiFetch(
    `${API_BASE}/api/signals/observations/${observationId}`,
    { signal: options?.signal },
  )
  if (res.status === 404) return null
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "每日觀察詳情載入失敗"))
  return res.json()
}

export async function fetchSignalTrackingSummary(
  reviewDate?: string,
  options?: FetchOptions,
): Promise<SignalTrackingSummaryResponse> {
  const qs = new URLSearchParams()
  if (reviewDate) qs.set("review_date", reviewDate)
  const url = `${API_BASE}/api/signals/observations/tracking-summary${
    qs.toString() ? `?${qs.toString()}` : ""
  }`
  const res = await apiFetch(url, { signal: options?.signal })
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "每日觀察摘要載入失敗"))
  return res.json()
}

export async function fetchSignalOutcomeSummary(
  filters?: SignalOutcomeFilters,
  options?: FetchOptions,
): Promise<SignalOutcomeSummary> {
  const qs = new URLSearchParams()
  appendDefinedParams(qs, filters)
  const res = await apiFetch(
    `${API_BASE}/api/signals/outcomes/summary${qs.size ? `?${qs}` : ""}`,
    { signal: options?.signal },
  )
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "結果分析載入失敗"))
  return res.json()
}

export async function fetchSignalOutcomeTimeseries(
  filters?: SignalOutcomeFilters,
  options?: FetchOptions,
): Promise<SignalOutcomeTimeseriesResponse> {
  const qs = new URLSearchParams()
  appendDefinedParams(qs, filters)
  const res = await apiFetch(
    `${API_BASE}/api/signals/outcomes/timeseries${qs.size ? `?${qs}` : ""}`,
    { signal: options?.signal },
  )
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "結果趨勢載入失敗"))
  return res.json()
}

export async function fetchSignalOutcomeItems(
  params?: SignalOutcomeFilters & {
    page?: number
    page_size?: number
    sort?: string
    direction?: "asc" | "desc"
  },
  options?: FetchOptions,
): Promise<SignalOutcomeItemsResponse> {
  const qs = new URLSearchParams()
  appendDefinedParams(qs, params)
  const res = await apiFetch(
    `${API_BASE}/api/signals/outcomes/items${qs.size ? `?${qs}` : ""}`,
    { signal: options?.signal },
  )
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "結果明細載入失敗"))
  return res.json()
}

export function signalOutcomeCsvUrl(filters?: SignalOutcomeFilters): string {
  const qs = new URLSearchParams({ export: "csv" })
  appendDefinedParams(qs, filters)
  return `${API_BASE}/api/signals/outcomes/items?${qs}`
}

export async function fetchSignalObservationAnalytics(
  options?: FetchOptions,
): Promise<SignalObservationAnalyticsResponse> {
  const res = await apiFetch(`${API_BASE}/api/signals/outcomes/observations`, {
    signal: options?.signal,
  })
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "觀察結果分析載入失敗"))
  return res.json()
}

export async function fetchSignalOutcomeReviewQueue(
  params?: {
    review_status?: "UNREVIEWED" | "REVIEWED"
    category?: string
    page?: number
    page_size?: number
  },
  options?: FetchOptions,
): Promise<SignalOutcomeReviewQueueResponse> {
  const qs = new URLSearchParams()
  appendDefinedParams(qs, params)
  const res = await apiFetch(
    `${API_BASE}/api/signals/outcomes/review-queue${qs.size ? `?${qs}` : ""}`,
    { signal: options?.signal },
  )
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "人工檢查清單載入失敗"))
  return res.json()
}

export async function updateSignalOutcomeReview(
  id: number,
  payload: { review_status: "UNREVIEWED" | "REVIEWED"; review_note?: string },
): Promise<SignalOutcomeReviewItem> {
  const res = await apiFetch(
    `${API_BASE}/api/signals/outcomes/review-queue/${id}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  )
  if (!res.ok)
    throw new Error(await buildErrorMessage(res, "人工檢查註記更新失敗"))
  return res.json()
}

export async function fetchCompletedSignalArchive(
  params?: {
    limit?: number
    /** 半年區間起始日（YYYY-MM-DD）；未指定回所有區間 */
    periodStart?: string | null
  },
  options?: FetchOptions,
): Promise<SignalArchiveCompletedResponse> {
  const qs = new URLSearchParams()
  if (params?.limit != null) qs.set("limit", String(params.limit))
  if (params?.periodStart) qs.set("period_start", params.periodStart)
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

// ── Phase 2 Comparison Debug View（2026-07-21，shadow mode 專用，不影響選股）──

export interface Phase2ShadowDateItem {
  snapshot_date: string
  pipeline_version: string
  candidate_pool_size: number | null
  role_survivor_count: number | null
  regime_survivor_count: number | null
  generated_at: string
}

export interface Phase2FunnelMetrics {
  candidate_count: number
  momentum_eligible_count: number
  role_counts: Record<string, number>
  hard_risk_survivor_count: number
  regime_survivor_count: number
  sent_to_llm_count: number
  watch_count: number
  classification_survival_rate: number
  sector_lockout_count: number
  sector_lockout_sectors: string[]
  no_output_day: boolean
  anomaly_flags: string[]
}

export interface Phase2ExplainTrace {
  stock_id: string
  candidate_channels: string[]
  sector_context: {
    primary_sector: string | null
    sub_sector: string | null
    primary_sector_stock_count: number
    sub_sector_stock_count: number
    peer_scope_used: string
    sector_context_quality: string
    peer_rs_percentile_20d: number | null
    sector_strength_percentile_20d: number | null
    canonical_mapping_usable: boolean
  } | null
  momentum_eligibility: { pass: boolean | null }
  role: { type: string | null; evidence: Record<string, boolean> | null }
  tracking_state: string | null
  entry_state: string | null
  hard_exclusion_result: { pass: boolean; reason: string | null }
  regime_gate_result: { pass: boolean | null; regime: string | null; conviction: string | null }
  final_stage: string
  first_exclusion_reason: string | null
}

export interface Phase2ComparisonSummary {
  legacy_survivor_count: number
  legacy_survivor_ids: string[]
  phase2_survivor_count: number
  phase2_survivor_ids: string[]
}

export interface Phase2ShadowSnapshotDetail {
  snapshot_date: string
  pipeline_version: string
  candidate_pool_size: number | null
  role_survivor_count: number | null
  regime_survivor_count: number | null
  funnel_metrics: Phase2FunnelMetrics
  explain_traces: Record<string, Phase2ExplainTrace>
  comparison_summary: Phase2ComparisonSummary | null
  generated_at: string
}

export async function fetchPhase2ShadowDates(options?: FetchOptions): Promise<Phase2ShadowDateItem[]> {
  const res = await apiFetch(`${API_BASE}/api/signals/phase2/shadow-dates`, { signal: options?.signal })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Phase 2 shadow 日期清單載入失敗"))
  return res.json()
}

export async function fetchPhase2ShadowSnapshot(
  snapshotDate: string,
  options?: FetchOptions,
): Promise<Phase2ShadowSnapshotDetail> {
  const res = await apiFetch(`${API_BASE}/api/signals/phase2/shadow/${snapshotDate}`, { signal: options?.signal })
  if (!res.ok) throw new Error(await buildErrorMessage(res, "Phase 2 shadow 結果載入失敗"))
  return res.json()
}
