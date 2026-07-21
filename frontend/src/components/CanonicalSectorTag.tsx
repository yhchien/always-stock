"use client"

import type { CanonicalClassification } from "@/lib/api"
import {
  etfAssetClassLabel,
  etfRegionLabel,
  etfStrategyLabel,
} from "@/lib/classificationLabels"

/**
 * Phase 1 Canonical Market Classification 顯示元件（2026-07-21）。
 *
 * 純顯示層：這裡看到的 primary_sector / sub_sector 是 canonical 分類，
 * **不代表**魚尾選股 pipeline 已改用這套分類——選股仍依原始 industry_name 運作
 * （Phase 2 才會切換）。`canonical` 為 null/undefined 時（舊資料或查無分類）不 render，
 * 呼叫端維持原本 industry_name/sub_industry 文字顯示即可。
 */
export function CanonicalSectorTag({
  canonical,
  compact = false,
}: {
  canonical: CanonicalClassification | null | undefined
  compact?: boolean
}) {
  if (!canonical) return null

  // ETF / ETN：顯示投資區域/策略/主題，不顯示「公司主要產業」（語意上不適用）
  if (canonical.asset_type === "ETF" || canonical.asset_type === "ETN") {
    const etf = canonical.etf
    if (!etf) return null
    return (
      <span
        className="inline-flex flex-wrap items-center gap-1"
        title={etf.tracking_index ? `追蹤指數：${etf.tracking_index}` : undefined}
      >
        <Tag tone="sky">{etfRegionLabel(etf.region)}</Tag>
        <Tag tone="violet">{etfStrategyLabel(etf.strategy)}</Tag>
        {!compact && etf.asset_class !== "EQUITY" && (
          <Tag tone="zinc">{etfAssetClassLabel(etf.asset_class)}</Tag>
        )}
        {!compact &&
          etf.themes.slice(0, 2).map((t) => (
            <Tag key={t} tone="amber">
              {t}
            </Tag>
          ))}
      </span>
    )
  }

  // 非真實證券（指數佔位列）不顯示
  if (canonical.asset_type === "INDEX_BENCHMARK" || !canonical.primary_sector_label) {
    return null
  }

  const title = [
    canonical.source_industry ? `原始分類：${canonical.source_industry}` : null,
    canonical.review_required ? "分類信心度較低，待人工複核" : null,
  ]
    .filter(Boolean)
    .join("　")

  return (
    <span className="inline-flex flex-wrap items-center gap-1" title={title || undefined}>
      <Tag tone="emerald">{canonical.primary_sector_label}</Tag>
      {!compact && canonical.sub_sector && <Tag tone="zinc">{canonical.sub_sector}</Tag>}
      {canonical.review_required && (
        <span className="text-[10px] text-amber-500" aria-label="待人工複核">
          ⚠
        </span>
      )}
    </span>
  )
}

function Tag({
  children,
  tone,
}: {
  children: React.ReactNode
  tone: "emerald" | "zinc" | "sky" | "violet" | "amber"
}) {
  const toneClass = {
    emerald: "border-emerald-700/60 bg-emerald-900/30 text-emerald-300",
    zinc: "border-zinc-600 bg-zinc-700/40 text-zinc-300",
    sky: "border-sky-700/60 bg-sky-900/30 text-sky-300",
    violet: "border-violet-700/60 bg-violet-900/30 text-violet-300",
    amber: "border-amber-700/60 bg-amber-900/30 text-amber-300",
  }[tone]
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneClass}`}
    >
      {children}
    </span>
  )
}
