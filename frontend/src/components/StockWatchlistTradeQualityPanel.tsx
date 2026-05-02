"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"

import KeyFactorsList from "@/components/KeyFactorsList"
import {
  fetchWatchlistTradeQuality,
  refreshWatchlistTradeQuality,
  toDisplayError,
  type TradeQualityRating,
  type WatchlistTradeQualityItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"

const RATING_STYLES: Record<
  TradeQualityRating,
  { chip: string; label: string }
> = {
  STRONG_BUY: { chip: "bg-emerald-600/30 border-emerald-500/50 text-emerald-100", label: "強烈推薦" },
  BUY: { chip: "bg-emerald-700/30 border-emerald-600/40 text-emerald-200", label: "推薦" },
  NEUTRAL: { chip: "bg-amber-700/30 border-amber-600/40 text-amber-200", label: "中立" },
  WATCH: { chip: "bg-orange-700/30 border-orange-600/40 text-orange-200", label: "再看看" },
  RUN: { chip: "bg-rose-700/30 border-rose-600/40 text-rose-200", label: "快跑" },
}

export default function StockWatchlistTradeQualityPanel({ stockId }: { stockId: string }) {
  const { status } = useAuth()
  const searchParams = useSearchParams()
  const buyDate = searchParams.get("buy_date")
  const [items, setItems] = useState<WatchlistTradeQualityItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [showReport, setShowReport] = useState(false)

  const reload = useCallback(async () => {
    if (status !== "authenticated") return
    setLoading(true)
    setError(null)
    try {
      const res = await fetchWatchlistTradeQuality({ bypassCache: true })
      setItems(res.items)
    } catch (err) {
      setError(toDisplayError(err))
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    void reload()
  }, [reload])

  const item = useMemo(() => {
    const candidates = items.filter((candidate) => candidate.stock_id === stockId)
    if (!buyDate) return candidates[0] ?? null
    return candidates.find((candidate) => candidate.buy_date === buyDate) ?? candidates[0] ?? null
  }, [buyDate, items, stockId])

  useEffect(() => {
    if (!item || item.latest || refreshing) return
    let cancelled = false

    async function run() {
      setRefreshing(true)
      try {
        await refreshWatchlistTradeQuality(stockId)
        if (!cancelled) await reload()
      } catch (err) {
        if (!cancelled) setError(toDisplayError(err))
      } finally {
        if (!cancelled) setRefreshing(false)
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [item, refreshing, reload, stockId])

  if (status !== "authenticated" || error) return null
  if (!item && !loading) return null

  const latest = item?.latest
  const style = latest?.rating ? RATING_STYLES[latest.rating] : null

  return (
    <section id="watchlist-trade-quality" className="rounded-xl border border-slate-700 bg-slate-800/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-slate-500">Watchlist Report</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">自選清單交易質量報告</h2>
          {item ? (
            <p className="mt-1 text-xs text-slate-500">
              買進日 {item.buy_date} {latest?.snapshot_trade_date ? `· 快照 ${latest.snapshot_trade_date}` : ""}
            </p>
          ) : null}
        </div>
        {latest?.rating && style ? (
          <span className={`inline-flex items-center rounded border px-3 py-1 text-sm font-semibold ${style.chip}`}>
            {latest.rating_label ?? style.label}
          </span>
        ) : null}
      </div>

      {loading && !item ? <p className="mt-3 text-sm text-slate-400">載入中…</p> : null}
      {refreshing ? <p className="mt-3 text-sm text-slate-400">正在補最新分析…</p> : null}

      {item && latest?.summary ? (
        <p className="mt-4 text-sm leading-relaxed text-slate-200">{latest.summary}</p>
      ) : null}

      {item && latest?.key_factors?.length ? (
        <div className="mt-4">
          <KeyFactorsList
            factors={latest.key_factors}
            previousFactors={item.previous?.key_factors ?? undefined}
          />
        </div>
      ) : null}

      {latest?.report_markdown ? (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowReport((prev) => !prev)}
            className="text-xs text-slate-300 underline underline-offset-2 hover:text-slate-100"
          >
            {showReport ? "收起完整報告" : "展開完整報告"}
          </button>
          {showReport ? (
            <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-950/40 p-4">
              <pre className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200 font-sans">
                {latest.report_markdown}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
