"use client"

import { useEffect, useState, useCallback, useMemo, useRef } from "react"
import { useRouter } from "next/navigation"
import ReactECharts from "echarts-for-react"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchStockHistory, fmtShares, toDisplayError, type StockHistoryResponse } from "@/lib/api"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"

const RANGE_OPTIONS = [
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
  { label: "All", days: 3650 },
] as const

// ── MA colours ────────────────────────────────────────────────────────────────
const MA_CONFIGS: Record<number, { color: string; label: string }> = {
  10:  { color: "#fbbf24", label: "MA10" },
  20:  { color: "#34d399", label: "MA20" },
  60:  { color: "#f472b6", label: "MA60" },
}
const CUSTOM_MA_COLOR = "#94a3b8"
const DEFAULT_MAS = new Set([10, 20, 60])

// ── Institutional line configs ─────────────────────────────────────────────────
const INST_CONFIGS = [
  { key: "foreign", label: "外資", color: "#f87171" },
  { key: "trust",   label: "投信", color: "#60a5fa" },
  { key: "dealer",  label: "自營", color: "#a78bfa" },
] as const
type InstKey = typeof INST_CONFIGS[number]["key"]

// ── MA calculation ────────────────────────────────────────────────────────────
function calcMA(prices: (number | null)[], period: number): (number | null)[] {
  return prices.map((_, i) => {
    if (i < period - 1) return null
    const slice = prices.slice(i - period + 1, i + 1)
    if (slice.some((v) => v == null)) return null
    return (slice as number[]).reduce((a, b) => a + b, 0) / period
  })
}

interface Props {
  stockId: string
  defaultDate?: string
  days?: number
}

