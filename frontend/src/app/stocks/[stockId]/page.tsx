"use client"

import { Suspense, use, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Skeleton } from "@/components/ui/skeleton"

const BROKER_TOGGLE_STORAGE_KEY = "always-stock:show-broker-panel"

// Lazy load ECharts-heavy components — excluded from initial bundle
const StockChart = dynamic(() => import("@/components/StockChart"), {
  ssr: false,
  loading: () => (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-[60vh] min-h-[400px] w-full rounded-lg" />
    </div>
  ),
})

const BrokerPanel = dynamic(() => import("@/components/BrokerPanel"), {
  ssr: false,
  loading: () => <Skeleton className="h-full min-h-[360px] w-full rounded-lg" />,
})

function readStoredToggle(key: string, defaultValue: boolean): boolean {
  if (typeof window === "undefined") return defaultValue
  const stored = window.localStorage.getItem(key)
  if (stored === null) return defaultValue
  return stored === "true"
}

function StockContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const date = searchParams.get("date") ?? undefined
  const [showBrokerPanel, setShowBrokerPanel] = useState(true)

  // 讀取 localStorage 必須在 useEffect（mount 後），否則 SSR 與 client 初始值不一致造成 hydration mismatch
  useEffect(() => {
    setShowBrokerPanel(readStoredToggle(BROKER_TOGGLE_STORAGE_KEY, true))
  }, [])

  useEffect(() => {
    window.localStorage.setItem(BROKER_TOGGLE_STORAGE_KEY, String(showBrokerPanel))
  }, [showBrokerPanel])

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
      <StockChart stockId={stockId} defaultDate={date} />

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-semibold text-zinc-200">功能顯示</h2>
            <p className="text-xs text-zinc-500">隱藏後就不會載入對應功能。</p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
            <Link
              href={`/stocks/${stockId}/backtest${date ? `?date=${date}` : ""}`}
              className="flex items-center justify-between gap-4 rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2 hover:border-zinc-600 hover:bg-zinc-900/80 transition-colors sm:min-w-[220px]"
            >
              <span className="text-sm text-zinc-200">回測程式</span>
              <span className="text-xs text-zinc-500">開啟 →</span>
            </Link>

            <div className="flex items-center justify-between gap-4 rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2 sm:min-w-[220px]">
              <span className="text-sm text-zinc-200">關鍵券商</span>
              <button
                type="button"
                role="switch"
                aria-checked={showBrokerPanel}
                onClick={() => setShowBrokerPanel((value) => !value)}
                className={`relative inline-flex h-7 w-14 items-center rounded-full border transition-colors ${
                  showBrokerPanel
                    ? "border-emerald-500/50 bg-emerald-500/20"
                    : "border-zinc-700 bg-zinc-800"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                    showBrokerPanel ? "translate-x-8" : "translate-x-1"
                  }`}
                />
              </button>
            </div>
          </div>
        </div>
      </section>

      {showBrokerPanel && (
        <div className="min-h-[360px]">
          <BrokerPanel stockId={stockId} date={date} />
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
