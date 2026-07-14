"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import dynamic from "next/dynamic"
import { Dialog } from "@base-ui/react/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchStockHistory,
  fmtShares,
  toDisplayError,
  type StockHistoryResponse,
} from "@/lib/api"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"

// ECharts 只在 popup 開啟時載入，不進 initial bundle
const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <Skeleton className="h-[56vh] min-h-[360px] w-full rounded-lg" />,
})

// ── 常數 ──────────────────────────────────────────────────────────────────────
// 顯示窗固定近 6 個月；抓 365 天讓 MA60 在窗口第一天就有值（需要 60 個交易日 warmup）
const DISPLAY_MONTHS = 6
const FETCH_DAYS = 365

const MA_CONFIGS: Record<number, { color: string; label: string }> = {
  10: { color: "#fbbf24", label: "MA10" },
  20: { color: "#34d399", label: "MA20" },
  60: { color: "#f472b6", label: "MA60" },
}
const MA_PERIODS = [10, 20, 60] as const

const INST_CONFIGS = [
  { key: "foreign", label: "外資", color: "#f87171" },
  { key: "trust", label: "投信", color: "#60a5fa" },
  { key: "dealer", label: "自營", color: "#a78bfa" },
] as const
type InstKey = (typeof INST_CONFIGS)[number]["key"]

function calcMA(prices: (number | null)[], period: number): (number | null)[] {
  return prices.map((_, i) => {
    if (i < period - 1) return null
    const slice = prices.slice(i - period + 1, i + 1)
    if (slice.some((v) => v == null)) return null
    return (slice as number[]).reduce((a, b) => a + b, 0) / period
  })
}

interface Props {
  /** null = 關閉 */
  stockId: string | null
  stockName?: string | null
  onClose: () => void
}

/**
 * 全站共用的 K 線圖 popup（2026-07-14 起取代常駐 StockChart）：
 * 固定顯示近 6 個月，無日期選擇 / 自訂 MA / dataZoom 拖拉；
 * 保留 10/20/60 MA 與外資/投信/自營累積買超線的 toggle。
 */
