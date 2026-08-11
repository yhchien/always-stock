"use client"

import Link from "next/link"
import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { Dialog } from "@base-ui/react/dialog"

import { MarginAnalysisPanel } from "@/components/DailySignalsPanel"
import ObservationStatusBadge from "@/components/ObservationStatusBadge"
import StockChartDialog from "@/components/StockChartDialog"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"

import {
  fetchCompletedSignalArchive,
  fetchSignalArchive,
  fetchSignalArchiveDetail,
  fetchSignalObservations,
  type RealtimeQuote,
  type SignalArchiveCompletedPeriod,
  type SignalArchiveCompletedResponse,
  type SignalArchiveDetailResponse,
  type SignalArchiveSummaryItem,
  type SignalArchiveSummaryResponse,
  type SignalClosureReason,
  type SignalObservationItem,
} from "@/lib/api"

// 4 個互斥分類（radio 式 chip）：排序純前端計算，一次抓全部後 client-side sort
const VIEW_OPTIONS = [
  { value: "first_seen", label: "追蹤日期" },
  { value: "return_desc", label: "最多報酬率" },
  { value: "return_asc", label: "最低報酬率" },
  { value: "hit_count", label: "抓到次數" },
] as const

type ArchiveView = (typeof VIEW_OPTIONS)[number]["value"]

const DEFAULT_VIEW: ArchiveView = "first_seen"
const VIEW_VALUES: ReadonlySet<string> = new Set(VIEW_OPTIONS.map((o) => o.value))

function isArchiveView(value: string | null): value is ArchiveView {
  return value !== null && VIEW_VALUES.has(value)
}

// 每個分類預設只顯示前 15 名；「查看更多」展開全部
const TOP_N = 15

const PERIOD_PATTERN = /^\d{4}-\d{2}-\d{2}$/

const ACTIVE_COLLAPSED_KEY = "always-stock:signals-archive:active-collapsed"
const COMPLETED_COLLAPSED_KEY = "always-stock:signals-archive:completed-collapsed"

function formatPct(value: number | null): string {
  if (value == null) return "--"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function formatPrice(value: number | null): string {
  if (value == null) return "--"
  return value.toFixed(2)
}

function formatShortDate(value: string | null): string {
  if (!value) return "--"
  const parts = value.split("-")
  if (parts.length !== 3) return value
  return `${Number(parts[1])}/${Number(parts[2])}`
}

const EARLY_EXIT_THRESHOLD_PCT = -30
const PEAK_MILESTONE_PCT = 45

function StopLossWarnChip() {
  return (
    <span
      className="inline-flex items-center rounded border border-rose-500/60 bg-rose-500/15 px-1.5 py-0.5 text-[11px] font-medium text-rose-200"
      title="期間曾跌破 -30%；若首次跌破後再 3 個交易日仍 < -30%，會提前移至永久紀錄。"
    >
      ⚠ 跌破 -30%
    </span>
  )
}

function PeakMilestoneChip() {
  return (
    <span
      className="inline-flex items-center rounded border border-amber-400/70 bg-amber-400/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-200"
      title="期間曾達 +45% 報酬率里程碑（僅標注，不結算）。"
    >
      ⭐ +45% 達標
    </span>
  )
}

function ClosureReasonChip({ reason }: { reason: SignalClosureReason }) {
  if (reason === "early_exit_stop_loss") {
    return (
      <span
        className="inline-flex items-center rounded border border-rose-500/60 bg-rose-500/20 px-1.5 py-0.5 text-[11px] font-medium text-rose-100"
        title="首次跌破 -30% 後再 3 個交易日仍未漲回，已提前結算。"
      >
        提前結算（跌破 -30%）
      </span>
    )
  }
  if (reason === "early_exit_drawdown_from_peak") {
    return (
      <span
        className="inline-flex items-center rounded border border-orange-500/60 bg-orange-500/20 px-1.5 py-0.5 text-[11px] font-medium text-orange-100"
        title="從歷史高點回落 30% 以上，且後續 3 個交易日仍未縮小差距至 30% 以內，已提前結算（停利紀律）。"
      >
        提前結算（高點回落 30%）
      </span>
    )
  }
  if (reason === "manual_reset") {
    return (
      <span
        className="inline-flex items-center rounded border border-slate-600 bg-slate-700/40 px-1.5 py-0.5 text-[11px] font-medium text-slate-300"
        title="2026-08-11 正式推薦頁併入魚尾單一入口時，一次性強制結算所有進行中追蹤週期。"
      >
        人工重置
      </span>
    )
  }
  if (reason === "p4_stopped") {
    return (
      <span
        className="inline-flex items-center rounded border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-200"
        title="每日觀察判定這檔股票的推薦論點已確認失效（停止觀察），提前結算，不用等 30 個交易日或價格觸發規則。"
      >
        觀察已停止
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded border border-slate-600 bg-slate-700/40 px-1.5 py-0.5 text-[11px] font-medium text-slate-200">
      追蹤期滿
    </span>
  )
}

function SignalTypeChip({ type }: { type: string }) {
  const upper = type.toUpperCase()
  if (upper === "LEADER") {
    return (
      <span className="inline-flex items-center rounded border border-emerald-500/60 bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-medium text-emerald-200">
        領漲
      </span>
    )
  }
  if (upper === "FOLLOWER") {
    return (
      <span className="inline-flex items-center rounded border border-sky-500/60 bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-200">
        跟漲
      </span>
    )
  }
  if (upper === "LAGGARD") {
    return (
      <span className="inline-flex items-center rounded border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-medium text-amber-200">
        補漲
      </span>
    )
  }
  return <span className="text-xs text-slate-400">{upper}</span>
}

// 判斷這檔追蹤週期內是否曾被新版 P3-P7 v7 pipeline 抓到（prompt_version 以 "v7" 開頭）；
// v7 上線後（2026-07-29）prod 的 prompt_version 一律是 "v7_..." 開頭，此前/手動 replay
// 才會是裸 v1~v6.1，用字串前綴判斷即可，不需要額外的 DB 欄位。
function PipelineFlagChip({ version }: { version?: string | null }) {
  const versions = (version || "v1")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
  const isNewPipeline = versions.some((v) => v.startsWith("v7"))
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${
        isNewPipeline
          ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200"
          : "border-slate-600 bg-slate-800/50 text-slate-400"
      }`}
      title={isNewPipeline ? "此追蹤週期曾由新版 v7 pipeline 選出" : "此追蹤週期只由舊版 pipeline 選出"}
    >
      {isNewPipeline ? "新選股" : "舊選股"}
    </span>
  )
}

function VersionChip({ version }: { version?: string | null }) {
  // 整個追蹤 cycle 抓過的 prompt 版本集合（後端回 "v1,v2"）；舊資料無 → 視為 v1
  const versions = (version || "v1")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1"
      title="此追蹤週期內抓到這檔的 prompt 版本（若跨版本會同時顯示）"
    >
      {versions.map((v) => (
        <span
          key={v}
          className="inline-flex items-center rounded border border-slate-600 bg-slate-700/40 px-1.5 py-0.5 text-[11px] font-medium text-slate-300"
        >
          {v}
        </span>
      ))}
    </span>
  )
}

function formatPeriodLabel(period: SignalArchiveCompletedPeriod): string {
  const [sy, sm] = period.period_start.split("-")
  const [ey, em] = period.period_end.split("-")
  return `${sy}/${sm} - ${ey}/${em}`
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

// 當日漲跌幅（相對前一交易日收盤）：紅漲綠跌台股慣例，與 ReturnCell 同色系但字級較小
function DailyChangeCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="font-mono text-xs text-slate-500">--</span>
  }
  const color =
    value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-300"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono text-xs font-semibold ${color}`}>
      {arrow ? `${arrow} ` : ""}
      {formatPct(value)}
    </span>
  )
}

