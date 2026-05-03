"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"

import KeyFactorsList from "@/components/KeyFactorsList"
import KeyFactorsTimeline from "@/components/KeyFactorsTimeline"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/lib/auth"
import {
  fetchWatchlistTradeQuality,
  refreshWatchlistTradeQuality,
  toDisplayError,
  type TradeQualityRating,
  type WatchlistTradeQualityResponse,
} from "@/lib/api"
import { useWatchlist } from "@/lib/watchlist"

const RATING_STYLES: Record<
  TradeQualityRating,
  { dot: string; chip: string; label: string }
> = {
  STRONG_BUY: { dot: "bg-emerald-400", chip: "bg-emerald-600/30 border-emerald-500/50 text-emerald-100", label: "強烈推薦" },
  BUY: { dot: "bg-emerald-400", chip: "bg-emerald-700/30 border-emerald-600/40 text-emerald-200", label: "推薦" },
  NEUTRAL: { dot: "bg-amber-400", chip: "bg-amber-700/30 border-amber-600/40 text-amber-200", label: "中立" },
  WATCH: { dot: "bg-orange-400", chip: "bg-orange-700/30 border-orange-600/40 text-orange-200", label: "再看看" },
  RUN: { dot: "bg-rose-500", chip: "bg-rose-700/30 border-rose-600/40 text-rose-200", label: "快跑" },
}

function PctDisplay({ value }: { value: number | null | undefined }) {
  if (value == null || Number.isNaN(value)) return <span className="text-sm text-slate-500">—</span>
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono text-sm font-semibold ${color}`}>
      {arrow} {value >= 0 ? "+" : ""}
      {value.toFixed(2)}%
    </span>
  )
}

function RatingPill({
  rating,
  label,
  isStale,
}: {
  rating: TradeQualityRating
  label: string | null
  isStale?: boolean
}) {
  const style = RATING_STYLES[rating] ?? RATING_STYLES.NEUTRAL
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} />
      <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${style.chip}`}>
        {label ?? style.label}
      </span>
      {isStale ? <span className="text-[11px] text-slate-500">資料較舊</span> : null}
    </div>
  )
}

