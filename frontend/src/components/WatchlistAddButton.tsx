"use client"

import { useState } from "react"
import Link from "next/link"

import { useAuth } from "@/lib/auth"
import { useWatchlist } from "@/lib/watchlist"

interface Props {
  stockId: string
  /** 樣式變體：緊湊 (`compact`) 用於表格列、一般 (`default`) 用於卡片角落 */
  variant?: "default" | "compact"
  className?: string
}

export default function WatchlistAddButton({
  stockId,
  variant: _variant = "default",
  className,
}: Props) {
  const { status } = useAuth()
  const { has, isReady, total, capacity, add } = useWatchlist()
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const inList = has(stockId)
  const atCap = total >= capacity
  const isLoggedIn = status === "authenticated"

  // 兩種 variant 樣式相同，沿用既有 inline-flex 設計，確保四個字不被擠掉且點擊區夠大。
  const baseClass =
    "inline-flex items-center justify-center whitespace-nowrap rounded-md border px-3 py-1 text-xs font-medium transition-colors"

  if (!isLoggedIn) {
    return (
      <Link
        href="/login"
        onClick={(e) => e.stopPropagation()}
        className={`${baseClass} border-sky-700/60 bg-sky-900/30 text-sky-200 hover:border-sky-500 hover:bg-sky-900/50 ${className ?? ""}`}
        title="登入後即可加入清單"
      >
        加入清單
      </Link>
    )
  }

  if (!isReady) {
    return (
      <span className={`${baseClass} border-slate-700 text-slate-500 ${className ?? ""}`}>
        …
      </span>
    )
  }

  if (inList) {
    return (
      <span
        className={`${baseClass} border-emerald-600/70 bg-emerald-900/40 text-emerald-200 ${className ?? ""}`}
      >
        已加入
      </span>
    )
  }

  if (atCap) {
    return (
      <Link
        href="/watchlist"
        onClick={(e) => e.stopPropagation()}
        className={`${baseClass} border-amber-600/70 bg-amber-900/30 text-amber-200 ${className ?? ""}`}
        title={`清單已達上限 ${capacity} 檔，前往管理`}
      >
        已滿 {capacity}/{capacity}
      </Link>
    )
  }

  const handleClick = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (submitting) return
    setSubmitting(true)
    setErrorMsg(null)
    try {
      await add(stockId)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "加入清單失敗")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={submitting}
        className={`${baseClass} border-sky-500/70 bg-sky-600 text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-60 ${className ?? ""}`}
      >
        {submitting ? "加入中…" : "加入清單"}
      </button>
      {errorMsg && (
        <span className="text-[11px] text-rose-300" onClick={(e) => e.stopPropagation()}>
          {errorMsg}
        </span>
      )}
    </span>
  )
}