// 2026-08-11：追蹤中卡片改顯示即時報價（開盤期間每 1 分鐘更新一次）。資料源沿用既有
// /api/realtime/quotes（TWSE mis.twse.com.tw 官方盤中 API，非另外爬 goodinfo/yahoo）；
// 有即時報價時優先顯示，收盤後或抓不到即時報價時 fallback 回 ETL 寫入的 EOD latest_
// close_price/daily_change_pct，不會出現空白。只套用在「追蹤中」，移出紀錄區與工程版
// 不接即時報價。
const REALTIME_INTERVAL_MS = 60_000

function resolveLivePrice(
  item: SignalArchiveSummaryItem,
  quote: RealtimeQuote | undefined,
): number | null {
  return quote?.price ?? item.latest_close_price ?? null
}

function resolveLiveChangePct(
  item: SignalArchiveSummaryItem,
  quote: RealtimeQuote | undefined,
): number | null {
  return quote?.change_pct ?? item.daily_change_pct ?? null
}

// 報酬率即時版：用即時價相對 baseline_price 重算；baseline_price 從第二個交易日起才有值
// （比照既有「第二天固定 0%」規則），沒有 baseline 或沒有即時報價時 fallback 回後端算好
// 的 return_pct。
function resolveLiveReturnPct(
  item: SignalArchiveSummaryItem,
  quote: RealtimeQuote | undefined,
): number | null {
  if (quote?.price != null && item.baseline_price != null && item.baseline_price !== 0) {
    return ((quote.price - item.baseline_price) / item.baseline_price) * 100
  }
  return item.return_pct
}

function ExtremeReturnCell({
  value,
  tradeDate,
}: {
  value: number | null
  tradeDate: string | null
}) {
  if (value == null || !tradeDate) {
    return <span className="font-mono text-sm text-slate-500">--</span>
  }
  return (
    <span className="font-mono text-sm text-slate-200">
      {formatPct(value)} ({formatShortDate(tradeDate)})
    </span>
  )
}

// M26：合併單欄顯示「保 / 夢」預測價；舊資料兩值皆 null → 整格顯示 —
function PredictionCell({
  conservative,
  dream,
}: {
  conservative?: number | null
  dream?: number | null
}) {
  if (conservative == null && dream == null) {
    return <span className="font-mono text-sm text-slate-500">--</span>
  }
  return (
    <div className="flex flex-col leading-tight">
      <span className="font-mono text-xs text-emerald-200">
        <span className="mr-1 text-slate-400">保</span>
        {conservative == null ? "--" : conservative.toFixed(2)}
      </span>
      <span className="font-mono text-xs text-amber-200">
        <span className="mr-1 text-slate-400">夢</span>
        {dream == null ? "--" : dream.toFixed(2)}
      </span>
    </div>
  )
}

