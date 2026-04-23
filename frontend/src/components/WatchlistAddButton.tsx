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

  // compact（列表欄位）故意比以前大：之前 px-2 py-0.5 text-xs 在深色表格幾乎看不見；
  // 現在用 inline-flex + 固定 min-width，確保四個字不被擠掉且點擊區夠大。
  const baseClass =
    variant === "compact"
      ? "inline-flex items-center justify-center whitespace-nowrap rounded-md border px-3 py-1 text-xs font-medium transition-colors"
      : "inline-flex items-center justify-center whitespace-nowrap rounded-md border px-3 py-1 text-xs font-medium transition-colors"

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

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          setOpen(true)
        }}
        className={`${baseClass} border-sky-500/70 bg-sky-600 text-white hover:bg-sky-500 ${className ?? ""}`}
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
