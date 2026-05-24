"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import {
  fetchSignalRegenerateQuota,
  fetchLatestSignalSnapshot,
  type RealtimeQuote,
  type SignalJobResponse,
  regenerateSignals,
  type SignalDecisionType,
  type SignalRegenerateQuotaResponse,
  type SignalSnapshotResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"
import { useSignalJobPolling } from "@/lib/useSignalJobPolling"
import {
  signalValueLabel,
  signalValueTone,
  toneChipClass,
} from "@/lib/signalPresentation"
import { Dialog } from "@base-ui/react/dialog"

import SignalEmotionCard, { type EmotionTone } from "@/components/SignalEmotionCard"
import TradingPlanPanel, {
  PanelBulletList,
  type TradingPlanAccent,
} from "@/components/TradingPlanPanel"
import WatchlistAddButton from "@/components/WatchlistAddButton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const LAST_SEEN_KEY = "always-stock:signals:last_seen_snapshot_date"
const COLLAPSED_KEY = "always-stock:signals:collapsed"
const TAB_QUERY_KEY = "signals_tab"
type SignalsTab = "leader" | "follower" | "laggard"

const VALID_TABS: ReadonlySet<SignalsTab> = new Set(["leader", "follower", "laggard"])

function isSignalsTab(value: string | null): value is SignalsTab {
  return value !== null && VALID_TABS.has(value as SignalsTab)
}

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

function decisionToTone(type: SignalDecisionType | null | undefined): EmotionTone {
  if (type === "FOLLOWER") return "follower"
  if (type === "LAGGARD") return "laggard"
  return "leader"
}

type ReasonSection = {
  key: keyof Pick<
    SignalWatchlistItem,
    "theme_reason" | "capital_reason" | "chip_reason" | "margin_reason" | "technical_reason"
  >
  number: number
  title: string
  accent: TradingPlanAccent
}

// 5 段 panel 順序 / 配色（對應 M2 backend 5 段 bullet）
const REASON_PANELS: ReasonSection[] = [
  { key: "theme_reason", number: 1, title: "題材", accent: "amber" },
  { key: "capital_reason", number: 2, title: "資金", accent: "cyan" },
  { key: "chip_reason", number: 3, title: "籌碼", accent: "emerald" },
  { key: "margin_reason", number: 4, title: "融券", accent: "rose" },
  { key: "technical_reason", number: 5, title: "技術", accent: "slate" },
]

