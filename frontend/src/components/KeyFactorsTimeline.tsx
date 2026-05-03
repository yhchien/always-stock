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

const CATEGORY_LABELS_SHORT: Record<string, string> = {
  industry: "產",
  industry_heat: "熱",
  return: "報",
  chip: "籌",
  technical: "技",
  fundamental: "基",
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

function findFactor(snapshot: WatchlistFactorSnapshot, category: string): KeyFactor | null {
  if (!snapshot.key_factors) return null
  return snapshot.key_factors.find((f) => f.category === category) ?? null
}

/**
 * 自選清單 trade quality 的「最近 N 個交易日因素趨勢」mini grid。
 *
 * 後端傳的 recent_factors 已是 snapshot_trade_date 倒序（最新在前）。
 * 視覺上把它反轉為「左舊 → 右新」更符合時間軸閱讀習慣。
 *
 * 顯示筆數由 `recent.length` 決定（即後端回傳幾筆就顯示幾筆），
 * 不在前端再 padding 灰點佔位。
 *
 * @param compact 緊湊版（給首頁 table cell 用）：N 列 × 6 因素轉置；
 *   日期在左、因素縮寫當欄頭、無外框。
 */
export default function KeyFactorsTimeline({
  recent,
  compact = false,
}: {
  recent: WatchlistFactorSnapshot[] | null | undefined
  compact?: boolean
}) {
  if (!recent || recent.length === 0) return null

  // 反轉成左舊→右新；不足時不再 padding（後端 limit 決定筆數）
  const padded: WatchlistFactorSnapshot[] = [...recent].reverse()

  if (compact) {
    return (
      <table className="text-[11px]">
        <thead>
          <tr className="text-slate-500">
            <th className="pr-2 text-left font-normal"></th>
            {CATEGORY_ORDER.map((category) => (
              <th key={category} className="px-1 text-center font-normal" title={CATEGORY_LABELS[category]}>
                {CATEGORY_LABELS_SHORT[category]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {padded.map((snapshot, idx) => (
            <tr key={idx}>
              <td className="pr-2 text-slate-500" title={snapshot.snapshot_trade_date}>
                {shortDate(snapshot.snapshot_trade_date)}
              </td>
              {CATEGORY_ORDER.map((category) => {
                const factor = findFactor(snapshot, category)
                const dot = factor ? LEVEL_DOT[factor.level] ?? FALLBACK_DOT : FALLBACK_DOT
                const tooltip = factor
                  ? `${snapshot.snapshot_trade_date} · ${CATEGORY_LABELS[category]} · ${factor.level}${factor.note ? ` · ${factor.note}` : ""}`
                  : `${snapshot.snapshot_trade_date} · ${CATEGORY_LABELS[category]} · 無資料`
                return (
                  <td key={category} className="px-1 py-0.5 text-center">
                    <span className={`inline-block h-2 w-2 rounded-full ${dot}`} title={tooltip} />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3">
      <div className="flex items-baseline justify-between">
        <p className="text-xs text-slate-500">近 {padded.length} 日因素趨勢</p>
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
                  title={snapshot.snapshot_trade_date}
                >
                  {shortDate(snapshot.snapshot_trade_date)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CATEGORY_ORDER.map((category) => (
              <tr key={category} className="border-t border-slate-800/70">
                <td className="py-1.5 pr-2 text-slate-300">{CATEGORY_LABELS[category]}</td>
                {padded.map((snapshot, idx) => {
                  const factor = findFactor(snapshot, category)
                  const dot = factor ? LEVEL_DOT[factor.level] ?? FALLBACK_DOT : FALLBACK_DOT
                  const tooltip = factor
                    ? `${snapshot.snapshot_trade_date} · ${factor.level}${factor.note ? ` · ${factor.note}` : ""}`
                    : `${snapshot.snapshot_trade_date} · 無資料`
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
