"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"

import {
  fetchSignalRegenerateQuota,
  fetchLatestSignalSnapshot,
  type RealtimeQuote,
  type SignalJobResponse,
  regenerateSignals,
  type SignalRegenerateQuotaResponse,
  type SignalSnapshotResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"
import { useSignalJobPolling } from "@/lib/useSignalJobPolling"
import {
  decisionBadgeClass,
  signalDecisionLabel,
  signalValueLabel,
  signalValueTone,
  toneChipClass,
} from "@/lib/signalPresentation"
import WatchlistAddButton from "@/components/WatchlistAddButton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const LAST_SEEN_KEY = "always-stock:signals:last_seen_snapshot_date"
const COLLAPSED_KEY = "always-stock:signals:collapsed"
type SignalsTab = "leader" | "follower" | "laggard"

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

function InlineMetric({
  label,
  value,
}: {
  label: string
  value: string | null | undefined
}) {
  return (
    <span className="inline-flex shrink-0 items-baseline gap-1.5 whitespace-nowrap text-xs">
      <span className="text-slate-500">{label}</span>
      <span
        className={`inline-flex rounded border px-1.5 py-0.5 font-medium ${toneChipClass(signalValueTone(label, value))}`}
      >
        {signalValueLabel(value)}
      </span>
    </span>
  )
}

function InlinePrice({
  quote,
}: {
  quote: RealtimeQuote | undefined
}) {
  if (!quote || quote.price == null) {
    return (
      <span className="shrink-0 whitespace-nowrap text-xs text-slate-500">報價載入中</span>
    )
  }
  const change = quote.change_pct
  const hasChange = change != null && !Number.isNaN(change)
  const color = !hasChange
    ? "text-slate-300"
    : (change as number) > 0
      ? "text-red-400"
      : (change as number) < 0
        ? "text-green-400"
        : "text-slate-300"
  const arrow = !hasChange
    ? ""
    : (change as number) > 0
      ? "▲"
      : (change as number) < 0
        ? "▼"
        : ""
  return (
    <span className="inline-flex shrink-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="font-mono text-sm text-slate-100">{quote.price.toFixed(2)}</span>
      {hasChange ? (
        <span className={`font-mono text-xs ${color}`}>
          {arrow} {(change as number) >= 0 ? "+" : ""}
          {(change as number).toFixed(2)}%
        </span>
      ) : null}
    </span>
  )
}

function SignalCard({
  item,
  quote,
}: {
  item: SignalWatchlistItem
  quote: RealtimeQuote | undefined
}) {
  const router = useRouter()
  const stockHref = `/stocks/${encodeURIComponent(item.stock)}`
  const themeFit = item.theme_fit

  return (
    <article
      onClick={() => router.push(stockHref)}
      className="cursor-pointer rounded-lg border border-slate-600/60 bg-slate-800/40 px-4 py-3 transition-colors hover:bg-slate-800/70"
    >
      {/* 各欄位給 min-w，多張卡片同欄會對齊；conditional 欄位（題材/產業）缺值時用 placeholder 佔位。 */}
      <div className="-mx-1 flex flex-nowrap items-center gap-4 overflow-x-auto px-1">
        <Link
          href={stockHref}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0 min-w-[140px] whitespace-nowrap text-sm font-semibold text-slate-100 hover:text-sky-300"
        >
          {item.stock} {item.name ?? ""}
        </Link>
        <div className="shrink-0 min-w-[64px]">
          {item.type ? (
            <span
              className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-medium ${decisionBadgeClass(
                item.type,
              )}`}
            >
              {signalDecisionLabel(item.type)}
            </span>
          ) : null}
        </div>
        <div className="shrink-0 min-w-[110px]">
          <InlinePrice quote={quote} />
        </div>
        <div className="shrink-0 min-w-[100px]">
          {themeFit ? (
            <span
              className={`whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone("theme_fit", themeFit))}`}
            >
              題材 {signalValueLabel(themeFit)}
            </span>
          ) : null}
        </div>
        <div className="shrink-0 min-w-[110px]">
          <InlineMetric label="資金" value={item.signals?.capital_flow} />
        </div>
        <div className="shrink-0 min-w-[110px]">
          <InlineMetric label="籌碼" value={item.signals?.chip_trend} />
        </div>
        <div className="shrink-0 min-w-[110px]">
          <InlineMetric label="融券" value={item.signals?.margin_short_signal} />
        </div>
        <div className="shrink-0 min-w-[110px]">
          <InlineMetric label="技術" value={item.signals?.technical_status} />
        </div>
        <div className="shrink-0 min-w-[140px] whitespace-nowrap text-xs text-slate-400">
          {item.industry ? (
            <>
              {item.industry}
              {item.sub_industry ? ` · ${item.sub_industry}` : ""}
            </>
          ) : null}
        </div>
        <div className="shrink-0 min-w-[88px]">
          <WatchlistAddButton
            stockId={item.stock}
            stockName={item.name ?? undefined}
            defaultAvgPrice={quote?.price ?? null}
            variant="compact"
          />
        </div>
        <Link
          href={stockHref}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0 inline-flex items-center whitespace-nowrap rounded border border-sky-500/50 bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
        >
          點我看更多分析結果
        </Link>
      </div>
    </article>
  )
}

export default function DailySignalsPanel({
  initialSnapshot,
  initialSnapshotLoaded = false,
  initialJob,
  initialJobLoaded = false,
}: {
  initialSnapshot?: SignalSnapshotResponse | null
  initialSnapshotLoaded?: boolean
  initialJob?: SignalJobResponse | null
  initialJobLoaded?: boolean
}) {
  const { status: authStatus } = useAuth()
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(initialSnapshot ?? null)
  const [snapshotLoading, setSnapshotLoading] = useState(!initialSnapshotLoaded)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(true)
  const [tab, setTab] = useState<SignalsTab>("leader")
  const [hasNewSignals, setHasNewSignals] = useState(false)
  const [bumpKey, setBumpKey] = useState(0)
  const [regenerating, setRegenerating] = useState(false)
  const [regenerateError, setRegenerateError] = useState<string | null>(null)
  const [regenerateQuota, setRegenerateQuota] = useState<SignalRegenerateQuotaResponse | null>(null)

  const { job } = useSignalJobPolling(bumpKey, initialJob ?? null, initialJobLoaded)
  const jobStatus = job?.status

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
      const data = await fetchLatestSignalSnapshot({ bypassCache: true })
      setSnapshot(data)
    } catch (err) {
      setSnapshotError(err instanceof Error ? err.message : "訊號清單載入失敗")
    } finally {
      setSnapshotLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialSnapshotLoaded) return
    void loadSnapshot()
  }, [initialSnapshotLoaded, loadSnapshot])

  const loadRegenerateQuota = useCallback(async () => {
    if (authStatus !== "authenticated") {
      setRegenerateQuota(null)
      return
    }
    try {
      const data = await fetchSignalRegenerateQuota()
      setRegenerateQuota(data)
    } catch {
      setRegenerateQuota(null)
    }
  }, [authStatus])

  useEffect(() => {
    void loadRegenerateQuota()
  }, [loadRegenerateQuota, bumpKey, jobStatus])

  // 偵測 job 完成 → 重新拉 snapshot
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
      // 成功觸發後立刻清掉前一份 snapshot，避免使用者看到 stale 資料停留到新 job 完成；
      // 失敗（429 / 409）不清，保留前次清單可用。
      setSnapshot(null)
      setSnapshotError(null)
      setSnapshotLoading(false)
      setHasNewSignals(false)
      void loadRegenerateQuota()
      // 觸發 polling 重啟
      setBumpKey((k) => k + 1)
    } catch (err) {
      setRegenerateError(err instanceof Error ? err.message : "重新產生失敗")
      void loadRegenerateQuota()
    } finally {
      setRegenerating(false)
    }
  }, [loadRegenerateQuota])

  const watchlist = useMemo(() => snapshot?.data.watchlist ?? [], [snapshot])
  const summary = snapshot?.data.summary
  const leaderCount = summary?.leader_count ?? watchlist.filter((w) => w.type === "LEADER").length
  const followerCount =
    summary?.follower_count ?? watchlist.filter((w) => w.type === "FOLLOWER").length
  const laggardCount =
    summary?.laggard_count ?? watchlist.filter((w) => w.type === "LAGGARD").length

  const isAuthed = authStatus === "authenticated"
  const isJobActive = jobStatus === "pending" || jobStatus === "running"
  const quotaReached = isAuthed && !!regenerateQuota?.disabled

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
  } else if (quotaReached) {
    regenerateLabel = `重新產生（今日已達 ${regenerateQuota?.daily_limit ?? 3} 次）`
    regenerateDisabled = true
  }

  const filteredLeader = watchlist.filter((w) => w.type === "LEADER")
  const filteredFollower = watchlist.filter((w) => w.type === "FOLLOWER")
  const filteredLaggard = watchlist.filter((w) => w.type === "LAGGARD")

  // 收集所有 SignalCard 顯示的股票 ID，一次抓 batch realtime quote。
  // 折疊狀態下不抓（避免無謂打 API），展開後 hook 會自動觸發。
  const watchlistStockIds = useMemo(
    () => (collapsed ? [] : watchlist.map((w) => w.stock).filter(Boolean)),
    [collapsed, watchlist],
  )
  const realtimeQuotes = useRealtimeQuotes(watchlistStockIds)

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
            <span>今日捕獲的大魚尾</span>
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
          <Link
            href="/signals/archive"
            className="inline-flex items-center rounded border border-slate-600 bg-slate-800/50 px-3 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            40日追蹤
          </Link>
          <button
            type="button"
            onClick={handleRegenerate}
            disabled={regenerateDisabled}
            className="inline-flex items-center rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {regenerateLabel}
          </button>
          {isAuthed && regenerateQuota && (
            <span className="text-[11px] text-slate-400">
              今日剩餘 {regenerateQuota.remaining_count}/{regenerateQuota.daily_limit}
            </span>
          )}
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
          {!snapshotLoading && !snapshotError && !snapshot && isJobActive && (
            <p className="text-sm text-slate-300">
              正在重新產生訊號清單，請稍候…完成後會自動更新。
            </p>
          )}
          {!snapshotLoading && !snapshotError && !snapshot && !isJobActive && (
            <p className="text-sm text-slate-400">
              目前尚無訊號清單。{isAuthed ? "點擊上方「重新產生」即可建立第一份清單。" : "請等待排程產生或登入後手動觸發。"}
            </p>
          )}
          {!snapshotLoading && !snapshotError && snapshot && (
            <div className="flex flex-col gap-3">
              <Tabs value={tab} onValueChange={(v) => handleTabChange(v as SignalsTab)}>
                <TabsList className="bg-slate-800/50 border border-slate-600/40">
                  <TabsTrigger
                    value="leader"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    領漲 ({leaderCount})
                  </TabsTrigger>
                  <TabsTrigger
                    value="follower"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    跟漲 ({followerCount})
                  </TabsTrigger>
                  <TabsTrigger
                    value="laggard"
                    className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-300"
                  >
                    補漲 ({laggardCount})
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="leader" className="mt-3 flex flex-col gap-3">
                  {filteredLeader.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無領漲訊號。</p>
                  ) : (
                    filteredLeader.map((item) => (
                      <SignalCard key={item.stock} item={item} quote={realtimeQuotes.get(item.stock)} />
                    ))
                  )}
                </TabsContent>
                <TabsContent value="follower" className="mt-3 flex flex-col gap-3">
                  {filteredFollower.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無跟漲訊號。</p>
                  ) : (
                    filteredFollower.map((item) => (
                      <SignalCard key={item.stock} item={item} quote={realtimeQuotes.get(item.stock)} />
                    ))
                  )}
                </TabsContent>
                <TabsContent value="laggard" className="mt-3 flex flex-col gap-3">
                  {filteredLaggard.length === 0 ? (
                    <p className="text-sm text-slate-400">本日無補漲訊號。</p>
                  ) : (
                    filteredLaggard.map((item) => (
                      <SignalCard key={item.stock} item={item} quote={realtimeQuotes.get(item.stock)} />
                    ))
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
