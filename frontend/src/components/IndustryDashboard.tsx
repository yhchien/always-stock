"use client"

import { useEffect, useState, useCallback } from "react"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { fetchIndustries, fmtAmount, type IndustryFlowItem } from "@/lib/api"

type Tab = "total" | "foreign" | "trust" | "dealer"

const TAB_LABELS: Record<Tab, string> = {
  total: "合計",
  foreign: "外資",
  trust: "投信",
  dealer: "自營商",
}

function getAmount(row: IndustryFlowItem, tab: Tab): number {
  switch (tab) {
    case "foreign": return row.foreign_net_amount
    case "trust":   return row.trust_net_amount
    case "dealer":  return row.dealer_net_amount
    default:        return row.total_net_amount
  }
}

function AmountCell({ value }: { value: number }) {
  const formatted = fmtAmount(value)
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-zinc-400"
  return <span className={`font-mono text-sm ${color}`}>{formatted}</span>
}

interface Props {
  defaultDate: string
  onSelectIndustry?: (name: string) => void
}

export default function IndustryDashboard({ defaultDate, onSelectIndustry }: Props) {
  const [date, setDate] = useState(defaultDate)
  const [tab, setTab] = useState<Tab>("total")
  const [rows, setRows] = useState<IndustryFlowItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (d: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchIndustries(d)
      setRows(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "載入失敗")
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(date)
  }, [date, load])

  // Sort by selected tab
  const sorted = [...rows].sort((a, b) => getAmount(b, tab) - getAmount(a, tab))

  return (
    <div className="flex flex-col gap-4">
      {/* Header row: title + date picker */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">產業法人流向</h1>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:ring-1 focus:ring-zinc-500"
        />
      </div>

      {/* Tabs */}
      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList className="bg-zinc-900 border border-zinc-800">
          {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
            <TabsTrigger
              key={t}
              value={t}
              className="data-[state=active]:bg-zinc-700 data-[state=active]:text-white text-zinc-400"
            >
              {TAB_LABELS[t]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Status */}
      {loading && <p className="text-sm text-zinc-500">載入中...</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Table */}
      {!loading && !error && sorted.length > 0 && (
        <div className="rounded-lg border border-zinc-800 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800 hover:bg-transparent">
                <TableHead className="text-zinc-400 w-8">#</TableHead>
                <TableHead className="text-zinc-400">產業</TableHead>
                <TableHead className="text-zinc-400 text-right">外資</TableHead>
                <TableHead className="text-zinc-400 text-right">投信</TableHead>
                <TableHead className="text-zinc-400 text-right">自營商</TableHead>
                <TableHead className="text-zinc-400 text-right">合計</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((row, i) => (
                <TableRow
                  key={row.industry_name}
                  className="border-zinc-800 hover:bg-zinc-900 cursor-pointer"
                  onClick={() => onSelectIndustry?.(row.industry_name)}
                >
                  <TableCell className="text-zinc-600 text-xs">{i + 1}</TableCell>
                  <TableCell className="font-medium text-sm">{row.industry_name}</TableCell>
                  <TableCell className="text-right"><AmountCell value={row.foreign_net_amount} /></TableCell>
                  <TableCell className="text-right"><AmountCell value={row.trust_net_amount} /></TableCell>
                  <TableCell className="text-right"><AmountCell value={row.dealer_net_amount} /></TableCell>
                  <TableCell className="text-right"><AmountCell value={row.total_net_amount} /></TableCell>
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
