"use client"

import { Suspense, use } from "react"
import { useSearchParams } from "next/navigation"
import StockChart from "@/components/StockChart"

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <StockChart stockId={stockId} defaultDate={date} />
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
