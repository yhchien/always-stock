"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import KeyFactorsTimeline from "@/components/KeyFactorsTimeline"
import {
  fetchWatchlistTradeQuality,
  refreshWatchlistTradeQuality,
  toDisplayError,
  type TradeQualityRating,
  type WatchlistTradeQualityItem,
  type WatchlistTradeQualityResponse,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"

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

function PctCell({ value }: { value: number | null | undefined }) {
  if (value == null || Number.isNaN(value)) return <span className="text-slate-500 text-xs">—</span>
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono text-xs ${color}`}>
      {arrow} {value >= 0 ? "+" : ""}{value.toFixed(2)}%
    </span>
  )
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
    <div className="flex items-center gap-2">
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

interface WatchlistTradeQualityTableProps {
  /** 預設折疊；點 header 展開 */
  defaultCollapsed?: boolean
}

export default function WatchlistTradeQualityTable({
  defaultCollapsed = false,
}: WatchlistTradeQualityTableProps) {
  const { status } = useAuth()
  const router = useRouter()
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

  if (status !== "authenticated") return null

  const items = data?.items ?? []

  return (
    <section className="flex flex-col gap-3">
      <div className="flex w-full items-baseline justify-between gap-3 rounded-lg border border-zinc-700 bg-zinc-800/40 px-4 py-3">
        <button
          type="button"
          className="flex flex-1 items-baseline gap-3 text-left"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span className="text-base font-semibold text-slate-100">
            {collapsed ? "▸" : "▾"} 自選清單表現
          </span>
          {data?.snapshot_trade_date ? (
            <span className="text-xs text-slate-500">
              快照日期 {data.snapshot_trade_date}
            </span>
          ) : null}
          {data ? (
            <span className="text-xs text-slate-500">
              共 {data.total} 檔
            </span>
          ) : null}
        </button>
        <div className="flex items-center gap-3">
          {loading ? <span className="text-xs text-slate-500">載入中…</span> : null}
          <Link
            href="/watchlist"
            className="text-xs text-sky-300 hover:text-sky-200 hover:underline"
          >
            看詳細 →
          </Link>
        </div>
      </div>

      {!collapsed && (
        <>
          {loading && (
            <div className="flex flex-col gap-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          )}
          {error && <p className="text-sm text-red-400">{error}</p>}

          {!loading && !error && items.length === 0 && (
            <p className="text-sm text-slate-500">
              你的自選清單目前沒有股票。先到熱錢排行或產業頁加入持股，每天 18:00 ETL 跑完後會自動產生交易質量分析。
            </p>
          )}

          {!loading && !error && items.length > 0 && (
            <div className="rounded-lg border border-slate-700 overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700 hover:bg-transparent">
                    <TableHead className="text-slate-300">個股</TableHead>
                    <TableHead className="text-slate-300 text-right">最新收盤</TableHead>
                    <TableHead className="text-slate-300 text-right">漲跌幅</TableHead>
                    <TableHead className="text-slate-300 text-right hidden sm:table-cell">未實現</TableHead>
                    <TableHead className="text-slate-300">動作建議</TableHead>
                    <TableHead className="text-slate-300 hidden md:table-cell">近 2 日燈號</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <Row
                      key={item.stock_id}
                      item={item}
                      isRefreshing={refreshing.has(item.stock_id)}
                      onClick={() =>
                        router.push(
                          `/stocks/${encodeURIComponent(item.stock_id)}?buy_date=${item.buy_date}#watchlist-trade-quality`,
                        )
                      }
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function Row({
  item,
  isRefreshing,
  onClick,
}: {
  item: WatchlistTradeQualityItem
  isRefreshing: boolean
  onClick: () => void
}) {
  const latest = item.latest
  const previous = item.previous
  const isFailed = latest?.status === "failed"

  return (
    <TableRow
      className="border-slate-700 cursor-pointer hover:bg-slate-800/40"
      onClick={onClick}
    >
      <TableCell>
        <div className="flex flex-col">
          <span className="text-sm font-medium text-slate-100">{item.stock_name}</span>
          <span className="font-mono text-xs text-slate-500">
            {item.stock_id} · 買進 {item.buy_date}
          </span>
        </div>
      </TableCell>
      <TableCell className="text-right font-mono text-xs text-slate-200">
        {item.latest_close != null ? item.latest_close.toFixed(2) : "—"}
      </TableCell>
      <TableCell className="text-right">
        <PctCell value={item.change_pct} />
      </TableCell>
      <TableCell className="text-right hidden sm:table-cell">
        <PctCell value={item.unrealized_pct} />
      </TableCell>
      <TableCell>
        {latest && latest.rating ? (
          <RatingPill
            rating={latest.rating}
            label={latest.rating_label}
            isStale={latest.is_stale}
            prevRating={previous?.rating ?? null}
          />
        ) : isRefreshing ? (
          <span className="text-xs text-slate-400">分析中…</span>
        ) : isFailed ? (
          <span className="text-xs text-rose-300">分析失敗</span>
        ) : (
          <span className="text-xs text-slate-500">尚未分析</span>
        )}
      </TableCell>
      <TableCell className="hidden md:table-cell">
        {item.recent_factors?.length ? (
          <KeyFactorsTimeline recent={item.recent_factors} compact />
        ) : (
          <span className="text-[11px] text-slate-600">—</span>
        )}
      </TableCell>
    </TableRow>
  )
}
