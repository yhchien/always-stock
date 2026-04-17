"use client"

import { useMemo } from "react"
import ReactECharts from "echarts-for-react"

import type { BacktestEquityPoint, BacktestTrade } from "@/lib/api"

interface Props {
  points: BacktestEquityPoint[]
  trades?: BacktestTrade[]
}

export default function BacktestEquityChart({ points, trades = [] }: Props) {
  const option = useMemo(() => {
    if (points.length === 0) return null

    const initial = points[0].equity
    const benchInitial = points[0].benchmark_equity
    const dates = points.map((p) => p.trade_date)
    const strategyPct = points.map((p) => +((p.equity / initial - 1) * 100).toFixed(2))
    const benchPct = points.map((p) => +((p.benchmark_equity / benchInitial - 1) * 100).toFixed(2))

    // Drawdown: peak-to-trough percentage
    let peak = initial
    const drawdown = points.map((p) => {
      if (p.equity > peak) peak = p.equity
      return +((p.equity / peak - 1) * 100).toFixed(2)
    })

    // Trade markers
    const dateIndex = new Map(dates.map((d, i) => [d, i]))
    const entryMarkers: { coord: [number, number]; symbol: string; symbolSize: number; itemStyle: { color: string } }[] = []
    const exitMarkers: { coord: [number, number]; symbol: string; symbolSize: number; itemStyle: { color: string } }[] = []

    for (const t of trades) {
      const entryIdx = dateIndex.get(t.entry_date)
      const exitIdx = dateIndex.get(t.exit_date)
      if (entryIdx != null) {
        entryMarkers.push({
          coord: [entryIdx, strategyPct[entryIdx]],
          symbol: "triangle",
          symbolSize: 8,
          itemStyle: { color: "#f87171" },
        })
      }
      if (exitIdx != null) {
        exitMarkers.push({
          coord: [exitIdx, strategyPct[exitIdx]],
          symbol: "pin",
          symbolSize: 8,
          itemStyle: { color: t.return_pct >= 0 ? "#f59e0b" : "#4ade80" },
        })
      }
    }

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(15,23,42,0.95)",
        borderColor: "#334155",
        textStyle: { color: "#fafafa", fontSize: 12 },
        formatter: (params: { seriesName: string; value: number; axisValue: string; seriesIndex: number }[]) => {
          if (!params?.length) return ""
          let html = `<div style="font-size:11px;color:#94a3b8;margin-bottom:4px">${params[0].axisValue}</div>`
          for (const p of params) {
            if (p.seriesIndex >= 2) continue // skip drawdown in main tooltip
            const sign = p.value >= 0 ? "+" : ""
            const color = p.seriesName === "策略報酬" ? "#f59e0b" : "#60a5fa"
            html += `<div><span style="color:${color}">${p.seriesName}</span>: <b>${sign}${p.value.toFixed(2)}%</b></div>`
          }
          // find drawdown value
          const ddParam = params.find((p) => p.seriesIndex === 2)
          if (ddParam) {
            html += `<div><span style="color:#f87171">回撤</span>: <b>${ddParam.value.toFixed(2)}%</b></div>`
          }
          return html
        },
      },
      legend: {
        data: ["策略報酬", "Buy & Hold"],
        top: 0,
        textStyle: { color: "#94a3b8", fontSize: 11 },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 56, right: 24, top: 36, bottom: "32%" },
        { left: 56, right: 24, top: "76%", bottom: 28 },
      ],
      xAxis: [
        {
          type: "category" as const,
          data: dates,
          gridIndex: 0,
          boundaryGap: false,
          axisLabel: { show: false },
          axisLine: { lineStyle: { color: "#334155" } },
        },
        {
          type: "category" as const,
          data: dates,
          gridIndex: 1,
          boundaryGap: false,
          axisLabel: { color: "#64748b", fontSize: 10 },
          axisLine: { lineStyle: { color: "#334155" } },
        },
      ],
      yAxis: [
        {
          type: "value" as const,
          gridIndex: 0,
          scale: true,
          axisLabel: {
            color: "#64748b",
            fontSize: 10,
            formatter: (v: number) => `${v >= 0 ? "+" : ""}${v}%`,
          },
          splitLine: { lineStyle: { color: "#27272a" } },
        },
        {
          type: "value" as const,
          gridIndex: 1,
          scale: true,
          axisLabel: {
            color: "#64748b",
            fontSize: 10,
            formatter: (v: number) => `${v}%`,
          },
          splitLine: { lineStyle: { color: "#27272a" } },
        },
      ],
      series: [
        {
          name: "策略報酬",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: strategyPct,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2.5, color: "#f59e0b" },
          areaStyle: { color: "rgba(245,158,11,0.08)" },
          markPoint: {
            data: [...entryMarkers, ...exitMarkers],
            animation: false,
          },
        },
        {
          name: "Buy & Hold",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: benchPct,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.75, color: "#60a5fa", type: "dashed" },
        },
        {
          name: "回撤",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: drawdown,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.5, color: "#f87171" },
          areaStyle: { color: "rgba(248,113,113,0.2)" },
        },
      ],
    }
  }, [points, trades])

  if (!option) return null

  return <ReactECharts option={option} style={{ height: 440, width: "100%" }} notMerge opts={{ renderer: "svg" }} />
}
