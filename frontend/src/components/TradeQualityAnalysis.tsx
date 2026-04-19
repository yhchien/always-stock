"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  analyzeTradeQuality,
  fetchLatestTradeDate,
  searchStocks,
  toDisplayError,
  type StockSearchItem,
  type TradeQualityRating,
  type TradeQualityResponse,
} from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"

const RATING_STYLE: Record<TradeQualityRating, { bg: string; text: string; border: string; label: string }> = {
  STRONG_BUY: { bg: "bg-emerald-600", text: "text-white", border: "border-emerald-400", label: "強烈推薦" },
  BUY: { bg: "bg-emerald-500/80", text: "text-white", border: "border-emerald-400", label: "推薦" },
  NEUTRAL: { bg: "bg-amber-500/80", text: "text-slate-900", border: "border-amber-400", label: "中立" },
  WATCH: { bg: "bg-orange-500/80", text: "text-white", border: "border-orange-400", label: "再看看" },
  RUN: { bg: "bg-red-600", text: "text-white", border: "border-red-400", label: "快跑" },
}

const SEARCH_DEBOUNCE_MS = 250

function formatPriceRange(low?: number | null, high?: number | null): string | null {
  if (low == null && high == null) return null
  if (low != null && high != null) return `${low.toFixed(1)} – ${high.toFixed(1)}`
  if (low != null) return `≥ ${low.toFixed(1)}`
  if (high != null) return `≤ ${high.toFixed(1)}`
  return null
}

