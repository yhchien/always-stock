"use client"

import { type ReactNode } from "react"

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

export interface MarketContextStripProps {
  market: SignalMarketContext
  riskNote?: string | null
  snapshotDate?: string | null
  generatedAt?: string | null
  mainHotIndustries?: string[] | null
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
}: MarketContextStripProps) {
  const marketStateLabel = signalValueLabel(market.market_state)
  const marketStateTone = signalValueTone("market_state", market.market_state)

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-700 bg-gradient-to-r from-zinc-900 via-zinc-800 to-zinc-900 shadow-md">
      <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-start lg:justify-between">
        {/* 左半：市場狀態描述 */}
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.28em] text-cyan-300/90">
              Market Today
            </span>
            <h2 className="text-lg font-black text-slate-100">今日市場狀態</h2>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-black ${toneChipClass(marketStateTone)}`}
            >
              {marketStateLabel}
            </span>
            {market.vix_status ? (
              <span className="text-xs text-slate-400">
                VIX <span className="font-bold text-slate-200">{signalValueLabel(market.vix_status)}</span>
              </span>
            ) : null}
            {market.futures_bias ? (
              <span className="text-xs text-slate-400">
                期貨 <span className="font-bold text-slate-200">{signalValueLabel(market.futures_bias)}</span>
              </span>
            ) : null}
          </div>

          {market.market_state_reason ? (
            <p className="text-sm leading-relaxed text-slate-300">
              {market.market_state_reason}
            </p>
          ) : null}

          {riskNote ? (
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
                <span
                  key={name}
                  className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-xs font-medium text-cyan-200"
                >
                  {name}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        {/* 右半：5 metric grid */}
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
    </section>
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
