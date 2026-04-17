"use client"

import { Suspense, use, useEffect, useMemo, useState } from "react"
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

// ── Sidebar item types ─────────────────────────────────────────────────────

type PanelKey = "backtest" | "financials" | "broker"

interface SidebarItem {
  key: PanelKey
  label: string
  isLink?: boolean
}

const SIDEBAR_ITEMS: SidebarItem[] = [
  { key: "backtest", label: "回測", isLink: true },
  { key: "financials", label: "基本面" },
  { key: "broker", label: "籌碼面" },
]

// ── Vertical Sidebar ───────────────────────────────────────────────────────

function Sidebar({
  stockId,
  date,
  toggles,
  onToggle,
}: {
  stockId: string
  date?: string
  toggles: Record<PanelKey, boolean>
  onToggle: (key: PanelKey) => void
}) {
  return (
    <nav className="fixed left-0 top-12 flex flex-col items-center gap-1 py-4 w-12 border-r border-slate-700/30 h-[calc(100dvh-3rem)] z-40 bg-slate-900">
      {SIDEBAR_ITEMS.map((item) => {
        const active = toggles[item.key]

        if (item.isLink) {
          return (
            <Link
              key={item.key}
              href={`/stocks/${stockId}/backtest${date ? `?date=${date}` : ""}`}
              className="group relative flex items-center justify-center w-10 h-16 rounded-md transition-colors hover:bg-slate-800/60"
              title={item.label}
            >
              <span className="writing-vertical text-[11px] font-medium text-slate-500 group-hover:text-slate-200 transition-colors">
                {item.label}
              </span>
              <span className="absolute right-0.5 top-1 text-[8px] text-slate-600 group-hover:text-slate-400">
                &rarr;
              </span>
            </Link>
          )
        }

        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onToggle(item.key)}
            className={`relative flex items-center justify-center w-10 h-16 rounded-md transition-colors ${
              active
                ? "bg-slate-800/60 text-slate-200"
                : "text-slate-600 hover:bg-slate-800/40 hover:text-slate-400"
            }`}
            title={item.label}
          >
            {active && (
              <span className="absolute left-0.5 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-full bg-emerald-400" />
            )}
            <span className="writing-vertical text-[11px] font-medium">
              {item.label}
            </span>
          </button>
        )
      })}
    </nav>
  )
}

// ── Main content ───────────────────────────────────────────────────────────

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined

  // Read date range passed back from L3
  const backtestStart = searchParams.get("start") ?? undefined
  const backtestEnd = searchParams.get("end") ?? undefined
  const externalDateRange = useMemo(
    () => (backtestStart && backtestEnd ? { start: backtestStart, end: backtestEnd } : null),
    [backtestStart, backtestEnd],
  )

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

  const toggles: Record<PanelKey, boolean> = {
    backtest: false,
    financials: showFinancialsPanel,
    broker: showBrokerPanel,
  }

  const handleToggle = (key: PanelKey) => {
    if (key === "financials") setShowFinancialsPanel((v) => !v)
    if (key === "broker") setShowBrokerPanel((v) => !v)
  }

  return (
    <div className="min-h-[calc(100dvh-3rem)]">
      {/* Left sidebar (fixed) */}
      <Sidebar
        stockId={stockId}
        date={date}
        toggles={toggles}
        onToggle={handleToggle}
      />

      {/* Main content — offset by sidebar width */}
      <main className="ml-12">
        <div className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
          <StockChart
            stockId={stockId}
            defaultDate={date}
            onDaysChange={setChartDays}
            selectedBroker={selectedBroker}
            dateRange={externalDateRange}
          />

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
        </div>
      </main>
    </div>
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
