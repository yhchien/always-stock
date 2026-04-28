"use client"

import { Suspense, useEffect, useRef, useState, type ReactNode } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import dynamic from "next/dynamic"
import IndustryDashboard from "@/components/IndustryDashboard"
import { fetchLatestTradeDate } from "@/lib/api"
import { todayInTaipei } from "@/lib/utils"

const TradeQualityAnalysis = dynamic(() => import("@/components/TradeQualityAnalysis"), { ssr: false })
const DailySignalsPanel = dynamic(() => import("@/components/DailySignalsPanel"), { ssr: false })
const HotMoneyList = dynamic(() => import("@/components/HotMoneyList"), { ssr: false })

function DeferredSection({
  children,
  minHeight = 240,
}: {
  children: ReactNode
  minHeight?: number
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (visible || !ref.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: "320px 0px" },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [visible])

  return (
    <div ref={ref} style={{ minHeight }}>
      {visible ? (
        children
      ) : (
        <div
          className="h-full animate-pulse rounded-xl border border-slate-700/50 bg-slate-800/20"
          style={{ minHeight }}
        />
      )}
    </div>
  )
}

function HomeContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryDate = searchParams.get("date")
  // 預設用台北今天，但立刻嘗試改成 DB 最近交易日（前一個有資料的交易日），
  // 避免 ETL 未跑的日期顯示空資料。有 query param 時尊重使用者選擇。
  const [latestTradeDate, setLatestTradeDate] = useState<string | null>(null)
  const defaultDate = queryDate ?? latestTradeDate ?? todayInTaipei()

  useEffect(() => {
    if (queryDate) return
    let cancelled = false
    fetchLatestTradeDate()
      .then((d) => {
        if (!cancelled && d) setLatestTradeDate(d)
      })
      .catch(() => {
        // fallback 已經是 todayInTaipei()
      })
    return () => {
      cancelled = true
    }
  }, [queryDate])

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <TradeQualityAnalysis />
      <DeferredSection minHeight={180}>
        <DailySignalsPanel />
      </DeferredSection>
      <DeferredSection minHeight={260}>
        <HotMoneyList date={defaultDate} days={3} limit={20} />
      </DeferredSection>
      <DeferredSection minHeight={520}>
        <IndustryDashboard
          defaultDate={defaultDate}
          onDateChange={(d) => router.replace(`/?date=${d}`, { scroll: false })}
          onSelectIndustry={(name, selectedDate) =>
            router.push(`/industries/${encodeURIComponent(name)}?date=${selectedDate}`)
          }
        />
      </DeferredSection>
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
