"use client"

import { Suspense, use } from "react"
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

  return (
    <div className="flex flex-col h-[calc(100dvh-3rem)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 border-b border-zinc-800 bg-zinc-900/60 shrink-0 h-10">
        <button
          type="button"
          onClick={() => router.back()}
          className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          ← 返回 {stockId}
        </button>
        <span className="text-zinc-700 select-none">|</span>
        <h1 className="text-sm font-semibold text-zinc-200">回測程式</h1>
      </div>

      {/* Two-pane content */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 overflow-hidden">
        {/* Left pane: K-line chart */}
        <div className="overflow-y-auto border-b lg:border-b-0 lg:border-r border-zinc-800">
          <StockChart
            stockId={stockId}
            defaultDate={date}
            chartHeight="calc(100dvh - 240px)"
          />
        </div>

        {/* Right pane: Backtest panel */}
        <div className="overflow-y-auto">
          <BacktestPanel stockId={stockId} />
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
