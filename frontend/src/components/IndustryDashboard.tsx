"use client"

import { Fragment, useEffect, useState, useCallback, useMemo } from "react"
import CollapsibleSection from "@/components/CollapsibleSection"
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
import {
  fetchIndustries,
  fetchSubIndustrySummary,
  fmtAmount,
  fmtStreak,
  toDisplayError,
  type IndustryFlowItem,
  type SubIndustrySummaryItem,
} from "@/lib/api"

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
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  return <span className={`font-mono text-sm ${color}`}>{formatted}</span>
}

function BarAmountCell({ value, maxAbs }: { value: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? Math.min((Math.abs(value) / maxAbs) * 100, 100) : 0
  const barColor = value > 0 ? "bg-red-500/15" : value < 0 ? "bg-green-500/15" : ""
  const textColor = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  return (
    <div className="relative flex items-center justify-end">
      <div className={`absolute inset-y-0 right-0 rounded-sm ${barColor}`} style={{ width: `${pct}%` }} />
      <span className={`relative font-mono text-sm ${textColor}`}>{fmtAmount(value)}</span>
    </div>
  )
}

function StreakCell({ value }: { value: number }) {
  const text = fmtStreak(value)
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-500"
  return <span className={`text-xs font-medium ${color}`}>{text}</span>
}

interface Props {
  defaultDate: string
  onDateChange?: (date: string) => void
  onSelectIndustry?: (name: string, date: string) => void
  onSelectSubIndustry?: (name: string, subIndustry: string, date: string) => void
  initialRows?: IndustryFlowItem[]
  initialRowsDate?: string | null
  collapsible?: boolean
  defaultCollapsed?: boolean
  storageKey?: string
}

export default function IndustryDashboard({
  defaultDate,
  onDateChange,
  onSelectIndustry,
  onSelectSubIndustry,
  initialRows,
  initialRowsDate,
  collapsible = false,
  defaultCollapsed = false,
  storageKey,
}: Props) {
  const hasInitialRows = initialRowsDate === defaultDate && initialRows != null
  const [date, setDate] = useState(defaultDate)
  const [tab, setTab] = useState<Tab>("total")
  const [rows, setRows] = useState<IndustryFlowItem[]>(hasInitialRows ? [] : (initialRows ?? []))
  const [loading, setLoading] = useState(hasInitialRows ? false : true)
  const [error, setError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>("total")
  const [sortAsc, setSortAsc] = useState(false)
  const [search, setSearch] = useState("")

  // 子產業樹：展開時才 lazy-fetch /summary，並以 `${date}::${industry}` 為 key 快取，
  // 重複展開即時顯示；換日期時收合全部（cache 仍依日期區隔，重展開會抓新日資料）。
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [subCache, setSubCache] = useState<Map<string, SubIndustrySummaryItem[]>>(new Map())
  const [subLoading, setSubLoading] = useState<Set<string>>(new Set())
  const [subError, setSubError] = useState<Map<string, string>>(new Map())

  const subKey = useCallback((industry: string) => `${date}::${industry}`, [date])

  useEffect(() => {
    setExpanded(new Set())
  }, [date])

  const toggleExpand = useCallback(
    (industry: string) => {
      const wasExpanded = expanded.has(industry)
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(industry)) next.delete(industry)
        else next.add(industry)
        return next
      })
      if (wasExpanded) return // 收合中，不需抓資料
      const key = subKey(industry)
      if (subCache.has(key) || subLoading.has(key)) return
      setSubLoading((prev) => new Set(prev).add(key))
      void fetchSubIndustrySummary(industry, date)
        .then((data) => {
          setSubCache((prev) => new Map(prev).set(key, data))
          setSubError((prev) => {
            const m = new Map(prev)
            m.delete(key)
            return m
          })
        })
        .catch((e) => {
          setSubError((prev) => new Map(prev).set(key, toDisplayError(e)))
        })
        .finally(() => {
          setSubLoading((prev) => {
            const s = new Set(prev)
            s.delete(key)
            return s
          })
        })
    },
    [date, expanded, subCache, subKey, subLoading],
  )

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
    if (initialRowsDate === date && initialRows) return
    let cleanup: (() => void) | void
    void load(date).then((fn) => {
      cleanup = fn
    })
    return () => {
      cleanup?.()
    }
  }, [date, initialRows, initialRowsDate, load])

  useEffect(() => {
    setDate(defaultDate)
  }, [defaultDate])

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
    ? (initialRowsDate === date && initialRows ? initialRows : rows).filter((r) =>
        r.industry_name.toLowerCase().includes(search.toLowerCase()),
      )
    : (initialRowsDate === date && initialRows ? initialRows : rows)

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

  const content = (
    <div className="flex flex-col gap-4">
      {!collapsible && (
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">產業法人流向</h1>
          <input
            type="date"
            value={date}
            onChange={(e) => { setDate(e.target.value); onDateChange?.(e.target.value) }}
            className="rounded-md border border-slate-700/50 bg-slate-800/50 px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-300"
          />
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋產業..."
            className="w-36 rounded-md border border-slate-600 bg-slate-800 px-3 py-1.5 pr-8 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-400"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-base leading-none text-slate-500 hover:text-slate-300"
              aria-label="清除搜尋"
            >
              ×
            </button>
          )}
        </div>
        {search && (
          <span className="text-xs text-slate-500">{sorted.length} / {rows.length}</span>
        )}
        <Tabs value={tab} onValueChange={(v) => handleTabChange(v as Tab)}>
          <TabsList className="bg-slate-800/50 border border-slate-600/40">
            {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
              <TabsTrigger
                key={t}
                value={t}
                className="text-slate-300 data-[state=active]:bg-slate-700 data-[state=active]:text-white"
              >
                {TAB_LABELS[t]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {loading && (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && !error && sorted.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-600">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-600 hover:bg-transparent">
                <TableHead className="text-slate-300 w-8">#</TableHead>
                <TableHead className="text-slate-300">產業</TableHead>
                <TableHead
                  className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100 hidden sm:table-cell"
                  onClick={() => handleSort("foreign")}
                >
                  外資{sortIndicator("foreign")}
                </TableHead>
                <TableHead
                  className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100 hidden sm:table-cell"
                  onClick={() => handleSort("trust")}
                >
                  投信{sortIndicator("trust")}
                </TableHead>
                <TableHead
                  className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100 hidden sm:table-cell"
                  onClick={() => handleSort("dealer")}
                >
                  自營商{sortIndicator("dealer")}
                </TableHead>
                <TableHead
                  className="text-slate-300 text-right cursor-pointer select-none hover:text-slate-100"
                  onClick={() => handleSort("total")}
                >
                  合計{sortIndicator("total")}
                </TableHead>
                <TableHead
                  className="text-slate-300 text-center cursor-pointer select-none hover:text-slate-100"
                  onClick={() => handleSort("streak")}
                >
                  趨勢{sortIndicator("streak")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row, i) => {
                const isExpanded = expanded.has(row.industry_name)
                const key = subKey(row.industry_name)
                const subs = subCache.get(key)
                const isSubLoading = subLoading.has(key)
                const subErr = subError.get(key)
                const sortedSubs = subs
                  ? [...subs].sort((a, b) => b.total_net_amount - a.total_net_amount)
                  : []
                const subMaxAbs = Math.max(1, ...sortedSubs.map((s) => Math.abs(s.total_net_amount)))
                return (
                  <Fragment key={row.industry_name}>
                    <TableRow
                      className="border-slate-600 hover:bg-slate-800/60 cursor-pointer"
                      onClick={() => toggleExpand(row.industry_name)}
                    >
                      <TableCell className="text-slate-600 text-xs">{i + 1}</TableCell>
                      <TableCell className="font-medium text-sm">
                        <div className="flex items-center gap-2">
                          <span className="w-3 text-xs text-slate-500" aria-hidden="true">
                            {isExpanded ? "▾" : "▸"}
                          </span>
                          <span>{row.industry_name}</span>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              onSelectIndustry?.(row.industry_name, date)
                            }}
                            className="ml-1 rounded border border-sky-500/30 px-1.5 py-0.5 text-[11px] text-sky-400 hover:border-sky-400/60 hover:text-sky-300"
                          >
                            進入 &rarr;
                          </button>
                        </div>
                      </TableCell>
                      <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.foreign_net_amount} /></TableCell>
                      <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.trust_net_amount} /></TableCell>
                      <TableCell className="text-right hidden sm:table-cell"><AmountCell value={row.dealer_net_amount} /></TableCell>
                      <TableCell className="text-right"><BarAmountCell value={row.total_net_amount} maxAbs={maxTotalAbs} /></TableCell>
                      <TableCell className="text-center"><StreakCell value={row.streak} /></TableCell>
                    </TableRow>

                    {isExpanded && isSubLoading && (
                      <TableRow className="border-slate-700 hover:bg-transparent">
                        <TableCell colSpan={7} className="py-2 pl-10 text-xs text-slate-500">
                          載入子產業中…
                        </TableCell>
                      </TableRow>
                    )}
                    {isExpanded && !isSubLoading && subErr && (
                      <TableRow className="border-slate-700 hover:bg-transparent">
                        <TableCell colSpan={7} className="py-2 pl-10 text-xs text-red-400">
                          {subErr}
                        </TableCell>
                      </TableRow>
                    )}
                    {isExpanded && !isSubLoading && !subErr && sortedSubs.length === 0 && subs && (
                      <TableRow className="border-slate-700 hover:bg-transparent">
                        <TableCell colSpan={7} className="py-2 pl-10 text-xs text-slate-500">
                          此產業無細分子產業
                        </TableCell>
                      </TableRow>
                    )}
                    {isExpanded && !isSubLoading && !subErr && sortedSubs.map((sub) => (
                      <TableRow
                        key={`${row.industry_name}::${sub.sub_industry}`}
                        className="border-slate-700/60 bg-slate-900/40 cursor-pointer hover:bg-slate-800/50"
                        onClick={() => onSelectSubIndustry?.(row.industry_name, sub.sub_industry, date)}
                      >
                        <TableCell />
                        <TableCell className="pl-8 text-sm text-slate-300">
                          <span className="mr-1 text-slate-600" aria-hidden="true">&#x2514;</span>
                          {sub.sub_industry}
                        </TableCell>
                        <TableCell className="text-right hidden sm:table-cell"><AmountCell value={sub.foreign_net_amount} /></TableCell>
                        <TableCell className="text-right hidden sm:table-cell"><AmountCell value={sub.trust_net_amount} /></TableCell>
                        <TableCell className="text-right hidden sm:table-cell"><AmountCell value={sub.dealer_net_amount} /></TableCell>
                        <TableCell className="text-right"><BarAmountCell value={sub.total_net_amount} maxAbs={subMaxAbs} /></TableCell>
                        <TableCell className="text-center"><StreakCell value={sub.streak} /></TableCell>
                      </TableRow>
                    ))}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {!loading && !error && sorted.length === 0 && (
        <p className="text-sm text-slate-500">此日期無資料，請選擇交易日。</p>
      )}
    </div>
  )

  if (!collapsible) return content

  return (
    <CollapsibleSection
      title="產業法人流向"
      subtitle={date}
      defaultCollapsed={defaultCollapsed}
      storageKey={storageKey}
      actions={(
        <input
          type="date"
          value={date}
          onChange={(e) => { setDate(e.target.value); onDateChange?.(e.target.value) }}
          className="rounded-md border border-slate-700/50 bg-slate-800/50 px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-slate-300"
        />
      )}
    >
      {content}
    </CollapsibleSection>
  )
}
