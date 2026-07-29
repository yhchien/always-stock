import type {
  SignalJobStatus,
  SignalProcessingSummary,
} from "@/lib/api"

export function isSignalProcessingIncomplete(
  summary: SignalProcessingSummary | null | undefined,
  jobStatus: SignalJobStatus | undefined,
): boolean {
  return (
    jobStatus === "partial_failure" ||
    (summary?.unprocessed_count ?? 0) > 0 ||
    summary?.is_complete === false
  )
}

export function SignalIncompleteWarning({
  summary,
}: {
  summary: SignalProcessingSummary | null | undefined
}) {
  if (summary?.global_selection_status === "FAILED") {
    return (
      <p className="text-xs font-medium text-amber-300">
        本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單。
      </p>
    )
  }
  return (
    <p className="text-xs font-medium text-amber-300">
      本次分析未完整完成
      {(summary?.unprocessed_count ?? 0) > 0
        ? `：${summary?.unprocessed_count} 檔未完成`
        : ""}
    </p>
  )
}

export function SignalProcessingCounts({
  summary,
  incomplete,
}: {
  summary: SignalProcessingSummary
  incomplete: boolean
}) {
  return (
    <>
      <span className="text-slate-500">Pipeline</span>
      <span className="text-slate-300">Raw {summary.raw_union_count ?? "—"}</span>
      <span className="text-slate-300">
        Phase 2 {summary.regime_survivor_count ?? "—"}
      </span>
      <span className="text-slate-300">
        Research {summary.research_completed_count ?? "—"}
      </span>
      <span className="text-slate-300">
        Assessment {summary.decision_completed_count ?? "—"}
      </span>
      <span className="text-slate-300">
        Global {summary.global_selection_eligible_count ?? "—"}
      </span>
      <span className="text-emerald-300">
        Recommend {summary.global_selection_recommended_count ?? summary.final_watch_count ?? "—"}
      </span>
      <span className="text-slate-300">
        Not selected {summary.global_selection_not_selected_count ?? "—"}
      </span>
      <span className="text-slate-300">
        Removed {summary.final_remove_count ?? "—"}
      </span>
      <span className={incomplete ? "text-amber-300" : "text-slate-300"}>
        Technical {summary.technical_failure_count ?? summary.unprocessed_count ?? 0}
      </span>
      <span className={incomplete ? "text-amber-300" : "text-slate-300"}>
        Unprocessed {summary.unprocessed_count ?? 0}
      </span>
    </>
  )
}