// popup 內的「標籤 + 值」小區塊
function Metric({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[11px] text-slate-500">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

// 每檔股票的「查看更多」popup：卡片極簡化後，所有數據 + 報告時間軸都收進這裡
function StockDetailDialog({
  item,
  detail,
  detailLoading,
  detailError,
  observation,
  quote,
  onClose,
  onOpenChart,
}: {
  item: SignalArchiveSummaryItem | null
  detail: SignalArchiveDetailResponse | null
  detailLoading: boolean
  detailError: string | null
  /** P4 每日觀察狀態；沒有對應觀察（archive 抓到但 P4 沒建立）則為 undefined。 */
  observation?: SignalObservationItem
  /** 即時報價（開盤期間每 1 分鐘更新）；無資料時 fallback 回 EOD latest_close_price。 */
  quote?: RealtimeQuote
  onClose: () => void
  /** K 線圖改 popup（StockChartDialog）：點擊時疊在本 popup 之上開啟 */
  onOpenChart: (stockId: string, stockName?: string | null) => void
}) {
  const hitPeak =
    item != null && (item.max_positive_return_pct ?? -Infinity) >= PEAK_MILESTONE_PCT
  const hitStopLoss =
    item != null &&
    ((item.return_pct ?? Infinity) <= EARLY_EXIT_THRESHOLD_PCT ||
      (item.max_negative_return_pct ?? Infinity) <= EARLY_EXIT_THRESHOLD_PCT)
  return (
    <Dialog.Root
      open={item !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(96vw,56rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl sm:p-6">
          {item && (
            <div className="flex flex-col gap-4">
              <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 pb-3">
                <div className="min-w-0">
                  <Dialog.Title className="flex flex-wrap items-baseline gap-2 text-lg font-semibold text-slate-100">
                    {item.stock_id} {item.stock_name}
                  </Dialog.Title>
                  <Dialog.Description className="mt-1 text-xs text-slate-400">
                    {item.industry_name ?? "—"}
                    {item.sub_industry ? ` · ${item.sub_industry}` : ""}
                  </Dialog.Description>
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    <SignalTypeChip type={item.latest_signal_type} />
                    <PipelineFlagChip version={item.prompt_version} />
                    <VersionChip version={item.prompt_version} />
                    {hitPeak && <PeakMilestoneChip />}
                    {hitStopLoss && <StopLossWarnChip />}
                    {observation && <ObservationStatusBadge status={observation.status} />}
                  </div>
                  {observation && (
                    <Link
                      href="/signals/observations"
                      className="mt-1 inline-block text-[11px] text-sky-300 hover:text-sky-200"
                    >
                      查看完整追蹤紀錄（推薦論點／每日檢查）→
                    </Link>
                  )}
                </div>
                <Dialog.Close className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700">
                  關閉 ✕
                </Dialog.Close>
              </header>

              <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                <Metric label="股價 / 當日漲跌（即時）">
                  <span className="flex items-baseline gap-2">
                    <span className="font-mono text-sm text-slate-100">
                      {formatPrice(resolveLivePrice(item, quote))}
                    </span>
                    <DailyChangeCell value={resolveLiveChangePct(item, quote)} />
                  </span>
                </Metric>
                <Metric label="報酬率（即時）">
                  <ReturnCell value={resolveLiveReturnPct(item, quote)} />
                </Metric>
                <Metric label="追蹤進度">
                  <span className="text-sm text-slate-200">
                    第 {item.tracking_day_index} 天 · {item.hit_count} 次
                  </span>
                </Metric>
                <Metric label="預測價（保 / 夢）">
                  <PredictionCell
                    conservative={item.conservative_price}
                    dream={item.dream_price}
                  />
                </Metric>
                <Metric label="最大正報酬">
                  <ExtremeReturnCell
                    value={item.max_positive_return_pct}
                    tradeDate={item.max_positive_return_trade_date}
                  />
                </Metric>
                <Metric label="最大負報酬">
                  <ExtremeReturnCell
                    value={item.max_negative_return_pct}
                    tradeDate={item.max_negative_return_trade_date}
                  />
                </Metric>
                <Metric label="首次 / 最近抓到">
                  <span className="font-mono text-xs text-slate-300">
                    {formatShortDate(item.first_seen_date)} → {formatShortDate(item.latest_hit_date)}
                  </span>
                </Metric>
                <Metric label="基準價">
                  <span className="font-mono text-sm text-slate-200">
                    {formatPrice(item.baseline_price)}
                    {item.baseline_trade_date ? ` (${formatShortDate(item.baseline_trade_date)})` : ""}
                  </span>
                </Metric>
                <Metric label="最新評價">
                  <span className="font-mono text-sm text-slate-200">
                    {formatPrice(item.latest_eval_price)}
                    {item.latest_eval_trade_date
                      ? ` (${formatShortDate(item.latest_eval_trade_date)})`
                      : ""}
                  </span>
                </Metric>
              </div>

              <div className="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
                <button
                  type="button"
                  onClick={() => onOpenChart(item.stock_id, item.stock_name)}
                  className="rounded border border-sky-500/50 bg-sky-500/10 px-2 py-1 text-xs text-sky-200 hover:bg-sky-500/20"
                >
                  K線圖
                </button>
                <Link
                  href={`/stocks/${encodeURIComponent(item.stock_id)}`}
                  className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
                >
                  個股頁 →
                </Link>
              </div>

              {detail && detail.stock_id === item.stock_id &&
                (detail.recommendation_thesis || detail.relative_advantage) && (
                  <div className="flex flex-col gap-2 border-t border-slate-800 pt-3 text-sm">
                    {detail.recommendation_thesis && (
                      <p>
                        <span className="mr-1.5 text-xs text-slate-500">推薦論點</span>
                        <span className="text-slate-200">{detail.recommendation_thesis}</span>
                      </p>
                    )}
                    {detail.relative_advantage && (
                      <p>
                        <span className="mr-1.5 text-xs text-slate-500">相對優勢</span>
                        <span className="text-slate-300">{detail.relative_advantage}</span>
                      </p>
                    )}
                  </div>
                )}

              {detail && detail.stock_id === item.stock_id && detail.margin_analysis && (
                <div className="border-t border-slate-800 pt-3">
                  <MarginAnalysisPanel analysis={detail.margin_analysis} stockId={item.stock_id} />
                </div>
              )}

              <div className="flex flex-col gap-3 border-t border-slate-800 pt-3">
                <h4 className="text-sm font-medium text-slate-200">報告時間軸</h4>
                {detailLoading && <p className="text-sm text-slate-400">載入報告中…</p>}
                {detailError && !detailLoading && (
                  <p className="text-sm text-rose-300">{detailError}</p>
                )}
                {!detailLoading &&
                  !detailError &&
                  detail &&
                  detail.stock_id === item.stock_id &&
                  detail.reports.map((report) => (
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
                {!detailLoading &&
                  !detailError &&
                  (!detail || detail.stock_id !== item.stock_id) && (
                    <p className="text-sm text-slate-400">找不到 {item.stock_id} 的報告內容。</p>
                  )}
              </div>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function SignalArchiveContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  // view（分類）+ selectedPeriodStart 由 URL 驅動，瀏覽器 back 自動還原
  const viewParam = searchParams.get("view")
  const view: ArchiveView = isArchiveView(viewParam) ? viewParam : DEFAULT_VIEW
  const periodParam = searchParams.get("period")
  // null = 尚未選擇（等載入完自動跳最新一段）；string = 指定半年起始日
  const selectedPeriodStart: string | null =
    periodParam && PERIOD_PATTERN.test(periodParam) ? periodParam : null

  const setView = useCallback(
    (next: ArchiveView) => {
      const params = new URLSearchParams(searchParams.toString())
      if (next === DEFAULT_VIEW) {
        params.delete("view")
      } else {
        params.set("view", next)
      }
      const queryString = params.toString()
      router.replace(queryString ? `${pathname}?${queryString}` : pathname, { scroll: false })
    },
    [pathname, router, searchParams],
  )

  const setSelectedPeriodStart = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams(searchParams.toString())
      if (next === null) {
        params.delete("period")
      } else {
        params.set("period", next)
      }
      const queryString = params.toString()
      router.replace(queryString ? `${pathname}?${queryString}` : pathname, { scroll: false })
    },
    [pathname, router, searchParams],
  )

  const [summary, setSummary] = useState<SignalArchiveSummaryResponse | null>(null)
  // 「追蹤中」全部股票代號（不受搜尋/展開篩選影響，一次抓好快取），驅動即時報價 polling
  const activeStockIds = useMemo(
    () => (summary?.items ?? []).map((item) => item.stock_id),
    [summary?.items],
  )
  const liveQuotes = useRealtimeQuotes(activeStockIds, REALTIME_INTERVAL_MS)
  const [completedSummary, setCompletedSummary] = useState<SignalArchiveCompletedResponse | null>(null)
  // popup 展開的股票（null = 關閉）；detail 內容依此 fetch
  const [popupStockId, setPopupStockId] = useState<string | null>(null)
  // K 線圖 popup（StockChartDialog）；可從詳情 popup 或紀錄卡片開啟
  const [chartStock, setChartStock] = useState<{ stockId: string; stockName?: string | null } | null>(null)
  const [detail, setDetail] = useState<SignalArchiveDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [completedLoading, setCompletedLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [completedError, setCompletedError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  // 每類預設只顯示前 15 名；「查看更多」展開全部（切換分類時收回）
  const [showAllActive, setShowAllActive] = useState(false)

  // 兩區各自的搜尋框：純前端 filter（不打 backend），by stock_id / stock_name 子字串
  // ?q= 讓其他頁（正式推薦／觀察生命週期）可以直接深連結到某檔股票的追蹤紀錄；
  // 只當初始值讀一次，不雙向同步回 URL（沿用本頁搜尋框刻意不進 URL 的既有決定）。
  const initialQuery = searchParams.get("q") ?? ""
  const [activeSearch, setActiveSearch] = useState(initialQuery)
  const [completedSearch, setCompletedSearch] = useState(initialQuery)

  // 兩區各自可折疊；偏好存 localStorage（追蹤中預設展開、紀錄區預設收合）
  const [activeCollapsed, setActiveCollapsed] = useState(false)
  const [completedCollapsed, setCompletedCollapsed] = useState(true)

  useEffect(() => {
    try {
      if (window.localStorage.getItem(ACTIVE_COLLAPSED_KEY) === "true") setActiveCollapsed(true)
      // 紀錄區預設收合；只有使用者明確展開過（存 "false"）才自動展開
      if (window.localStorage.getItem(COMPLETED_COLLAPSED_KEY) === "false") setCompletedCollapsed(false)
    } catch {
      // ignore
    }
  }, [])

  const toggleActiveCollapsed = useCallback(() => {
    setActiveCollapsed((prev) => {
      const next = !prev
      try {
        window.localStorage.setItem(ACTIVE_COLLAPSED_KEY, String(next))
      } catch {
        // ignore
      }
      return next
    })
  }, [])

  const toggleCompletedCollapsed = useCallback(() => {
    setCompletedCollapsed((prev) => {
      const next = !prev
      try {
        window.localStorage.setItem(COMPLETED_COLLAPSED_KEY, String(next))
      } catch {
        // ignore
      }
      return next
    })
  }, [])

  // 切換分類時收回「查看更多」展開狀態
  useEffect(() => {
    setShowAllActive(false)
  }, [view])

  // 分類排序：純前端。null 報酬率（第一天抓到還沒 baseline）在兩種報酬排序都排最後
  const sortedActiveItems = useMemo(() => {
    const items = summary?.items ?? []
    const copy = [...items]
    switch (view) {
      case "return_desc":
        copy.sort(
          (a, b) =>
            (b.return_pct ?? -Infinity) - (a.return_pct ?? -Infinity) ||
            a.stock_id.localeCompare(b.stock_id),
        )
        break
      case "return_asc":
        copy.sort(
          (a, b) =>
            (a.return_pct ?? Infinity) - (b.return_pct ?? Infinity) ||
            a.stock_id.localeCompare(b.stock_id),
        )
        break
      case "hit_count":
        copy.sort(
          (a, b) =>
            b.hit_count - a.hit_count || a.first_seen_date.localeCompare(b.first_seen_date),
        )
        break
      default:
        // 追蹤日期：最早抓到的在前
        copy.sort(
          (a, b) =>
            a.first_seen_date.localeCompare(b.first_seen_date) ||
            a.stock_id.localeCompare(b.stock_id),
        )
    }
    return copy
  }, [summary?.items, view])

  const filteredActiveItems = useMemo(() => {
    const q = activeSearch.trim().toLowerCase()
    if (!q) return sortedActiveItems
    return sortedActiveItems.filter(
      (item) =>
        item.stock_id.toLowerCase().includes(q) ||
        (item.stock_name ?? "").toLowerCase().includes(q),
    )
  }, [sortedActiveItems, activeSearch])

  // 搜尋中直接搜全部（忽略前 15 限制）；未搜尋時依「查看更多」狀態截斷
  const isSearchingActive = activeSearch.trim() !== ""
  const visibleActiveItems =
    isSearchingActive || showAllActive
      ? filteredActiveItems
      : filteredActiveItems.slice(0, TOP_N)

  const filteredCompletedItems = useMemo(() => {
    if (!completedSummary?.items) return []
    const q = completedSearch.trim().toLowerCase()
    if (!q) return completedSummary.items
    return completedSummary.items.filter(
      (item) =>
        item.stock_id.toLowerCase().includes(q) ||
        (item.stock_name ?? "").toLowerCase().includes(q),
    )
  }, [completedSummary?.items, completedSearch])

  const loadSummary = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // limit: 0 = 不限筆數；分類排序由前端計算，一次抓全部即可
      const data = await fetchSignalArchive({ limit: 0 })
      setSummary(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : "訊號追蹤清單載入失敗")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary])

  // 魚尾與每日觀察（P4）合併為單一入口：archive 卡片額外標示 P4 觀察狀態
  // （觀察中/警戒/已停止觀察）。兩套後端邏輯仍各自獨立運作，這裡只是把 P4 的
  // 狀態當一個徽章顯示；沒有對應觀察（archive 抓到但 P4 沒建立）就不顯示徽章。
  const [observations, setObservations] = useState<SignalObservationItem[]>([])
  useEffect(() => {
    let cancelled = false
    fetchSignalObservations({ limit: 2000 })
      .then((data) => {
        if (!cancelled) setObservations(data.observations)
      })
      .catch(() => {
        // 靜默失敗：P4 狀態徽章是附加資訊，載入失敗不影響 archive 主要功能
      })
    return () => {
      cancelled = true
    }
  }, [])
  const observationByStock = useMemo(() => {
    const latest = new Map<string, SignalObservationItem>()
    observations.forEach((item) => {
      if (!latest.has(item.stock)) latest.set(item.stock, item)
    })
    return latest
  }, [observations])

  useEffect(() => {
    let cancelled = false
    async function run() {
      setCompletedLoading(true)
      setCompletedError(null)
      try {
        const data = await fetchCompletedSignalArchive({
          limit: 0, // 0 = 不限筆數，封存紀錄全部留存
          periodStart: selectedPeriodStart,
        })
        if (!cancelled) {
          setCompletedSummary(data)
          // 首次載入時：若還沒選 period 且後端有 periods → 預設選最新一段，避免一次顯示全部
          if (selectedPeriodStart === null && data.periods.length > 0) {
            setSelectedPeriodStart(data.periods[0].period_start)
          }
        }
      } catch (err) {
        if (!cancelled) {
          setCompletedError(err instanceof Error ? err.message : "追蹤期滿移出紀錄載入失敗")
        }
      } finally {
        if (!cancelled) setCompletedLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
    // setSelectedPeriodStart 的 identity 隨任何 URL 變動改變（含 view chip），
    // 放進 deps 會讓切換分類時 completed 區重新 fetch；刻意只依賴 selectedPeriodStart
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPeriodStart])

  useEffect(() => {
    if (!popupStockId) {
      setDetail(null)
      return
    }
    const stockId = popupStockId

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
  }, [popupStockId])

  const popupItem = useMemo(() => {
    if (!popupStockId) return null
    return summary?.items.find((item) => item.stock_id === popupStockId) ?? null
  }, [summary?.items, popupStockId])

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">抓到的股票觀察總覽（30 個交易日）</h1>
          <p className="mt-1 text-sm text-slate-400">
            追蹤最近 {summary?.retention_trade_days ?? 30} 個交易日內，被納入每日大魚尾清單的股票。
          </p>
          <div className="mt-3 grid gap-2 rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs leading-6 text-slate-400 sm:grid-cols-3">
            <div>
              <p className="text-slate-200">
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-emerald-400 align-middle" />
                <span className="font-medium">領漲（LEADER）</span>
              </p>
              <p className="mt-1 text-slate-400">
                產業裡最早上漲、漲幅領先、資金排名靠前、法人連續買超、量能放大且題材明確的個股。市場帶頭往上的火車頭。
              </p>
            </div>
            <div>
              <p className="text-slate-200">
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-sky-400 align-middle" />
                <span className="font-medium">跟漲（FOLLOWER）</span>
              </p>
              <p className="mt-1 text-slate-400">
                與 LEADER 同產業或同供應鏈、已同步上漲但漲幅不如 LEADER、籌碼仍支持。題材擴散下被資金接力推升的個股。
              </p>
            </div>
            <div>
              <p className="text-slate-200">
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-amber-400 align-middle" />
                <span className="font-medium">補漲（LAGGARD）</span>
              </p>
              <p className="mt-1 text-slate-400">
                同產業 LEADER 已先漲、該股漲幅落後、業務題材高度相關、法人或量能開始轉強、技術出現 early_turn 訊號。落後段位開始補漲的個股。
              </p>
            </div>
          </div>
          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs leading-6 text-slate-400">
            <p>報酬率規則說明：</p>
            <p>第一個交易日抓到：報酬率顯示 `--`，當天不計算。</p>
            <p>第二個交易日：以當日 `(開盤價 + 收盤價) / 2` 建立 baseline 基準價，當天報酬率固定顯示 `0.00%`。</p>
            <p>第三個交易日起：才開始用最新評估日的收盤價，相對這個 baseline 基準價計算報酬率。</p>
          </div>
          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs leading-6 text-slate-400">
            <p>結算與標注規則：</p>
            <p>
              <span className="mr-1 inline-flex items-center rounded border border-rose-500/60 bg-rose-500/15 px-1 py-0 text-[11px] text-rose-200">
                ⚠ 跌破 -30%
              </span>
              期間曾跌破 -30%；若首次跌破後再 3 個交易日仍 &lt; -30%，會
              <span className="mx-1 inline-flex items-center rounded border border-rose-500/60 bg-rose-500/20 px-1 py-0 text-[11px] text-rose-100">
                提前結算（跌破 -30%）
              </span>
              並移到下方「永久紀錄」，不必等追蹤期滿。
            </p>
            <p>
              <span className="mr-1 inline-flex items-center rounded border border-orange-500/60 bg-orange-500/20 px-1 py-0 text-[11px] text-orange-100">
                提前結算（高點回落 30%）
              </span>
              若曾漲過正報酬，又回落到負區且高低差 ≥ 30%，後續 3 個交易日仍未縮小差距至 30% 以內，也會提前結算（停利紀律）。
            </p>
            <p>
              <span className="mr-1 inline-flex items-center rounded border border-amber-400/70 bg-amber-400/15 px-1 py-0 text-[11px] text-amber-200">
                ⭐ +45% 達標
              </span>
              期間曾達 +45% 報酬率里程碑；僅標注、不結算。
            </p>
          </div>
          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs leading-6 text-slate-400">
            <p>
              <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-emerald-400 align-middle" />
              「追蹤中」的股價與報酬率在開盤期間（09:00–13:30）每 1 分鐘自動更新一次，資料來源為證交所盤中即時報價；收盤後或非交易日顯示最後一次的即時或前一交易日收盤數字。移出紀錄區不會即時更新。
            </p>
          </div>
          {summary?.as_of_trade_date && (
            <p className="mt-1 text-xs text-slate-500">最新評估交易日：{summary.as_of_trade_date}</p>
          )}
        </div>
        <Link href="/signals/phase2" className="text-xs text-slate-500 hover:text-sky-300 hover:underline">
          Phase 2 Debug View →
        </Link>
      </header>

      <section className="rounded-xl border border-slate-700 bg-slate-900/40 p-4">
        <button
          type="button"
          onClick={toggleActiveCollapsed}
          className="mb-3 flex w-full items-center gap-2 text-left text-base font-semibold text-slate-100 hover:text-sky-300"
          aria-expanded={!activeCollapsed}
        >
          <span aria-hidden className="text-slate-400">
            {activeCollapsed ? "▸" : "▾"}
          </span>
          <span>追蹤中（30 個交易日）</span>
          {summary && summary.items.length > 0 && (
            <span className="text-xs font-normal text-slate-500">{summary.items.length} 檔</span>
          )}
        </button>
        {!activeCollapsed && (
        <>
        {!loading && !error && summary && summary.items.length > 0 && (
          <>
            {/* 4 個互斥分類：一次只能選一種排序方式，各顯示前 15 名 */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-400">分類：</span>
              {VIEW_OPTIONS.map((option) => {
                const isActive = view === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setView(option.value)}
                    aria-pressed={isActive}
                    className={
                      "rounded border px-2.5 py-1 text-xs font-medium transition " +
                      (isActive
                        ? "border-sky-400 bg-sky-500/20 text-sky-100"
                        : "border-slate-700 bg-slate-800/40 text-slate-300 hover:border-slate-500")
                    }
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
            <div className="mb-3 flex items-center gap-2">
              <input
                type="text"
                value={activeSearch}
                onChange={(e) => setActiveSearch(e.target.value)}
                placeholder="搜尋股票代號或名稱…"
                className="w-56 rounded border border-slate-600 bg-slate-800/40 px-2 py-1 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400 focus:outline-none"
              />
              {activeSearch && (
                <button
                  type="button"
                  onClick={() => setActiveSearch("")}
                  className="text-xs text-slate-400 hover:text-slate-200"
                >
                  清除
                </button>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {isSearchingActive
                  ? `${filteredActiveItems.length} / ${summary.items.length} 檔`
                  : `顯示 ${visibleActiveItems.length} / ${summary.items.length} 檔`}
              </span>
            </div>
          </>
        )}
        {loading && <p className="text-sm text-slate-400">載入中…</p>}
        {error && !loading && <p className="text-sm text-rose-300">{error}</p>}
        {!loading && !error && (summary?.items.length ?? 0) === 0 && (
          <p className="text-sm text-slate-400">目前還沒有可追蹤的訊號紀錄。</p>
        )}
        {!loading && !error && summary && summary.items.length > 0 && (
          <>
            {filteredActiveItems.length === 0 && isSearchingActive && (
              <p className="py-4 text-center text-sm text-slate-400">
                找不到符合「{activeSearch}」的股票
              </p>
            )}
            {/* 極簡卡片：只留代號+名稱 / 即時股價 / 當日漲跌幅；其餘資訊在點開的 popup */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {visibleActiveItems.map((item) => {
                const quote = liveQuotes.get(item.stock_id)
                return (
                  <button
                    key={item.stock_id}
                    type="button"
                    onClick={() => setPopupStockId(item.stock_id)}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-left transition hover:border-sky-500/50 hover:bg-slate-800/60"
                  >
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-sm font-semibold text-slate-100">
                        {item.stock_id} {item.stock_name}
                      </span>
                      <span className="text-[11px] text-slate-500">查看更多 →</span>
                    </div>
                    <div className="flex shrink-0 flex-col items-end">
                      <span className="font-mono text-sm text-slate-100">
                        {formatPrice(resolveLivePrice(item, quote))}
                      </span>
                      <DailyChangeCell value={resolveLiveChangePct(item, quote)} />
                    </div>
                  </button>
                )
              })}
            </div>
            {!isSearchingActive && filteredActiveItems.length > TOP_N && (
              <div className="mt-3 flex justify-center">
                <button
                  type="button"
                  onClick={() => setShowAllActive((prev) => !prev)}
                  className="rounded border border-slate-600 bg-slate-800/50 px-4 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
                >
                  {showAllActive
                    ? `收合（只顯示前 ${TOP_N} 名）`
                    : `查看更多（共 ${filteredActiveItems.length} 檔）`}
                </button>
              </div>
            )}
          </>
        )}
        </>
        )}
      </section>

      <section className="rounded-xl border border-slate-700 bg-slate-900/40 p-4">
        <header className="mb-4 flex flex-col gap-3">
          <div>
            <button
              type="button"
              onClick={toggleCompletedCollapsed}
              className="flex w-full items-center gap-2 text-left text-lg font-semibold text-slate-100 hover:text-sky-300"
              aria-expanded={!completedCollapsed}
            >
              <span aria-hidden className="text-slate-400">
                {completedCollapsed ? "▸" : "▾"}
              </span>
              <span>追蹤期滿移出紀錄</span>
            </button>
            <p className="mt-1 text-sm text-slate-400">
              當股票完成一個追蹤 cycle 後（追蹤 30 個交易日期滿 / 跌破 -30% / 從高點回落 30%），會在這裡留下封存摘要；
              依移出時間切半年一張表（2026/05 起算），同一檔之後若重新被抓到會以新的首次抓到日新增一列。
            </p>
          </div>
          {!completedCollapsed && completedSummary && completedSummary.periods.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-400">半年區間：</span>
              {completedSummary.periods.map((p) => {
                const isActive = selectedPeriodStart === p.period_start
                return (
                  <button
                    key={p.period_start}
                    type="button"
                    onClick={() => setSelectedPeriodStart(p.period_start)}
                    className={
                      "rounded border px-2.5 py-1 text-xs font-medium transition " +
                      (isActive
                        ? "border-sky-400 bg-sky-500/20 text-sky-100"
                        : "border-slate-700 bg-slate-800/40 text-slate-300 hover:border-slate-500")
                    }
                  >
                    {formatPeriodLabel(p)}（{p.count}）
                  </button>
                )
              })}
            </div>
          )}
          {!completedCollapsed && completedSummary && completedSummary.items.length > 0 && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={completedSearch}
                onChange={(e) => setCompletedSearch(e.target.value)}
                placeholder="搜尋股票代號或名稱…"
                className="w-56 rounded border border-slate-600 bg-slate-800/40 px-2 py-1 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400 focus:outline-none"
              />
              {completedSearch && (
                <button
                  type="button"
                  onClick={() => setCompletedSearch("")}
                  className="text-xs text-slate-400 hover:text-slate-200"
                >
                  清除
                </button>
              )}
              <span className="ml-auto text-xs text-slate-500">
                {filteredCompletedItems.length} / {completedSummary.items.length} 檔
              </span>
            </div>
          )}
        </header>
        {!completedCollapsed && (
        <>
        {completedLoading && <p className="text-sm text-slate-400">載入中…</p>}
        {completedError && !completedLoading && (
          <p className="text-sm text-rose-300">{completedError}</p>
        )}
        {!completedLoading && !completedError && (completedSummary?.items.length ?? 0) === 0 && (
          <p className="text-sm text-slate-400">此區間暫無資料</p>
        )}
        {!completedLoading && !completedError && completedSummary && completedSummary.items.length > 0 && (
          <>
            {filteredCompletedItems.length === 0 && completedSearch.trim() !== "" && (
              <p className="py-4 text-center text-sm text-slate-400">
                找不到符合「{completedSearch}」的股票
              </p>
            )}
            {/* 響應式卡片網格：手機單欄 / 桌機兩欄 */}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {filteredCompletedItems.map((item) => {
                const hitPeak =
                  (item.max_positive_return_pct ?? -Infinity) >= PEAK_MILESTONE_PCT
                return (
                  <article
                    key={`${item.stock_id}-${item.first_seen_date}`}
                    className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3"
                  >
                    <header className="flex flex-wrap items-start justify-between gap-2">
                      <div className="flex min-w-0 flex-col">
                        <span className="text-sm font-semibold text-slate-100">
                          {item.stock_id} {item.stock_name}
                        </span>
                        <span className="text-xs text-slate-500">
                          {item.industry_name ?? "—"}
                          {item.sub_industry ? ` · ${item.sub_industry}` : ""}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-1">
                        <SignalTypeChip type={item.latest_signal_type} />
                        <PipelineFlagChip version={item.prompt_version} />
                        <VersionChip version={item.prompt_version} />
                      </div>
                    </header>
                    {hitPeak && (
                      <div className="flex flex-wrap gap-1">
                        <PeakMilestoneChip />
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
                      <Metric label="首次抓到">
                        <span className="font-mono text-xs text-slate-300">
                          {item.first_seen_date}
                        </span>
                      </Metric>
                      <Metric label="抓到次數">
                        <span className="text-sm text-slate-200">{item.hit_count} 次</span>
                      </Metric>
                      <Metric label="預測價（保 / 夢）">
                        <PredictionCell
                          conservative={item.conservative_price}
                          dream={item.dream_price}
                        />
                      </Metric>
                      <Metric label="最大正報酬">
                        <ExtremeReturnCell
                          value={item.max_positive_return_pct}
                          tradeDate={item.max_positive_return_trade_date}
                        />
                      </Metric>
                      <Metric label="最大負報酬">
                        <ExtremeReturnCell
                          value={item.max_negative_return_pct}
                          tradeDate={item.max_negative_return_trade_date}
                        />
                      </Metric>
                    </div>
                    <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 pt-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <ClosureReasonChip reason={item.closure_reason} />
                        <span className="font-mono text-[10px] text-slate-500">
                          {item.completed_trade_date}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => setChartStock({ stockId: item.stock_id, stockName: item.stock_name })}
                        className="rounded border border-sky-500/50 bg-sky-500/10 px-2 py-1 text-xs text-sky-200 hover:bg-sky-500/20"
                      >
                        K線圖
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </>
        )}
        </>
        )}
      </section>

      <StockDetailDialog
        item={popupItem}
        detail={detail}
        detailLoading={detailLoading}
        detailError={detailError}
        observation={popupItem ? observationByStock.get(popupItem.stock_id) : undefined}
        quote={popupItem ? liveQuotes.get(popupItem.stock_id) : undefined}
        onClose={() => setPopupStockId(null)}
        onOpenChart={(stockId, stockName) => setChartStock({ stockId, stockName })}
      />

      <StockChartDialog
        stockId={chartStock?.stockId ?? null}
        stockName={chartStock?.stockName}
        onClose={() => setChartStock(null)}
      />
    </main>
  )
}

export default function SignalArchivePage() {
  return (
    <Suspense>
      <SignalArchiveContent />
    </Suspense>
  )
}
