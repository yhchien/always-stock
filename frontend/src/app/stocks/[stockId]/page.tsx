"use client"

import { Suspense, use } from "react"
import { useSearchParams } from "next/navigation"
import StockChart from "@/components/StockChart"
import BacktestPanel from "@/components/BacktestPanel"
import BrokerPanel from "@/components/BrokerPanel"

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <StockChart stockId={stockId} defaultDate={date} />

      {/* Bottom panels: Backtest (left) + Broker (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-[360px]">
        <BacktestPanel stockId={stockId} />
        <BrokerPanel stockId={stockId} />
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
