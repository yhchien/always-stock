"use client"

import { useEffect, useState, useCallback, useMemo, useRef } from "react"
import { useRouter } from "next/navigation"
import ReactECharts from "echarts-for-react"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchStockHistory,
  fetchBrokerHistory,
  fmtShares,
  fmtLots,
  toDisplayError,
  type StockHistoryResponse,
  type BrokerDailyItem,
  type BrokerTradeItem,
} from "@/lib/api"
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
  chartHeight?: string
  onDaysChange?: (days: number) => void
  selectedBroker?: BrokerTradeItem | null
}

// 永遠載入全量資料，用 dataZoom 控制初始視窗
const FULL_LOAD_DAYS = 3650
// Broker history polling interval (ms) — stops when count stabilises
const BROKER_POLL_MS = 7000

export default function StockChart({ stockId, defaultDate, days: initialDays = 90, chartHeight, onDaysChange, selectedBroker }: Props) {
  const router = useRouter()
  const chartRef = useRef<HTMLDivElement>(null)
  const [days, setDays] = useState(initialDays)
  const [data, setData] = useState<StockHistoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const realtimeQuotes = useRealtimeQuotes([stockId])

  // Broker sub-panel state
  const [brokerHistory, setBrokerHistory] = useState<BrokerDailyItem[]>([])
  const [brokerLoading, setBrokerLoading] = useState(false)
  const brokerPollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastBrokerCountRef = useRef<number>(-1)

  // Custom date range state
  const [customStart, setCustomStart] = useState("")
  const [customEnd, setCustomEnd] = useState("")
  const [appliedCustom, setAppliedCustom] = useState<{ start: string; end: string } | null>(null)

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

  const applyCustomRange = () => {
    if (!customStart || !customEnd) return
    if (customStart > customEnd) return
    setAppliedCustom({ start: customStart, end: customEnd })
  }

  const clearCustomRange = (newDays: number) => {
    setAppliedCustom(null)
    setDays(newDays)
    onDaysChange?.(newDays)
  }

  const load = useCallback(async () => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    try {
      const resp = appliedCustom
        ? await fetchStockHistory(stockId, 1, appliedCustom.end, {
            signal: controller.signal,
            startDate: appliedCustom.start,
          })
        : await fetchStockHistory(stockId, FULL_LOAD_DAYS, undefined, {
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
  }, [stockId, defaultDate, appliedCustom])

  useEffect(() => {
    let cleanup: (() => void) | void
    void load().then((fn) => {
      cleanup = fn
    })
    return () => {
      cleanup?.()
    }
  }, [load])

  // Fetch broker history for the full loaded date range when broker is selected.
  // Polls every BROKER_POLL_MS until the count stabilises (backfill is filling in gradually).
  useEffect(() => {
    if (!selectedBroker || !data || data.history.length === 0) {
      setBrokerHistory([])
      lastBrokerCountRef.current = -1
      return
    }

    const start = String(data.history[0].trade_date)
    const end = String(data.history[data.history.length - 1].trade_date)
    let cancelled = false

    const fetchOnce = (isFirst: boolean) => {
      if (isFirst) setBrokerLoading(true)
      fetchBrokerHistory(stockId, selectedBroker.broker_id, start, end)
        .then((r) => {
          if (cancelled) return
          setBrokerHistory(r.history)
          setBrokerLoading(false)

          const count = r.history.length
          if (count > 0 && count === lastBrokerCountRef.current) {
            // Count stabilised — backfill done (or no new data coming), stop polling
            return
          }
          lastBrokerCountRef.current = count
          // Schedule next poll
          brokerPollRef.current = setTimeout(() => fetchOnce(false), BROKER_POLL_MS)
        })
        .catch(() => {
          if (!cancelled) setBrokerLoading(false)
        })
    }

    fetchOnce(true)

    return () => {
      cancelled = true
      if (brokerPollRef.current) clearTimeout(brokerPollRef.current)
      lastBrokerCountRef.current = -1
    }
  }, [selectedBroker?.broker_id, data, stockId])

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

    // Broker sub-panel: align history to main dates array
    const hasBroker = !brokerLoading && brokerHistory.length > 0 && !!selectedBroker
    const brokerNetData = hasBroker
      ? (() => {
          const map = new Map(brokerHistory.map((h) => [h.trade_date, h.net_shares]))
          return dates.map((d) => {
            const v = map.get(d) ?? 0
            return { value: v / 1000, itemStyle: { color: v > 0 ? "#f87171" : v < 0 ? "#4ade80" : "#52525b" } }
          })
        })()
      : []

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const priceSeries: any = hasOHLC && candleData
      ? {
          name: "股價",
          type: "candlestick",
          xAxisIndex: 0,
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
          xAxisIndex: 0,
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
        xAxisIndex: 0,
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

    // DataZoom: compute window start/end percentages
    let zoomStart: number
    let zoomEnd: number
    if (appliedCustom) {
      zoomStart = 0
      zoomEnd = 100
    } else if (defaultDate) {
      const total = dates.length
      const idx = dates.indexOf(defaultDate)
      const pivot = idx >= 0 ? idx : total - 1
      const half = Math.floor(days / 2)
      const startIdx = Math.max(0, pivot - half)
      const endIdx = Math.min(total - 1, pivot + half)
      zoomStart = Math.round((startIdx / total) * 100)
      zoomEnd = Math.round(((endIdx + 1) / total) * 100)
    } else {
      zoomStart = Math.max(0, Math.round((1 - days / dates.length) * 100))
      zoomEnd = 100
    }

    const xAxisIndexes = hasBroker ? [0, 1] : [0]

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
            } else if (p.seriesName?.includes("淨買超")) {
              const v = p.value as { value: number } | number
              const val = typeof v === "object" ? v.value : v
              const color2 = val > 0 ? "#f87171" : val < 0 ? "#4ade80" : "#a1a1aa"
              html += `<div style="color:${color2}">${p.marker} ${p.seriesName}: <b>${val >= 0 ? "+" : ""}${val.toFixed(0)} 張</b></div>`
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
      grid: hasBroker
        ? [
            { left: 60, right: 70, top: 40, bottom: "30%" },
            { left: 60, right: 70, top: "74%", bottom: 60 },
          ]
        : { left: 60, right: 70, top: 40, bottom: 70 },
      dataZoom: [
        {
          type: "slider",
          xAxisIndex: xAxisIndexes,
          start: zoomStart,
          end: zoomEnd,
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
        {
          type: "inside",
          xAxisIndex: xAxisIndexes,
          start: zoomStart,
          end: zoomEnd,
        },
      ],
      xAxis: hasBroker
        ? [
            {
              type: "category" as const,
              data: dates,
              gridIndex: 0,
              axisLabel: { color: "#71717a", fontSize: 11, formatter: (v: string) => v.slice(5) },
              axisLine: { lineStyle: { color: "#3f3f46" } },
              splitLine: { show: false },
            },
            {
              type: "category" as const,
              data: dates,
              gridIndex: 1,
              show: false,
            },
          ]
        : {
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
          gridIndex: hasBroker ? 0 : undefined,
          type: "value" as const,
          name: hasOHLC ? "股價" : "收盤價",
          nameTextStyle: { color: "#a1a1aa", fontSize: 11 },
          axisLabel: { color: "#a1a1aa", fontSize: 11 },
          axisLine: { lineStyle: { color: "#3f3f46" } },
          splitLine: { lineStyle: { color: "#27272a" } },
          scale: true,
        },
        {
          gridIndex: hasBroker ? 0 : undefined,
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
        ...(hasBroker
          ? [
              {
                gridIndex: 1,
                type: "value" as const,
                name: `${selectedBroker!.display_name}(張)`,
                nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
                axisLabel: {
                  color: "#a1a1aa",
                  fontSize: 10,
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter: (v: any) => fmtLots(v * 1000),
                },
                axisLine: { lineStyle: { color: "#3f3f46" } },
                splitLine: { lineStyle: { color: "#27272a" } },
              },
            ]
          : []),
      ],
      series: [
        priceSeries,
        ...maSeries,
        ...(activeInst.has("foreign") ? [{
          name: "外資累積",
          type: "line",
          xAxisIndex: 0,
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
          xAxisIndex: 0,
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
          xAxisIndex: 0,
          yAxisIndex: 1,
          data: dealerCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#a78bfa", width: 1.5 },
          itemStyle: { color: "#a78bfa" },
        }] : []),
        ...(hasBroker
          ? [
              {
                name: `${selectedBroker!.display_name} 淨買超`,
                type: "bar",
                xAxisIndex: 1,
                yAxisIndex: 2,
                data: brokerNetData,
                barMaxWidth: 12,
              },
            ]
          : []),
      ],
    }
  }, [data, activeMAs, activeInst, days, appliedCustom, defaultDate, brokerHistory, brokerLoading, selectedBroker])

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
      <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        {/* Range selector */}
        <div className="flex gap-1">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.label}
              onClick={() => clearCustomRange(opt.days)}
              className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                !appliedCustom && days === opt.days
                  ? "bg-zinc-500 border-zinc-400 text-zinc-50"
                  : "bg-zinc-700 border-zinc-600 text-zinc-300 hover:text-zinc-100 hover:border-zinc-400"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Custom date range */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-zinc-500">自訂</span>
          <input
            type="date"
            value={customStart}
            max={customEnd || undefined}
            onChange={(e) => setCustomStart(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyCustomRange()}
            className="rounded border border-zinc-600 bg-zinc-700 px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-400 [color-scheme:dark]"
          />
          <span className="text-xs text-zinc-600">～</span>
          <input
            type="date"
            value={customEnd}
            min={customStart || undefined}
            onChange={(e) => setCustomEnd(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyCustomRange()}
            className="rounded border border-zinc-600 bg-zinc-700 px-2 py-0.5 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-400 [color-scheme:dark]"
          />
          <button
            onClick={applyCustomRange}
            disabled={!customStart || !customEnd || customStart > customEnd}
            className="px-2 py-0.5 text-xs rounded border border-zinc-600 bg-zinc-700 text-zinc-300 hover:text-zinc-100 hover:border-zinc-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            套用
          </button>
          {appliedCustom && (
            <button
              onClick={() => { setAppliedCustom(null); setCustomStart(""); setCustomEnd("") }}
              className="px-2 py-0.5 text-xs rounded border border-zinc-600 bg-zinc-700 text-zinc-400 hover:text-zinc-100 transition-colors"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Available data range hint */}
      {data?.earliest_date && data?.latest_date && (
        <p className="text-[11px] text-zinc-600">
          資料範圍：{data.earliest_date} ～ {data.latest_date}
        </p>
      )}

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
                    ? "border-zinc-400 bg-zinc-500 text-zinc-50"
                    : "border-zinc-600 bg-zinc-700 text-zinc-400 hover:text-zinc-200"
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
                    ? "border-zinc-400 bg-zinc-500 text-zinc-50"
                    : "border-zinc-600 bg-zinc-700 text-zinc-400 hover:text-zinc-200"
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
                  ? "border-zinc-400 bg-zinc-500 text-zinc-50"
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
              className="w-16 rounded-md border border-zinc-600 bg-zinc-700 px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-400"
            />
            <button
              onClick={addCustomMA}
              className="px-2 py-1 text-xs rounded-md border border-zinc-600 bg-zinc-700 text-zinc-300 hover:text-zinc-100 hover:border-zinc-400 transition-colors"
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
          <Skeleton className="h-[70vh] min-h-[500px] w-full rounded-lg" />
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Broker sub-panel loading indicator */}
      {brokerLoading && selectedBroker && (
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <div className="h-3 w-3 animate-spin rounded-full border border-zinc-500 border-t-zinc-200" />
          正在載入 {selectedBroker.display_name} 買賣超資料...
        </div>
      )}

      {/* Chart — responsive height via CSS */}
      {!loading && !error && chartOption && (
        <div ref={chartRef} className="rounded-lg border border-zinc-600 p-4">
          <ReactECharts
            option={chartOption}
            notMerge
            style={{ height: chartHeight ?? "70vh", minHeight: chartHeight ? 320 : 500, width: "100%" }}
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
