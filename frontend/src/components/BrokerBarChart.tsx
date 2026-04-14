"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import ReactECharts from "echarts-for-react"
import { fetchBrokerHistory, fmtLots, toDisplayError, type BrokerDailyItem } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"

interface Props {
  stockId: string
  brokerId: string
  brokerDisplayName: string
  startDate: string
  endDate: string
  onClose?: () => void
}

export default function BrokerBarChart({
  stockId,
  brokerId,
  brokerDisplayName,
  startDate,
  endDate,
  onClose,
}: Props) {
  const [history, setHistory] = useState<BrokerDailyItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    try {
      const data = await fetchBrokerHistory(stockId, brokerId, startDate, endDate, {
        signal: controller.signal,
      })
      if (!controller.signal.aborted) {
        setHistory(data.history)
      }
    } catch (e) {
      if (controller.signal.aborted) return
      setError(toDisplayError(e))
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
    return () => controller.abort()
  }, [stockId, brokerId, startDate, endDate])

  useEffect(() => {
    let cleanup: (() => void) | void
    void load().then((fn) => { cleanup = fn })
    return () => { cleanup?.() }
  }, [load])

  const chartOption = useMemo(() => {
    if (history.length === 0) return null
    const dates = history.map((h) => h.trade_date)
    const netData = history.map((h) => h.net_shares / 1000) // in lots

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
        formatter: (params: { axisValue: string; value: number }[]) => {
          if (!params || params.length === 0) return ""
          const p = params[0]
          const val = p.value
          const color = val > 0 ? "#f87171" : val < 0 ? "#4ade80" : "#a1a1aa"
          return `<div style="font-size:12px">${p.axisValue}</div>` +
            `<div style="color:${color}">${brokerDisplayName} 淨買超: <b>${val >= 0 ? "+" : ""}${val.toFixed(0)} 張</b></div>`
        },
      },
      grid: { left: 60, right: 16, top: 24, bottom: 40 },
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLabel: {
          color: "#71717a",
          fontSize: 10,
          formatter: (v: string) => v.slice(5),
          rotate: 45,
        },
        axisLine: { lineStyle: { color: "#52525b" } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value" as const,
        name: "淨買超(張)",
        nameTextStyle: { color: "#a1a1aa", fontSize: 10 },
        axisLabel: {
          color: "#a1a1aa",
          fontSize: 10,
          formatter: (v: number) => fmtLots(v * 1000),
        },
        axisLine: { lineStyle: { color: "#52525b" } },
        splitLine: { lineStyle: { color: "#3f3f46" } },
      },
      series: [
        {
          name: "淨買超",
          type: "bar",
          data: netData.map((v) => ({
            value: v,
            itemStyle: { color: v > 0 ? "#f87171" : "#4ade80" },
          })),
          barMaxWidth: 16,
        },
      ],
    }
  }, [history, brokerDisplayName])

  return (
    <div className="rounded-lg border border-zinc-600 bg-zinc-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="text-sm font-semibold text-zinc-100">{brokerDisplayName}</span>
          <span className="ml-2 text-xs text-zinc-400">逐日淨買超</span>
          <span className="ml-2 text-xs text-zinc-500">{startDate} ～ {endDate}</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-xs text-zinc-400 hover:text-zinc-200 px-2 py-0.5 rounded border border-zinc-600 hover:border-zinc-400 transition-colors"
          >
            關閉
          </button>
        )}
      </div>

      {loading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-[200px] w-full" />
        </div>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}
      {!loading && !error && chartOption && (
        <ReactECharts
          option={chartOption}
          notMerge
          style={{ height: 220, width: "100%" }}
          opts={{ renderer: "svg" }}
        />
      )}
      {!loading && !error && history.length === 0 && (
        <p className="text-xs text-zinc-500">此時間區間無此券商的交易紀錄。</p>
      )}
    </div>
  )
}
