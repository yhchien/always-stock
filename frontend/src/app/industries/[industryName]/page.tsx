"use client"

import { Suspense, use } from "react"
import { useSearchParams } from "next/navigation"
import StockList from "@/components/StockList"
import { todayInTaipei } from "@/lib/utils"

function IndustryContent({ industryName }: { industryName: string }) {
  const decoded = decodeURIComponent(industryName)
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? todayInTaipei()
  const sub = searchParams.get("sub") ?? null

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <StockList industryName={decoded} defaultDate={date} defaultSubFilter={sub} />
    </main>
  )
}

export default function IndustryStocksPage({
  params,
}: {
  params: Promise<{ industryName: string }>
}) {
  const { industryName } = use(params)

  return (
    <Suspense>
      <IndustryContent industryName={industryName} />
    </Suspense>
  )
}
