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
        Decision {summary.decision_completed_count ?? "—"}
      </span>
      <span className={incomplete ? "text-amber-300" : "text-slate-300"}>
        Unprocessed {summary.unprocessed_count ?? 0}
      </span>
    </>
  )
}
