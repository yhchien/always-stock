const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

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

export async function fetchIndustries(date: string): Promise<IndustryFlowItem[]> {
  const res = await fetch(`${API_BASE}/api/industries?date=${date}`)
  if (!res.ok) throw new Error(`Failed to fetch industries: ${res.status}`)
  return res.json()
}

export async function fetchIndustryStocks(industryName: string, date: string): Promise<StockFlowItem[]> {
  const res = await fetch(
    `${API_BASE}/api/industries/${encodeURIComponent(industryName)}/stocks?date=${date}`
  )
  if (!res.ok) throw new Error(`Failed to fetch stocks: ${res.status}`)
  return res.json()
}

export async function fetchSubIndustrySummary(industryName: string, date: string): Promise<SubIndustrySummaryItem[]> {
  const res = await fetch(
    `${API_BASE}/api/industries/${encodeURIComponent(industryName)}/summary?date=${date}`
  )
  if (!res.ok) throw new Error(`Failed to fetch summary: ${res.status}`)
  return res.json()
}

export async function fetchStockHistory(
  stockId: string,
  days = 60,
  endDate?: string
): Promise<StockHistoryResponse> {
  const params = new URLSearchParams({ days: String(days) })
  if (endDate) params.set("end_date", endDate)
  const res = await fetch(`${API_BASE}/api/stocks/${stockId}/history?${params}`)
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
  const ids = stockIds.join(",")
  const res = await fetch(`${API_BASE}/api/realtime/quotes?stock_ids=${ids}`)
  if (!res.ok) return [] // graceful fallback if market is closed
  return res.json()
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
  return streak > 0 ? `連買${streak}天` : `連賣${Math.abs(streak)}天`
}
