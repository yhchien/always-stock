"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import KeyFactorsTimeline from "@/components/KeyFactorsTimeline"
import SignalEmotionCard, { type EmotionTone } from "@/components/SignalEmotionCard"
import {
  fetchWatchlistTradeQuality,
  refreshWatchlistTradeQuality,
  toDisplayError,
  type TradeQualityRating,
  type WatchlistTradeQualityItem,
  type WatchlistTradeQualityResponse,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"

// 評級排序權重：強烈推薦 → 推薦 → 中立 → 再看看 → 快跑 → 未分析
const RATING_ORDER: Record<TradeQualityRating, number> = {
  STRONG_BUY: 0,
  BUY: 1,
  NEUTRAL: 2,
  WATCH: 3,
  RUN: 4,
}

function ratingWeight(item: WatchlistTradeQualityItem): number {
  const rating = item.latest?.rating
  if (rating && rating in RATING_ORDER) return RATING_ORDER[rating]
  return 99 // 未分析 / failed / null 排最後
}

const RATING_STYLES: Record<
  TradeQualityRating,
  { dot: string; chip: string; label: string }
> = {
  STRONG_BUY: { dot: "bg-emerald-400", chip: "bg-emerald-600/30 border-emerald-500/50 text-emerald-100", label: "強烈推薦" },
  BUY:        { dot: "bg-emerald-400", chip: "bg-emerald-700/30 border-emerald-600/40 text-emerald-200", label: "推薦" },
  NEUTRAL:    { dot: "bg-amber-400",   chip: "bg-amber-700/30 border-amber-600/40 text-amber-200",       label: "中立" },
  WATCH:      { dot: "bg-orange-400",  chip: "bg-orange-700/30 border-orange-600/40 text-orange-200",    label: "再看看" },
  RUN:        { dot: "bg-rose-500",    chip: "bg-rose-700/30 border-rose-600/40 text-rose-200",          label: "快跑" },
}

function ratingToTone(rating: TradeQualityRating | null | undefined): EmotionTone {
  switch (rating) {
    case "STRONG_BUY":
      return "strong-buy"
    case "BUY":
      return "buy"
    case "NEUTRAL":
      return "neutral"
    case "WATCH":
      return "watch"
    case "RUN":
      return "run"
    default:
      return "neutral"
  }
}

interface RatingPillProps {
  rating: TradeQualityRating
  label: string | null
  isStale?: boolean
  prevRating?: TradeQualityRating | null
}

function RatingPill({ rating, label, isStale, prevRating }: RatingPillProps) {
  const style = RATING_STYLES[rating] ?? RATING_STYLES.NEUTRAL
  const display = label ?? style.label
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} />
      <span
        className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${style.chip}`}
      >
        {display}
      </span>
      {prevRating && prevRating !== rating ? (
        <span className="text-[11px] text-slate-400">
          上次「{RATING_STYLES[prevRating]?.label ?? prevRating}」 →
        </span>
      ) : null}
      {isStale ? (
        <span className="text-[11px] text-slate-500">資料較舊</span>
      ) : null}
    </div>
  )
}

function PriceLine({
  value,
  change,
}: {
  value: number | null | undefined
  change: number | null | undefined
}) {
  if (value == null) return <span className="text-sm text-slate-500">—</span>
  const hasChange = change != null && !Number.isNaN(change)
  const color = !hasChange
    ? "text-slate-400"
    : (change as number) > 0
      ? "text-red-400"
      : (change as number) < 0
        ? "text-green-400"
        : "text-slate-400"
  const arrow = !hasChange
    ? ""
    : (change as number) > 0
      ? "▲"
      : (change as number) < 0
        ? "▼"
        : ""
  return (
    <div className="inline-flex items-baseline gap-1.5">
      <span className="font-mono text-sm text-slate-100">{value.toFixed(2)}</span>
      {hasChange ? (
        <span className={`font-mono text-xs ${color}`}>
          {arrow} {(change as number) >= 0 ? "+" : ""}
          {(change as number).toFixed(2)}%
        </span>
      ) : null}
    </div>
  )
}

function WatchlistCard({
  item,
  isRefreshing,
}: {
  item: WatchlistTradeQualityItem
  isRefreshing: boolean
}) {
  const latest = item.latest
  const previous = item.previous
  const isFailed = latest?.status === "failed"
  const detailHref = `/stocks/${encodeURIComponent(item.stock_id)}#watchlist-trade-quality`

  return (
    <SignalEmotionCard
      tone={ratingToTone(latest?.rating)}
      stockId={item.stock_id}
      stockName={item.stock_name}
      href={detailHref}
    >
      <div className="space-y-3">
        {/* 動作建議 */}
        <div>
          {latest && latest.rating ? (
            <RatingPill
              rating={latest.rating}
              label={latest.rating_label}
              isStale={latest.is_stale}
              prevRating={previous?.rating ?? null}
            />
          ) : isRefreshing ? (
            <span className="text-xs text-slate-300">分析中…</span>
          ) : isFailed ? (
            <span className="text-xs text-rose-200">分析失敗</span>
          ) : (
            <span className="text-xs text-slate-300">尚未分析</span>
          )}
        </div>

        {/* 今日股價 */}
        <div className="flex items-center justify-between">
          <PriceLine value={item.latest_close} change={item.change_pct} />
        </div>

        {/* 燈號趨勢 */}
        {item.recent_factors?.length ? (
          <KeyFactorsTimeline recent={item.recent_factors} compact />
        ) : (
          <p className="text-xs text-slate-400">尚無燈號</p>
        )}
      </div>
    </SignalEmotionCard>
  )
}

