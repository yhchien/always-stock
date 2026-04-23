"use client"

import { Dialog } from "@base-ui/react/dialog"
import { useEffect, useState } from "react"

import { useWatchlist } from "@/lib/watchlist"
import { todayInTaipei } from "@/lib/utils"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: string
  stockName?: string
  /** 預設買進日期；未帶時用 Asia/Taipei 今天 */
  defaultDate?: string
  /** 預設均價（通常是該日收盤價）；可為空 */
  defaultAvgPrice?: number | null
}

export default function WatchlistAddDialog({
  open,
  onOpenChange,
  stockId,
  stockName,
  defaultDate,
  defaultAvgPrice,
}: Props) {
  const { add } = useWatchlist()
  const [buyDate, setBuyDate] = useState<string>(defaultDate ?? todayInTaipei())
  const [avgPrice, setAvgPrice] = useState<string>(
    defaultAvgPrice != null ? String(defaultAvgPrice) : "",
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 每次開啟時重置為預設值
  useEffect(() => {
    if (open) {
      setBuyDate(defaultDate ?? todayInTaipei())
      setAvgPrice(defaultAvgPrice != null ? String(defaultAvgPrice) : "")
      setError(null)
    }
  }, [open, defaultDate, defaultAvgPrice])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const parsed = Number.parseFloat(avgPrice)
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("均價必須大於 0")
      return
    }
    if (!buyDate) {
      setError("請選擇買進日期")
      return
    }

    setSubmitting(true)
    try {
      await add({ stock_id: stockId, buy_date: buyDate, avg_price: parsed })
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "加入失敗")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 transition-opacity" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[90vw] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-slate-700 bg-slate-900 p-5 shadow-xl focus:outline-none data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 transition-all">
          <Dialog.Title className="text-sm font-semibold text-slate-100">
            加入關注清單
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-slate-400">
            {stockName ? `${stockName} ` : ""}
            <span className="font-mono">{stockId}</span>
          </Dialog.Description>

          <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-slate-400">買進日期</span>
              <input
                type="date"
                value={buyDate}
                onChange={(e) => setBuyDate(e.target.value)}
                className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
                required
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-xs text-slate-400">均價</span>
              <input
                type="number"
                step="0.01"
                min="0"
                value={avgPrice}
                onChange={(e) => setAvgPrice(e.target.value)}
                placeholder="請輸入買進均價"
                className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
                required
              />
            </label>

            {error && (
              <p className="text-xs text-red-400">{error}</p>
            )}

            <div className="mt-1 flex justify-end gap-2">
              <Dialog.Close
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                disabled={submitting}
              >
                取消
              </Dialog.Close>
              <button
                type="submit"
                disabled={submitting}
                className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-60"
              >
                {submitting ? "加入中…" : "加入清單"}
              </button>
            </div>
          </form>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
