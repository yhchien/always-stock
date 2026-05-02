"use client"

import { useState } from "react"

import RequireAuth from "@/components/RequireAuth"
import WatchlistTradeQualityCards from "@/components/WatchlistTradeQualityCards"
import { useWatchlist } from "@/lib/watchlist"

function WatchlistPageContent() {
  const { items, total, capacity, isLoading, error, clear, refresh } = useWatchlist()
  const [clearConfirming, setClearConfirming] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)

  async function handleClear() {
    if (clearing) return
    setClearing(true)
    setClearError(null)
    try {
      await clear()
      setClearConfirming(false)
    } catch (err) {
      setClearError(err instanceof Error ? err.message : "清空失敗")
    } finally {
      setClearing(false)
    }
  }

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">關注買進清單</h1>
          <p className="text-xs text-slate-500 mt-1">
            已加入 <span className="font-mono text-slate-300">{total}</span> / {capacity} 檔
          </p>
        </div>
        {items.length > 0 && (
          <div className="flex items-center gap-2">
            {clearConfirming ? (
              <>
                <span className="text-xs text-amber-300">確定清空？</span>
                <button
                  type="button"
                  onClick={handleClear}
                  disabled={clearing}
                  className="rounded-md bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-60"
                >
                  {clearing ? "清空中…" : "確認清空"}
                </button>
                <button
                  type="button"
                  onClick={() => setClearConfirming(false)}
                  disabled={clearing}
                  className="rounded-md border border-slate-600 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                >
                  取消
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setClearConfirming(true)}
                className="rounded-md border border-red-700/60 bg-red-900/20 px-3 py-1 text-xs text-red-300 hover:bg-red-900/40"
              >
                清空清單
              </button>
            )}
          </div>
        )}
      </div>

      {clearError && <p className="text-xs text-red-400 mb-3">{clearError}</p>}

      {isLoading && items.length === 0 && (
        <p className="text-sm text-slate-500">載入中…</p>
      )}

      {error && (
        <div className="flex items-center gap-3 mb-4">
          <p className="text-sm text-red-400">{error}</p>
          <button
            type="button"
            onClick={refresh}
            className="rounded border border-slate-600 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            重試
          </button>
        </div>
      )}

      {!isLoading && !error && items.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-sm text-slate-500">
          清單還沒有任何股票。到首頁或產業頁面的個股卡片點「加入清單」即可開始。
        </div>
      )}

      {items.length > 0 && (
        <WatchlistTradeQualityCards />
      )}
    </main>
  )
}

export default function WatchlistPage() {
  return (
    <RequireAuth>
      <WatchlistPageContent />
    </RequireAuth>
  )
}
