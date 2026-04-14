"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchIndustries, fmtAmount, fmtStreak, toDisplayError, type IndustryFlowItem } from "@/lib/api"

type Tab = "total" | "foreign" | "trust" | "dealer"
type SortKey = "total" | "foreign" | "trust" | "dealer" | "streak"

const TAB_LABELS: Record<Tab, string> = {
  total: "合計",
  foreign: "外資",
  trust: "投信",
  dealer: "自營商",
}

function getSortValue(row: IndustryFlowItem, key: SortKey): number {
  switch (key) {
    case "foreign": return row.foreign_net_amount
    case "trust":   return row.trust_net_amount
    case "dealer":  return row.dealer_net_amount
    case "streak":  return row.streak
    default:        return row.total_net_amount
  }
}

function AmountCell({ value }: { value: number }) {
  const formatted = fmtAmount(value)
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-zinc-400"
  return <span className={`font-mono text-sm ${color}`}>{formatted}</span>
}

function BarAmountCell({ value, maxAbs }: { value: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? Math.min((Math.abs(value) / maxAbs) * 100, 100) : 0
  const barColor = value > 0 ? "bg-red-500/15" : value < 0 ? "bg-green-500/15" : ""
  const textColor = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-zinc-400"
  return (
    <div className="relative flex items-center justify-end">
      <div className={`absolute inset-y-0 right-0 rounded-sm ${barColor}`} style={{ width: `${pct}%` }} />
      <span className={`relative font-mono text-sm ${textColor}`}>{fmtAmount(value)}</span>
    </div>
  )
}

function StreakCell({ value }: { value: number }) {
  const text = fmtStreak(value)
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-zinc-500"
  return <span className={`text-xs font-medium ${color}`}>{text}</span>
}

interface Props {
  defaultDate: string
  onDateChange?: (date: string) => void
  onSelectIndustry?: (name: string) => void
}

export default function IndustryDashboard({ defaultDate, onDateChange, onSelectIndustry }: Props) {
  const [date, setDate] = useState(defaultDate)
  const [tab, setTab] = useState<Tab>("total")
  const [rows, setRows] = useState<IndustryFlowItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>("total")
  const [sortAsc, setSortAsc] = useState(false)
  const [search, setSearch] = useState("")

  const load = useCallback(async (d: string) => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    try {
      const data = await fetchIndustries(d, { signal: controller.signal })
      if (!controller.signal.aborted) {
        setRows(data)
      }
    } catch (e) {
      if (controller.signal.aborted) return () => controller.abort()
      setError(toDisplayError(e))
      setRows([])
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
    return () => controller.abort()
  }, [])

  useEffect(() => {
    let cleanup: (() => void) | void
    void load(date).then((fn) => {
      cleanup = fn
    })
    return () => {
      cleanup?.()
    }
  }, [date, load])

  // Sync tab → sortKey when tab changes
  const handleTabChange = (t: Tab) => {
    setTab(t)
    setSortKey(t)
    setSortAsc(false)
  }

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const filtered = search
    ? rows.filter((r) => r.industry_name.toLowerCase().includes(search.toLowerCase()))
    : rows

  const sorted = [...filtered].sort((a, b) => {
    const va = getSortValue(a, sortKey)
    const vb = getSortValue(b, sortKey)
    return sortAsc ? va - vb : vb - va
  })

  const maxTotalAbs = useMemo(
    () => Math.max(1, ...filtered.map((r) => Math.abs(r.total_net_amount))),
    [filtered],
  )

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortAsc ? " \u25B2" : " \u25BC") : ""

  return (
    <div className="flex flex-col gap-4">
      {/* Header row: title + date picker */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">產業法人流向</h1>
        <input
          type="date"
          value={date}
          onChange={(e) => { setDate(e.target.value); onDateChange?.(e.target.value) }}
          className="rounded-md border border-zinc-600 bg-zinc-700 px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:ring-1 focus:ring-zinc-300"
        />
      </div>

      {/* Search + Tabs（同一列）*/}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋產業..."
            className="rounded-md border border-zinc-600 bg-zinc-800 px-3 py-1.5 pr-8 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-400 w-36"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 text-base leading-none"
              aria-label="清除搜尋"
            >
              ×
            </button>
          )}
        </div>
        {search && (
          <span className="text-xs text-zinc-500">{sorted.length} / {rows.length}</span>
        )}
        <Tabs value={tab} onValueChange={(v) => handleTabChange(v as Tab)}>
          <TabsList className="bg-zinc-700 border border-zinc-500">
            {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
              <TabsTrigger
                key={t}
                value={t}
                className="data-[state=active]:bg-zinc-600 data-[state=active]:text-white text-zinc-300"
              >
                {TAB_LABELS[t]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Status */}
      {loading && (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Table */}
      {!loading && !error && sorted.length > 0 && (
        <div className="rounded-lg border border-zinc-600 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-600 hover:bg-transparent">
                <TableHead className="text-zinc-300 w-8">#</TableHead>
                <TableHead className="text-zinc-300">產業</TableHead>
                <TableHead
                  className="text-zinc-300 text-right cursor-pointer select-none hover:text-zinc-100 hidden sm:table-cell"
                  onClick={() => handleSort("foreign")}
                >
                  外資{sortIndicator("foreign")}
                </TableHead>
                <TableHead
                  className="text-zinc-300 text-right cursor-pointer select-none hover:text-zinc-100 hidden sm:table-cell"
                  onClick={() => handleSort("trust")}
                >
                  投信{sortIndicator("trust")}
                </TableHead>
                <TableHead
                  className="text-zinc-300 text-right cursor-pointer select-none hover:text-zinc-100 hidden sm:table-cell"
                  onClick={() => handleSort("dealer")}
                >
                  自營商{sortIndicator("dealer")}
                </TableHead>
                <TableHead
                  className="text-zinc-300 text-right cursor-pointer select-none hover:text-zinc-100"
                  onClick={() => handleSort("total")}
                >
                  合計{sortIndicator("total")}
                </TableHead>
                <TableHead
                  className="text-zinc-300 text-center cursor-pointer select-none hover:text-zinc-100"
                  onClick={() => handleSort("streak")}
                >
                  趨勢{sortIndicator("streak")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row, i) => (
                <TableRow
                  key={row.industry_name}
                  className="border-zinc-600 hover:bg-zinc-600 cursor-pointer"
                  onClick={() => onSelectIndustry?.(row.industry_name)}
                >
                  <TableCell className="text-zinc-600 text-xs">{i + 1}</TableCell>
                  <TableCell className="font-medium text-sm">{row.industry_name}</TableCell>
                  <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.foreign_net_amount} /></TableCell>
                  <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.trust_net_amount} /></TableCell>
                  <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.dealer_net_amount} /></TableCell>
                  <TableCell className="text-right"><BarAmountCell value={row.total_net_amount} maxAbs={maxTotalAbs} /></TableCell>
                  <TableCell className="text-center"><StreakCell value={row.streak} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {!loading && !error && sorted.length === 0 && (
        <p className="text-sm text-zinc-500">此日期無資料，請選擇交易日。</p>
      )}
    </div>
  )
}
