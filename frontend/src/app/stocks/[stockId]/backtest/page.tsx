"use client"

import { Suspense, use, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import RequireAuth from "@/components/RequireAuth"
import BacktestPanel from "@/components/BacktestPanel"
import StockChartDialog from "@/components/StockChartDialog"

// 2026-07-14：K 線圖全站改 popup（StockChartDialog，固定近 6 個月）；
// 回測頁不再常駐 StockChart，也不再把回測區間帶回 L2（?start=&end= 已無圖可套用）。

function BacktestContent({ stockId }: { stockId: string }) {
  const searchParams = useSearchParams()
  const router = useRouter()
  const date = searchParams.get("date") ?? undefined
  const [chartOpen, setChartOpen] = useState(false)

  const handleBack = () => {
    const params = new URLSearchParams()
    if (date) params.set("date", date)
    const qs = params.toString()
    router.push(`/stocks/${stockId}${qs ? `?${qs}` : ""}`)
  }

  return (
    <div className="flex min-h-[calc(100dvh-3rem)] flex-col bg-slate-950">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 border-b border-slate-800 bg-slate-900/60 shrink-0 h-10">
        <button
          type="button"
          onClick={handleBack}
          className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          ← 返回 {stockId}
        </button>
        <span className="text-slate-700 select-none">|</span>
        <h1 className="text-sm font-semibold text-slate-200">回測程式</h1>
        <button
          type="button"
          onClick={() => setChartOpen(true)}
          className="ml-auto rounded border border-sky-500/50 bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
        >
          K線圖（近 6 個月）
        </button>
      </div>

      <div className="flex-1">
        <BacktestPanel stockId={stockId} />
      </div>

      <StockChartDialog
        stockId={chartOpen ? stockId : null}
        onClose={() => setChartOpen(false)}
      />
    </div>
  )
}

export default function BacktestPage({
  params,
}: {
  params: Promise<{ stockId: string }>
}) {
  const { stockId } = use(params)

  return (
    <RequireAuth>
      <Suspense>
        <BacktestContent stockId={stockId} />
      </Suspense>
    </RequireAuth>
  )
}
