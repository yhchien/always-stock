"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import IndustryDashboard from "@/components/IndustryDashboard"

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

export default function Home() {
  const router = useRouter()
  const [date, setDate] = useState(todayString())

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <IndustryDashboard
        defaultDate={date}
        onDateChange={setDate}
        onSelectIndustry={(name) =>
          router.push(`/industries/${encodeURIComponent(name)}?date=${date}`)
        }
      />
    </main>
  )
}
