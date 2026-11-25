"use client"

import { useEffect, useState } from "react"
import Link from "next/link"

import {
  fetchMarketCalendarHistory,
  type MarketCalendarHistoryResponse,
  type MarketCalendarPredictionItem,
} from "@/lib/api"

function formatPct(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function formatDate(value: string | null): string {
  if (!value) return "下一交易日"
  return value.slice(5).replace("-", "/")
}

function directionClass(direction: string | null): string {
  return direction === "偏漲" ? "text-rose-300" : "text-emerald-300"
}

function resultLabel(row: MarketCalendarPredictionItem): string {
  if (row.result_status === "pending") return "待驗證"
  if (row.result_status === "tolerated") return "盤整容許"
  if (row.result_status === "correct") return "正確"
  return "錯誤"
}

function resultClass(row: MarketCalendarPredictionItem): string {
  if (row.result_status === "pending") return "text-slate-400"
  if (row.result_status === "tolerated") return "text-amber-300"
  if (row.result_status === "correct") return "text-emerald-300"
  return "text-rose-300"
}

function LatestCard({ row }: { row: MarketCalendarPredictionItem }) {
  return (
    <section className="rounded-2xl border border-sky-800/50 bg-gradient-to-br from-slate-900 via-slate-900 to-sky-950/50 p-5 shadow-xl">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-slate-400">下一交易日預測</p>
          <p className="mt-1 text-xl font-semibold text-slate-100">
            {formatDate(row.target_date)}
          </p>
          <p className="mt-1 text-sm text-slate-500">訊號日 {row.signal_date}</p>
        </div>
        <div className="text-right">
          <p className={`text-4xl font-black ${directionClass(row.predicted_binary)}`}>
            {row.predicted_binary}
          </p>
          <p className="mt-1 text-base text-slate-300">
            預估 {formatPct(row.predicted_return)}
          </p>
        </div>
      </div>
      <div className="mt-5">
        <div className="max-w-md rounded-xl border border-slate-800 bg-slate-950/40 p-3">
          <p className="text-sm text-slate-500">預估區間</p>
          <p className="mt-1 text-base font-semibold text-slate-200">
            {row.predicted_return_low != null && row.predicted_return_high != null
              ? `${formatPct(row.predicted_return_low)} ～ ${formatPct(row.predicted_return_high)}`
              : "尚未提供"}
          </p>
        </div>
      </div>
    </section>
  )
}

function HistoryTable({ rows }: { rows: MarketCalendarPredictionItem[] }) {
  return (
    <section className="mt-5 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">最近預測紀錄</h2>
          <p className="mt-1 text-sm text-slate-500">顯示近 10 日；資料庫保留最新 50 筆。</p>
        </div>
        <span className="text-sm text-slate-500">預測日／目標日</span>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-base">
          <thead className="border-b border-slate-800 text-sm text-slate-500">
            <tr>
              <th className="px-3 py-3 font-medium">訊號日</th>
              <th className="px-3 py-3 font-medium">目標日</th>
              <th className="px-3 py-3 font-medium">預測</th>
              <th className="px-3 py-3 font-medium">預估</th>
              <th className="px-3 py-3 font-medium">實際</th>
              <th className="px-3 py-3 font-medium">結果</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.signal_date} className="border-b border-slate-800/70 last:border-0">
                <td className="px-3 py-3 text-slate-300">{row.signal_date}</td>
                <td className="px-3 py-3 text-slate-400">{formatDate(row.target_date)}</td>
                <td className={`px-3 py-3 font-semibold ${directionClass(row.predicted_binary)}`}>
                  {row.predicted_binary}
                </td>
                <td className="px-3 py-3 font-mono text-slate-300">
                  {formatPct(row.predicted_return)}
                </td>
                <td className="px-3 py-3 font-mono text-slate-400">
                  {formatPct(row.actual_return)}
                </td>
                <td className={`px-3 py-3 font-semibold ${resultClass(row)}`}>
                  {resultLabel(row)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-base text-slate-500">目前尚無台股行事曆預測資料。</p>
        ) : null}
      </div>
    </section>
  )
}

export default function MarketCalendarPage() {
  const [data, setData] = useState<MarketCalendarHistoryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchMarketCalendarHistory(10, { signal: controller.signal })
      .then(setData)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "台股行事曆載入失敗")
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  const latest = data?.items[0]
  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-6 text-slate-100">
      <header className="mb-5">
        <Link href="/" className="text-sm text-sky-300 hover:text-sky-200">
          回首頁
        </Link>
        <h1 className="mt-3 text-3xl font-semibold">台股行事曆</h1>
        <p className="mt-2 max-w-3xl text-base leading-7 text-slate-400">
          每日開盤前依台股技術、法人、期貨、融資融券與全球市場因素，預測下一個交易日偏漲或偏跌。
        </p>
      </header>

      {loading ? <p className="text-base text-slate-500">正在載入台股行事曆…</p> : null}
      {error ? (
        <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-base text-rose-100">
          {error}
        </p>
      ) : null}
      {!loading && !error && data && latest ? <LatestCard row={latest} /> : null}

      {!loading && !error && data ? (
        <>
          <section className="mt-5 grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <p className="text-sm text-slate-500">資料庫保留</p>
              <p className="mt-1 text-2xl font-semibold text-slate-100">{data.available} 日</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <p className="text-sm text-slate-500">已驗證</p>
              <p className="mt-1 text-2xl font-semibold text-slate-100">{data.evaluated} 日</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <p className="text-sm text-slate-500">近 50 日方向準確率</p>
              <p className="mt-1 text-2xl font-semibold text-slate-100">
                {data.accuracy == null
                  ? "—"
                  : `${data.correct}/${data.evaluated}（${(data.accuracy * 100).toFixed(1)}%）`}
              </p>
            </div>
          </section>
          <HistoryTable rows={data.items} />
        </>
      ) : null}
    </main>
  )
}
