"use client"

import { useEffect, useState } from "react"

import SignalProductNav from "@/components/SignalProductNav"
import {
  fetchLatestSignalJob,
  fetchLatestSignalSnapshot,
  type SignalJobResponse,
  type SignalSnapshotResponse,
} from "@/lib/api"
import { selectionCompleteness } from "@/lib/signalP6Presentation"

export default function SignalDebugPage() {
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(null)
  const [job, setJob] = useState<SignalJobResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchLatestSignalSnapshot({ signal: controller.signal }),
      fetchLatestSignalJob({ signal: controller.signal }),
    ])
      .then(([latest, latestJob]) => {
        setSnapshot(latest)
        setJob(latestJob)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "Debug 資料載入失敗")
      })
    return () => controller.abort()
  }, [])

  const processing = snapshot?.data.summary.processing_summary
  const selection = snapshot?.data.summary.selection_summary
  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-6 text-slate-100">
      <SignalProductNav />
      <header className="mb-5">
        <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">Operational Debug</p>
        <h1 className="mt-1 text-2xl font-semibold">訊號版本與處理診斷</h1>
        <p className="mt-2 text-sm text-slate-400">
          僅呈現執行版本、容量與失敗資訊；不提供修改 selection policy、prompt 或 P4 state machine 的控制。
        </p>
      </header>
      {!snapshot && !error && <p className="text-sm text-slate-500">正在載入最新 snapshot 與 job…</p>}
      {error && <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>}
      {snapshot && (
        <div className="space-y-4">
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Snapshot", snapshot.snapshot_date],
              ["Completeness", selectionCompleteness(processing?.global_selection_status, selection?.selection_complete)],
              ["Prompt Family", processing?.prompt_family_version ?? "歷史版本"],
              ["Selection", selection?.selection_version ?? processing?.global_selector_version ?? "—"],
              ["Momentum Score", processing?.momentum_score_version ?? "—"],
              ["Tracking Prompt", processing?.tracking_prompt_version ?? "—"],
              ["State Machine", processing?.tracking_state_machine_version ?? "—"],
              ["LLM Model", snapshot.llm_model ?? "—"],
            ].map(([label, value]) => (
              <article key={label} className="rounded-xl border border-slate-800 bg-slate-900/45 p-3">
                <p className="text-[11px] text-slate-500">{label}</p>
                <p className="mt-1 break-all font-mono text-sm text-slate-200">{value}</p>
              </article>
            ))}
          </section>
          <section className="grid gap-4 lg:grid-cols-2">
            <article className="rounded-xl border border-slate-800 p-4">
              <h2 className="text-sm font-semibold">Processing Summary</h2>
              <pre className="mt-3 max-h-[36rem] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">
                {JSON.stringify(processing ?? {}, null, 2)}
              </pre>
            </article>
            <article className="rounded-xl border border-slate-800 p-4">
              <h2 className="text-sm font-semibold">Job / Technical Failures</h2>
              <pre className="mt-3 max-h-[36rem] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">
                {JSON.stringify({ job, technical_failures: snapshot.data.technical_failures ?? [] }, null, 2)}
              </pre>
            </article>
          </section>
          <p className="text-xs leading-5 text-slate-600">
            Leakage guard：本頁與 Outcome APIs 都是 production decision 完成後的只讀 consumer；Global Selector payload、Tracking Prompt 與 replay decision stage 不讀 Outcome cache。
          </p>
        </div>
      )}
    </main>
  )
}
