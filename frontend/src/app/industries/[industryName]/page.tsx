"use client"

import { use } from "react"
import { useSearchParams } from "next/navigation"
import StockList from "@/components/StockList"

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

export default function IndustryStocksPage({
  params,
}: {
  params: Promise<{ industryName: string }>
}) {
  const { industryName } = use(params)
  const decoded = decodeURIComponent(industryName)
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? todayString()

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <StockList industryName={decoded} defaultDate={date} />
    </main>
  )
}
