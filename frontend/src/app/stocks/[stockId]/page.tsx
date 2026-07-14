"use client"

import { Suspense, use, useEffect, useState } from "react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import RequireAuth from "@/components/RequireAuth"
import StockSignalSummaryPanel from "@/components/StockSignalSummaryPanel"
import StockWatchlistTradeQualityPanel from "@/components/StockWatchlistTradeQualityPanel"
import WatchlistAddButton from "@/components/WatchlistAddButton"
import StockChartDialog from "@/components/StockChartDialog"
import { Skeleton } from "@/components/ui/skeleton"

const FINANCIALS_TOGGLE_STORAGE_KEY = "always-stock:show-financials-panel"

// 2026-07-14：K 線圖全站改 popup（StockChartDialog，固定近 6 個月）；
// 常駐 StockChart 與 chart_days URL param / 回測 dateRange 連動一併移除。
// FinancialsPanel 原本跟著 K 線天數連動，現固定以 180 天（≈ 6 個月）為基準。
const FINANCIALS_CHART_DAYS = 180

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
  // 關鍵券商（籌碼面）暫時隱藏：使用者希望優先聚焦策略回測與主動推薦。
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

  // K 線圖 popup 開關（全站 K 線一律走 StockChartDialog）
  const [chartOpen, setChartOpen] = useState(false)

  const [showFinancialsPanel, setShowFinancialsPanel] = useState(() =>
    readStoredToggle(FINANCIALS_TOGGLE_STORAGE_KEY, true),
  )

  useEffect(() => {
    window.localStorage.setItem(FINANCIALS_TOGGLE_STORAGE_KEY, String(showFinancialsPanel))
  }, [showFinancialsPanel])

  const toggles: Record<PanelKey, boolean> = {
    backtest: false,
    financials: showFinancialsPanel,
    broker: false,
  }

  const handleToggle = (key: PanelKey) => {
    if (key === "financials") setShowFinancialsPanel((v) => !v)
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
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-semibold tracking-tight text-slate-100">
              <span className="font-mono text-slate-400 mr-2">{stockId}</span>
            </h1>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setChartOpen(true)}
                className="rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-sm font-medium text-sky-200 hover:bg-sky-500/20"
              >
                K線圖（近 6 個月）
              </button>
              <WatchlistAddButton stockId={stockId} />
            </div>
          </div>

          <StockSignalSummaryPanel stockId={stockId} onOpenChart={() => setChartOpen(true)} />

          <StockWatchlistTradeQualityPanel stockId={stockId} />

          {showFinancialsPanel && (
            <FinancialsPanel stockId={stockId} chartDays={FINANCIALS_CHART_DAYS} />
          )}
        </div>
      </main>

      <StockChartDialog
        stockId={chartOpen ? stockId : null}
        onClose={() => setChartOpen(false)}
      />
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
    <RequireAuth>
      <Suspense>
        <StockContent stockId={stockId} />
      </Suspense>
    </RequireAuth>
  )
}
