"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import ReactECharts from "echarts-for-react"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchValuation,
  fetchRevenue,
  fetchFinancials,
  toDisplayError,
  type ValuationItem,
  type RevenueItem,
  type FinancialItem,
} from "@/lib/api"

interface Props {
  stockId: string
}

type TabKey = "valuation" | "revenue" | "financials"

// ── Valuation Chart ──────────────────────────────────────────────────────────

function ValuationChart({ stockId }: { stockId: string }) {
  const [data, setData] = useState<ValuationItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchValuation(stockId, undefined, undefined, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setData(res.history)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(toDisplayError(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [stockId])

  const chartOption = useMemo(() => {
    if (data.length === 0) return null
    const dates = data.map((d) => d.trade_date)
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
      },
      legend: {
        data: ["本益比", "股價淨值比", "殖利率(%)"],
        textStyle: { color: "#a1a1aa", fontSize: 11 },
        top: 0,
      },
      grid: { left: 50, right: 50, top: 36, bottom: 40 },
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLabel: { color: "#71717a", fontSize: 10, formatter: (v: string) => v.slice(5) },
        axisLine: { lineStyle: { color: "#52525b" } },
      },
      yAxis: [
        {
          type: "value" as const,
          name: "PER / PBR",
          nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
          axisLabel: { color: "#a1a1aa", fontSize: 10 },
          splitLine: { lineStyle: { color: "#3f3f46" } },
        },
        {
          type: "value" as const,
          name: "殖利率(%)",
          nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
          axisLabel: { color: "#a1a1aa", fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "本益比",
          type: "line",
          data: data.map((d) => d.per),
          symbol: "none",
          lineStyle: { width: 1.5 },
          itemStyle: { color: "#60a5fa" },
        },
        {
          name: "股價淨值比",
          type: "line",
          data: data.map((d) => d.pbr),
          symbol: "none",
          lineStyle: { width: 1.5 },
          itemStyle: { color: "#f59e0b" },
        },
        {
          name: "殖利率(%)",
          type: "line",
          yAxisIndex: 1,
          data: data.map((d) => d.dividend_yield),
          symbol: "none",
          lineStyle: { width: 1.5 },
          itemStyle: { color: "#34d399" },
        },
      ],
    }
  }, [data])

  if (loading) return <Skeleton className="h-[320px] w-full" />
  if (error) return <p className="text-xs text-red-400">{error}</p>
  if (!chartOption) return <p className="text-xs text-zinc-500">無估值資料。</p>

  return (
    <ReactECharts
      option={chartOption}
      notMerge
      style={{ height: 320, width: "100%" }}
      opts={{ renderer: "svg" }}
    />
  )
}

// ── Revenue Chart ────────────────────────────────────────────────────────────