function SignalCard({
  item,
  quote,
}: {
  item: SignalWatchlistItem
  quote: RealtimeQuote | undefined
}) {
  const [detailOpen, setDetailOpen] = useState(false)
  const themeFit = item.theme_fit
  const subtitle =
    item.industry != null ? (
      <>
        {item.industry}
        {item.sub_industry ? <span className="text-slate-500"> · {item.sub_industry}</span> : null}
      </>
    ) : null

  // 偵測是否有任一段 bullet array 有內容（舊快照可能全 null/empty → 不顯示「看細節」按鈕）
  const hasReasonSections = REASON_PANELS.some((p) => {
    const bullets = item[p.key]
    return Array.isArray(bullets) && bullets.length > 0
  })

  return (
    <>
      <SignalEmotionCard
        tone={decisionToTone(item.type)}
        stockId={item.stock}
        stockName={item.name ?? null}
        subtitle={subtitle}
        headerRight={<WatchlistAddButton stockId={item.stock} variant="compact" />}
      >
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <InlinePrice quote={quote} />
            {themeFit ? (
              <span
                className={`shrink-0 inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone("theme_fit", themeFit))}`}
              >
                題材 {signalValueLabel(themeFit, "theme_fit")}
              </span>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <ChipWithLabel label="資金" kind="capital_flow" value={item.signals?.capital_flow} />
            <ChipWithLabel label="籌碼" kind="chip_trend" value={item.signals?.chip_trend} />
            <ChipWithLabel
              label="融券"
              kind="margin_short_signal"
              value={item.signals?.margin_short_signal}
            />
            <ChipWithLabel label="技術" kind="technical_status" value={item.signals?.technical_status} />
            {hasReasonSections ? (
              <button
                type="button"
                onClick={(e) => {
                  // SignalEmotionCard 整張卡是 Link，需 preventDefault 否則點按鈕也會跳 L2
                  e.preventDefault()
                  e.stopPropagation()
                  setDetailOpen(true)
                }}
                className="ml-auto inline-flex items-center gap-1 rounded border border-slate-500/50 bg-slate-700/40 px-1.5 py-0.5 text-[11px] font-medium text-slate-200 hover:bg-slate-700/60"
              >
                看細節 <span aria-hidden>↗</span>
              </button>
            ) : null}
          </div>
        </div>
      </SignalEmotionCard>

      <SignalDetailDialog
        item={item}
        quote={quote}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </>
  )
}

function SignalDetailDialog({
  item,
  quote,
  open,
  onOpenChange,
}: {
  item: SignalWatchlistItem
  quote: RealtimeQuote | undefined
  open: boolean
  onOpenChange: (next: boolean) => void
}) {
  const stockHref = `/stocks/${encodeURIComponent(item.stock)}`
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[min(96vw,56rem)] max-h-[88vh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl sm:p-6">
          {/* Header */}
          <div className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="flex items-baseline gap-2 text-xl font-black text-slate-100">
                <span>{item.stock}</span>
                {item.name ? <span className="text-base">{item.name}</span> : null}
                {item.type ? (
                  <span className="rounded-full border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-xs font-medium text-rose-200">
                    {item.type === "LEADER" ? "領漲" : item.type === "FOLLOWER" ? "跟漲" : "補漲"}
                  </span>
                ) : null}
              </Dialog.Title>
              <Dialog.Description className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                {item.industry ? (
                  <span>
                    {item.industry}
                    {item.sub_industry ? <span className="text-slate-500"> · {item.sub_industry}</span> : null}
                  </span>
                ) : null}
                <InlinePrice quote={quote} />
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="shrink-0 rounded-lg border border-slate-600 bg-slate-800/60 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
              aria-label="關閉"
            >
              ✕
            </Dialog.Close>
          </div>

          {/* 訊號 chip 列 */}
          <div className="mb-4 flex flex-wrap gap-1.5">
            {item.theme_fit ? (
              <span
                className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone("theme_fit", item.theme_fit))}`}
              >
                題材 {signalValueLabel(item.theme_fit, "theme_fit")}
              </span>
            ) : null}
            <ChipWithLabel label="資金" kind="capital_flow" value={item.signals?.capital_flow} />
            <ChipWithLabel label="籌碼" kind="chip_trend" value={item.signals?.chip_trend} />
            <ChipWithLabel label="融券" kind="margin_short_signal" value={item.signals?.margin_short_signal} />
            <ChipWithLabel label="技術" kind="technical_status" value={item.signals?.technical_status} />
          </div>

          {/* 5 panel grid（modal 寬度 56rem，明顯比卡片寬，每行字數舒服） */}
          <div className="grid gap-3 sm:grid-cols-2">
            {REASON_PANELS.map((p) => {
              const bullets = (item[p.key] ?? []) as string[]
              if (bullets.length === 0) return null
              return (
                <TradingPlanPanel
                  key={p.key}
                  number={p.number}
                  title={p.title}
                  accent={p.accent}
                >
                  <PanelBulletList items={bullets} bulletAccent={p.accent} />
                </TradingPlanPanel>
              )
            })}
          </div>

          {/* Footer: 跳 L2 入口 */}
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-zinc-700 pt-4">
            <p className="text-xs text-slate-500">
              想看 K 線、財報、回測等完整研究頁面 →
            </p>
            <Link
              href={stockHref}
              className="inline-flex items-center rounded-lg border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-sm font-medium text-sky-200 hover:bg-sky-500/20"
            >
              前往個股研究頁 →
            </Link>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ChipWithLabel({
  label,
  kind,
  value,
}: {
  label: string
  kind: string
  value: string | null | undefined
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone(kind, value))}`}
    >
      <span className="text-slate-400">{label}</span>
      <span>{signalValueLabel(value, kind)}</span>
    </span>
  )
}

function SignalCardGrid({
  items,
  realtimeQuotes,
  emptyText,
}: {
  items: SignalWatchlistItem[]
  realtimeQuotes: Map<string, RealtimeQuote>
  emptyText: string
}) {
  if (items.length === 0) {
    return <p className="text-sm text-slate-400">{emptyText}</p>
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item) => (
        <SignalCard key={item.stock} item={item} quote={realtimeQuotes.get(item.stock)} />
      ))}
    </div>
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
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const tabParam = searchParams.get(TAB_QUERY_KEY)
  const tab: SignalsTab = isSignalsTab(tabParam) ? tabParam : "leader"

  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(initialSnapshot ?? null)
  const [snapshotLoading, setSnapshotLoading] = useState(!initialSnapshotLoaded)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(true)
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
      const params = new URLSearchParams(searchParams.toString())
      if (next === "leader") {
        // 預設 tab 不寫進 URL，避免 query 過於雜亂
        params.delete(TAB_QUERY_KEY)
      } else {
        params.set(TAB_QUERY_KEY, next)
      }
      const queryString = params.toString()
      router.replace(queryString ? `${pathname}?${queryString}` : pathname, { scroll: false })
      markSeen()
    },
    [markSeen, pathname, router, searchParams],
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
          <span className="text-[11px] text-slate-500">每日將於晚上 21:30 更新</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/signals/archive"
            className="inline-flex items-center rounded border border-slate-600 bg-slate-800/50 px-3 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            30日追蹤
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

                <TabsContent value="leader" className="mt-3">
                  <SignalCardGrid
                    items={filteredLeader}
                    realtimeQuotes={realtimeQuotes}
                    emptyText="本日無領漲訊號。"
                  />
                </TabsContent>
                <TabsContent value="follower" className="mt-3">
                  <SignalCardGrid
                    items={filteredFollower}
                    realtimeQuotes={realtimeQuotes}
                    emptyText="本日無跟漲訊號。"
                  />
                </TabsContent>
                <TabsContent value="laggard" className="mt-3">
                  <SignalCardGrid
                    items={filteredLaggard}
                    realtimeQuotes={realtimeQuotes}
                    emptyText="本日無補漲訊號。"
                  />
                </TabsContent>
              </Tabs>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
