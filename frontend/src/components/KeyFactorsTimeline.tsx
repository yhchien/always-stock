"use client"

import type { KeyFactor, WatchlistFactorSnapshot } from "@/lib/api"

const CATEGORY_LABELS: Record<string, string> = {
  industry: "產業",
  industry_heat: "產業熱度",
  return: "報酬",
  chip: "籌碼",
  technical: "技術面",
  fundamental: "基本面",
}

const CATEGORY_ORDER = ["industry", "industry_heat", "return", "chip", "technical", "fundamental"] as const

const LEVEL_DOT: Record<string, string> = {
  A: "bg-emerald-400",
  B: "bg-amber-400",
  C: "bg-rose-400",
}

const FALLBACK_DOT = "bg-slate-700"

function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return iso
  return `${m[2]}/${m[3]}`
}

function findFactor(snapshot: WatchlistFactorSnapshot | undefined, category: string): KeyFactor | null {
  if (!snapshot?.key_factors) return null
  return snapshot.key_factors.find((f) => f.category === category) ?? null
}

/**
 * 自選清單 trade quality 的「最近 3 個交易日因素趨勢」mini grid。
 *
 * 後端傳的 recent_factors 已是 snapshot_trade_date 倒序（最新在前）。
 * 視覺上把它反轉為「左舊 → 右新」更符合時間軸閱讀習慣。
 *
 * 不足 3 筆時剩餘格子顯示為灰點（無資料）。
 */
export default function KeyFactorsTimeline({
  recent,
}: {
  recent: WatchlistFactorSnapshot[] | null | undefined
}) {
  if (!recent || recent.length === 0) return null

  // 反轉成左舊→右新；若不足 3 筆，前面 padding null（佔灰點位置）
  const ordered = [...recent].slice(0, 3).reverse()
  const padded: (WatchlistFactorSnapshot | null)[] = [
    ...Array(Math.max(0, 3 - ordered.length)).fill(null),
    ...ordered,
  ]

  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3">
      <div className="flex items-baseline justify-between">
        <p className="text-xs text-slate-500">近 3 日因素趨勢</p>
        <p className="text-[10px] text-slate-600">A 強 / B 中 / C 弱</p>
      </div>

      <div className="mt-2 overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="text-slate-500">
              <th className="py-1 pr-2 text-left font-normal">因素</th>
              {padded.map((snapshot, idx) => (
                <th
                  key={idx}
                  className="px-2 py-1 text-center font-normal"
                  title={snapshot?.snapshot_trade_date ?? "無資料"}
                >
                  {snapshot ? shortDate(snapshot.snapshot_trade_date) : "—"}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CATEGORY_ORDER.map((category) => (
              <tr key={category} className="border-t border-slate-800/70">
                <td className="py-1.5 pr-2 text-slate-300">{CATEGORY_LABELS[category]}</td>
                {padded.map((snapshot, idx) => {
                  const factor = findFactor(snapshot ?? undefined, category)
                  const dot = factor ? LEVEL_DOT[factor.level] ?? FALLBACK_DOT : FALLBACK_DOT
                  const tooltip = factor
                    ? `${snapshot?.snapshot_trade_date ?? ""} · ${factor.level}${factor.note ? ` · ${factor.note}` : ""}`
                    : "無資料"
                  return (
                    <td key={idx} className="px-2 py-1.5 text-center">
                      <span
                        className={`inline-flex h-3 w-3 items-center justify-center rounded-full ${dot}`}
                        title={tooltip}
                      >
                        <span className="sr-only">
                          {factor?.level ?? "無資料"}
                        </span>
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
