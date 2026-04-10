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
  chain: string | null
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
  chain: string | null
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
}

export interface FetchOptions {
  signal?: AbortSignal
}

export function toDisplayError(error: unknown, fallback = "載入失敗"): string {
  if (error instanceof Error) {
    if (error.message.includes("503")) return "資料庫忙碌中，請稍後再試"
    if (error.message.includes("404")) return "此日期無資料，請選擇其他交易日"
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
  options?: FetchOptions,
): Promise<StockHistoryResponse> {
  const params = new URLSearchParams({ days: String(days) })
  if (endDate) params.set("end_date", endDate)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/history?${params}`, {
    signal: options?.signal,
  })
  if (!res.ok) throw new Error(`Failed to fetch history: ${res.status}`)
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