function RevenueChart({ stockId }: { stockId: string }) {
  const [data, setData] = useState<RevenueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchRevenue(stockId, 24, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setData(res.history)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(toDisplayError(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [stockId])

  const chartOption = useMemo(() => {
    if (data.length === 0) return null
    const months = data.map((d) => d.revenue_month.slice(0, 7))
    const revenues = data.map((d) => (d.revenue ? d.revenue / 1e8 : null))
    const yoyData = data.map((d) => d.yoy_pct)

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
        formatter: (params: { seriesName: string; value: number | null; axisValue: string }[]) => {
          if (!params?.length) return ""
          let html = `<div style="font-size:12px;margin-bottom:4px">${params[0].axisValue}</div>`
          for (const p of params) {
            if (p.value == null) continue
            const unit = p.seriesName === "月營收" ? " 億" : "%"
            html += `<div>${p.seriesName}: <b>${p.value.toFixed(2)}${unit}</b></div>`
          }
          return html
        },
      },
      legend: {
        data: ["月營收", "YoY(%)"],
        textStyle: { color: "#a1a1aa", fontSize: 11 },
        top: 0,
      },
      grid: { left: 55, right: 55, top: 36, bottom: 40 },
      xAxis: {
        type: "category" as const,
        data: months,
        axisLabel: { color: "#71717a", fontSize: 10, rotate: 45 },
        axisLine: { lineStyle: { color: "#52525b" } },
      },
      yAxis: [
        {
          type: "value" as const,
          name: "營收(億)",
          nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
          axisLabel: { color: "#a1a1aa", fontSize: 10 },
          splitLine: { lineStyle: { color: "#3f3f46" } },
        },
        {
          type: "value" as const,
          name: "YoY(%)",
          nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
          axisLabel: { color: "#a1a1aa", fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "月營收",
          type: "bar",
          data: revenues,
          barMaxWidth: 24,
          itemStyle: { color: "#60a5fa" },
        },
        {
          name: "YoY(%)",
          type: "line",
          yAxisIndex: 1,
          data: yoyData,
          symbol: "none",
          lineStyle: { width: 1.5 },
          itemStyle: { color: "#f59e0b" },
        },
      ],
    }
  }, [data])

  if (loading) return <Skeleton className="h-[320px] w-full" />
  if (error) return <p className="text-xs text-red-400">{error}</p>
  if (!chartOption) return <p className="text-xs text-zinc-500">無月營收資料。</p>

  return (
    <ReactECharts
      option={chartOption}
      notMerge
      style={{ height: 320, width: "100%" }}
      opts={{ renderer: "svg" }}
    />
  )
}

// ── Financials Table ─────────────────────────────────────────────────────────

const KEY_ITEMS = "基本每股盈餘,營業收入,本期淨利（淨損）,營業毛利（毛損）,營業利益（損失）"

function FinancialsTable({ stockId }: { stockId: string }) {
  const [data, setData] = useState<FinancialItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchFinancials(stockId, 8, KEY_ITEMS, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setData(res.items)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(toDisplayError(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [stockId])

  // pivot: rows = item_name, columns = report_date
  const { itemNames, reportDates, pivot } = useMemo(() => {
    const dates = [...new Set(data.map((d) => d.report_date))].sort()
    const names = [...new Set(data.map((d) => d.item_name))]
    // keep order from KEY_ITEMS
    const ordered = KEY_ITEMS.split(",").filter((n) => names.includes(n))
    const map = new Map<string, Map<string, number | null>>()
    for (const item of data) {
      if (!map.has(item.item_name)) map.set(item.item_name, new Map())
      map.get(item.item_name)!.set(item.report_date, item.value)
    }
    return { itemNames: ordered, reportDates: dates, pivot: map }
  }, [data])

  const fmtVal = (name: string, val: number | null): string => {
    if (val == null) return "—"
    if (name === "基本每股盈餘") return val.toFixed(2)
    const yi = val / 1e8
    if (Math.abs(yi) >= 1) return yi.toFixed(1) + " 億"
    return (val / 1e4).toFixed(0) + " 萬"
  }

  const fmtDate = (d: string) => {
    const [y, m] = d.split("-")
    const qMap: Record<string, string> = { "03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4" }
    return `${y} ${qMap[m] ?? m}`
  }

  if (loading) return <Skeleton className="h-[200px] w-full" />
  if (error) return <p className="text-xs text-red-400">{error}</p>
  if (itemNames.length === 0) return <p className="text-xs text-zinc-500">無財報資料。</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-zinc-600">
            <th className="py-2 pr-4 text-left text-zinc-400 font-medium sticky left-0 bg-zinc-700">項目</th>
            {reportDates.map((d) => (
              <th key={d} className="py-2 px-3 text-right text-zinc-400 font-medium whitespace-nowrap">
                {fmtDate(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {itemNames.map((name) => (
            <tr key={name} className="border-b border-zinc-700/50">
              <td className="py-2 pr-4 text-zinc-200 whitespace-nowrap sticky left-0 bg-zinc-700">{name}</td>
              {reportDates.map((d) => {
                const val = pivot.get(name)?.get(d) ?? null
                return (
                  <td key={d} className="py-2 px-3 text-right text-zinc-300 whitespace-nowrap">
                    {fmtVal(name, val)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main Panel ───────────────────────────────────────────────────────────────

export default function FinancialsPanel({ stockId }: Props) {
  const [activeTab, setActiveTab] = useState<TabKey>("valuation")

  const tabs: { key: TabKey; label: string }[] = [
    { key: "valuation", label: "估值" },
    { key: "revenue", label: "月營收" },
    { key: "financials", label: "財報" },
  ]

  return (
    <div className="rounded-lg border border-zinc-600 bg-zinc-700 p-4">
      <div className="flex items-center gap-1 mb-4">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
              activeTab === tab.key
                ? "bg-zinc-600 text-zinc-100 font-medium"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-600/50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "valuation" && <ValuationChart stockId={stockId} />}
      {activeTab === "revenue" && <RevenueChart stockId={stockId} />}
      {activeTab === "financials" && <FinancialsTable stockId={stockId} />}
    </div>
  )
}
