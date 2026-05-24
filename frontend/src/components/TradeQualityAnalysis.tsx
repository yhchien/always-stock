"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useSearchParams } from "next/navigation"
import KeyFactorsList from "@/components/KeyFactorsList"
import TradingPlanPanel, { PanelBulletList } from "@/components/TradingPlanPanel"
import {
  fetchLatestTradeDate,
  searchStocks,
  streamTradeQuality,
  toDisplayError,
  type StockSearchItem,
  type TradeQualityRating,
  type TradeQualityResponse,
  type TradeQualityStreamStage,
} from "@/lib/api"

const STAGE_PERCENT: Record<TradeQualityStreamStage, number> = {
  collect_raw: 15,
  build_context: 35,
  openai_call: 60,
  done: 100,
  error: 100,
}

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

function PricePredictionBar({
  targetLow,
  targetHigh,
  exitLow,
  exitHigh,
}: {
  targetLow: number | null | undefined
  targetHigh: number | null | undefined
  exitLow: number | null | undefined
  exitHigh: number | null | undefined
}) {
  const values = [targetLow, targetHigh, exitLow, exitHigh].filter(
    (v): v is number => typeof v === "number" && !Number.isNaN(v),
  )
  if (values.length < 2) return null

  const min = Math.min(...values)
  const max = Math.max(...values)
  if (min === max) return null
  const padding = (max - min) * 0.1 || max * 0.05
  const domainMin = min - padding
  const domainMax = max + padding
  const range = domainMax - domainMin
  const mapX = (v: number) => ((v - domainMin) / range) * 100

  const ticks: { pct: number; label: string; tone: string }[] = []
  if (exitLow != null) ticks.push({ pct: mapX(exitLow), label: exitLow.toFixed(1), tone: "text-rose-300" })
  if (exitHigh != null) ticks.push({ pct: mapX(exitHigh), label: exitHigh.toFixed(1), tone: "text-rose-300" })
  if (targetLow != null) ticks.push({ pct: mapX(targetLow), label: targetLow.toFixed(1), tone: "text-emerald-300" })
  if (targetHigh != null) ticks.push({ pct: mapX(targetHigh), label: targetHigh.toFixed(1), tone: "text-emerald-300" })
  ticks.sort((a, b) => a.pct - b.pct)

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-3 text-[10px] text-slate-400">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-3 rounded-sm bg-rose-500/60" /> 出場區間
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-3 rounded-sm bg-emerald-500/60" /> 目標區間
        </span>
      </div>
      <div className="relative h-6 overflow-hidden rounded border border-slate-700 bg-slate-900/60">
        {exitLow != null && exitHigh != null && exitHigh > exitLow && (
          <div
            className="absolute top-0 bottom-0 bg-rose-500/40"
            style={{ left: `${mapX(exitLow)}%`, width: `${mapX(exitHigh) - mapX(exitLow)}%` }}
          />
        )}
        {targetLow != null && targetHigh != null && targetHigh > targetLow && (
          <div
            className="absolute top-0 bottom-0 bg-emerald-500/40"
            style={{ left: `${mapX(targetLow)}%`, width: `${mapX(targetHigh) - mapX(targetLow)}%` }}
          />
        )}
      </div>
      <div className="relative h-3 text-[10px] font-mono">
        {ticks.map((t, i) => (
          <span
            key={i}
            className={`absolute top-0 -translate-x-1/2 whitespace-nowrap ${t.tone}`}
            style={{ left: `${t.pct}%` }}
          >
            {t.label}
          </span>
        ))}
      </div>
    </div>
  )
}

