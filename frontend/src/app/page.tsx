"use client"

import { Suspense, useEffect, useRef, useState, type ReactNode } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import dynamic from "next/dynamic"
import IndustryDashboard from "@/components/IndustryDashboard"
import {
  fetchIndustries,
  fetchLatestSignalJob,
  fetchLatestSignalSnapshot,
  fetchLatestTradeDate,
  fetchMarketHotMoney,
  type HotMoneyResponse,
  type IndustryFlowItem,
  type SignalJobResponse,
  type SignalSnapshotResponse,
} from "@/lib/api"
import { todayInTaipei } from "@/lib/utils"

const TradeQualityAnalysis = dynamic(() => import("@/components/TradeQualityAnalysis"), { ssr: false })
const DailySignalsPanel = dynamic(() => import("@/components/DailySignalsPanel"), { ssr: false })
const HotMoneyList = dynamic(() => import("@/components/HotMoneyList"), { ssr: false })
const WatchlistTradeQualityTable = dynamic(
  () => import("@/components/WatchlistTradeQualityTable"),
  { ssr: false },
)

const TRADE_QUALITY_TOGGLE_STORAGE_KEY = "always-stock:show-trade-quality"

function readStoredToggle(key: string, defaultValue: boolean): boolean {
  if (typeof window === "undefined") return defaultValue
  const stored = window.localStorage.getItem(key)
  if (stored === null) return defaultValue
  return stored === "true"
}

function HomeSidebar({
  showTradeQuality,
  onToggleTradeQuality,
}: {
  showTradeQuality: boolean
  onToggleTradeQuality: () => void
}) {
  return (
    <nav className="fixed left-0 top-12 flex flex-col items-center gap-1 py-4 w-12 border-r border-slate-700/30 h-[calc(100dvh-3rem)] z-40 bg-slate-900">
      <button
        type="button"
        onClick={onToggleTradeQuality}
        className={`relative flex items-center justify-center w-10 h-20 rounded-md transition-colors ${
          showTradeQuality
            ? "bg-slate-800/60 text-slate-200"
            : "text-slate-600 hover:bg-slate-800/40 hover:text-slate-400"
        }`}
        title="交易質量分析"
      >
        {showTradeQuality && (
          <span className="absolute left-0.5 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-full bg-emerald-400" />
        )}
        <span className="writing-vertical text-[11px] font-medium">
          交易質量
        </span>
      </button>
    </nav>
  )
}

type BootTaskKey = "tradeDate" | "signals" | "job" | "hotMoney" | "industries"
type BootTaskState = "idle" | "loading" | "done" | "error"

const BOOT_TASK_LABELS: Record<BootTaskKey, string> = {
  tradeDate: "確認最新交易日",
  signals: "載入今日訊號清單",
  job: "確認訊號生成狀態",
  hotMoney: "載入 20 大買超",
  industries: "載入產業法人流向",
}

const BOOT_TASK_WEIGHTS: Record<BootTaskKey, number> = {
  tradeDate: 20,
  signals: 28,
  job: 12,
  hotMoney: 16,
  industries: 24,
}

const BOOT_LOADING_CREDIT = 0.58

function getBootProgress(tasks: Record<BootTaskKey, BootTaskState>) {
  const totalWeight = Object.values(BOOT_TASK_WEIGHTS).reduce((sum, weight) => sum + weight, 0)
  const completedWeight = (Object.keys(tasks) as BootTaskKey[]).reduce((sum, key) => {
    const weight = BOOT_TASK_WEIGHTS[key]
    const state = tasks[key]
    if (state === "done" || state === "error") return sum + weight
    if (state === "loading") return sum + weight * BOOT_LOADING_CREDIT
    return sum
  }, 0)
  return Math.round((completedWeight / totalWeight) * 100)
}

