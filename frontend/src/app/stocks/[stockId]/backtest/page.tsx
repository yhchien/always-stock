"use client"

import { Suspense, use, useCallback, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import RequireAuth from "@/components/RequireAuth"
import { Skeleton } from "@/components/ui/skeleton"
import BacktestPanel from "@/components/BacktestPanel"

const StockChart = dynamic(() => import("@/components/StockChart"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-col gap-4 p-4">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-[70vh] min-h-[500px] w-full rounded-lg" />
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

      {/* Single-column content: chart on top, backtest panel below */}
      <div className="flex flex-1 flex-col bg-slate-950">
        <div className="border-b border-slate-800">
          <StockChart
            stockId={stockId}
            defaultDate={date}
            dateRange={backtestRange}
          />
        </div>
        <div className="flex-1">
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
    <RequireAuth>
      <Suspense>
        <BacktestContent stockId={stockId} />
      </Suspense>
    </RequireAuth>
  )
}
