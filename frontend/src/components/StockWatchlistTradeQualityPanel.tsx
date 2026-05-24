"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

import KeyFactorsList from "@/components/KeyFactorsList"
import TradingPlanPanel, { PanelBulletList } from "@/components/TradingPlanPanel"
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

  // 同 user × 同 stock 在 watchlist 是 unique（DB constraint），直接拿第一筆即可。
  const item = useMemo(
    () => items.find((candidate) => candidate.stock_id === stockId) ?? null,
    [items, stockId],
  )

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
          {item && latest?.snapshot_trade_date ? (
            <p className="mt-1 text-xs text-slate-500">
              快照 {latest.snapshot_trade_date}
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

      {/* M3 action_one_liner */}
      {item && latest?.action_one_liner ? (
        <div className="mt-3 flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <span className="text-[10px] text-amber-400/70 shrink-0 uppercase tracking-wide">建議</span>
          <span className="text-sm font-semibold text-amber-100">{latest.action_one_liner}</span>
        </div>
      ) : null}

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

      {/* M3 六段分析 panels */}
      {item && (latest?.industry_section || latest?.chip_section || latest?.fundamental_section ||
        latest?.technical_section || latest?.peer_section || latest?.news_section) ? (
        <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-3">
          <TradingPlanPanel number="1" title="產業位置" accent="amber">
            <PanelBulletList items={latest.industry_section ?? []} bulletAccent="amber" emptyText="資料待更新" />
          </TradingPlanPanel>
          <TradingPlanPanel number="2" title="籌碼動向" accent="cyan">
            <PanelBulletList items={latest.chip_section ?? []} bulletAccent="cyan" emptyText="資料待更新" />
          </TradingPlanPanel>
          <TradingPlanPanel number="3" title="基本面動能" accent="emerald">
            <PanelBulletList items={latest.fundamental_section ?? []} bulletAccent="emerald" emptyText="資料待更新" />
          </TradingPlanPanel>
          <TradingPlanPanel number="4" title="技術面" accent="rose">
            <PanelBulletList items={latest.technical_section ?? []} bulletAccent="rose" emptyText="資料待更新" />
          </TradingPlanPanel>
          <TradingPlanPanel number="5" title="同儕比較" accent="slate">
            <PanelBulletList items={latest.peer_section ?? []} bulletAccent="slate" emptyText="資料待更新" />
          </TradingPlanPanel>
          <TradingPlanPanel number="6" title="近期訊號" accent="slate">
            <PanelBulletList items={latest.news_section ?? []} bulletAccent="slate" emptyText="資料待更新" />
          </TradingPlanPanel>
        </div>
      ) : null}

      {latest?.report_markdown ? (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowReport((prev) => !prev)}
            className="text-xs text-slate-500 underline underline-offset-2 hover:text-slate-300"
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
