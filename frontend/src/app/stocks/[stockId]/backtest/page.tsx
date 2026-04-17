"use client"

import { Suspense, use, useCallback, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import { Skeleton } from "@/components/ui/skeleton"
import BacktestPanel from "@/components/BacktestPanel"

const StockChart = dynamic(() => import("@/components/StockChart"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-col gap-4 p-4">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-[calc(100dvh-240px)] min-h-[280px] w-full rounded-lg" />
    </div>
  ),
})

function BacktestContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const router = useRouter()
  const date = searchParams.get("date") ?? undefined
  const [backtestRange, setBacktestRange] = useState<{ start: string; end: string } | null>(null)

  const handleDateRangeChange = useCallback((start: string, end: string) => {
    setBacktestRange({ start, end })
  }, [])

  const handleBack = () => {
    // Navigate back to L2 with backtest date range in URL
    const params = new URLSearchParams()
    if (date) params.set("date", date)
    if (backtestRange) {
      params.set("start", backtestRange.start)
      params.set("end", backtestRange.end)
    }
    const qs = params.toString()
    router.push(`/stocks/${stockId}${qs ? `?${qs}` : ""}`)
  }

  return (
    <div className="flex min-h-[calc(100dvh-3rem)] flex-col bg-slate-950">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 border-b border-slate-800 bg-slate-900/60 shrink-0 h-10">
        <button
          type="button"
          onClick={handleBack}
          className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          ← 返回 {stockId}
        </button>
        <span className="text-slate-700 select-none">|</span>
        <h1 className="text-sm font-semibold text-slate-200">回測程式</h1>
      </div>

      {/* Two-pane content */}
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-2">
        {/* Left pane: K-line chart */}
        <div className="min-h-0 overflow-y-auto border-b border-slate-800 bg-slate-950 lg:border-r lg:border-b-0">
          <StockChart
            stockId={stockId}
            defaultDate={date}
            chartHeight="calc(100dvh - 240px)"
            dateRange={backtestRange}
          />
        </div>

        {/* Right pane: Backtest panel */}
        <div className="min-h-0 overflow-y-auto bg-slate-950">
          <BacktestPanel stockId={stockId} onDateRangeChange={handleDateRangeChange} />
        </div>
      </div>
    </div>
  )
}

export default function BacktestPage({
  params,
}: {
  params: Promise<{ stockId: string }>
}) {
  const { stockId } = use(params)

  return (
    <Suspense>
      <BacktestContent stockId={stockId} />
    </Suspense>
  )
}