export default function TradeQualityAnalysis() {
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<StockSearchItem | null>(null)
  const [suggestions, setSuggestions] = useState<StockSearchItem[]>([])
  const [showSuggest, setShowSuggest] = useState(false)

  const [buyDate, setBuyDate] = useState("")
  const [latestDate, setLatestDate] = useState<string | null>(null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<TradeQualityResponse | null>(null)
  const [showDetail, setShowDetail] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)

  // Load latest trade date once on mount
  useEffect(() => {
    const ctrl = new AbortController()
    fetchLatestTradeDate({ signal: ctrl.signal })
      .then((d) => setLatestDate(d))
      .catch(() => {})
    return () => ctrl.abort()
  }, [])

  // Autocomplete: debounce search
  useEffect(() => {
    const keyword = query.trim()
    if (!keyword || selected?.stock_id === keyword || selected?.stock_name === keyword) {
      setSuggestions([])
      return
    }
    const ctrl = new AbortController()
    const handle = window.setTimeout(() => {
      searchStocks(keyword, { signal: ctrl.signal })
        .then((items) => {
          setSuggestions(items)
          setShowSuggest(true)
        })
        .catch(() => {})
    }, SEARCH_DEBOUNCE_MS)
    return () => {
      window.clearTimeout(handle)
      ctrl.abort()
    }
  }, [query, selected])

  // Click outside to close suggestions
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowSuggest(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  const handleSelectStock = (item: StockSearchItem) => {
    setSelected(item)
    setQuery(`${item.stock_id} ${item.stock_name}`)
    setShowSuggest(false)
    setSuggestions([])
  }

  const handleQueryChange = (value: string) => {
    setQuery(value)
    setSelected(null)
    setError(null)
  }

  const handleAnalyze = useCallback(async () => {
    if (!selected) {
      setError("請先選擇股票")
      return
    }
    setLoading(true)
    setError(null)
    setResult(null)
    setShowDetail(false)
    try {
      const res = await analyzeTradeQuality({
        stock_id: selected.stock_id,
        buy_date: buyDate || null,
      })
      setResult(res)
    } catch (e) {
      setError(toDisplayError(e, "分析失敗"))
    } finally {
      setLoading(false)
    }
  }, [selected, buyDate])

  const ratingStyle = result ? RATING_STYLE[result.rating] : null
  const targetRange = result
    ? formatPriceRange(result.target_price_low, result.target_price_high)
    : null
  const exitRange = result
    ? formatPriceRange(result.exit_price_low, result.exit_price_high)
    : null

  return (
    <div className="rounded-xl border border-slate-600 bg-slate-800/40 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-700/40">
        <span className="text-sm font-semibold text-slate-100">交易質量分析</span>
        <span className="ml-2 text-xs text-slate-400">
          輸入股票與買進日期，AI 會還原當天的市場情境並給出評級
        </span>
      </div>

      {/* Inputs */}
      <div className="p-4 flex flex-col gap-3" ref={containerRef}>
        <div className="grid grid-cols-1 sm:grid-cols-[1fr_180px_auto] gap-2">
          {/* Stock autocomplete */}
          <div className="relative">
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              onFocus={() => suggestions.length > 0 && setShowSuggest(true)}
              placeholder="股票代號或名稱（例：2330、台積電）"
              className="w-full rounded-md border border-slate-600 bg-slate-900/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-slate-400 focus:outline-none"
            />
            {showSuggest && suggestions.length > 0 && (
              <div className="absolute z-20 mt-1 w-full max-h-64 overflow-y-auto rounded-md border border-slate-600 bg-slate-900 shadow-lg">
                {suggestions.map((item) => (
                  <button
                    key={item.stock_id}
                    type="button"
                    onClick={() => handleSelectStock(item)}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-slate-200 hover:bg-slate-700/60"
                  >
                    <span className="flex items-center gap-2">
                      <span className="font-mono text-slate-300">{item.stock_id}</span>
                      <span>{item.stock_name}</span>
                    </span>
                    <span className="text-xs text-slate-500">{item.industry_name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Buy date */}
          <input
            type="date"
            value={buyDate}
            onChange={(e) => setBuyDate(e.target.value)}
            max={latestDate ?? undefined}
            placeholder={latestDate ?? ""}
            className="rounded-md border border-slate-600 bg-slate-900/60 px-3 py-2 text-sm text-slate-100 focus:border-slate-400 focus:outline-none"
          />

          {/* Submit */}
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={loading || !selected}
            className="rounded-md bg-slate-700 px-4 py-2 text-sm text-slate-100 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "分析中…" : "AI 分析"}
          </button>
        </div>

        <p className="text-xs text-slate-500">
          未填買進日期時，預設使用 DB 最近交易日
          {latestDate ? `（${latestDate}）` : ""}。
        </p>
      </div>

      {/* Body */}
      {loading && (
        <div className="border-t border-slate-700/40 p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
            <span className="text-xs text-slate-400">AI 正在還原當天市場情境…</span>
          </div>
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      )}

      {error && (
        <div className="border-t border-slate-700/40 p-4">
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {result && ratingStyle && (
        <div className="border-t border-slate-700/40 p-4 flex flex-col gap-3">
          {/* Rating badge + meta */}
          <div className="flex flex-wrap items-center gap-3">
            <span
              className={`inline-flex items-center rounded-md border-2 ${ratingStyle.bg} ${ratingStyle.text} ${ratingStyle.border} px-4 py-1.5 text-base font-bold tracking-wide`}
            >
              {ratingStyle.label}
            </span>
            <span className="text-xs text-slate-400">
              {result.stock_id} {result.stock_name}
            </span>
            <span className="text-xs text-slate-500">買進日：{result.buy_date}</span>
            {result.source === "unavailable" && (
              <span className="text-xs text-amber-400">AI 服務不可用（fallback 中）</span>
            )}
          </div>

          {/* Summary */}
          <p className="text-sm text-slate-200 leading-relaxed">{result.summary}</p>

          {/* Price info */}
          <div className="flex flex-wrap gap-4 text-xs">
            {targetRange && (
              <span className="text-slate-300">
                目標價：<span className="font-semibold text-emerald-300">{targetRange}</span>
                {result.time_horizon_days ? (
                  <span className="text-slate-500">（{result.time_horizon_days} 天）</span>
                ) : null}
              </span>
            )}
            {exitRange && (
              <span className="text-slate-300">
                出場價：<span className="font-semibold text-red-300">{exitRange}</span>
                {result.max_holding_days ? (
                  <span className="text-slate-500">（最多 {result.max_holding_days} 天）</span>
                ) : null}
              </span>
            )}
            {result.classification && (
              <span className="text-slate-400">分類：{result.classification}</span>
            )}
            {result.risk_level && (
              <span className="text-slate-400">風險：{result.risk_level}</span>
            )}
          </div>

          {/* Warnings */}
          {result.warnings.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-400 space-y-0.5">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}

          {/* Detail toggle */}
          <div>
            <button
              type="button"
              onClick={() => setShowDetail((v) => !v)}
              className="text-xs text-slate-300 hover:text-slate-100 underline underline-offset-2"
            >
              {showDetail ? "收起詳細" : "詳細"}
            </button>
          </div>

          {showDetail && (
            <div className="rounded-md border border-slate-700/60 bg-slate-900/50 p-4">
              <pre className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap font-sans">
                {result.report_markdown}
              </pre>
            </div>
          )}
        </div>
      )}

      {!loading && !error && !result && (
        <div className="border-t border-slate-700/40 px-4 py-3">
          <p className="text-xs text-slate-500">
            選擇股票後點擊「AI 分析」以產生交易質量評級與分析報告。
          </p>
        </div>
      )}
    </div>
  )
}