export default function WatchlistTradeQualityCards() {
  const { status } = useAuth()
  const { remove, entryIdOf } = useWatchlist()
  const [data, setData] = useState<WatchlistTradeQualityResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState<Set<string>>(new Set())
  const [removing, setRemoving] = useState<Set<string>>(new Set())

  const reload = useCallback(async () => {
    if (status !== "authenticated") return
    setLoading(true)
    setError(null)
    try {
      const res = await fetchWatchlistTradeQuality()
      setData(res)
    } catch (err) {
      setError(toDisplayError(err))
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (!data || status !== "authenticated") return
    const targets = data.items.filter((item) => item.latest === null && !refreshing.has(item.stock_id))
    if (targets.length === 0) return
    let cancelled = false

    async function run() {
      for (const item of targets) {
        if (cancelled) return
        setRefreshing((prev) => new Set(prev).add(item.stock_id))
        try {
          await refreshWatchlistTradeQuality(item.stock_id)
        } catch {
          // ignore and reload the fallback state later
        } finally {
          setRefreshing((prev) => {
            const next = new Set(prev)
            next.delete(item.stock_id)
            return next
          })
        }
      }
      if (!cancelled) await reload()
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [data, refreshing, reload, status])

  const handleRefresh = useCallback(async (stockId: string) => {
    setRefreshing((prev) => new Set(prev).add(stockId))
    try {
      await refreshWatchlistTradeQuality(stockId)
      await reload()
    } catch (err) {
      setError(toDisplayError(err))
    } finally {
      setRefreshing((prev) => {
        const next = new Set(prev)
        next.delete(stockId)
        return next
      })
    }
  }, [reload])

  const handleRemove = useCallback(async (stockId: string) => {
    const entryId = entryIdOf(stockId)
    if (entryId == null) return
    setRemoving((prev) => new Set(prev).add(stockId))
    try {
      await remove(entryId)
      setData((prev) => (
        prev
          ? { ...prev, items: prev.items.filter((item) => item.stock_id !== stockId), total: Math.max(0, prev.total - 1) }
          : prev
      ))
    } catch (err) {
      setError(toDisplayError(err, "移除失敗"))
    } finally {
      setRemoving((prev) => {
        const next = new Set(prev)
        next.delete(stockId)
        return next
      })
    }
  }, [entryIdOf, remove])

  if (status !== "authenticated") return null

  const items = data?.items ?? []

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h2 className="text-base font-semibold text-slate-100">自選清單表現</h2>
          {data?.snapshot_trade_date ? (
            <span className="text-xs text-slate-500">快照日期 {data.snapshot_trade_date}</span>
          ) : null}
          {data ? <span className="text-xs text-slate-500">共 {data.total} 檔</span> : null}
        </div>
        <button
          type="button"
          onClick={reload}
          className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
        >
          {loading ? "載入中…" : "重新整理"}
        </button>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-72 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      ) : null}

      {error ? <p className="text-sm text-red-400">{error}</p> : null}

      {!loading && !error && items.length === 0 ? (
        <p className="text-sm text-slate-500">
          你的自選清單目前沒有股票。先加入標的，系統會在 ETL 後自動補上交易質量分析。
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="flex flex-col gap-3">
          {items.map((item) => {
            const latest = item.latest
            const isRefreshing = refreshing.has(item.stock_id)
            const isRemoving = removing.has(item.stock_id)
            const showRetry = !latest || latest.status === "failed" || latest.is_stale
            return (
              <article
                key={item.stock_id}
                className="flex min-w-0 flex-col rounded-xl border border-slate-700 bg-slate-800/40 p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link
                      href={`/stocks/${encodeURIComponent(item.stock_id)}?buy_date=${item.buy_date}`}
                      className="flex flex-wrap items-baseline gap-2 hover:underline"
                    >
                      <span className="font-mono text-sm text-slate-400">{item.stock_id}</span>
                      <span className="text-base font-semibold text-slate-100">{item.stock_name}</span>
                    </Link>
                    <p className="mt-1 text-xs text-slate-500">
                      {item.industry_name ?? "未分類"} · 買進 {item.buy_date}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleRemove(item.stock_id)}
                    disabled={isRemoving}
                    className="rounded p-1 text-slate-500 hover:bg-red-900/30 hover:text-red-300 disabled:opacity-50"
                    title="從清單移除"
                  >
                    ✕
                  </button>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                  <span className="text-slate-500">最新收盤</span>
                  <span className="text-right font-mono text-slate-200">
                    {item.latest_close != null ? item.latest_close.toFixed(2) : "—"}
                  </span>
                  <span className="text-slate-500">單日漲跌</span>
                  <span className="text-right">
                    <PctDisplay value={item.change_pct} />
                  </span>
                  <span className="text-slate-500">未實現損益</span>
                  <span className="text-right">
                    <PctDisplay value={item.unrealized_pct} />
                  </span>
                </div>

                <div className="mt-4 flex flex-col gap-2">
                  {latest?.rating ? (
                    <RatingPill
                      rating={latest.rating}
                      label={latest.rating_label}
                      isStale={latest.is_stale}
                    />
                  ) : isRefreshing ? (
                    <span className="text-xs text-slate-400">分析中…</span>
                  ) : latest?.status === "failed" ? (
                    <span className="text-xs text-rose-300">分析失敗，先顯示上一筆可用快照</span>
                  ) : (
                    <span className="text-xs text-slate-500">尚未分析</span>
                  )}

                  {latest?.summary ? (
                    <p className="text-sm leading-relaxed text-slate-200">{latest.summary}</p>
                  ) : null}

                  {latest?.key_factors?.length ? (
                    <KeyFactorsList factors={latest.key_factors} previousFactors={item.previous?.key_factors ?? undefined} />
                  ) : null}

                  {item.recent_factors?.length ? (
                    <KeyFactorsTimeline recent={item.recent_factors} />
                  ) : null}
                </div>

                <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-4">
                  <div className="flex flex-wrap gap-2">
                    <Link
                      href={`/stocks/${encodeURIComponent(item.stock_id)}?buy_date=${item.buy_date}#watchlist-trade-quality`}
                      className="rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
                    >
                      點我看更多分析結果
                    </Link>
                    {showRetry ? (
                      <button
                        type="button"
                        onClick={() => void handleRefresh(item.stock_id)}
                        disabled={isRefreshing}
                        className="rounded border border-slate-600 bg-slate-700/40 px-3 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                      >
                        {isRefreshing ? "分析中…" : "重新分析"}
                      </button>
                    ) : null}
                  </div>
                  {latest?.generated_at ? (
                    <span className="text-[11px] text-slate-500">
                      更新 {latest.generated_at.slice(0, 16).replace("T", " ")}
                    </span>
                  ) : null}
                </div>
              </article>
            )
          })}
        </div>
      ) : null}
    </section>
  )
}
