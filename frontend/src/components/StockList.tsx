"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchIndustryStocks,
  fetchSubIndustrySummary,
  fmtAmount,
  fmtShares,
  fmtStreak,
  toDisplayError,
  type StockFlowItem,
  type SubIndustrySummaryItem,
} from "@/lib/api"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"

// ── Sub-industry summary table ────────────────────────────────────────────────

type SummarySortKey = "total" | "foreign" | "trust" | "dealer" | "streak"

function SummaryTable({
  rows,
  onFilter,
  activeFilter,
}: {
  rows: SubIndustrySummaryItem[]
  onFilter: (sub: string | null) => void
  activeFilter: string | null
}) {
  const [sortKey, setSortKey] = useState<SummarySortKey>("total")
  const [sortAsc, setSortAsc] = useState(false)

  const handleSort = (key: SummarySortKey) => {
    if (key === sortKey) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      let va: number, vb: number
      switch (sortKey) {
        case "foreign": va = a.foreign_net_amount; vb = b.foreign_net_amount; break
        case "trust":   va = a.trust_net_amount; vb = b.trust_net_amount; break
        case "dealer":  va = a.dealer_net_amount; vb = b.dealer_net_amount; break
        case "streak":  va = a.streak; vb = b.streak; break
        default:        va = a.total_net_amount; vb = b.total_net_amount; break
      }
      return sortAsc ? va - vb : vb - va
    })
  }, [rows, sortKey, sortAsc])

  const indicator = (key: SummarySortKey) =>
    sortKey === key ? (sortAsc ? " \u25B2" : " \u25BC") : ""

  function AmountCell({ value }: { value: number }) {
    const formatted = fmtAmount(value)
    const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
    return <span className={`font-mono text-sm ${color}`}>{formatted}</span>
  }

  return (
    <div className="rounded-lg border border-slate-700 overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="border-slate-700 hover:bg-transparent">
            <TableHead className="text-slate-300">子產業</TableHead>
            <TableHead className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100" onClick={() => handleSort("foreign")}>
              外資{indicator("foreign")}
            </TableHead>
            <TableHead className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100" onClick={() => handleSort("trust")}>
              投信{indicator("trust")}
            </TableHead>
            <TableHead className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100" onClick={() => handleSort("dealer")}>
              自營商{indicator("dealer")}
            </TableHead>
            <TableHead className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100" onClick={() => handleSort("total")}>
              合計{indicator("total")}
            </TableHead>
            <TableHead className="text-slate-300 text-center cursor-pointer select-none hover:text-slate-100" onClick={() => handleSort("streak")}>
              趨勢{indicator("streak")}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row) => {
            const isActive = activeFilter === row.sub_industry
            return (
              <TableRow
                key={row.sub_industry}
                className={`border-slate-700 cursor-pointer ${isActive ? "bg-slate-700/60" : "hover:bg-slate-800/40"}`}
                onClick={() => onFilter(isActive ? null : row.sub_industry)}
              >
                <TableCell className="font-medium text-sm">{row.sub_industry}</TableCell>
                <TableCell className="text-right"><AmountCell value={row.foreign_net_amount} /></TableCell>
                <TableCell className="text-right"><AmountCell value={row.trust_net_amount} /></TableCell>
                <TableCell className="text-right"><AmountCell value={row.dealer_net_amount} /></TableCell>
                <TableCell className="text-right"><AmountCell value={row.total_net_amount} /></TableCell>
                <TableCell className="text-center">
                  <span className={`text-xs font-medium ${row.streak > 0 ? "text-red-400" : row.streak < 0 ? "text-green-400" : "text-slate-500"}`}>
                    {fmtStreak(row.streak)}
                  </span>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

// ── Card helpers ──────────────────────────────────────────────────────────────

function PriceChange({ change, pct }: { change: number | null; pct: number | null }) {
  if (change == null || pct == null) return <span className="text-slate-500 text-xs">-</span>
  const color = change > 0 ? "text-red-400" : change < 0 ? "text-green-400" : "text-slate-400"
  const arrow = change > 0 ? "\u25B2" : change < 0 ? "\u25BC" : ""
  return (
    <span className={`font-mono text-sm ${color}`}>
      {arrow} {Math.abs(change).toFixed(2)} ({pct >= 0 ? "+" : ""}{pct.toFixed(2)}%)
    </span>
  )
}

function FlowBadge({ label, value }: { label: string; value: number }) {
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-500"
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-500">{label}</span>
      <span className={`font-mono ${color}`}>{fmtShares(value)}</span>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

interface Props {
  industryName: string
  defaultDate: string
  defaultSubFilter?: string | null
}

export default function StockList({ industryName, defaultDate, defaultSubFilter = null }: Props) {
  const router = useRouter()
  const [date, setDate] = useState(defaultDate)
  const [rows, setRows] = useState<StockFlowItem[]>([])
  const [summary, setSummary] = useState<SubIndustrySummaryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [subFilter, setSubFilter] = useState<string | null>(defaultSubFilter)

  // Sync state changes to URL (without navigation)
  const syncUrl = useCallback((d: string, sub: string | null) => {
    const params = new URLSearchParams()
    params.set("date", d)
    if (sub) params.set("sub", sub)
    const url = `/industries/${encodeURIComponent(industryName)}?${params}`
    router.replace(url, { scroll: false })
  }, [router, industryName])

  const load = useCallback(async (d: string) => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    try {
      const [stockData, summaryData] = await Promise.all([
        fetchIndustryStocks(industryName, d, { signal: controller.signal }),
        fetchSubIndustrySummary(industryName, d, { signal: controller.signal }),
      ])
      if (!controller.signal.aborted) {
        setRows(stockData)
        setSummary(summaryData)
      }
    } catch (e) {
      if (controller.signal.aborted) return () => controller.abort()
      setError(toDisplayError(e))
      setRows([])
      setSummary([])
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
    return () => controller.abort()
  }, [industryName])

  useEffect(() => {
    let cleanup: (() => void) | void
    void load(date).then((fn) => {
      cleanup = fn
    })
    return () => {
      cleanup?.()
    }
  }, [date, load])

  // Real-time quotes (poll every 15s)
  const stockIds = useMemo(() => rows.map((r) => r.stock_id), [rows])
  const realtimeQuotes = useRealtimeQuotes(stockIds)

  // Filter stocks by sub_industry
  const filteredRows = useMemo(() => {
    if (!subFilter) return rows
    return rows.filter((r) => (r.sub_industry || r.industry_name) === subFilter)
  }, [rows, subFilter])

  // Group filtered stocks by sub_industry, ordered by summary total desc
  const grouped = useMemo(() => {
    const map = new Map<string, StockFlowItem[]>()
    for (const row of filteredRows) {
      const key = row.sub_industry ?? "其他"
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(row)
    }
    for (const items of map.values()) {
      items.sort(
        (a, b) =>
          (b.foreign_net_amount + b.trust_net_amount + b.dealer_net_amount) -
          (a.foreign_net_amount + a.trust_net_amount + a.dealer_net_amount)
      )
    }
    const orderIndex = new Map<string, number>()
    const ordered = [...summary].sort((a, b) => b.total_net_amount - a.total_net_amount)
    ordered.forEach((s, i) => orderIndex.set(s.sub_industry, i))
    const sortKeyOf = (name: string) => orderIndex.get(name) ?? 99
    return new Map([...map.entries()].sort((a, b) => sortKeyOf(a[0]) - sortKeyOf(b[0])))
  }, [filteredRows, summary])

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="text-slate-400 hover:text-slate-100 text-sm"
          >
            &larr; 返回
          </button>
          <h1 className="text-xl font-semibold tracking-tight">{industryName}</h1>
          <span className="text-sm text-slate-500">{rows.length} 檔</span>
        </div>
        <input
          type="date"
          value={date}
          onChange={(e) => { setDate(e.target.value); syncUrl(e.target.value, subFilter) }}
          className="rounded-md border border-slate-600 bg-slate-800/50 px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-400"
        />
      </div>

      {/* Status */}
      {loading && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-8 w-full" />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-40 w-full rounded-lg" />
            ))}
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Sub-industry summary table */}
      {!loading && !error && summary.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h2 className="text-sm font-medium text-slate-300">子產業彙總</h2>
            {subFilter && (
              <button
                onClick={() => { setSubFilter(null); syncUrl(date, null) }}
                className="text-xs text-slate-300 hover:text-slate-100 border border-slate-600 bg-slate-800 rounded px-2 py-0.5"
              >
                清除篩選: {subFilter} &times;
              </button>
            )}
          </div>
          <SummaryTable rows={summary} onFilter={(sub) => { setSubFilter(sub); syncUrl(date, sub) }} activeFilter={subFilter} />
        </div>
      )}

      {/* Cards grouped by sub-industry */}
      {!loading && !error && grouped.size > 0 && (
        <div className="flex flex-col gap-8">
          {[...grouped.entries()].map(([sub, items]) => (
            <section key={sub}>
              <div className="flex items-center gap-2 mb-3">
                <Badge variant="outline" className="text-sm text-slate-200 border-slate-500 px-3 py-0.5">
                  {sub}
                </Badge>
                <span className="text-xs text-slate-600">{items.length} 檔</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {items.map((stock) => {
                  const rt = realtimeQuotes.get(stock.stock_id)
                  const hasRt = rt && rt.price != null
                  const displayPrice = hasRt ? rt.price : stock.close_price
                  const displayChange = hasRt ? rt.change : stock.price_change
                  const displayPct = hasRt ? rt.change_pct : stock.price_change_pct
                  const chg = displayChange ?? 0
                  const tintClass = chg > 0
                    ? "border-red-900/40 bg-red-950/20 hover:bg-red-950/30 hover:border-red-800/50"
                    : chg < 0
                    ? "border-green-900/40 bg-green-950/20 hover:bg-green-950/30 hover:border-green-800/50"
                    : "border-slate-700 bg-slate-800/50 hover:border-slate-500 hover:bg-slate-800/40"

                  return (
                  <div
                    key={stock.stock_id}
                    onClick={() => router.push(`/stocks/${stock.stock_id}?date=${date}`)}
                    className={`rounded-lg border p-4 cursor-pointer transition-colors ${tintClass}`}
                  >
                    {/* Stock header */}
                    <div className="flex items-baseline justify-between mb-2">
                      <div className="flex items-baseline gap-2">
                        <span className="font-mono text-sm text-slate-400">{stock.stock_id}</span>
                        <span className="font-medium text-sm">{stock.stock_name}</span>
                      </div>
                      {stock.sub_industry && (
                        <span className="text-xs text-slate-600 truncate ml-2">{stock.sub_industry}</span>
                      )}
                    </div>

                    {/* Price + change */}
                    <div className="flex items-baseline gap-3 mb-3">
                      <span className="font-mono text-lg text-slate-100">
                        {displayPrice != null ? displayPrice.toFixed(2) : "-"}
                      </span>
                      <PriceChange change={displayChange ?? null} pct={displayPct ?? null} />
                      {hasRt && (
                        <span className="text-[10px] text-yellow-500 font-medium">即時</span>
                      )}
                    </div>

                    {/* Institutional flows */}
                    <div className="flex flex-col gap-1 border-t border-slate-700 pt-2">
                      <FlowBadge label="外資" value={stock.foreign_net_shares} />
                      <FlowBadge label="投信" value={stock.trust_net_shares} />
                      <FlowBadge label="自營" value={stock.dealer_net_shares} />
                    </div>

                    {/* Total net amount */}
                    <div className="flex items-center justify-between mt-2 pt-2 border-t border-slate-700">
                      <span className="text-xs text-slate-500">法人合計</span>
                      <span className={`font-mono text-xs ${
                        stock.foreign_net_amount + stock.trust_net_amount + stock.dealer_net_amount > 0
                          ? "text-red-400"
                          : stock.foreign_net_amount + stock.trust_net_amount + stock.dealer_net_amount < 0
                          ? "text-green-400"
                          : "text-slate-400"
                      }`}>
                        {fmtAmount(stock.foreign_net_amount + stock.trust_net_amount + stock.dealer_net_amount)}
                      </span>
                    </div>
                  </div>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <p className="text-sm text-slate-500">此日期無資料，請選擇交易日。</p>
      )}
    </div>
  )
}