export default function StockChartDialog({ stockId, stockName, onClose }: Props) {
  const [data, setData] = useState<StockHistoryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isMobile, setIsMobile] = useState(false)

  const [activeMAs, setActiveMAs] = useState<Set<number>>(new Set(MA_PERIODS))
  const [activeInst, setActiveInst] = useState<Set<InstKey>>(
    new Set(["foreign", "trust", "dealer"]),
  )

  // 關閉時不打即時報價 API
  const realtimeQuotes = useRealtimeQuotes(stockId ? [stockId] : [])

  // 手機橫向捲動容器：圖表 render 後預設捲到最右（最新 K 棒）
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    const mq = window.matchMedia("(max-width: 640px)")
    const update = () => setIsMobile(mq.matches)
    update()
    mq.addEventListener("change", update)
    return () => mq.removeEventListener("change", update)
  }, [])

  useEffect(() => {
    if (!stockId) {
      setData(null)
      setError(null)
      return
    }
    const controller = new AbortController()
    let cancelled = false
    async function run() {
      setLoading(true)
      setError(null)
      try {
        const resp = await fetchStockHistory(stockId as string, FETCH_DAYS, undefined, {
          signal: controller.signal,
        })
        if (!cancelled) setData(resp)
      } catch (e) {
        if (!cancelled && !controller.signal.aborted) {
          setError(toDisplayError(e))
          setData(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [stockId])

  // 資料載入完成後把橫向捲動位置推到最右（最新交易日）；桌機無 overflow 此操作為 no-op
  useEffect(() => {
    if (loading || !data) return
    const el = scrollRef.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [loading, data])

  const toggleMA = (period: number) => {
    setActiveMAs((prev) => {
      const next = new Set(prev)
      if (next.has(period)) next.delete(period)
      else next.add(period)
      return next
    })
  }

  const toggleInst = (key: InstKey) => {
    setActiveInst((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const chartOption = useMemo(() => {
    if (!data || data.history.length === 0) return null

    const fullDates = data.history.map((h) => String(h.trade_date))
    const fullClose = data.history.map((h) => h.close_price)

    // 顯示窗起點：最後一個交易日往前推 6 個月
    const lastDate = new Date(fullDates[fullDates.length - 1])
    const cutoff = new Date(lastDate)
    cutoff.setMonth(cutoff.getMonth() - DISPLAY_MONTHS)
    const cutoffStr = cutoff.toISOString().slice(0, 10)
    let startIdx = fullDates.findIndex((d) => d >= cutoffStr)
    if (startIdx < 0) startIdx = 0

    const dates = fullDates.slice(startIdx)

    const hasOHLC = data.history.some((h) => h.open_price != null)
    const candleData = hasOHLC
      ? data.history.slice(startIdx).map((h) => [
          h.open_price ?? h.close_price,
          h.close_price,
          h.low_price ?? h.close_price,
          h.high_price ?? h.close_price,
        ])
      : null
    const closePrices = fullClose.slice(startIdx)

    // 法人累積線 re-baseline 成「顯示窗內累積」：扣掉窗口前一天的值，讓線從 0 附近起算
    const rebase = (values: (number | null)[]): (number | null)[] => {
      const base = (startIdx > 0 ? values[startIdx - 1] : 0) ?? 0
      return values.slice(startIdx).map((v) => (v == null ? null : v - base))
    }
    const foreignCum = rebase(data.history.map((h) => h.foreign_cumulative))
    const trustCum = rebase(data.history.map((h) => h.trust_cumulative))
    const dealerCum = rebase(data.history.map((h) => h.dealer_cumulative))

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const priceSeries: any =
      hasOHLC && candleData
        ? {
            name: "股價",
            type: "candlestick",
            data: candleData,
            itemStyle: {
              color: "#ef4444",
              color0: "#22c55e",
              borderColor: "#ef4444",
              borderColor0: "#22c55e",
            },
          }
        : {
            name: "收盤價",
            type: "line",
            data: closePrices,
            smooth: true,
            symbol: "none",
            lineStyle: { color: "#f4f4f5", width: 2 },
            itemStyle: { color: "#f4f4f5" },
          }

    // MA 在「完整序列」上計算再切窗口，MA60 在窗口第一天就有值
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const maSeries: any[] = []
    for (const period of MA_PERIODS) {
      if (!activeMAs.has(period)) continue
      const maData = calcMA(fullClose, period).slice(startIdx)
      const cfg = MA_CONFIGS[period]
      maSeries.push({
        name: cfg.label,
        type: "line",
        data: maData,
        smooth: false,
        symbol: "none",
        lineStyle: { color: cfg.color, width: 1.5, type: "solid" },
        itemStyle: { color: cfg.color },
      })
    }

    const priceName = hasOHLC ? "股價" : "收盤價"
    const maNames = MA_PERIODS.filter((p) => activeMAs.has(p)).map((p) => MA_CONFIGS[p].label)
    const instNames = INST_CONFIGS.filter((c) => activeInst.has(c.key)).map((c) =>
      c.key === "dealer" ? "自營商累積" : `${c.label}累積`,
    )
    const legendData = [priceName, ...maNames, ...instNames]

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: "rgba(15,23,42,0.95)",
        borderColor: "#334155",
        textStyle: { color: "#fafafa", fontSize: 12 },
        axisPointer: { type: "cross" as const },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any[]) => {
          if (!Array.isArray(params) || params.length === 0) return ""
          let html = `<div style="font-size:12px">${params[0].axisValue}</div>`
          for (const p of params) {
            if (p.seriesType === "candlestick") {
              const [, open, close, low, high] = p.data as number[]
              const color = close >= open ? "#ef4444" : "#22c55e"
              html +=
                `<div style="color:${color}">` +
                `開 ${open.toFixed(2)}　高 ${high.toFixed(2)}　` +
                `低 ${low.toFixed(2)}　收 <b>${close.toFixed(2)}</b> 元</div>`
            } else if (p.seriesName === "收盤價" || p.seriesName?.startsWith("MA")) {
              if (typeof p.value === "number") {
                html += `<div>${p.marker} ${p.seriesName}: <b>${p.value.toFixed(2)} 元</b></div>`
              }
            } else if (p.value != null) {
              html += `<div>${p.marker} ${p.seriesName}: <b>${fmtShares(p.value as number)}</b></div>`
            }
          }
          return html
        },
      },
      legend: {
        data: legendData,
        textStyle: { color: "#94a3b8", fontSize: isMobile ? 10 : 11 },
        top: 0,
        itemWidth: isMobile ? 12 : 16,
        itemHeight: isMobile ? 8 : 10,
        itemGap: isMobile ? 6 : 10,
      },
      // 手機：左側股價刻度整排拿掉（數值靠 tooltip），寬度全讓給線圖看趨勢
      grid: {
        left: isMobile ? 8 : 60,
        right: isMobile ? 38 : 70,
        top: isMobile ? 56 : 40,
        bottom: isMobile ? 28 : 32,
      },
      xAxis: {
        type: "category" as const,
        data: dates,
        axisLabel: {
          color: "#64748b",
          fontSize: isMobile ? 10 : 11,
          formatter: (v: string) => v.slice(5),
        },
        axisLine: { lineStyle: { color: "#334155" } },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value" as const,
          name: isMobile ? undefined : priceName,
          nameTextStyle: { color: "#94a3b8", fontSize: 11 },
          // 手機不顯示股價刻度（左側留白全給繪圖區），數值看 tooltip
          axisLabel: isMobile
            ? { show: false }
            : { color: "#94a3b8", fontSize: 11 },
          axisLine: { lineStyle: { color: "#334155" } },
          splitLine: { lineStyle: { color: "#27272a" } },
          scale: true,
        },
        {
          type: "value" as const,
          name: isMobile ? undefined : "累積張數",
          nameTextStyle: { color: "#94a3b8", fontSize: 11 },
          axisLabel: {
            color: "#94a3b8",
            fontSize: isMobile ? 10 : 11,
            formatter: (v: number) => fmtShares(v),
          },
          axisLine: { lineStyle: { color: "#334155" } },
          splitLine: { show: false },
        },
      ],
      series: [
        priceSeries,
        ...maSeries,
        ...(activeInst.has("foreign")
          ? [
              {
                name: "外資累積",
                type: "line",
                yAxisIndex: 1,
                data: foreignCum,
                smooth: true,
                symbol: "none",
                lineStyle: { color: "#f87171", width: 1.5 },
                itemStyle: { color: "#f87171" },
              },
            ]
          : []),
        ...(activeInst.has("trust")
          ? [
              {
                name: "投信累積",
                type: "line",
                yAxisIndex: 1,
                data: trustCum,
                smooth: true,
                symbol: "none",
                lineStyle: { color: "#60a5fa", width: 1.5 },
                itemStyle: { color: "#60a5fa" },
              },
            ]
          : []),
        ...(activeInst.has("dealer")
          ? [
              {
                name: "自營商累積",
                type: "line",
                yAxisIndex: 1,
                data: dealerCum,
                smooth: true,
                symbol: "none",
                lineStyle: { color: "#a78bfa", width: 1.5 },
                itemStyle: { color: "#a78bfa" },
              },
            ]
          : []),
      ],
    }
  }, [data, activeMAs, activeInst, isMobile])

  const rt = stockId ? realtimeQuotes.get(stockId) : undefined

  return (
    <Dialog.Root
      open={stockId !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        {/* 手機全螢幕（K 線需要最大可用寬度）；≥sm 才是置中卡片 */}
        <Dialog.Popup className="fixed inset-0 z-50 w-full overflow-y-auto bg-slate-900 p-2.5 shadow-2xl sm:inset-auto sm:left-1/2 sm:top-1/2 sm:max-h-[92vh] sm:w-[min(96vw,64rem)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:border sm:border-slate-700 sm:p-5">
          {stockId && (
            <div className="flex flex-col gap-3">
              <header className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <Dialog.Title className="text-lg font-semibold text-slate-100">
                    {data ? `${data.stock_id} ${data.stock_name}` : `${stockId} ${stockName ?? ""}`}
                  </Dialog.Title>
                  {data && (
                    <span className="text-xs text-slate-500">
                      {data.sub_industry ?? data.industry_name}
                    </span>
                  )}
                  <Dialog.Description className="text-xs text-slate-500">
                    近 {DISPLAY_MONTHS} 個月
                  </Dialog.Description>
                </div>
                <div className="flex items-center gap-3">
                  {rt && rt.price != null && (
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-lg text-slate-100">{rt.price.toFixed(2)}</span>
                      <span
                        className={`font-mono text-xs ${
                          (rt.change ?? 0) > 0
                            ? "text-red-400"
                            : (rt.change ?? 0) < 0
                              ? "text-green-400"
                              : "text-slate-300"
                        }`}
                      >
                        {(rt.change ?? 0) > 0 ? "▲" : (rt.change ?? 0) < 0 ? "▼" : ""}{" "}
                        {Math.abs(rt.change ?? 0).toFixed(2)} (
                        {rt.change_pct != null
                          ? (rt.change_pct >= 0 ? "+" : "") + rt.change_pct.toFixed(2)
                          : "0.00"}
                        %)
                      </span>
                      <span className="text-[10px] font-medium text-yellow-500">
                        即時 {rt.trade_time ?? ""}
                      </span>
                    </div>
                  )}
                  <Dialog.Close className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700">
                    關閉 ✕
                  </Dialog.Close>
                </div>
              </header>

              {/* MA + 法人線 toggles */}
              <div className="flex flex-wrap items-center gap-1">
                {MA_PERIODS.map((period) => {
                  const cfg = MA_CONFIGS[period]
                  const active = activeMAs.has(period)
                  return (
                    <button
                      key={period}
                      type="button"
                      onClick={() => toggleMA(period)}
                      className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors ${
                        active
                          ? "border-slate-400 bg-slate-500 text-slate-50"
                          : "border-slate-600 bg-slate-700 text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-sm"
                        style={{ backgroundColor: active ? cfg.color : "#475569" }}
                      />
                      {cfg.label}
                    </button>
                  )
                })}
                <span className="select-none text-slate-700">|</span>
                {INST_CONFIGS.map(({ key, label, color }) => {
                  const active = activeInst.has(key)
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => toggleInst(key)}
                      className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors ${
                        active
                          ? "border-slate-400 bg-slate-500 text-slate-50"
                          : "border-slate-600 bg-slate-700 text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-sm"
                        style={{ backgroundColor: active ? color : "#475569" }}
                      />
                      {label}
                    </button>
                  )
                })}
              </div>

              {loading && (
                <Skeleton className="h-[56vh] min-h-[360px] w-full rounded-lg" />
              )}
              {error && !loading && <p className="text-sm text-red-400">{error}</p>}
              {!loading && !error && chartOption && (
                // 手機全螢幕時拔掉外框與內距，把寬度全讓給繪圖區；
                // 繪圖內容做寬到 150vw，容器 overflow-x-auto 讓使用者左右滑（預設捲到最右＝最新）
                <div
                  ref={scrollRef}
                  className="overflow-x-auto sm:overflow-x-visible sm:rounded-lg sm:border sm:border-slate-600 sm:p-3"
                >
                  <div className="w-[150vw] sm:w-full">
                    <ReactECharts
                      option={chartOption}
                      notMerge
                      style={{
                        height: isMobile ? "62vh" : "56vh",
                        minHeight: 380,
                        width: "100%",
                      }}
                      opts={{ renderer: "svg" }}
                    />
                  </div>
                </div>
              )}
              {!loading && !error && data && data.history.length === 0 && (
                <p className="text-sm text-slate-500">此區間無資料。</p>
              )}
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
