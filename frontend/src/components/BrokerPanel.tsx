"use client"

import { useState } from "react"
import ReactECharts from "echarts-for-react"

interface Props {
  stockId: string
  dates?: string[]  // aligned with K-line x-axis dates; provided when M13 is ready
}

// Placeholder broker data type — will be filled in when M13 backend is ready
interface BrokerVolume {
  date: string
  buy: number
  sell: number
}

const BROKER_COLORS = ["#60a5fa", "#f87171", "#34d399", "#fbbf24"]

export default function BrokerPanel({ stockId, dates = [] }: Props) {
  const [brokerInput, setBrokerInput] = useState("")
  const [watchedBrokers, setWatchedBrokers] = useState<string[]>([])
  // placeholder data keyed by broker
  const [brokerData] = useState<Record<string, BrokerVolume[]>>({})

  const addBroker = () => {
    const code = brokerInput.trim()
    if (!code || watchedBrokers.includes(code)) return
    // TODO (M13): fetch broker volume for stockId + code
    setWatchedBrokers((prev) => [...prev, code])
    setBrokerInput("")
  }

  const removeBroker = (code: string) => {
    setWatchedBrokers((prev) => prev.filter((b) => b !== code))
  }

  // Build ECharts bar option from placeholder / real data
  const chartOption = watchedBrokers.length > 0 ? {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis" as const,
      backgroundColor: "rgba(24,24,27,0.95)",
      borderColor: "#3f3f46",
      textStyle: { color: "#fafafa", fontSize: 12 },
    },
    legend: {
      textStyle: { color: "#a1a1aa", fontSize: 11 },
      top: 0,
    },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: "category" as const,
      data: dates.slice(-60), // show last 60 dates
      axisLabel: { color: "#71717a", fontSize: 10, formatter: (v: string) => v.slice(5) },
      axisLine: { lineStyle: { color: "#3f3f46" } },
    },
    yAxis: {
      type: "value" as const,
      axisLabel: { color: "#a1a1aa", fontSize: 10 },
      splitLine: { lineStyle: { color: "#27272a" } },
    },
    series: watchedBrokers.map((broker, i) => {
      const color = BROKER_COLORS[i % BROKER_COLORS.length]
      const data = brokerData[broker] ?? dates.slice(-60).map((d) => ({
        date: d,
        buy: Math.floor(Math.random() * 500),   // placeholder
        sell: -Math.floor(Math.random() * 500),  // placeholder
      }))
      return {
        name: broker,
        type: "bar",
        stack: broker,
        data: data.map((d) => d.buy),
        itemStyle: { color },
        barMaxWidth: 8,
      }
    }),
  } : null

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-zinc-700 bg-zinc-900 p-4 h-full">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">關注券商買賣</h2>
        <span className="text-xs text-zinc-600 border border-zinc-700 rounded px-1.5 py-0.5">M13 — 開發中</span>
      </div>

      {/* Broker input */}
      <div className="flex gap-2">
        <input
          value={brokerInput}
          onChange={(e) => setBrokerInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addBroker()}
          placeholder="券商代號（如 1020）"
          className="flex-1 rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
        />
        <button
          onClick={addBroker}
          className="rounded-md bg-zinc-700 hover:bg-zinc-600 px-3 py-1.5 text-sm text-zinc-100 transition-colors"
        >
          +
        </button>
      </div>

      {/* Active brokers */}
      {watchedBrokers.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {watchedBrokers.map((broker, i) => (
            <span
              key={broker}
              className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs border border-zinc-600"
              style={{ borderColor: BROKER_COLORS[i % BROKER_COLORS.length] + "80" }}
            >
              <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: BROKER_COLORS[i % BROKER_COLORS.length] }} />
              {broker}
              <button onClick={() => removeBroker(broker)} className="text-zinc-500 hover:text-zinc-300 ml-0.5">×</button>
            </span>
          ))}
        </div>
      )}

      {/* Chart or placeholder */}
      {chartOption ? (
        <div className="flex-1">
          <ReactECharts
            option={chartOption}
            style={{ height: "100%", minHeight: 160, width: "100%" }}
            opts={{ renderer: "svg" }}
          />
          <p className="text-[10px] text-zinc-600 mt-1 text-center">目前顯示模擬資料，M13 券商爬蟲完成後接實際數據</p>
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center rounded-md border border-dashed border-zinc-700">
          <p className="text-xs text-zinc-600">輸入券商代號後顯示買賣長條圖</p>
        </div>
      )}
    </div>
  )
}
