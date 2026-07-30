import { SELECTION_REASON_LABELS } from "@/lib/signalP6Presentation"

export default function SelectionReasonBadge({
  code,
}: {
  code: string | null | undefined
}) {
  if (!code) return null
  return (
    <span
      title={`${code}：${SELECTION_REASON_LABELS[code] ?? "未列入今日推薦原因"}`}
      className="inline-flex rounded-full border border-slate-600/70 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-300"
    >
      {SELECTION_REASON_LABELS[code] ?? code}
    </span>
  )
}
