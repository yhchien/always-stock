"use client"

import { Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import dynamic from "next/dynamic"
import IndustryDashboard from "@/components/IndustryDashboard"
import { todayInTaipei } from "@/lib/utils"

const TradeQualityAnalysis = dynamic(() => import("@/components/TradeQualityAnalysis"), { ssr: false })

function HomeContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? todayInTaipei()

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <TradeQualityAnalysis />
      <IndustryDashboard
        defaultDate={date}
        onDateChange={(d) => router.replace(`/?date=${d}`, { scroll: false })}
        onSelectIndustry={(name) =>
          router.push(`/industries/${encodeURIComponent(name)}?date=${date}`)
        }
      />
    </main>
  )
}

export default function Home() {
  return (
    <Suspense>
      <HomeContent />
    </Suspense>
  )
}
