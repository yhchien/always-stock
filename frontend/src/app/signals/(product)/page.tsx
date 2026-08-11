"use client"

import { useEffect, useState } from "react"
import Link from "next/link"

import OutcomeMetricCard from "@/components/OutcomeMetricCard"
import {
  fetchLatestSignalSnapshot,
  fetchSignalOutcomeSummary,
  fetchSignalTrackingSummary,
  type SignalOutcomeSummary,
  type SignalSnapshotResponse,
  type SignalTrackingSummary,
} from "@/lib/api"
import { useSignalsViewMode } from "@/lib/signalsViewMode"
import { formatRate } from "@/lib/signalP6Presentation"

interface NavCard {
  href: string
  title: string
  description: string
  engineeringOnly?: boolean
}

const NAV_CARDS: NavCard[] = [
  {
    href: "/signals/archive",
    title: "追蹤紀錄",
    description: "查看今日新推薦的股票、每檔的報酬率、預期價格與歷史追蹤紀錄。",
  },
  {
    href: "/signals/observations",
    title: "觀察生命週期",
    description: "工程用：原始推薦論點、每日檢查 Review Timeline 與後端證據。",
    engineeringOnly: true,
  },
  {
    href: "/signals/outcomes",
    title: "結果分析",
    description: "檢視過去推薦的事後成效（工程稽核用，個股報酬率請直接看追蹤紀錄卡片）。",
    engineeringOnly: true,
  },
  {
    href: "/signals/debug",
    title: "Debug",
    description: "查看 prompt、selection、score、tracking 版本、處理 Funnel 與完整性診斷。",
    engineeringOnly: true,
  },
]

export default function SignalsOverviewPage() {
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(null)
  const [tracking, setTracking] = useState<SignalTrackingSummary | null>(null)
  const [outcomes, setOutcomes] = useState<SignalOutcomeSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchLatestSignalSnapshot({ signal: controller.signal }),
      fetchSignalTrackingSummary(undefined, { signal: controller.signal }),
      fetchSignalOutcomeSummary(undefined, { signal: controller.signal }),
    ])
      .then(([latest, trackingSummary, outcomeSummary]) => {
        setSnapshot(latest)
        setTracking(trackingSummary.tracking_summary)
        setOutcomes(outcomeSummary)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "Signals 總覽載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const summary = snapshot?.data.summary
  const processing = summary?.processing_summary
  const selection = summary?.selection_summary
  const globalFailed =
    processing?.global_selection_status === "FAILED" ||
    selection?.selection_complete === false

  const { isEngineering } = useSignalsViewMode()
  const visibleNavCards = NAV_CARDS.filter((card) => isEngineering || !card.engineeringOnly)

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 py-6 text-slate-100">
      <header className="mb-5">
        {isEngineering && (
          <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">
            Signals Product Overview
          </p>
        )}
        <h1 className="mt-1 text-2xl font-semibold">魚尾選股總覽</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
          {isEngineering
            ? "今日正式推薦、既有觀察生命週期與 Day10 事後結果彼此分離；結果分析不會回饋 production 選股。"
            : "查看今日推薦、既有推薦的追蹤狀態，以及過去推薦的事後成效。"}
        </p>
      </header>

      {loading && <p className="text-sm text-slate-500">正在載入推薦、觀察與結果摘要…</p>}
      {error && (
        <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">
          {error}
        </p>
      )}
      {!loading && !error && !snapshot && (
        <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">
          目前還沒有可顯示的訊號快照。
        </p>
      )}

      {snapshot && (
        <>
          {globalFailed && (
            <p className="mb-4 rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
              本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單。
            </p>
          )}
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <OutcomeMetricCard
              label="最新有效交易日"
              value={snapshot.snapshot_date}
              detail={
                isEngineering
                  ? `Prompt Family：${processing?.prompt_family_version ?? "歷史版本"}`
                  : undefined
              }
            />
            <OutcomeMetricCard
              label="今日正式推薦"
              value={globalFailed ? "未完成" : snapshot.data.watchlist.length}
              detail={
                isEngineering
                  ? `未列入 ${snapshot.data.not_selected?.length ?? 0}・明確移除 ${snapshot.data.removed?.length ?? 0}`
                  : undefined
              }
              status={globalFailed ? "not-met" : "neutral"}
            />
            <OutcomeMetricCard
              label="追蹤中 / 警戒中"
              value={`${tracking?.active_before_review ?? 0} / ${tracking?.caution_count ?? 0}`}
              detail={
                isEngineering
                  ? `今日停止 ${tracking?.stopped_count ?? 0}・Review Failed ${tracking?.review_failed_count ?? 0}`
                  : `今日新停止追蹤 ${tracking?.stopped_count ?? 0}`
              }
            />
            {isEngineering && (
              <OutcomeMetricCard
                label="10 日後達標率"
                value={formatRate(outcomes?.recommendation.acceptable_rate)}
                detail={`成熟樣本 ${outcomes?.sample.matured ?? 0}・目標 80%`}
                status={
                  outcomes?.sample.matured
                    ? outcomes.recommendation.acceptable_target_met
                      ? "met"
                      : "not-met"
                    : "neutral"
                }
              />
            )}
          </section>

          <section className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {visibleNavCards.map(({ href, title, description }) => (
              <Link
                key={href}
                href={href}
                className="group flex items-center justify-between gap-3 rounded-xl border border-sky-800/40 bg-sky-950/20 p-4 transition-colors hover:border-sky-400/60 hover:bg-sky-950/30"
              >
                <div>
                  <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
                  <p className="mt-2 text-xs leading-5 text-slate-500">{description}</p>
                </div>
                <span className="shrink-0 text-sky-400 transition-transform group-hover:translate-x-0.5">
                  →
                </span>
              </Link>
            ))}
          </section>
        </>
      )}
    </main>
  )
}
