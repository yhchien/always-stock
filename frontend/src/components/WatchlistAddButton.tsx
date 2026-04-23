"use client"

import { useState } from "react"
import Link from "next/link"

import { useAuth } from "@/lib/auth"
import { useWatchlist } from "@/lib/watchlist"
import WatchlistAddDialog from "@/components/WatchlistAddDialog"

interface Props {
  stockId: string
  stockName?: string
  /** 預設買進日期（通常是目前頁面情境日期） */
  defaultDate?: string
  /** 預設均價（通常為該日收盤價） */
  defaultAvgPrice?: number | null
  /** 樣式變體：緊湊 (`compact`) 用於表格列、一般 (`default`) 用於卡片角落 */
  variant?: "default" | "compact"
  className?: string
}

export default function WatchlistAddButton({
  stockId,
  stockName,
  defaultDate,
  defaultAvgPrice,
  variant = "default",
  className,
}: Props) {
  const { status } = useAuth()
  const { has, isReady, total, capacity } = useWatchlist()
  const [open, setOpen] = useState(false)

  const inList = has(stockId)
  const atCap = total >= capacity
  const isLoggedIn = status === "authenticated"

  const baseClass =
    variant === "compact"
      ? "rounded border px-2 py-0.5 text-xs transition-colors"
      : "rounded-md border px-3 py-1 text-xs transition-colors"

  if (!isLoggedIn) {
    return (
      <Link
        href="/login"
        onClick={(e) => e.stopPropagation()}
        className={`${baseClass} border-slate-700 text-slate-500 hover:border-sky-600 hover:text-sky-300 ${className ?? ""}`}
        title="登入後即可加入清單"
      >
        加入清單
      </Link>
    )
  }

  if (!isReady) {
    return (
      <span className={`${baseClass} border-slate-800 text-slate-600 ${className ?? ""}`}>
        …
      </span>
    )
  }

  if (inList) {
    return (
      <span
        className={`${baseClass} border-emerald-700/60 bg-emerald-900/30 text-emerald-300 ${className ?? ""}`}
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
        className={`${baseClass} border-amber-700/60 bg-amber-900/20 text-amber-300 ${className ?? ""}`}
        title={`清單已達上限 ${capacity} 檔，前往管理`}
      >
        已滿 {capacity}/{capacity}
      </Link>
    )
  }

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen(true)
        }}
        className={`${baseClass} border-sky-700/60 bg-sky-900/20 text-sky-200 hover:border-sky-500 hover:bg-sky-900/40 ${className ?? ""}`}
      >
        加入清單
      </button>
      <WatchlistAddDialog
        open={open}
        onOpenChange={setOpen}
        stockId={stockId}
        stockName={stockName}
        defaultDate={defaultDate}
        defaultAvgPrice={defaultAvgPrice}
      />
    </>
  )
}
