"use client"

import ReactECharts from "echarts-for-react"

import type { SignalOutcomeSummary, SignalOutcomeTimeseriesItem } from "@/lib/api"

export function OutcomeDistributionChart({
  summary,
}: {
  summary: SignalOutcomeSummary
}) {
  if (!summary.sample.matured) {
    return <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">此區間尚無成熟 Day10 Outcome。</p>
  }
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <h2 className="text-sm font-semibold">成熟推薦 Outcome 分布</h2>
      <p className="mt-1 text-xs text-slate-500">
        {summary.date_range.actual_start ?? "—"}～{summary.date_range.actual_end ?? "—"}・樣本 {summary.sample.matured}
      </p>
      <ReactECharts
        style={{ height: 260 }}
        option={{
          tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
          grid: { left: 44, right: 20, top: 28, bottom: 42 },
          xAxis: {
            type: "category",
            data: ["Winner／正向", "中性結果", "大幅負報酬"],
            axisLabel: { color: "#94a3b8" },
            axisLine: { lineStyle: { color: "#334155" } },
          },
          yAxis: {
            type: "value",
            minInterval: 1,
            axisLabel: { color: "#64748b" },
            splitLine: { lineStyle: { color: "#1e293b" } },
          },
          series: [
            {
              type: "bar",
              data: [
                summary.recommendation.winner_count,
                summary.recommendation.neutral_count,
                summary.recommendation.big_loser_count,
              ],
              itemStyle: { color: "#64748b", borderRadius: [4, 4, 0, 0] },
              label: { show: true, position: "top", color: "#cbd5e1" },
            },
          ],
        }}
      />
    </section>
  )
}

export function OutcomeTimeseriesChart({
  items,
}: {
  items: SignalOutcomeTimeseriesItem[]
}) {
  if (!items.length) {
    return <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">此區間沒有推薦趨勢資料。</p>
  }
  const sample = items.reduce((total, item) => total + item.matured_sample, 0)
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <h2 className="text-sm font-semibold">每日候選壓縮與正式推薦數</h2>
      <p className="mt-1 text-xs text-slate-500">
        {items[0].date}～{items.at(-1)?.date}・成熟樣本 {sample}
      </p>
      <ReactECharts
        style={{ height: 280 }}
        option={{
          tooltip: { trigger: "axis" },
          legend: {
            data: ["Phase 2 Eligible", "Global Eligible", "正式推薦", "未列入今日推薦", "明確移除", "技術失敗"],
            textStyle: { color: "#94a3b8" },
          },
          grid: { left: 44, right: 20, top: 48, bottom: 42 },
          xAxis: {
            type: "category",
            data: items.map((item) => item.date),
            axisLabel: { color: "#64748b", hideOverlap: true },
            axisLine: { lineStyle: { color: "#334155" } },
          },
          yAxis: {
            type: "value",
            minInterval: 1,
            axisLabel: { color: "#64748b" },
            splitLine: { lineStyle: { color: "#1e293b" } },
          },
          series: [
            { name: "Phase 2 Eligible", type: "line", data: items.map((item) => item.phase2_eligible), itemStyle: { color: "#a78bfa" } },
            { name: "Global Eligible", type: "line", data: items.map((item) => item.eligible), itemStyle: { color: "#94a3b8" } },
            { name: "正式推薦", type: "bar", data: items.map((item) => item.recommended), itemStyle: { color: "#0ea5e9" } },
            { name: "未列入今日推薦", type: "bar", data: items.map((item) => item.not_selected), itemStyle: { color: "#475569" } },
            { name: "明確移除", type: "line", data: items.map((item) => item.removed), itemStyle: { color: "#f59e0b" }, lineStyle: { type: "dashed" } },
            { name: "技術失敗", type: "line", data: items.map((item) => item.technical_failure), itemStyle: { color: "#64748b" }, lineStyle: { type: "dotted" } },
          ],
        }}
      />
    </section>
  )
}
