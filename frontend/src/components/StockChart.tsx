"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import ReactECharts from "echarts-for-react"
import { fetchStockHistory, fmtShares, type StockHistoryResponse } from "@/lib/api"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"

interface Props {
  stockId: string
  defaultDate?: string
  days?: number
}

export default function StockChart({ stockId, defaultDate, days = 90 }: Props) {
  const router = useRouter()
  const [data, setData] = useState<StockHistoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const realtimeQuotes = useRealtimeQuotes([stockId])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await fetchStockHistory(stockId, days, defaultDate)
      setData(resp)
    } catch (e) {
      setError(e instanceof Error ? e.message : "載入失敗")
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [stockId, days, defaultDate])

  useEffect(() => {
    load()
  }, [load])

  const chartOption = useMemo(() => {
    if (!data || data.history.length === 0) return null

    const dates = data.history.map((h) => h.trade_date)
    const foreignCum = data.history.map((h) => h.foreign_cumulative)
    const trustCum = data.history.map((h) => h.trust_cumulative)
    const dealerCum = data.history.map((h) => h.dealer_cumulative)

    // Check if OHLC data is available (may be null for older records)
    const hasOHLC = data.history.some((h) => h.open_price != null)

    // Candlestick data: [open, close, low, high]
    const candleData = hasOHLC
      ? data.history.map((h) => [
          h.open_price ?? h.close_price,
          h.close_price,
          h.low_price ?? h.close_price,
          h.high_price ?? h.close_price,
        ])
      : null

    // Fallback: line chart with close prices only
    const closePrices = data.history.map((h) => h.close_price)

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const priceSeries: any = hasOHLC && candleData
      ? {
          name: "股價",
          type: "candlestick",
          yAxisIndex: 0,
          data: candleData,
          itemStyle: {
            color: "#ef4444",          // 漲 (收 > 開) — 紅色填充
            color0: "#22c55e",         // 跌 (收 < 開) — 綠色填充
            borderColor: "#ef4444",    // 漲 — 紅色邊框
            borderColor0: "#22c55e",   // 跌 — 綠色邊框
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

    const legendData = hasOHLC
      ? ["股價", "外資累積", "投信累積", "自營商累積"]
      : ["收盤價", "外資累積", "投信累積", "自營商累積"]

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
              // p.data = [index, open, close, low, high]
              const [, open, close, low, high] = p.data as number[]
              const color = close >= open ? "#ef4444" : "#22c55e"
              html += `<div style="color:${color}">` +
                `開 ${open.toFixed(2)}　高 ${high.toFixed(2)}　` +
                `低 ${low.toFixed(2)}　收 <b>${close.toFixed(2)}</b> 元</div>`
            } else if (p.seriesName === "收盤價") {
              html += `<div>${p.marker} ${p.seriesName}: <b>${(p.value as number).toFixed(2)} 元</b></div>`
            } else {
              html += `<div>${p.marker} ${p.seriesName}: <b>${fmtShares(p.value as number)}</b></div>`
            }
          }
          return html
        },
      },
      legend: {
        data: legendData,
        textStyle: { color: "#a1a1aa", fontSize: 12 },
        top: 0,
      },
      grid: {
        left: 60,
        right: 70,
        top: 40,
        bottom: 70,
      },
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
        {
          type: "inside",
          xAxisIndex: 0,
          start: 0,
          end: 100,
        },
      ],
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLabel: {
          color: "#71717a",
          fontSize: 11,
          formatter: (v: string) => v.slice(5), // MM-DD
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
        {
          name: "外資累積",
          type: "line",
          yAxisIndex: 1,
          data: foreignCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#f87171", width: 1.5 },
          itemStyle: { color: "#f87171" },
        },
        {
          name: "投信累積",
          type: "line",
          yAxisIndex: 1,
          data: trustCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#60a5fa", width: 1.5 },
          itemStyle: { color: "#60a5fa" },
        },
        {
          name: "自營商累積",
          type: "line",
          yAxisIndex: 1,
          data: dealerCum,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#a78bfa", width: 1.5 },
          itemStyle: { color: "#a78bfa" },
        },
      ],
    }
  }, [data])

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
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

      {/* Status */}
      {loading && <p className="text-sm text-zinc-500">載入中...</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* Chart */}
      {!loading && !error && chartOption && (
        <div className="rounded-lg border border-zinc-800 p-4">
          <ReactECharts
            option={chartOption}
            style={{ height: 400, width: "100%" }}
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
