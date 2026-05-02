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
const WatchlistTradeQualityTable = dynamic(
  () => import("@/components/WatchlistTradeQualityTable"),
  { ssr: false },
)
const DailySignalsPanel = dynamic(() => import("@/components/DailySignalsPanel"), { ssr: false })
const HotMoneyList = dynamic(() => import("@/components/HotMoneyList"), { ssr: false })

type BootTaskKey = "tradeDate" | "signals" | "job" | "hotMoney" | "industries"
type BootTaskState = "idle" | "loading" | "done" | "error"

const BOOT_TASK_LABELS: Record<BootTaskKey, string> = {
  tradeDate: "確認最新交易日",
  signals: "載入今日訊號清單",
  job: "確認訊號生成狀態",
  hotMoney: "載入 20 大買超",
  industries: "載入產業法人流向",
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
  const progress = Math.round((settledCount / entries.length) * 100)
  const activeLabel =
    entries.find((entry) => entry.state === "loading")?.label ??
    (progress >= 100 ? "初始化完成" : "準備中")

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/88 backdrop-blur-sm">
      <div className="w-[min(92vw,34rem)] rounded-2xl border border-slate-700 bg-slate-900/95 p-6 shadow-2xl">
        <p className="text-xs font-medium uppercase tracking-[0.28em] text-sky-300/90">Always Stock</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-100">首頁資料載入中</h1>
        <p className="mt-2 text-sm text-slate-400">{activeLabel}</p>
        <div className="mt-5 h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-sky-400 via-cyan-300 to-emerald-300 transition-[width] duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
          <span>{progress}%</span>
          <span>{settledCount} / {entries.length}</span>
        </div>
        <div className="mt-5 grid gap-2">
          {entries.map((entry) => (
            <div
              key={entry.key}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/70 px-3 py-2"
            >
              <span className="text-sm text-slate-300">{entry.label}</span>
              <span className="text-xs text-slate-500">
                {entry.state === "done" ? "完成" : entry.state === "error" ? "略過" : entry.state === "loading" ? "載入中" : "等待中"}
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
      <main className="mx-auto w-full max-w-5xl px-4 py-8 flex flex-col gap-6">
        {defaultDate && (
          <>
            <TradeQualityAnalysis initialLatestDate={latestTradeDate ?? defaultDate} />
            <DeferredSection minHeight={120}>
              <WatchlistTradeQualityTable />
            </DeferredSection>
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
