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

// 2026-08-24：STOPPED 狀態的時機說明從只寫在 archive 頁頂部的說明卡挪一份到
// badge 本身的 title（hover tooltip）——使用者實際看到「已停止觀察」徽章時最需要
// 這個答案，不該只靠頁面上方的一段長文字說明碰運氣被看到。
const DEFAULT_STATUS_TITLES: Partial<Record<SignalObservationStatus, string>> = {
  STOPPED:
    "系統已判定推薦論點失效，卡片會先在「追蹤中」保留一個複核日讓您看到這個狀態；" +
    "下一次每日複核（通常是下一個交易日）才會正式結算、移到「停止觀察的股票」。",
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
  title,
}: {
  status: SignalObservationStatus
  title?: string
}) {
  return (
    <span
      title={title ?? DEFAULT_STATUS_TITLES[status]}
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
