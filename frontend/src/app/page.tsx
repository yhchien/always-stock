"use client"

import { Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import IndustryDashboard from "@/components/IndustryDashboard"

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

function HomeContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? todayString()

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
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
