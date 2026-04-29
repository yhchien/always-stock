"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"

import {
  fetchSignalArchive,
  fetchSignalArchiveDetail,
  type SignalArchiveDetailResponse,
  type SignalArchiveSortBy,
  type SignalArchiveSummaryResponse,
} from "@/lib/api"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const SORT_OPTIONS: { value: SignalArchiveSortBy; label: string }[] = [
  { value: "tracking_days_desc", label: "追蹤天數" },
  { value: "return_desc", label: "報酬率高到低" },
  { value: "return_asc", label: "報酬率低到高" },
  { value: "hit_count_desc", label: "命中次數" },
  { value: "latest_hit_desc", label: "最近抓到日期" },
  { value: "stock_id_asc", label: "股票代號" },
]

function formatPct(value: number | null): string {
  if (value == null) return "--"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function formatPrice(value: number | null): string {
  if (value == null) return "--"
  return value.toFixed(2)
}

function ReturnCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="font-mono text-sm text-slate-500">--</span>
  }
  const color =
    value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-300"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono text-sm font-semibold ${color}`}>
      {arrow ? `${arrow} ` : ""}
      {formatPct(value)}
    </span>
  )
}

export default function SignalArchivePage() {
  const [sortBy, setSortBy] = useState<SignalArchiveSortBy>("tracking_days_desc")
  const [summary, setSummary] = useState<SignalArchiveSummaryResponse | null>(null)
  const [selectedStockId, setSelectedStockId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SignalArchiveDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  const loadSummary = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchSignalArchive({ sort_by: sortBy, limit: 200 })
      setSummary(data)
      setSelectedStockId((prev) => prev ?? data.items[0]?.stock_id ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "訊號追蹤清單載入失敗")
    } finally {
      setLoading(false)
    }
  }, [sortBy])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary])

  useEffect(() => {
    if (!selectedStockId) {
      setDetail(null)
      return
    }
    const stockId = selectedStockId

    let cancelled = false
    async function run() {
      setDetailLoading(true)
      setDetailError(null)
      try {
        const data = await fetchSignalArchiveDetail(stockId)
        if (!cancelled) setDetail(data)
      } catch (err) {
        if (!cancelled) {
          setDetail(null)
          setDetailError(err instanceof Error ? err.message : "訊號追蹤詳情載入失敗")
        }
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [selectedStockId])

  const selectedSummary = useMemo(() => {
    if (!selectedStockId) return null
    return summary?.items.find((item) => item.stock_id === selectedStockId) ?? null
  }, [selectedStockId, summary?.items])

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">M23 40日訊號追蹤</h1>
          <p className="mt-1 text-sm text-slate-400">
            追蹤最近 {summary?.retention_trade_days ?? 40} 個交易日內，被納入每日異常訊號清單的股票。
          </p>
          {summary?.as_of_trade_date && (
            <p className="mt-1 text-xs text-slate-500">最新評估交易日：{summary.as_of_trade_date}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">排序</span>
          <Select
            value={sortBy}
            onValueChange={(value) => setSortBy(value as SignalArchiveSortBy)}
          >
            <SelectTrigger className="border-slate-600 bg-slate-800/40 text-slate-200">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </header>

      <section className="rounded-xl border border-slate-700 bg-slate-900/40 p-4">
        {loading && <p className="text-sm text-slate-400">載入中…</p>}
        {error && !loading && <p className="text-sm text-rose-300">{error}</p>}
        {!loading && !error && (summary?.items.length ?? 0) === 0 && (
          <p className="text-sm text-slate-400">目前還沒有可追蹤的訊號紀錄。</p>
        )}
        {!loading && !error && summary && summary.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className="text-slate-300">股票</TableHead>
                <TableHead className="text-slate-300">首次抓到</TableHead>
                <TableHead className="text-slate-300">最近抓到</TableHead>
                <TableHead className="text-slate-300">追蹤第幾天</TableHead>
                <TableHead className="text-slate-300">命中次數</TableHead>
                <TableHead className="text-slate-300">最新類型</TableHead>
                <TableHead className="text-slate-300">報酬率</TableHead>
                <TableHead className="text-slate-300">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.items.map((item) => {
                const active = item.stock_id === selectedStockId
                return (
                  <TableRow
                    key={item.stock_id}
                    className={`border-slate-800 ${active ? "bg-slate-800/40" : ""}`}
                  >
                    <TableCell className="align-top">
                      <div className="flex flex-col">
                        <button
                          type="button"
                          onClick={() => setSelectedStockId(item.stock_id)}
                          className="w-fit text-left text-sm font-semibold text-slate-100 hover:text-sky-300"
                        >
                          {item.stock_id} {item.stock_name}
                        </button>
                        <span className="text-xs text-slate-500">
                          {item.industry_name ?? "—"}
                          {item.sub_industry ? ` · ${item.sub_industry}` : ""}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-slate-300">
                      {item.first_seen_date}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-slate-300">
                      {item.latest_hit_date}
                    </TableCell>
                    <TableCell className="text-sm text-slate-200">
                      第 {item.tracking_day_index} 天
                    </TableCell>
                    <TableCell className="text-sm text-slate-200">
                      {item.hit_count} 次
                    </TableCell>
                    <TableCell className="text-sm text-slate-300">
                      {item.latest_signal_type}
                    </TableCell>
                    <TableCell>
                      <ReturnCell value={item.return_pct} />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-2">
                        <Link
                          href={`/stocks/${encodeURIComponent(item.stock_id)}`}
                          className="rounded border border-sky-500/50 bg-sky-500/10 px-2 py-1 text-xs text-sky-200 hover:bg-sky-500/20"
                        >
                          K線圖
                        </Link>
                        <button
                          type="button"
                          onClick={() => setSelectedStockId(item.stock_id)}
                          className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
                        >
                          看報告
                        </button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="rounded-xl border border-slate-700 bg-slate-900/40 p-4">
        {!selectedStockId && <p className="text-sm text-slate-400">選一檔股票即可查看報告。</p>}
        {detailLoading && <p className="text-sm text-slate-400">載入報告中…</p>}
        {detailError && !detailLoading && <p className="text-sm text-rose-300">{detailError}</p>}
        {!detailLoading && !detailError && detail && (
          <div className="flex flex-col gap-4">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 pb-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-100">
                  {detail.stock_id} {detail.stock_name}
                </h2>
                <p className="mt-1 text-sm text-slate-400">
                  {detail.industry_name ?? "—"}
                  {detail.sub_industry ? ` · ${detail.sub_industry}` : ""}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                <span>首次抓到</span>
                <span className="font-mono text-slate-200">{detail.first_seen_date}</span>
                <span>最近抓到</span>
                <span className="font-mono text-slate-200">{detail.latest_hit_date}</span>
                <span>目前追蹤</span>
                <span className="text-slate-200">第 {detail.tracking_day_index} 天</span>
                <span>命中次數</span>
                <span className="text-slate-200">{detail.hit_count} 次</span>
                <span>基準價</span>
                <span className="font-mono text-slate-200">
                  {formatPrice(detail.baseline_price)}
                  {detail.baseline_trade_date ? ` (${detail.baseline_trade_date})` : ""}
                </span>
                <span>最新評價</span>
                <span className="font-mono text-slate-200">
                  {formatPrice(detail.latest_eval_price)}
                  {detail.latest_eval_trade_date ? ` (${detail.latest_eval_trade_date})` : ""}
                </span>
                <span>報酬率</span>
                <ReturnCell value={detail.return_pct} />
              </div>
            </header>

            <div className="flex flex-col gap-3">
              <h3 className="text-sm font-medium text-slate-200">報告時間軸</h3>
              {detail.reports.map((report) => (
                <article
                  key={`${report.snapshot_date}-${report.signal_type}`}
                  className="rounded-lg border border-slate-800 bg-slate-800/30 p-4"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                    <span className="font-mono text-slate-200">{report.snapshot_date}</span>
                    <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[11px] text-slate-300">
                      {report.signal_type}
                    </span>
                    {report.snapshot_generated_at && (
                      <span>{report.snapshot_generated_at}</span>
                    )}
                  </div>
                  {report.business_summary && (
                    <p className="mt-2 text-xs text-slate-400">{report.business_summary}</p>
                  )}
                  <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-slate-200">
                    {report.reason}
                  </p>
                </article>
              ))}
            </div>
          </div>
        )}
        {!detailLoading && !detailError && !detail && selectedSummary && (
          <p className="text-sm text-slate-400">
            找不到 {selectedSummary.stock_id} 的報告內容。
          </p>
        )}
      </section>
    </main>
  )
}
