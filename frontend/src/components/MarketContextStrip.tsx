"use client"

import Link from "next/link"
import { type ReactNode } from "react"

import CollapsibleSection from "@/components/CollapsibleSection"
import type { SignalMarketContext } from "@/lib/api"
import {
  signalValueLabel,
  signalValueTone,
  toneChipClass,
} from "@/lib/signalPresentation"

interface ChangePctProps {
  value: number | null | undefined
}

function ChangePct({ value }: ChangePctProps) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-slate-500">—</span>
  }
  // 台股慣例：紅漲綠跌
  const cls =
    value > 0 ? "text-rose-300" : value < 0 ? "text-emerald-300" : "text-slate-300"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : "·"
  return (
    <span className={`font-mono font-bold ${cls}`}>
      {arrow} {value >= 0 ? "+" : ""}
      {value.toFixed(2)}%
    </span>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="mt-0.5 truncate text-sm font-black text-slate-100">
        {value}
      </div>
    </div>
  )
}

function ToneDot({ tone }: { tone: "green" | "amber" | "red" | "slate" }) {
  const cls =
    tone === "green"
      ? "bg-emerald-400"
      : tone === "amber"
        ? "bg-amber-400"
        : tone === "red"
          ? "bg-rose-500"
          : "bg-slate-500"
  return <span className={`h-2.5 w-2.5 rounded-full ${cls}`} aria-hidden="true" />
}

export interface MarketContextStripProps {
  market: SignalMarketContext
  riskNote?: string | null
  snapshotDate?: string | null
  generatedAt?: string | null
  mainHotIndustries?: string[] | null
  collapsible?: boolean
  defaultCollapsed?: boolean
  storageKey?: string
}

/**
 * 仿 jianxuanchiustock 「交易計畫」頂部 header strip：
 *   左半：標題 + market_state label chip + reason 一句話 + risk_note 提示
 *   右半：5 metric 水平排列（更新日期 / TAIEX / OTC / VIX / 期貨）
 *
 * 「字詞 label 化」：market_state 透過 signalValueLabel 已是中文短語（盤整 / 強多 …）。
 * risk_note 第一版維持全文（amber chip）；若 M2 prompt 改完加 risk_label 短欄位再升級。
 */
export default function MarketContextStrip({
  market,
  riskNote,
  snapshotDate,
  generatedAt,
  mainHotIndustries,
  collapsible = false,
  defaultCollapsed = false,
  storageKey,
}: MarketContextStripProps) {
  const marketStateLabel = signalValueLabel(market.market_state)
  const marketStateTone = signalValueTone("market_state", market.market_state)
  const content = (
    <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-start lg:justify-between">
      <div className="min-w-0 flex-1 space-y-2">
        {!collapsible ? (
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-black text-slate-100">今日市場狀態</h2>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-black ${toneChipClass(marketStateTone)}`}
          >
            <ToneDot tone={marketStateTone} />
            {marketStateLabel}
          </span>
          {market.vix_status ? (
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${toneChipClass(signalValueTone("vix_status", market.vix_status))}`}>
              <ToneDot tone={signalValueTone("vix_status", market.vix_status)} />
              VIX <span className="font-bold">{signalValueLabel(market.vix_status)}</span>
            </span>
          ) : null}
          {market.futures_bias ? (
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${toneChipClass(signalValueTone("futures_bias", market.futures_bias))}`}>
              <ToneDot tone={signalValueTone("futures_bias", market.futures_bias)} />
              期貨 <span className="font-bold">{signalValueLabel(market.futures_bias)}</span>
            </span>
          ) : null}
        </div>

        {market.market_state_reason ? (
          <p className="text-sm leading-relaxed text-slate-300">
            {market.market_state_reason}
          </p>
        ) : null}

        {/* 2026-05-27：暫時隱藏市場風險提示黃色框框（保留邏輯便於日後開回） */}
        {false && riskNote ? (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
            <span className="mt-0.5 shrink-0 rounded bg-amber-500/30 px-1.5 py-0.5 text-[10px] font-black text-amber-100">
              ⚠ 風險提示
            </span>
            <p className="text-sm leading-relaxed text-amber-100/95">
              {riskNote}
            </p>
          </div>
        ) : null}

        {mainHotIndustries && mainHotIndustries.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            <span className="text-xs text-slate-500">主要熱門產業：</span>
            {mainHotIndustries.map((name) => (
              <Link
                key={name}
                href={`/industries/${encodeURIComponent(name)}${snapshotDate ? `?date=${snapshotDate}` : ""}`}
                className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-xs font-medium text-cyan-200 transition hover:border-cyan-400/60 hover:bg-cyan-500/20"
              >
                {name}
              </Link>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid w-full grid-cols-2 gap-x-4 gap-y-3 border-t border-zinc-700/60 pt-4 sm:grid-cols-3 lg:w-auto lg:max-w-md lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
        <Metric label="更新日期" value={snapshotDate ?? "—"} />
        <Metric
          label="TAIEX 加權"
          value={<ChangePct value={market.taiex_change_pct} />}
        />
        <Metric
          label="OTC 櫃買"
          value={<ChangePct value={market.otc_change_pct} />}
        />
        <Metric
          label="VIX 恐慌"
          value={signalValueLabel(market.vix_status)}
        />
        <Metric
          label="台指期"
          value={signalValueLabel(market.futures_bias)}
        />
        {generatedAt ? (
          <Metric label="產生時間" value={formatGeneratedAt(generatedAt)} />
        ) : null}
      </div>
    </div>
  )

  if (!collapsible) {
    return (
      <section className="overflow-hidden rounded-2xl border border-zinc-700 bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-900 shadow-md">
        {content}
      </section>
    )
  }

  return (
    <CollapsibleSection
      title="今日市場狀態"
      subtitle={
        <>
          {snapshotDate ?? "—"} · {marketStateLabel}
        </>
      }
      defaultCollapsed={defaultCollapsed}
      storageKey={storageKey}
      className="overflow-hidden rounded-2xl border border-zinc-700 bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-900 shadow-md"
      headerClassName="flex flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-5"
      contentClassName="border-t border-zinc-700/60"
    >
      {content}
    </CollapsibleSection>
  )
}

function formatGeneratedAt(iso: string): string {
  try {
    const dt = new Date(iso)
    if (Number.isNaN(dt.getTime())) return iso
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(dt)
  } catch {
    return iso
  }
}
