"use client"

import { Suspense, use } from "react"
import dynamic from "next/dynamic"
import { useSearchParams } from "next/navigation"
import { Skeleton } from "@/components/ui/skeleton"
import BacktestPanel from "@/components/BacktestPanel"

// Lazy load ECharts-heavy components — excluded from initial bundle
const StockChart = dynamic(() => import("@/components/StockChart"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-[60vh] min-h-[400px] w-full rounded-lg" />
    </div>
  ),
})

const BrokerPanel = dynamic(() => import("@/components/BrokerPanel"), {
  ssr: false,
  loading: () => <Skeleton className="h-full min-h-[360px] w-full rounded-lg" />,
})

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <StockChart stockId={stockId} defaultDate={date} />

      {/* Bottom panels: Backtest (left) + Broker (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-[360px]">
        <BacktestPanel stockId={stockId} />
        <BrokerPanel stockId={stockId} date={date} />
      </div>
    </main>
  )
}

export default function StockDetailPage({
  params,
}: {
  params: Promise<{ stockId: string }>
}) {
  const { stockId } = use(params)

  return (
    <Suspense>
      <StockContent stockId={stockId} />
    </Suspense>
  )
}
