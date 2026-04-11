"use client"

import { useMemo } from "react"
import ReactECharts from "echarts-for-react"

import type { BacktestEquityPoint } from "@/lib/api"

interface Props {
  points: BacktestEquityPoint[]
}

export default function BacktestEquityChart({ points }: Props) {
  const option = useMemo(() => {
    const dates = points.map((point) => point.trade_date)
    const equity = points.map((point) => point.equity)
    const benchmark = points.map((point) => point.benchmark_equity)

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "#3f3f46",
        textStyle: { color: "#fafafa", fontSize: 12 },
      },
      legend: {
        data: ["策略淨值", "Buy & Hold"],
        top: 0,
        textStyle: { color: "#a1a1aa", fontSize: 11 },
      },
      grid: { left: 56, right: 24, top: 36, bottom: 28 },
      xAxis: {
        type: "category" as const,
        data: dates,
        boundaryGap: false,
        axisLabel: { color: "#71717a", fontSize: 10 },
        axisLine: { lineStyle: { color: "#3f3f46" } },
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        axisLabel: { color: "#71717a", fontSize: 10 },
        splitLine: { lineStyle: { color: "#27272a" } },
      },
      series: [
        {
          name: "策略淨值",
          type: "line",
          data: equity,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2.5, color: "#f59e0b" },
          areaStyle: { color: "rgba(245,158,11,0.12)" },
        },
        {
          name: "Buy & Hold",
          type: "line",
          data: benchmark,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.75, color: "#60a5fa", type: "dashed" },
        },
      ],
    }
  }, [points])

  return <ReactECharts option={option} style={{ height: 240, width: "100%" }} notMerge />
}