interface WatchlistTradeQualityTableProps {
  /** 預設折疊；點 header 展開 */
  defaultCollapsed?: boolean
}

export default function WatchlistTradeQualityTable({
  defaultCollapsed = false,
}: WatchlistTradeQualityTableProps) {
  const { status } = useAuth()
  const [data, setData] = useState<WatchlistTradeQualityResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const [refreshing, setRefreshing] = useState<Set<string>>(new Set())

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

  // 對 latest=null 的 row 自動觸發 on-demand refresh（fire-and-forget）
  useEffect(() => {
    if (!data || status !== "authenticated") return
    const targets = data.items.filter(
      (it) => it.latest === null && !refreshing.has(it.stock_id),
    )
    if (targets.length === 0) return
    let cancelled = false
    async function run() {
      for (const it of targets) {
        if (cancelled) return
        setRefreshing((prev) => new Set(prev).add(it.stock_id))
        try {
          await refreshWatchlistTradeQuality(it.stock_id)
        } catch {
          // 失敗會在 reload 時讀回 failed 狀態 / fallback 上一筆
        } finally {
          setRefreshing((prev) => {
            const next = new Set(prev)
            next.delete(it.stock_id)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.snapshot_trade_date, data?.total])

  // 排序：依評級權重，相同則保留 backend 原順序（加入時間）
  const items = useMemo(() => {
    const raw = data?.items ?? []
    return [...raw].sort((a, b) => ratingWeight(a) - ratingWeight(b))
  }, [data])

  if (status !== "authenticated") return null

  return (
    <section className="rounded-lg border border-zinc-700 bg-zinc-700/50">
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex flex-wrap items-baseline gap-2 text-base font-semibold text-slate-100 hover:text-sky-300"
          aria-expanded={!collapsed}
        >
          <span aria-hidden className="text-slate-400">
            {collapsed ? "▸" : "▾"}
          </span>
          <span>自選清單表現</span>
          {data?.snapshot_trade_date ? (
            <span className="text-xs font-normal text-slate-500">
              快照 {data.snapshot_trade_date}
            </span>
          ) : null}
          {data ? (
            <span className="text-xs font-normal text-slate-500">共 {data.total} 檔</span>
          ) : null}
        </button>
        <div className="flex items-center gap-3">
          {loading ? <span className="text-xs text-slate-500">載入中…</span> : null}
          <Link
            href="/watchlist"
            className="text-xs text-sky-300 hover:text-sky-200 hover:underline"
          >
            我的清單 →
          </Link>
        </div>
      </header>

      {!collapsed && (
        <div className="border-t border-zinc-700 px-4 py-4">
          {loading && (
            <div className="grid gap-3 lg:grid-cols-2">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {error && <p className="text-sm text-rose-300">{error}</p>}

          {!loading && !error && items.length === 0 && (
            <p className="text-sm text-slate-500">
              你的自選清單目前沒有股票。先到熱錢排行或產業頁加入持股，每天 18:00 ETL 跑完後會自動產生交易質量分析。
            </p>
          )}

          {!loading && !error && items.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <WatchlistCard
                  key={item.stock_id}
                  item={item}
                  isRefreshing={refreshing.has(item.stock_id)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