export default function StockChart({ stockId, defaultDate, days: initialDays = 90 }: Props) {
  const router = useRouter()
  const chartRef = useRef<HTMLDivElement>(null)
  const [days, setDays] = useState(initialDays)
  const [data, setData] = useState<StockHistoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const realtimeQuotes = useRealtimeQuotes([stockId])

  // Institutional line toggle state
  const [activeInst, setActiveInst] = useState<Set<InstKey>>(new Set(["foreign", "trust", "dealer"]))

  const toggleInst = (key: InstKey) => {
    setActiveInst((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  // MA state
  const [activeMAs, setActiveMAs] = useState<Set<number>>(DEFAULT_MAS)
  const [customMA, setCustomMA] = useState<string>("")
  const [customMAPeriod, setCustomMAPeriod] = useState<number | null>(null)

  const load = useCallback(async () => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    try {
      const resp = await fetchStockHistory(stockId, days, defaultDate, {
        signal: controller.signal,
      })
      if (!controller.signal.aborted) {
        setData(resp)
      }
    } catch (e) {
      if (controller.signal.aborted) return () => controller.abort()
      setError(toDisplayError(e))
      setData(null)
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
    return () => controller.abort()
  }, [stockId, days, defaultDate])

  useEffect(() => {
    let cleanup: (() => void) | void
    void load().then((fn) => {
      cleanup = fn
    })
    return () => {
      cleanup?.()
    }
  }, [load])

  const toggleMA = (period: number) => {
    setActiveMAs((prev) => {
      const next = new Set(prev)
      if (next.has(period)) next.delete(period)
      else next.add(period)
      return next
    })
  }

  const addCustomMA = () => {
    const n = parseInt(customMA, 10)
    if (!n || n < 2 || n > 200) return
    setCustomMAPeriod(n)
    setActiveMAs((prev) => new Set([...prev, n]))
    setCustomMA("")
  }

  const chartOption = useMemo(() => {
    if (!data || data.history.length === 0) return null

    const dates = data.history.map((h) => h.trade_date)
    const closePrices = data.history.map((h) => h.close_price)
    const foreignCum = data.history.map((h) => h.foreign_cumulative)
    const trustCum = data.history.map((h) => h.trust_cumulative)
    const dealerCum = data.history.map((h) => h.dealer_cumulative)

    const hasOHLC = data.history.some((h) => h.open_price != null)
    const candleData = hasOHLC
      ? data.history.map((h) => [
          h.open_price ?? h.close_price,
          h.close_price,
          h.low_price ?? h.close_price,
          h.high_price ?? h.close_price,
        ])
      : null

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const priceSeries: any = hasOHLC && candleData
      ? {
          name: "股價",
          type: "candlestick",
          yAxisIndex: 0,
          data: candleData,
          itemStyle: {
            color: "#ef4444",
            color0: "#22c55e",
            borderColor: "#ef4444",
            borderColor0: "#22c55e",
          },
        }
      : {
          name: "收盤價",
          type: "line",
          yAxisIndex: 0,
          data: closePrices,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#f4f4f5", width: 2 },
          itemStyle: { color: "#f4f4f5" },
        }

    // Build MA series for all active periods
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const maSeries: any[] = []
    const allActivePeriods = [...activeMAs].filter((p) => p > 0)
    for (const period of allActivePeriods) {
      const maData = calcMA(closePrices, period)
      const isCustom = !MA_CONFIGS[period]
      const color = isCustom ? CUSTOM_MA_COLOR : MA_CONFIGS[period].color
      const label = isCustom ? `MA${period}` : MA_CONFIGS[period].label
      maSeries.push({
        name: label,
        type: "line",
        yAxisIndex: 0,
        data: maData,
        smooth: false,
        symbol: "none",
        lineStyle: { color, width: 1.5, type: "solid" },
        itemStyle: { color },
      })
    }

    const priceName = hasOHLC ? "股價" : "收盤價"
    const maNames = allActivePeriods.map((p) => (MA_CONFIGS[p]?.label ?? `MA${p}`))
    const instNames = INST_CONFIGS
      .filter((c) => activeInst.has(c.key))
      .map((c) => (c.key === "dealer" ? "自營商累積" : `${c.label}累積`))
    const legendData = [priceName, ...maNames, ...instNames]

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
        axisPointer: { type: "cross" as const },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any[]) => {
          if (!Array.isArray(params) || params.length === 0) return ""
          let html = `<div style="font-size:12px">${params[0].axisValue}</div>`
          for (const p of params) {
            if (p.seriesType === "candlestick") {
              const [, open, close, low, high] = p.data as number[]
              const color = close >= open ? "#ef4444" : "#22c55e"
              html += `<div style="color:${color}">` +
                `開 ${open.toFixed(2)}　高 ${high.toFixed(2)}　` +
                `低 ${low.toFixed(2)}　收 <b>${close.toFixed(2)}</b> 元</div>`
            } else if (p.seriesName === "收盤價") {
              html += `<div>${p.marker} ${p.seriesName}: <b>${(p.value as number).toFixed(2)} 元</b></div>`
            } else if (typeof p.value === "number" && p.seriesName?.startsWith("MA")) {
              html += `<div>${p.marker} ${p.seriesName}: <b>${(p.value as number).toFixed(2)} 元</b></div>`
            } else if (p.value != null) {
              html += `<div>${p.marker} ${p.seriesName}: <b>${fmtShares(p.value as number)}</b></div>`
            }
          }
          return html
        },
      },
      legend: {
        data: legendData,
        textStyle: { color: "#a1a1aa", fontSize: 11 },
        top: 0,
        itemWidth: 16,
        itemHeight: 10,
      },
      grid: { left: 60, right: 70, top: 40, bottom: 70 },
      dataZoom: [
        {
          type: "slider",
          xAxisIndex: 0,
          start: 0,
          end: 100,
          height: 20,
          bottom: 10,
          borderColor: "#3f3f46",
          backgroundColor: "#27272a",
          fillerColor: "rgba(113,113,122,0.2)",
          handleStyle: { color: "#71717a" },
          textStyle: { color: "#a1a1aa", fontSize: 10 },
          dataBackground: {
            lineStyle: { color: "#52525b" },
            areaStyle: { color: "#3f3f46" },
          },
        },
        { type: "inside", xAxisIndex: 0, start: 0, end: 100 },
      ],
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLabel: {
          color: "#71717a",
          fontSize: 11,
          formatter: (v: string) => v.slice(5),
        },
        axisLine: { lineStyle: { color: "#3f3f46" } },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value" as const,
          name: hasOHLC ? "股價" : "收盤價",
          nameTextStyle: { color: "#a1a1aa", fontSize: 11 },
          axisLabel: { color: "#a1a1aa", fontSize: 11 },
          axisLine: { lineStyle: { color: "#3f3f46" } },
          splitLine: { lineStyle: { color: "#27272a" } },
          scale: true,
        },
        {
          type: "value" as const,
          name: "累積張數",
          nameTextStyle: { color: "#a1a1aa", fontSize: 11 },
          axisLabel: {
            color: "#a1a1aa",
            fontSize: 11,
            formatter: (v: number) => fmtShares(v),
          },
          axisLine: { lineStyle: { color: "#3f3f46" } },
          splitLine: { show: false },
        },
      ],
      series: [
        priceSeries,
        ...maSeries,
        ...(activeInst.has("foreign") ? [{
          name: "外資累積",
          type: "line",
          yAxisIndex: 1,
          data: foreignCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#f87171", width: 1.5 },
          itemStyle: { color: "#f87171" },
        }] : []),
        ...(activeInst.has("trust") ? [{
          name: "投信累積",
          type: "line",
          yAxisIndex: 1,
          data: trustCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#60a5fa", width: 1.5 },
          itemStyle: { color: "#60a5fa" },
        }] : []),
        ...(activeInst.has("dealer") ? [{
          name: "自營商累積",
          type: "line",
          yAxisIndex: 1,
          data: dealerCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#a78bfa", width: 1.5 },
          itemStyle: { color: "#a78bfa" },
        }] : []),
      ],
    }
  }, [data, activeMAs, activeInst])

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="text-zinc-400 hover:text-zinc-100 text-sm"
          >
            &larr; 返回
          </button>
          {data && (
            <div className="flex items-baseline gap-2">
              <h1 className="text-xl font-semibold tracking-tight">
                {data.stock_id} {data.stock_name}
              </h1>
              <span className="text-sm text-zinc-500">
                {data.sub_industry ?? data.industry_name}
              </span>
            </div>
          )}
        </div>
        {/* Real-time quote */}
        {(() => {
          const rt = realtimeQuotes.get(stockId)
          if (!rt || rt.price == null) return null
          const color = (rt.change ?? 0) > 0 ? "text-red-400" : (rt.change ?? 0) < 0 ? "text-green-400" : "text-zinc-300"
          const arrow = (rt.change ?? 0) > 0 ? "\u25B2" : (rt.change ?? 0) < 0 ? "\u25BC" : ""
          return (
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-2xl text-zinc-100">{rt.price.toFixed(2)}</span>
              <span className={`font-mono text-sm ${color}`}>
                {arrow} {Math.abs(rt.change ?? 0).toFixed(2)} ({rt.change_pct != null ? (rt.change_pct >= 0 ? "+" : "") + rt.change_pct.toFixed(2) : "0.00"}%)
              </span>
              <span className="text-[10px] text-yellow-500 font-medium">即時 {rt.trade_time ?? ""}</span>
            </div>
          )
        })()}
      </div>

      {/* Range + MA controls */}
      <div className="flex items-center gap-4 flex-wrap">
        {/* Range selector */}
        <div className="flex gap-1">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.label}
              onClick={() => setDays(opt.days)}
              className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                days === opt.days
                  ? "bg-zinc-700 border-zinc-500 text-zinc-100"
                  : "bg-zinc-900 border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Institutional line toggles */}
        <div className="flex items-center gap-1">
          {INST_CONFIGS.map(({ key, label, color }) => {
            const active = activeInst.has(key)
            return (
              <button
                key={key}
                onClick={() => toggleInst(key)}
                className={`flex items-center gap-1 px-2 py-1 text-xs rounded-md border transition-colors ${
                  active
                    ? "border-zinc-500 bg-zinc-700 text-zinc-100"
                    : "border-zinc-700 bg-zinc-900 text-zinc-500 hover:text-zinc-300"
                }`}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm"
                  style={{ backgroundColor: active ? color : "#52525b" }}
                />
                {label}
              </button>
            )
          })}
        </div>

        {/* MA toggles */}
        <div className="flex items-center gap-1 flex-wrap">
          {Object.entries(MA_CONFIGS).map(([p, cfg]) => {
            const period = Number(p)
            const active = activeMAs.has(period)
            return (
              <button
                key={period}
                onClick={() => toggleMA(period)}
                className={`flex items-center gap-1 px-2 py-1 text-xs rounded-md border transition-colors ${
                  active
                    ? "border-zinc-500 bg-zinc-700 text-zinc-100"
                    : "border-zinc-700 bg-zinc-900 text-zinc-500 hover:text-zinc-300"
                }`}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm"
                  style={{ backgroundColor: active ? cfg.color : "#52525b" }}
                />
                {cfg.label}
              </button>
            )
          })}
          {/* Custom MA */}
          {customMAPeriod && (
            <button
              onClick={() => {
                setCustomMAPeriod(null)
                setActiveMAs((prev) => {
                  const next = new Set(prev)
                  next.delete(customMAPeriod)
                  return next
                })
              }}
              className={`flex items-center gap-1 px-2 py-1 text-xs rounded-md border transition-colors ${
                activeMAs.has(customMAPeriod)
                  ? "border-zinc-500 bg-zinc-700 text-zinc-100"
                  : "border-zinc-700 bg-zinc-900 text-zinc-500"
              }`}
            >
              <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: CUSTOM_MA_COLOR }} />
              MA{customMAPeriod} ×
            </button>
          )}
          <div className="flex items-center gap-1">
            <input
              type="number"
              value={customMA}
              onChange={(e) => setCustomMA(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addCustomMA()}
              placeholder="自訂"
              min={2}
              max={200}
              className="w-16 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
            <button
              onClick={addCustomMA}
              className="px-2 py-1 text-xs rounded-md border border-zinc-700 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors"
            >
              +
            </button>
          </div>
        </div>
      </div>

      {/* Status */}
      {loading && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-[60vh] min-h-[400px] w-full rounded-lg" />
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Chart — responsive height via CSS */}
      {!loading && !error && chartOption && (
        <div ref={chartRef} className="rounded-lg border border-zinc-800 p-4">
          <ReactECharts
            option={chartOption}
            notMerge
            style={{ height: "60vh", minHeight: 400, width: "100%" }}
            opts={{ renderer: "svg" }}
          />
        </div>
      )}

      {!loading && !error && data && data.history.length === 0 && (
        <p className="text-sm text-zinc-500">此區間無資料。</p>
      )}
    </div>
  )
}
