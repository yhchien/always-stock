export interface SignalFunnelStep {
  key: string
  label: string
  value: number
  help: string
}

export default function SignalFunnel({ steps }: { steps: SignalFunnelStep[] }) {
  return (
    <section aria-label="今日推薦處理 Funnel" className="overflow-x-auto">
      <div className="flex min-w-max items-stretch gap-2">
        {steps.map((step, index) => {
          const previous = index > 0 ? steps[index - 1].value : null
          const difference = previous == null ? null : step.value - previous
          return (
            <div key={step.key} className="flex items-center gap-2">
              {index > 0 && <span className="text-slate-700">→</span>}
              <article
                className="w-36 rounded-lg border border-slate-700/60 bg-slate-950/60 p-3"
                title={step.help}
              >
                <p className="text-[10px] text-slate-500">{step.label}</p>
                <p className="mt-1 font-mono text-lg text-slate-100">{step.value}</p>
                {difference != null && difference !== 0 && (
                  <p className="text-[10px] text-slate-600">
                    前一步 {difference > 0 ? "+" : ""}
                    {difference}
                  </p>
                )}
              </article>
            </div>
          )
        })}
      </div>
    </section>
  )
}
