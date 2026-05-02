"use client"

import type { KeyFactor } from "@/lib/api"

const CATEGORY_LABELS: Record<string, string> = {
  industry: "產業",
  industry_heat: "產業熱度",
  return: "報酬",
  chip: "籌碼",
  technical: "技術面",
  fundamental: "基本面",
}

const LEVEL_STYLES: Record<string, { dot: string; chip: string; text: string }> = {
  A: {
    dot: "bg-emerald-400",
    chip: "bg-emerald-500/15 border-emerald-500/40 text-emerald-200",
    text: "text-emerald-200",
  },
  B: {
    dot: "bg-amber-400",
    chip: "bg-amber-500/15 border-amber-500/40 text-amber-200",
    text: "text-amber-200",
  },
  C: {
    dot: "bg-rose-400",
    chip: "bg-rose-500/15 border-rose-500/40 text-rose-200",
    text: "text-rose-200",
  },
}

const TREND_GLYPHS: Record<string, { glyph: string; cls: string; label: string }> = {
  improving: { glyph: "↑", cls: "text-emerald-300", label: "轉強" },
  stable: { glyph: "→", cls: "text-slate-400", label: "持平" },
  weakening: { glyph: "↘", cls: "text-amber-300", label: "轉弱" },
  deteriorating: { glyph: "↓", cls: "text-rose-300", label: "惡化" },
}

const FALLBACK_STYLE = {
  dot: "bg-slate-500",
  chip: "bg-slate-600/30 border-slate-500/40 text-slate-200",
  text: "text-slate-300",
}

const FALLBACK_TREND = { glyph: "·", cls: "text-slate-500", label: "" }

const ORDER: Record<string, number> = {
  industry: 0,
  industry_heat: 1,
  return: 2,
  chip: 3,
  technical: 4,
  fundamental: 5,
}

function sortFactors(factors: KeyFactor[]): KeyFactor[] {
  return [...factors].sort(
    (a, b) => (ORDER[a.category] ?? 99) - (ORDER[b.category] ?? 99),
  )
}

export interface KeyFactorsListProps {
  factors: KeyFactor[] | null | undefined
  /** 上一次成功的 key_factors，存在時會在每項顯示 level / trend 變化箭頭 */
  previousFactors?: KeyFactor[] | null
  /** 緊湊版（給表格內 expand 區塊用） */
  compact?: boolean
}

/**
 * M25：把 trade quality 的 key_factors 渲染成 6 條燈號（A=綠/B=黃/C=紅）+ 趨勢箭頭。
 * 若 previousFactors 存在，且某 category 的 level 與本次不同 → 顯示「B → C」。
 */
export default function KeyFactorsList({
  factors,
  previousFactors,
  compact = false,
}: KeyFactorsListProps) {
  if (!factors || factors.length === 0) return null

  const prevByCategory = new Map<string, KeyFactor>()
  if (previousFactors) {
    for (const p of previousFactors) prevByCategory.set(p.category, p)
  }

  const sorted = sortFactors(factors)

  return (
    <ul
      className={`grid gap-2 ${
        compact ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
      }`}
    >
      {sorted.map((f) => {
        const style = LEVEL_STYLES[f.level] ?? FALLBACK_STYLE
        const trend = TREND_GLYPHS[f.trend] ?? FALLBACK_TREND
        const prev = prevByCategory.get(f.category)
        const showDelta = prev && prev.level !== f.level
        const label = CATEGORY_LABELS[f.category] ?? f.category

        return (
          <li
            key={f.category}
            className="flex items-start gap-3 rounded-lg border border-zinc-700 bg-zinc-800/60 p-3"
          >
            <span className={`mt-1 inline-block h-2.5 w-2.5 flex-none rounded-full ${style.dot}`} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-100">{label}</span>
                <span
                  className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-bold ${style.chip}`}
                >
                  {f.level}
                </span>
                <span className={`text-sm ${trend.cls}`} title={trend.label}>
                  {trend.glyph}
                </span>
                {showDelta && prev ? (
                  <span className="text-[11px] text-slate-400">
                    上次{" "}
                    <span className={LEVEL_STYLES[prev.level]?.text ?? FALLBACK_STYLE.text}>
                      {prev.level}
                    </span>{" "}
                    → 本次{" "}
                    <span className={style.text}>{f.level}</span>
                  </span>
                ) : null}
              </div>
              {f.note ? (
                <p className="mt-1 text-xs leading-relaxed text-slate-300">{f.note}</p>
              ) : null}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
