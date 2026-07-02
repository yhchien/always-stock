"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import {
  fetchLatestSignalSnapshot,
  toDisplayError,
  type SignalSnapshotResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import {
  decisionBadgeClass,
  signalDecisionLabel,
  signalValueLabel,
  signalValueTone,
  toneChipClass,
} from "@/lib/signalPresentation"

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

function SignalMetric({
  label,
  value,
}: {
  label: string
  value: string | null | undefined
}) {
  const tone = signalValueTone(label, value)
  return (
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 px-3 py-2">
      <p className="text-[11px] text-slate-500">{label}</p>
      <span className={`mt-1 inline-flex rounded border px-2 py-0.5 text-xs font-medium ${toneChipClass(tone)}`}>
        {signalValueLabel(value)}
      </span>
    </div>
  )
}

export default function StockSignalSummaryPanel({ stockId }: { stockId: string }) {
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchLatestSignalSnapshot({ bypassCache: true })
      .then((value) => {
        if (!cancelled) setSnapshot(value)
      })
      .catch((err) => {
        if (!cancelled) setError(toDisplayError(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const match = useMemo(() => {
    const watchItem = snapshot?.data.watchlist.find((item) => item.stock === stockId) ?? null
    return { watchItem }
  }, [snapshot, stockId])

  if (error || !snapshot) return null

  const market = snapshot.data.market_context
  const item: SignalWatchlistItem | null = match.watchItem
  const marketTone = signalValueTone("market_state", market.market_state)

  if (!item) return null

  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-slate-500">Signal Detail</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">今日捕獲的大魚尾摘要</h2>
          <p className="mt-1 text-xs text-slate-500">快照日期 {snapshot.snapshot_date}</p>
        </div>
        <Link
          href="#stock-chart"
          className="inline-flex items-center rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20"
        >
          看 K 線圖
        </Link>
      </div>

      <div className="mt-4 rounded-xl border border-slate-700/70 bg-slate-900/40 p-4">
        <p className="text-xs text-slate-500">市場狀態</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${toneChipClass(marketTone)}`}>
            <ToneDot tone={marketTone} />
            {signalValueLabel(market.market_state)}
          </span>
          {market.vix_status ? (
            <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${toneChipClass(signalValueTone("vix_status", market.vix_status))}`}>
              <ToneDot tone={signalValueTone("vix_status", market.vix_status)} />
              VIX {signalValueLabel(market.vix_status)}
            </span>
          ) : null}
          {market.futures_bias ? (
            <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${toneChipClass(signalValueTone("futures_bias", market.futures_bias))}`}>
              <ToneDot tone={signalValueTone("futures_bias", market.futures_bias)} />
              期貨 {signalValueLabel(market.futures_bias)}
            </span>
          ) : null}
        </div>
        {market.market_state_reason ? (
          <p className="mt-2 text-sm leading-relaxed text-slate-300">{market.market_state_reason}</p>
        ) : null}
      </div>

      {snapshot.data.summary?.risk_note ? (
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
          <p className="text-xs text-amber-300">風險提示</p>
          <p className="mt-2 text-sm leading-relaxed text-amber-100">{snapshot.data.summary.risk_note}</p>
        </div>
      ) : null}

      {item ? (
        <div className="mt-4 rounded-xl border border-slate-700/70 bg-slate-900/40 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold text-slate-100">
              {item.stock} {item.name ?? ""}
            </span>
            {item.type ? (
              <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${decisionBadgeClass(item.type)}`}>
                {signalDecisionLabel(item.type)}
              </span>
            ) : null}
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-300">
            <span>{item.industry ?? "未分類"}</span>
            {item.sub_industry ? <span className="text-slate-500">·</span> : null}
            {item.sub_industry ? <span>{item.sub_industry}</span> : null}
          </div>

          {item.theme_fit ? (
            <div className="mt-3">
              <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${toneChipClass(signalValueTone("theme_fit", item.theme_fit))}`}>
                題材契合 {signalValueLabel(item.theme_fit)}
              </span>
            </div>
          ) : null}

          {item.business_summary ? (
            <div className="mt-4 rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
              <p className="text-xs text-slate-500">保留摘要</p>
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-200">
                {item.business_summary}
              </p>
            </div>
          ) : null}

          <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
            <SignalMetric label="資金" value={item.signals?.capital_flow} />
            <SignalMetric label="籌碼" value={item.signals?.chip_trend} />
            <SignalMetric label="融資券" value={item.signals?.margin_short_signal} />
            <SignalMetric label="技術" value={item.signals?.technical_status} />
          </div>

          {item.reason ? (
            <div className="mt-4 rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
              <p className="text-xs text-slate-500">入選理由</p>
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-slate-200">
                {item.reason}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

    </section>
  )
}
