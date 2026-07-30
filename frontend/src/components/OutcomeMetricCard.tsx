import type { ReactNode } from "react"

export default function OutcomeMetricCard({
  label,
  value,
  detail,
  status,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  status?: "met" | "not-met" | "neutral"
}) {
  const statusClass =
    status === "met"
      ? "border-sky-500/30"
      : status === "not-met"
        ? "border-amber-500/30"
        : "border-slate-700/60"
  return (
    <article className={`rounded-xl border bg-slate-900/55 p-4 ${statusClass}`}>
      <p className="text-[11px] text-slate-500">{label}</p>
      <div className="mt-1 font-mono text-xl font-semibold text-slate-100">{value}</div>
      {detail && <div className="mt-2 text-xs leading-5 text-slate-400">{detail}</div>}
    </article>
  )
}
