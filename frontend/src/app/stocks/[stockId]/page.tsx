"use client"

import { Suspense, use, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Skeleton } from "@/components/ui/skeleton"
import type { BrokerTradeItem } from "@/lib/api"

const BROKER_TOGGLE_STORAGE_KEY = "always-stock:show-broker-panel"
const FINANCIALS_TOGGLE_STORAGE_KEY = "always-stock:show-financials-panel"

// Lazy load ECharts-heavy components — excluded from initial bundle
const StockChart = dynamic(() => import("@/components/StockChart"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-[70vh] min-h-[500px] w-full rounded-lg" />
    </div>
  ),
})

const BrokerPanel = dynamic(() => import("@/components/BrokerPanel"), {
  ssr: false,
  loading: () => <Skeleton className="h-full min-h-[360px] w-full rounded-lg" />,
})

const FinancialsPanel = dynamic(() => import("@/components/FinancialsPanel"), {
  ssr: false,
  loading: () => <Skeleton className="h-[380px] w-full rounded-lg" />,
})


function readStoredToggle(key: string, defaultValue: boolean): boolean {
  if (typeof window === "undefined") return defaultValue
  const stored = window.localStorage.getItem(key)
  if (stored === null) return defaultValue
  return stored === "true"
}

function ToggleChip({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
        checked
          ? "bg-slate-600 text-slate-100 border border-slate-500"
          : "bg-slate-800/60 text-slate-500 border border-slate-700 hover:text-slate-300 hover:border-slate-600"
      }`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${checked ? "bg-emerald-400" : "bg-slate-600"}`}
      />
      {label}
    </button>
  )
}

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined
  const [showBrokerPanel, setShowBrokerPanel] = useState(true)
  const [showFinancialsPanel, setShowFinancialsPanel] = useState(true)
  const [selectedBroker, setSelectedBroker] = useState<BrokerTradeItem | null>(null)
  const [chartDays, setChartDays] = useState(90)

  // 讀取 localStorage 必須在 useEffect（mount 後）
  useEffect(() => {
    setShowBrokerPanel(readStoredToggle(BROKER_TOGGLE_STORAGE_KEY, true))
    setShowFinancialsPanel(readStoredToggle(FINANCIALS_TOGGLE_STORAGE_KEY, true))
  }, [])

  useEffect(() => {
    window.localStorage.setItem(BROKER_TOGGLE_STORAGE_KEY, String(showBrokerPanel))
  }, [showBrokerPanel])

  useEffect(() => {
    window.localStorage.setItem(FINANCIALS_TOGGLE_STORAGE_KEY, String(showFinancialsPanel))
  }, [showFinancialsPanel])

  // Clear selected broker when date changes
  useEffect(() => {
    setSelectedBroker(null)
  }, [date, stockId])

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <StockChart
        stockId={stockId}
        defaultDate={date}
        onDaysChange={setChartDays}
        selectedBroker={selectedBroker}
      />

      {/* Compact toggle row */}
      <div className="flex items-center gap-2 flex-wrap">
        <Link
          href={`/stocks/${stockId}/backtest${date ? `?date=${date}` : ""}`}
          className="flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium bg-slate-800/60 text-slate-400 border border-slate-700 hover:text-slate-200 hover:border-slate-500 transition-colors"
        >
          回測程式 →
        </Link>
        <ToggleChip label="財報" checked={showFinancialsPanel} onChange={setShowFinancialsPanel} />
        <ToggleChip label="關鍵券商" checked={showBrokerPanel} onChange={setShowBrokerPanel} />
      </div>

      {showFinancialsPanel && (
        <FinancialsPanel stockId={stockId} chartDays={chartDays} />
      )}

      {showBrokerPanel && (
        <div className="min-h-[360px]">
          <BrokerPanel
            stockId={stockId}
            date={date}
            days={chartDays}
            onSelectBroker={setSelectedBroker}
            selectedBrokerId={selectedBroker?.broker_id ?? null}
          />
        </div>
      )}
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
