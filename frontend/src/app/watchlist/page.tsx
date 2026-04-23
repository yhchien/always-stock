"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"

import RequireAuth from "@/components/RequireAuth"
import { useWatchlist } from "@/lib/watchlist"
import type { WatchlistItem } from "@/lib/api"

function PctDisplay({ value }: { value: number | null }) {
  if (value == null) return <span className="text-slate-500 text-sm">—</span>
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono text-sm font-semibold ${color}`}>
      {arrow} {value >= 0 ? "+" : ""}{value.toFixed(2)}%
    </span>
  )
}

function EntryCard({
  item,
  onRemove,
}: {
  item: WatchlistItem
  onRemove: (id: number) => Promise<void>
}) {
  const router = useRouter()
  const [removing, setRemoving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleRemove(e: React.MouseEvent) {
    e.stopPropagation()
    if (removing) return
    setRemoving(true)
    setError(null)
    try {
      await onRemove(item.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除失敗")
      setRemoving(false)
    }
  }

  function goAnalysis() {
    const params = new URLSearchParams({
      stock_id: item.stock_id,
      buy_date: item.buy_date,
    })
    router.push(`/?${params.toString()}#trade-quality`)
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/40 p-4 flex flex-col gap-2 relative">
      {/* Remove X */}
      <button
        type="button"
        onClick={handleRemove}
        disabled={removing}
        title="從清單移除"
        className="absolute right-2 top-2 rounded p-1 text-slate-500 hover:bg-red-900/30 hover:text-red-300 disabled:opacity-50"
        aria-label="移除"
      >
        <span className="text-sm leading-none">✕</span>
      </button>

      {/* Header */}
      <Link
        href={`/stocks/${item.stock_id}`}
        className="flex items-baseline gap-2 hover:underline"
      >
        <span className="font-mono text-sm text-slate-400">{item.stock_id}</span>
        <span className="text-base font-medium text-slate-100">{item.stock_name}</span>
      </Link>
      {item.industry_name && (
        <span className="text-xs text-slate-500">{item.industry_name}</span>
      )}

      {/* Price panel */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs mt-1">
        <span className="text-slate-500">買進日</span>
        <span className="font-mono text-slate-200 text-right">{item.buy_date}</span>
        <span className="text-slate-500">均價</span>
        <span className="font-mono text-slate-200 text-right">{item.avg_price.toFixed(2)}</span>
        <span className="text-slate-500">最新收盤</span>
        <span className="font-mono text-slate-200 text-right">
          {item.latest_close != null ? item.latest_close.toFixed(2) : "—"}
          {item.latest_trade_date && (
            <span className="text-[10px] text-slate-500 ml-1">({item.latest_trade_date})</span>
          )}
        </span>
      </div>

      {/* Unrealized */}
      <div className="mt-1 flex items-center justify-between border-t border-slate-700 pt-2">
        <span className="text-xs text-slate-500">未實現損益</span>
        <PctDisplay value={item.unrealized_pct} />
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* Actions */}
      <div className="mt-1 flex justify-end">
        <button
          type="button"
          onClick={goAnalysis}
          className="rounded-md bg-sky-600 px-3 py-1 text-xs font-semibold text-white hover:bg-sky-500"
        >
          交易分析 →
        </button>
      </div>
    </div>
  )
}

function WatchlistPageContent() {
  const { items, total, capacity, isLoading, error, remove, clear, refresh } = useWatchlist()
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
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <EntryCard key={item.id} item={item} onRemove={remove} />
          ))}
        </div>
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
