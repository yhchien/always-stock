"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import ReactECharts from "echarts-for-react"
import { fetchStockHistory, fmtShares, type StockHistoryResponse } from "@/lib/api"

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
    const prices = data.history.map((h) => h.close_price)
    const foreignCum = data.history.map((h) => h.foreign_cumulative)
    const trustCum = data.history.map((h) => h.trust_cumulative)
    const dealerCum = data.history.map((h) => h.dealer_cumulative)

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
        axisPointer: { type: "cross" as const },
      },
      legend: {
        data: ["收盤價", "外資累積", "投信累積", "自營商累積"],
        textStyle: { color: "#a1a1aa", fontSize: 12 },
        top: 0,
      },
      grid: {
        left: 60,
        right: 70,
        top: 40,
        bottom: 40,
      },
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
          name: "收盤價",
          nameTextStyle: { color: "#a1a1aa", fontSize: 11 },
          axisLabel: { color: "#a1a1aa", fontSize: 11 },
          axisLine: { lineStyle: { color: "#3f3f46" } },
          splitLine: { lineStyle: { color: "#27272a" } },
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
        {
          name: "收盤價",
          type: "line",
          yAxisIndex: 0,
          data: prices,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#f4f4f5", width: 2 },
          itemStyle: { color: "#f4f4f5" },
        },
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