function HomeBootstrapOverlay({
  tasks,
}: {
  tasks: Record<BootTaskKey, BootTaskState>
}) {
  const entries = (Object.keys(tasks) as BootTaskKey[]).map((key) => ({
    key,
    label: BOOT_TASK_LABELS[key],
    state: tasks[key],
  }))
  const settledCount = entries.filter((entry) => entry.state === "done" || entry.state === "error").length
  const loadingCount = entries.filter((entry) => entry.state === "loading").length
  const progress = getBootProgress(tasks)
  const activeLabel =
    entries.find((entry) => entry.state === "loading")?.label ??
    (progress >= 100 ? "初始化完成" : "準備中")

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/88 backdrop-blur-sm">
      <div className="w-[min(92vw,34rem)] rounded-2xl border border-slate-700 bg-slate-900/95 p-6 shadow-2xl">
        <p className="text-xs font-medium uppercase tracking-[0.28em] text-sky-300/90">Always Stock</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-100">首頁資料載入中</h1>
        <div className="mt-3 flex items-center gap-3 text-sm text-slate-400">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-sky-300/30 border-t-sky-300" aria-hidden="true" />
          <span>{activeLabel}</span>
          {loadingCount > 1 ? <span className="text-slate-500">另外 {loadingCount - 1} 項同步處理中</span> : null}
        </div>
        <div className="mt-5 h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-sky-400 via-cyan-300 to-emerald-300 transition-[width] duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
          <span>{progress}%</span>
          <span>{settledCount} / {entries.length} 完成</span>
        </div>
        <div className="mt-5 grid gap-2">
          {entries.map((entry) => (
            <div
              key={entry.key}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/70 px-3 py-2"
            >
              <span className="text-sm text-slate-300">{entry.label}</span>
              <span className="flex items-center gap-2 text-xs text-slate-500">
                {entry.state === "loading" ? (
                  <span className="h-3 w-3 animate-spin rounded-full border border-cyan-300/30 border-t-cyan-300" aria-hidden="true" />
                ) : null}
                <span>
                  {entry.state === "done" ? "完成" : entry.state === "error" ? "略過" : entry.state === "loading" ? "載入中" : "等待中"}
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function DeferredSection({
  children,
  minHeight = 240,
}: {
  children: ReactNode
  minHeight?: number
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (visible || !ref.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: "320px 0px" },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [visible])

  return (
    <div ref={ref} style={{ minHeight }}>
      {visible ? (
        children
      ) : (
        <div
          className="h-full animate-pulse rounded-xl border border-slate-700/50 bg-slate-800/20"
          style={{ minHeight }}
        />
      )}
    </div>
  )
}

function HomeContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryDate = searchParams.get("date")
  const [latestTradeDate, setLatestTradeDate] = useState<string | null>(null)
  const [latestTradeDateReady, setLatestTradeDateReady] = useState(Boolean(queryDate))
  const [tasks, setTasks] = useState<Record<BootTaskKey, BootTaskState>>({
    tradeDate: queryDate ? "done" : "loading",
    signals: "idle",
    job: "idle",
    hotMoney: "idle",
    industries: "idle",
  })
  const [initialSnapshot, setInitialSnapshot] = useState<SignalSnapshotResponse | null>(null)
  const [initialSnapshotLoaded, setInitialSnapshotLoaded] = useState(false)
  const [initialJob, setInitialJob] = useState<SignalJobResponse | null>(null)
  const [initialJobLoaded, setInitialJobLoaded] = useState(false)
  const [initialHotMoney, setInitialHotMoney] = useState<HotMoneyResponse | null>(null)
  const [initialHotMoneyDate, setInitialHotMoneyDate] = useState<string | null>(null)
  const [initialIndustries, setInitialIndustries] = useState<IndustryFlowItem[] | null>(null)
  const [initialIndustriesDate, setInitialIndustriesDate] = useState<string | null>(null)
  const defaultDate = queryDate ?? latestTradeDate ?? (latestTradeDateReady ? todayInTaipei() : null)
  const [showTradeQuality, setShowTradeQuality] = useState(() =>
    readStoredToggle(TRADE_QUALITY_TOGGLE_STORAGE_KEY, false),
  )

  useEffect(() => {
    window.localStorage.setItem(TRADE_QUALITY_TOGGLE_STORAGE_KEY, String(showTradeQuality))
  }, [showTradeQuality])

  // M19 watchlist 卡片深連結帶 stock_id+buy_date → 強制展開分析欄位，
  // 避免使用者跳過來但畫面看不到分析。
  const stockIdParam = searchParams.get("stock_id")
  const buyDateParam = searchParams.get("buy_date")
  useEffect(() => {
    if (stockIdParam && buyDateParam) setShowTradeQuality(true)
  }, [stockIdParam, buyDateParam])

  function markTask(task: BootTaskKey, state: BootTaskState) {
    setTasks((prev) => (prev[task] === state ? prev : { ...prev, [task]: state }))
  }

  useEffect(() => {
    if (queryDate) return
    let cancelled = false
    fetchLatestTradeDate()
      .then((d) => {
        if (cancelled) return
        if (d) setLatestTradeDate(d)
        markTask("tradeDate", "done")
      })
      .catch(() => {
        if (cancelled) return
        markTask("tradeDate", "error")
      })
      .finally(() => {
        if (!cancelled) setLatestTradeDateReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [queryDate])

  useEffect(() => {
    if (!defaultDate) return
    let cancelled = false
    const controller = new AbortController()

    const runTask = async <T,>(
      task: BootTaskKey,
      loader: () => Promise<T>,
      onDone: (value: T) => void,
    ) => {
      markTask(task, "loading")
      try {
        const value = await loader()
        if (cancelled) return
        onDone(value)
        markTask(task, "done")
      } catch {
        if (cancelled) return
        markTask(task, "error")
      }
    }

    void runTask("signals", () => fetchLatestSignalSnapshot({ signal: controller.signal }), (value) => {
      setInitialSnapshot(value)
      setInitialSnapshotLoaded(true)
    })
    void runTask("job", () => fetchLatestSignalJob({ signal: controller.signal }), (value) => {
      setInitialJob(value)
      setInitialJobLoaded(true)
    })
    void runTask("hotMoney", () => fetchMarketHotMoney(defaultDate, 3, 20, { signal: controller.signal }), (value) => {
      setInitialHotMoney(value)
      setInitialHotMoneyDate(defaultDate)
    })
    void runTask("industries", () => fetchIndustries(defaultDate, { signal: controller.signal }), (value) => {
      setInitialIndustries(value)
      setInitialIndustriesDate(defaultDate)
    })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [defaultDate])

  const showBootOverlay =
    !defaultDate ||
    Object.values(tasks).some((state) => state === "idle" || state === "loading")

  return (
    <>
      {showBootOverlay && <HomeBootstrapOverlay tasks={tasks} />}
      <HomeSidebar
        showTradeQuality={showTradeQuality}
        onToggleTradeQuality={() => setShowTradeQuality((v) => !v)}
      />
      <main className="ml-12">
        <div className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
        {defaultDate && (
          <>
            {showTradeQuality && !showBootOverlay && (
              <TradeQualityAnalysis initialLatestDate={latestTradeDate ?? defaultDate} />
            )}
            <WatchlistTradeQualityTable />
            <DeferredSection minHeight={180}>
              <DailySignalsPanel
                initialSnapshot={initialSnapshot}
                initialSnapshotLoaded={initialSnapshotLoaded}
                initialJob={initialJob}
                initialJobLoaded={initialJobLoaded}
              />
            </DeferredSection>
            <DeferredSection minHeight={260}>
              <HotMoneyList
                date={defaultDate}
                days={3}
                limit={20}
                initialData={initialHotMoney}
                initialDataDate={initialHotMoneyDate}
              />
            </DeferredSection>
            <DeferredSection minHeight={520}>
              <IndustryDashboard
                defaultDate={defaultDate}
                initialRows={initialIndustries ?? undefined}
                initialRowsDate={initialIndustriesDate}
                onDateChange={(d) => router.replace(`/?date=${d}`, { scroll: false })}
                onSelectIndustry={(name, selectedDate) =>
                  router.push(`/industries/${encodeURIComponent(name)}?date=${selectedDate}`)
                }
              />
            </DeferredSection>
          </>
        )}
        </div>
      </main>
    </>
  )
}

export default function Home() {
  return (
    <Suspense>
      <HomeContent />
    </Suspense>
  )
}
