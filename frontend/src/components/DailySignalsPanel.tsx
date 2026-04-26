"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"

import {
  fetchLatestSignalSnapshot,
  regenerateSignals,
  type SignalDecisionType,
  type SignalRemovedItem,
  type SignalSnapshotResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useSignalJobPolling } from "@/lib/useSignalJobPolling"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const LAST_SEEN_KEY = "always-stock:signals:last_seen_snapshot_date"
const COLLAPSED_KEY = "always-stock:signals:collapsed"
type SignalsTab = "leader" | "follower" | "laggard" | "removed"

function formatTpeDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  try {
    const dt = new Date(iso)
    if (Number.isNaN(dt.getTime())) return ""
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(dt)
  } catch {
    return ""
  }
}

function decisionBadgeClass(type: SignalDecisionType | null | undefined): string {
  switch (type) {
    case "LEADER":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
    case "FOLLOWER":
      return "bg-sky-500/20 text-sky-300 border-sky-500/40"
    case "LAGGARD":
      return "bg-amber-500/20 text-amber-300 border-amber-500/40"
    default:
      return "bg-slate-500/20 text-slate-300 border-slate-500/40"
  }
}

function SignalCard({ item }: { item: SignalWatchlistItem }) {
  const stockHref = `/stocks/${encodeURIComponent(item.stock)}`
  const theme = item.theme?.main_theme
  const themeReason = item.theme?.theme_reason
  const themeFit = item.theme_fit
  const signalChips = useMemo(() => {
    const out: { label: string; value: string }[] = []
    if (item.signals?.capital_flow) out.push({ label: "資金", value: item.signals.capital_flow })
    if (item.signals?.chip_trend) out.push({ label: "籌碼", value: item.signals.chip_trend })
    if (item.signals?.margin_short_signal)
      out.push({ label: "融資券", value: item.signals.margin_short_signal })
    if (item.signals?.technical_status)
      out.push({ label: "技術", value: item.signals.technical_status })
    return out
  }, [item.signals])

  return (
    <article className="rounded-lg border border-slate-600/60 bg-slate-800/40 p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={stockHref}
            className="text-base font-semibold text-slate-100 hover:text-sky-300"
          >
            {item.stock} {item.name ?? ""}
          </Link>
          {item.type && (
            <span
              className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${decisionBadgeClass(
                item.type,
              )}`}
            >
              {item.type}
            </span>
          )}
          {item.industry && (
            <span className="text-xs text-slate-400">
              {item.industry}
              {item.sub_industry ? ` · ${item.sub_industry}` : ""}
            </span>
          )}
        </div>
        {themeFit && (
          <span className="shrink-0 text-xs text-slate-400">題材契合 {themeFit}</span>
        )}
      </header>

      {(theme || themeReason) && (
        <p className="mt-2 text-xs text-slate-300">
          {theme && <span className="font-medium text-slate-200">主題：{theme}</span>}
          {theme && themeReason && <span className="mx-1 text-slate-500">·</span>}
          {themeReason}
        </p>
      )}

      {signalChips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {signalChips.map((c) => (
            <span
              key={c.label}
              className="inline-flex items-center gap-1 rounded border border-slate-600/60 bg-slate-700/40 px-1.5 py-0.5 text-[11px] text-slate-300"
            >
              <span className="text-slate-500">{c.label}</span>
              <span>{c.value}</span>
            </span>
          ))}
        </div>
      )}

      {item.reason && (
        <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-slate-200">
          {item.reason}
        </p>
      )}
    </article>
  )
}

function RemovedCard({ item }: { item: SignalRemovedItem }) {
  return (
    <article className="rounded-lg border border-slate-600/60 bg-slate-800/40 p-3">
      <header className="flex items-center gap-2">
        <Link
          href={`/stocks/${encodeURIComponent(item.stock)}`}
          className="text-sm font-medium text-slate-200 hover:text-sky-300"
        >
          {item.stock} {item.name ?? ""}
        </Link>
        <span className="rounded border border-rose-500/40 bg-rose-500/15 px-1.5 py-0.5 text-[11px] font-medium text-rose-300">
          REMOVED
        </span>
      </header>
      {item.remove_reason && (
        <p className="mt-2 text-sm text-slate-300">{item.remove_reason}</p>
      )}
    </article>
  )
}

export default function DailySignalsPanel() {
  const { status: authStatus } = useAuth()
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(null)
  const [snapshotLoading, setSnapshotLoading] = useState(true)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(true)
  const [tab, setTab] = useState<SignalsTab>("leader")
  const [hasNewSignals, setHasNewSignals] = useState(false)
  const [bumpKey, setBumpKey] = useState(0)
  const [regenerating, setRegenerating] = useState(false)
  const [regenerateError, setRegenerateError] = useState<string | null>(null)

  const { job } = useSignalJobPolling(bumpKey)

  // 初始展開狀態：讀 localStorage（預設 collapse）
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(COLLAPSED_KEY)
      if (saved === "false") setCollapsed(false)
    } catch {
      // ignore
    }
  }, [])

  const persistCollapsed = useCallback((next: boolean) => {
    setCollapsed(next)
    try {
      window.localStorage.setItem(COLLAPSED_KEY, String(next))
    } catch {
      // ignore
    }
  }, [])

  // 載入最新 snapshot；當 job 由 running 轉 done 時重新拉一次
  const loadSnapshot = useCallback(async () => {
    setSnapshotLoading(true)
    setSnapshotError(null)
    try {
      const data = await fetchLatestSignalSnapshot()
      setSnapshot(data)
    } catch (err) {
      setSnapshotError(err instanceof Error ? err.message : "訊號清單載入失敗")
    } finally {
      setSnapshotLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSnapshot()
  }, [loadSnapshot])

  // 偵測 job 完成 → 重新拉 snapshot
  const jobStatus = job?.status
  useEffect(() => {
    if (jobStatus === "done") {
      void loadSnapshot()
    }
  }, [jobStatus, loadSnapshot])

  // 比對 last_seen → pulse badge
  useEffect(() => {
    if (!snapshot) {
      setHasNewSignals(false)
      return
    }
    try {
      const lastSeen = window.localStorage.getItem(LAST_SEEN_KEY)
      setHasNewSignals(!lastSeen || lastSeen < snapshot.snapshot_date)
    } catch {
      setHasNewSignals(false)
    }
  }, [snapshot])

  // 點擊任何 tab → 標記為已讀（清掉 pulse）
  const markSeen = useCallback(() => {
    if (!snapshot) return
    try {
      window.localStorage.setItem(LAST_SEEN_KEY, snapshot.snapshot_date)
    } catch {
      // ignore
    }
    setHasNewSignals(false)
  }, [snapshot])

  const handleTabChange = useCallback(
    (next: SignalsTab) => {
      setTab(next)
      markSeen()
    },
    [markSeen],
  )

  const handleToggleCollapse = useCallback(() => {
    const next = !collapsed
    persistCollapsed(next)
    if (!next) {
      // 展開時自動標已讀
      markSeen()
    }
  }, [collapsed, markSeen, persistCollapsed])

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true)
    setRegenerateError(null)
    try {
      await regenerateSignals()
      // 立刻觸發 polling 重啟
      setBumpKey((k) => k + 1)
    } catch (err) {
      setRegenerateError(err instanceof Error ? err.message : "重新產生失敗")
    } finally {
      setRegenerating(false)
    }
  }, [])

  const watchlist = snapshot?.data.watchlist ?? []
  const removed = snapshot?.data.removed ?? []
  const summary = snapshot?.data.summary
  const leaderCount = summary?.leader_count ?? watchlist.filter((w) => w.type === "LEADER").length
  const followerCount =
    summary?.follower_count ?? watchlist.filter((w) => w.type === "FOLLOWER").length
  const laggardCount =
    summary?.laggard_count ?? watchlist.filter((w) => w.type === "LAGGARD").length
  const removedCount = removed.length

  const isAuthed = authStatus === "authenticated"
  const isJobActive = jobStatus === "pending" || jobStatus === "running"

  // 「重新產生」按鈕狀態（spec §13.5）
  let regenerateLabel = "重新產生"
  let regenerateDisabled = false
  if (authStatus === "loading") {
    regenerateLabel = "重新產生"
    regenerateDisabled = true
  } else if (!isAuthed) {
    regenerateLabel = "重新產生（需登入）"
    regenerateDisabled = true
  } else if (isJobActive) {
    regenerateLabel = "產生中…"
    regenerateDisabled = true
  } else if (regenerating) {
    regenerateLabel = "送出中…"
    regenerateDisabled = true
  }

  const filteredLeader = watchlist.filter((w) => w.type === "LEADER")
  const filteredFollower = watchlist.filter((w) => w.type === "FOLLOWER")
  const filteredLaggard = watchlist.filter((w) => w.type === "LAGGARD")

  return (
    <section className="rounded-lg border border-zinc-700 bg-zinc-700/50">
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleToggleCollapse}
            className="flex items-center gap-2 text-base font-semibold text-slate-100 hover:text-sky-300"
            aria-expanded={!collapsed}
          >
            <span aria-hidden className="text-slate-400">
              {collapsed ? "▸" : "▾"}
            </span>
            <span>今日異常訊號清單</span>
          </button>
          {hasNewSignals && (
            <span className="ml-1 inline-flex items-center gap-1">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <span className="text-xs text-emerald-400">新</span>
            </span>
          )}
          {snapshot && (
            <span className="text-xs text-slate-400">
              {snapshot.snapshot_date}
              {snapshot.generated_at ? ` · ${formatTpeDateTime(snapshot.generated_at)}` : ""}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleRegenerate}
            disabled={regenerateDisabled}
            className="inline-flex items-center rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {regenerateLabel}
          </button>
        </div>
      </header>

      {(isJobActive || regenerateError) && (
        <div className="border-t border-zinc-700 px-4 py-2">
          {isJobActive && job && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-300">
                <span>{job.progress_label ?? job.current_stage ?? "進行中"}</span>
                <span>{Math.round(job.progress_pct ?? 0)}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700/60">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-[width]"
                  style={{ width: `${Math.min(100, Math.max(0, job.progress_pct ?? 0))}%` }}
                />
              </div>
            </div>
          )}
          {regenerateError && (
            <p className="mt-1 text-xs text-rose-300">{regenerateError}</p>
          )}
        </div>
      )}

      {!collapsed && (
        <div className="border-t border-zinc-700 px-4 py-4">
          {snapshotLoading && (
            <p className="text-sm text-slate-400">載入中…</p>
          )}
          {snapshotError && !snapshotLoading && (
            <p className="text-sm text-rose-300">{snapshotError}</p>
          )}
          {!snapshotLoading && !snapshotError && !snapshot && (
            <p className="text-sm text-slate-400">
              目前尚無訊號清單。{isAuthed ? "點擊上方「重新產生」即可建立第一份清單。" : "請等待排程產生或登入後手動觸發。"}
            </p>
          )}
          {!snapshotLoading && !snapshotError && snapshot && (
            <div className="flex flex-col gap-3">
              {snapshot.data.market_context?.market_state_reason && (
                <p className="text-xs text-slate-400">
                  <span className="font-medium text-slate-300">市場狀態：</span>
                  {snapshot.data.market_context.market_state ?? "—"}
                  <span className="mx-1 text-slate-600">·</span>
                  {snapshot.data.market_context.market_state_reason}
                </p>
              )}

              <Tabs value={tab} onValueChange={(v) => handleTabChange(v as SignalsTab)}>
                <TabsList className="bg-slate-800/50 border border-slate-600/40">
                  <TabsTrigger
                    value="leader"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    LEADER ({leaderCount})
                  </TabsTrigger>
                  <TabsTrigger
                    value="follower"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    FOLLOWER ({followerCount})
                  </TabsTrigger>
                  <TabsTrigger
                    value="laggard"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    LAGGARD ({laggardCount})
                  </TabsTrigger>
                  <TabsTrigger
                    value="removed"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    REMOVED ({removedCount})
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="leader" className="mt-3 flex flex-col gap-3">
                  {filteredLeader.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無 LEADER。</p>
                  ) : (
                    filteredLeader.map((item) => <SignalCard key={item.stock} item={item} />)
                  )}
                </TabsContent>
                <TabsContent value="follower" className="mt-3 flex flex-col gap-3">
                  {filteredFollower.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無 FOLLOWER。</p>
                  ) : (
                    filteredFollower.map((item) => <SignalCard key={item.stock} item={item} />)
                  )}
                </TabsContent>
                <TabsContent value="laggard" className="mt-3 flex flex-col gap-3">
                  {filteredLaggard.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無 LAGGARD。</p>
                  ) : (
                    filteredLaggard.map((item) => <SignalCard key={item.stock} item={item} />)
                  )}
                </TabsContent>
                <TabsContent value="removed" className="mt-3 flex flex-col gap-3">
                  {removed.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無排除股票。</p>
                  ) : (
                    removed.map((item) => <RemovedCard key={item.stock} item={item} />)
                  )}
                </TabsContent>
              </Tabs>

              {summary?.risk_note && (
                <p className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                  風險提示：{summary.risk_note}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