export default function TradeQualityAnalysis({
  initialLatestDate,
}: {
  initialLatestDate?: string | null
}) {
  const searchParams = useSearchParams()
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<StockSearchItem | null>(null)
  const [suggestions, setSuggestions] = useState<StockSearchItem[]>([])
  const [showSuggest, setShowSuggest] = useState(false)

  const [useCustomDate, setUseCustomDate] = useState(false)
  const [buyDate, setBuyDate] = useState("")
  const [latestDate, setLatestDate] = useState<string | null>(initialLatestDate ?? null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<TradeQualityResponse | null>(null)
  const [showDetail, setShowDetail] = useState(false)
  const [pendingAnalyze, setPendingAnalyze] = useState(false)
  const [progressStage, setProgressStage] = useState<TradeQualityStreamStage | null>(null)
  const [progressLabel, setProgressLabel] = useState<string>("")

  const containerRef = useRef<HTMLDivElement>(null)
  const lastPrefillKeyRef = useRef<string>("")

  // Load latest trade date once on mount
  useEffect(() => {
    if (initialLatestDate !== undefined) return
    const ctrl = new AbortController()
    fetchLatestTradeDate({ signal: ctrl.signal })
      .then((d) => setLatestDate(d))
      .catch(() => {})
    return () => ctrl.abort()
  }, [initialLatestDate])

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
    setResult(null)
    setError(null)
  }

  const handleAnalyze = useCallback(async () => {
    const keyword = query.trim()
    let resolved = selected

    if (!resolved && keyword) {
      const exact = suggestions.find((item) =>
        item.stock_id === keyword ||
        item.stock_name === keyword ||
        `${item.stock_id} ${item.stock_name}` === keyword
      )
      if (exact) {
        resolved = exact
      }
    }

    if (!resolved && /^\d{4,6}$/.test(keyword)) {
      try {
        const items = await searchStocks(keyword)
        const exact = items.find((item) => item.stock_id === keyword)
        if (exact) {
          resolved = exact
          setSuggestions(items)
        }
      } catch {
        // Keep original UX and let the common error path handle execution issues later.
      }
    }

    if (!resolved) {
      setError("請輸入或選擇正確股票")
      return
    }

    setSelected(resolved)
    setQuery(`${resolved.stock_id} ${resolved.stock_name}`)
    setShowSuggest(false)
    setLoading(true)
    setError(null)
    setResult(null)
    setShowDetail(false)
    setProgressStage("collect_raw")
    setProgressLabel("正在跟後台要資料")
    try {
      const res = await streamTradeQuality(
        {
          stock_id: resolved.stock_id,
          buy_date: useCustomDate ? (buyDate || null) : null,
        },
        (event) => {
          setProgressStage(event.stage)
          setProgressLabel(event.label)
        },
      )
      setResult(res)
    } catch (e) {
      setError(toDisplayError(e, "執行失敗"))
    } finally {
      setLoading(false)
      setProgressStage(null)
      setProgressLabel("")
    }
  }, [selected, query, suggestions, useCustomDate, buyDate])

  // URL prefill: when navigated with ?stock_id=XXX&buy_date=YYYY-MM-DD, auto populate + analyze.
  // Uses a key-based ref (sid|bd) so consecutive clicks with different params on the same
  // mounted component re-trigger prefill instead of being ignored.
  useEffect(() => {
    const sid = searchParams.get("stock_id")
    const bd = searchParams.get("buy_date")
    if (!sid) return
    const key = `${sid}|${bd ?? ""}`
    if (lastPrefillKeyRef.current === key) return
    lastPrefillKeyRef.current = key
    const ctrl = new AbortController()
    searchStocks(sid, { signal: ctrl.signal })
      .then((items) => {
        const exact = items.find((i) => i.stock_id === sid)
        if (!exact) return
        setSelected(exact)
        setQuery(`${exact.stock_id} ${exact.stock_name}`)
        setSuggestions(items)
        setShowSuggest(false)
        if (bd) {
          setUseCustomDate(true)
          setBuyDate(bd)
        } else {
          setUseCustomDate(false)
          setBuyDate("")
        }
        setPendingAnalyze(true)
      })
      .catch(() => {})
    return () => ctrl.abort()
  }, [searchParams])

  // Fire handleAnalyze after state from URL prefill has settled
  useEffect(() => {
    if (!pendingAnalyze) return
    if (!selected) return
    setPendingAnalyze(false)
    handleAnalyze()
  }, [pendingAnalyze, selected, handleAnalyze])

  const ratingStyle = result ? RATING_STYLE[result.rating] : null
  const targetRange = result
    ? formatPriceRange(result.target_price_low, result.target_price_high)
    : null
  const exitRange = result
    ? formatPriceRange(result.exit_price_low, result.exit_price_high)
    : null

  return (
    <div id="trade-quality" className="rounded-xl border border-slate-600 bg-slate-800/40 overflow-hidden scroll-mt-16">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-700/40">
        <span className="text-sm font-semibold text-slate-100">交易質量分析</span>
        <span className="ml-2 text-xs text-slate-400">
          輸入股票後直接執行，系統會還原當天市場情境並給出評級
        </span>
      </div>

      {/* Inputs */}
      <div className="p-4 flex flex-col gap-3" ref={containerRef}>
        <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
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

          {/* Submit */}
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={loading || !query.trim()}
            className="rounded-md bg-slate-700 px-4 py-2 text-sm text-slate-100 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "執行中…" : "執行"}
          </button>
        </div>

        <div className="flex flex-col gap-2 rounded-md border border-slate-700/50 bg-slate-900/30 px-3 py-2">
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={useCustomDate}
              onChange={(e) => {
                setUseCustomDate(e.target.checked)
                if (!e.target.checked) {
                  setBuyDate("")
                }
              }}
              className="h-4 w-4 rounded border-slate-500 bg-slate-900 text-slate-200"
            />
            自訂買進日期
          </label>

          {useCustomDate ? (
            <input
              type="date"
              value={buyDate}
              onChange={(e) => setBuyDate(e.target.value)}
              className="rounded-md border border-slate-600 bg-slate-900/60 px-3 py-2 text-sm text-slate-100 focus:border-slate-400 focus:outline-none"
            />
          ) : (
            <p className="text-xs text-slate-500">
              未指定日期時，預設使用最近可用交易日
              {latestDate ? `（${latestDate}）` : ""}。
            </p>
          )}
        </div>
      </div>

      {/* Body */}
      {loading && (
        <div className="border-t border-slate-700/40 p-4 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <div className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
              <span className="text-xs text-slate-300 truncate">
                {progressLabel || "正在跟後台要資料"}
              </span>
            </div>
            <span className="text-xs font-mono text-slate-500 shrink-0">
              {progressStage ? STAGE_PERCENT[progressStage] : 0}%
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
              style={{
                width: `${progressStage ? STAGE_PERCENT[progressStage] : 0}%`,
              }}
            />
          </div>
          <p className="text-[11px] text-slate-500">
            分析過程包含資料蒐集、訊號組裝、AI 推論三段；OpenAI 回應通常 5~30 秒。
          </p>
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
            {result.source === "market_not_open" && (
              <span className="text-xs text-amber-400">所選日期台股還沒開盤</span>
            )}
          </div>

          {/* M3 action_one_liner：一句話建議 */}
          {result.action_one_liner && (
            <div className="flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2">
              <span className="text-[10px] text-amber-400/70 shrink-0 uppercase tracking-wide">建議</span>
              <span className="text-sm font-semibold text-amber-100">{result.action_one_liner}</span>
            </div>
          )}

          {/* Summary */}
          <p className="text-sm text-slate-200 leading-relaxed">{result.summary}</p>

          {/* M25：條列指標 + 燈號（A 綠 / B 黃 / C 紅）+ delta 箭頭 */}
          {result.key_factors && result.key_factors.length > 0 && (
            <KeyFactorsList factors={result.key_factors} />
          )}

          {/* Price prediction：視覺化價位帶 + 純文字數字 */}
          {(targetRange || exitRange) && (
            <div className="flex flex-col gap-2 rounded-md border border-slate-700/40 bg-slate-900/30 p-3">
              <PricePredictionBar
                targetLow={result.target_price_low}
                targetHigh={result.target_price_high}
                exitLow={result.exit_price_low}
                exitHigh={result.exit_price_high}
              />
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
              </div>
            </div>
          )}

          {/* Other metadata */}
          <div className="flex flex-wrap gap-4 text-xs">
            {result.market_state && (
              <span className="text-slate-400">市場：{result.market_state}</span>
            )}
            {result.quadrant && (
              <span className="text-slate-400">象限：{result.quadrant}</span>
            )}
            {result.expectation_gap && (
              <span className="text-slate-400">預期差：{result.expectation_gap}</span>
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

          {/* M3 六段分析 panels */}
          {(result.industry_section || result.chip_section || result.fundamental_section ||
            result.technical_section || result.peer_section || result.news_section) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <TradingPlanPanel number="1" title="產業位置" accent="amber">
                <PanelBulletList
                  items={result.industry_section ?? []}
                  bulletAccent="amber"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
              <TradingPlanPanel number="2" title="籌碼動向" accent="cyan">
                <PanelBulletList
                  items={result.chip_section ?? []}
                  bulletAccent="cyan"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
              <TradingPlanPanel number="3" title="基本面動能" accent="emerald">
                <PanelBulletList
                  items={result.fundamental_section ?? []}
                  bulletAccent="emerald"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
              <TradingPlanPanel number="4" title="技術面" accent="rose">
                <PanelBulletList
                  items={result.technical_section ?? []}
                  bulletAccent="rose"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
              <TradingPlanPanel number="5" title="同儕比較" accent="slate">
                <PanelBulletList
                  items={result.peer_section ?? []}
                  bulletAccent="slate"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
              <TradingPlanPanel number="6" title="近期訊號" accent="slate">
                <PanelBulletList
                  items={result.news_section ?? []}
                  bulletAccent="slate"
                  emptyText="資料待更新"
                />
              </TradingPlanPanel>
            </div>
          )}

          {/* 完整報告（收合備用，向後相容） */}
          {result.report_markdown && (
            <div>
              <button
                type="button"
                onClick={() => setShowDetail((v) => !v)}
                className="text-xs text-slate-500 hover:text-slate-300 underline underline-offset-2"
              >
                {showDetail ? "收起完整報告" : "展開完整報告"}
              </button>
              {showDetail && (
                <div className="mt-2 rounded-md border border-slate-700/60 bg-slate-900/50 p-4">
                  <pre className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap font-sans">
                    {result.report_markdown}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {!loading && !error && !result && (
        <div className="border-t border-slate-700/40 px-4 py-3">
          <p className="text-xs text-slate-500">
            輸入股票後點擊「執行」以產生交易質量評級與分析報告。
          </p>
        </div>
      )}
    </div>
  )
}
