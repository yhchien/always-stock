"use client"

import { useEffect, useState } from "react"

import SelectionReasonBadge from "@/components/SelectionReasonBadge"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import SignalFunnel from "@/components/SignalFunnel"
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
  const completeness = selectionCompleteness(processing?.global_selection_status, selection?.selection_complete)
  // 2026-08-11：正式推薦頁併入魚尾單一入口後，工程稽核用的 Funnel／未列入今日推薦／
  // 明確移除清單搬到這裡（純搬移，不改資料流）；P4 觀察狀態附註（原頁面對這兩份清單額外
  // 標註「既有觀察是否繼續」）不帶過來，避免這頁多拉一個 fetchSignalObservations——
  // 要看完整 P4 狀態請到 /signals/observations
  const recommendations = (snapshot?.data.watchlist ?? []).filter(
    (item) => item.selection_status === "RECOMMEND" || item.decision === "RECOMMEND",
  )
  const notSelected = snapshot?.data.not_selected ?? []
  const removed = snapshot?.data.removed ?? []
  const funnel = [
    ["raw", "Raw Union", processing?.raw_union_count ?? snapshot?.data.candidate_pool_size ?? 0, "A/B/C/D 原始聯集"],
    ["p2", "Phase 2 Eligible", processing?.llm_eligible_count ?? selection?.phase2_eligible_count ?? 0, "通過 P2 eligibility"],
    ["research", "Research", processing?.research_completed_count ?? selection?.research_completed_count ?? 0, "研究成功"],
    ["assessment", "Assessment Eligible", processing?.global_selection_eligible_count ?? selection?.global_eligible_count ?? 0, "assessment 後可比較"],
    ["removed", "True Removed", selection?.veto_removed_count ?? removed.length, "具真實 veto 的明確移除"],
    ["global", "Global Eligible", selection?.global_eligible_count ?? notSelected.length + recommendations.length, "進入一次完整全體比較"],
    ["recommended", "Recommended", recommendations.length, "今日正式推薦"],
    ["not-selected", "Not Selected", notSelected.length, "候選有效但未列入今日推薦"],
    ["technical", "Technical", snapshot?.data.technical_failures?.length ?? 0, "技術處理失敗"],
    ["unprocessed", "Unprocessed", processing?.unprocessed_count ?? 0, "尚未完成處理"],
  ].map(([key, label, value, help]) => ({ key: String(key), label: String(label), value: Number(value), help: String(help) }))
  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-6 text-slate-100">
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
              ["Completeness", completeness],
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

          <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">處理 Funnel</h2>
              <span className="rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400">{completeness}</span>
              <span className="text-[11px] text-slate-600">快照 {snapshot.snapshot_date}</span>
            </div>
            <SignalFunnel steps={funnel} />
          </section>

          <section className="rounded-xl border border-slate-700/60 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold text-slate-200">未列入今日推薦（{notSelected.length}）</h2>
            <p className="mt-1 text-xs text-slate-500">候選仍有效；這是中性的同日相對選擇，不代表永久負面或停止追蹤。</p>
            <div className="mt-3 space-y-2">
              {notSelected.map((item) => (
                <article key={item.stock} className="rounded-lg border border-slate-800 bg-slate-950/35 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-mono text-slate-300">{item.stock}</span>
                    <span>{item.name}</span>
                    <SignalAssetBadge assetType={item.asset_type} />
                    <SelectionReasonBadge code={item.selection_reason_code} />
                    <span className="text-xs text-slate-600">Backend Rank {item.backend_priority_rank ?? "—"}</span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-slate-400">{item.selection_reason ?? "歷史快照未保存未入選原因"}</p>
                  {item.overlap_with?.length ? <p className="mt-1 text-[11px] text-slate-600">論點重疊：{item.overlap_with.join("、")}・{item.overlap_reason}</p> : null}
                </article>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-slate-700/60 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold text-slate-200">明確移除（{removed.length}）</h2>
            <p className="mt-1 text-xs text-slate-500">此區只顯示 backend 驗證成立的 true veto。</p>
            <div className="mt-3 space-y-2">
              {removed.map((item) => (
                <article key={item.stock} className="rounded-lg border border-slate-800 p-3 text-xs text-slate-400">
                  <span className="font-mono text-slate-300">{item.stock}</span> {item.name}・{item.veto_reason ?? "VALIDATED_VETO"}・{item.short_reason ?? item.reason}
                </article>
              ))}
            </div>
          </section>

          <p className="text-xs leading-5 text-slate-600">
            Leakage guard：本頁與 Outcome APIs 都是 production decision 完成後的只讀 consumer；Global Selector payload、Tracking Prompt 與 replay decision stage 不讀 Outcome cache。
          </p>
        </div>
      )}
    </main>
  )
}
