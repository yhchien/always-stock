"use client"

import { use } from "react"
import { useSearchParams } from "next/navigation"
import StockChart from "@/components/StockChart"

export default function StockDetailPage({
  params,
}: {
  params: Promise<{ stockId: string }>
}) {
  const { stockId } = use(params)
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <StockChart stockId={stockId} defaultDate={date} />
    </main>
  )
}
