import type {
  SignalObservationDecision,
  SignalObservationStatus,
} from "@/lib/api"

const STATUS_LABELS: Record<SignalObservationStatus, string> = {
  OBSERVING: "觀察中",
  CAUTION: "警戒",
  STOPPED: "已停止觀察",
}

const STATUS_STYLES: Record<SignalObservationStatus, string> = {
  OBSERVING: "border-sky-500/30 bg-sky-500/10 text-sky-200",
  CAUTION: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  STOPPED: "border-slate-600 bg-slate-800/80 text-slate-300",
}

const DECISION_LABELS: Record<SignalObservationDecision, string> = {
  CONTINUE: "繼續觀察",
  CAUTION: "警戒",
  STOP_OBSERVING: "停止觀察",
  REVIEW_FAILED: "檢查未完成",
}

export function observationStatusLabel(status: SignalObservationStatus): string {
  return STATUS_LABELS[status]
}

export function observationDecisionLabel(
  decision: SignalObservationDecision,
): string {
  return DECISION_LABELS[decision]
}

export default function ObservationStatusBadge({
  status,
}: {
  status: SignalObservationStatus
}) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  )
}

export function ObservationLifecycleNotice({
  status,
  technicalStatus,
}: {
  status: SignalObservationStatus
  technicalStatus?: string | null
}) {
  return (
    <>
      {status === "STOPPED" && (
        <p className="rounded border border-slate-600 bg-slate-800/70 px-3 py-2 text-sm text-slate-200">
          停止觀察僅代表不再列入魚尾追蹤名單，不構成賣出建議。
        </p>
      )}
      {technicalStatus && (
        <p className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          本次追蹤檢查未完成，維持上一個有效狀態。
        </p>
      )}
    </>
  )
}
