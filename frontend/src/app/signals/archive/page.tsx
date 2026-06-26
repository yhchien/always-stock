"use client"

import Link from "next/link"
import { Fragment, Suspense, useCallback, useEffect, useMemo, useState } from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import {
  fetchCompletedSignalArchive,
  fetchSignalArchive,
  fetchSignalArchiveDetail,
  type SignalArchiveCompletedPeriod,
  type SignalArchiveCompletedResponse,
  type SignalArchiveDetailResponse,
  type SignalArchiveSortBy,
  type SignalArchiveSummaryResponse,
  type SignalClosureReason,
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

const DEFAULT_SORT_BY: SignalArchiveSortBy = "tracking_days_desc"
const SORT_VALUES: ReadonlySet<SignalArchiveSortBy> = new Set(SORT_OPTIONS.map((o) => o.value))

function isSortBy(value: string | null): value is SignalArchiveSortBy {
  return value !== null && SORT_VALUES.has(value as SignalArchiveSortBy)
}

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

function VersionChip({ version }: { version?: string | null }) {
  // 產生這檔的 prompt 版本（v1 / v2 …）；舊資料無 → 視為 v1
  const label = version || "v1"
  return (
    <span
      className="inline-flex items-center rounded border border-slate-600 bg-slate-700/40 px-1.5 py-0.5 text-[11px] font-medium text-slate-300"
      title="產生這檔的 prompt 版本"
    >
      {label}
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

// 凍結第一欄（股票）的 sticky 樣式：用比表格深的背景區分「凍結欄」視覺
// hover / selected row 不影響第一欄，視覺一致（spreadsheet freeze panes 慣例）
// md: 前綴 → 僅在 ≥ 768px 套用，手機直立 (< 768px) 第一欄正常 scroll，
// 避免「股票欄」把窄視口塞滿後完全無法看右側內容。
const STICKY_FIRST_COL_HEAD =
  "md:sticky md:left-0 md:z-20 md:bg-slate-950 md:border-r md:border-slate-700"
const STICKY_FIRST_COL_CELL =
  "md:sticky md:left-0 md:z-10 md:bg-slate-950 md:border-r md:border-slate-700"

function SignalArchiveContent() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  // sortBy + selectedPeriodStart 由 URL 驅動，瀏覽器 back 自動還原
  const sortByParam = searchParams.get("sort_by")
  const sortBy: SignalArchiveSortBy = isSortBy(sortByParam) ? sortByParam : DEFAULT_SORT_BY
  const periodParam = searchParams.get("period")
  // null = 尚未選擇（等載入完自動跳最新一段）；string = 指定半年起始日
  const selectedPeriodStart: string | null =
    periodParam && PERIOD_PATTERN.test(periodParam) ? periodParam : null

  const setSortBy = useCallback(
    (next: SignalArchiveSortBy) => {
      const params = new URLSearchParams(searchParams.toString())
      if (next === DEFAULT_SORT_BY) {
        params.delete("sort_by")
      } else {
        params.set("sort_by", next)
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
  const [completedSummary, setCompletedSummary] = useState<SignalArchiveCompletedResponse | null>(null)
  const [selectedStockId, setSelectedStockId] = useState<string | null>(null)
  const [detail, setDetail] = useState<SignalArchiveDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [completedLoading, setCompletedLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [completedError, setCompletedError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  // 兩張表各自的搜尋框：純前端 filter（不打 backend），by stock_id / stock_name 子字串
  const [activeSearch, setActiveSearch] = useState("")
  const [completedSearch, setCompletedSearch] = useState("")

  // 兩張表各自可折疊；偏好存 localStorage（預設展開）
  const [activeCollapsed, setActiveCollapsed] = useState(false)
  const [completedCollapsed, setCompletedCollapsed] = useState(false)

  useEffect(() => {
    try {
      if (window.localStorage.getItem(ACTIVE_COLLAPSED_KEY) === "true") setActiveCollapsed(true)
      if (window.localStorage.getItem(COMPLETED_COLLAPSED_KEY) === "true") setCompletedCollapsed(true)
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

  const filteredActiveItems = useMemo(() => {
    if (!summary?.items) return []
    const q = activeSearch.trim().toLowerCase()
    if (!q) return summary.items
    return summary.items.filter(
      (item) =>
        item.stock_id.toLowerCase().includes(q) ||
        (item.stock_name ?? "").toLowerCase().includes(q),
    )
  }, [summary?.items, activeSearch])

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
      // limit: 0 = 不限筆數，魚尾追蹤期內有多少就顯示多少
      const data = await fetchSignalArchive({ sort_by: sortBy, limit: 0 })
      setSummary(data)
      // 預設不展開任何一檔（inline expand UX：使用者主動點才展開）
    } catch (err) {
      setError(err instanceof Error ? err.message : "訊號追蹤清單載入失敗")
    } finally {
      setLoading(false)
    }
  }, [sortBy])

  // Toggle：再點同一檔 → 收合；點別檔 → 切換
  const toggleExpand = useCallback((stockId: string) => {
    setSelectedStockId((prev) => (prev === stockId ? null : stockId))
  }, [])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary])

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
  }, [selectedPeriodStart])

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
              {filteredActiveItems.length} / {summary.items.length} 檔
            </span>
          </div>
        )}
        {loading && <p className="text-sm text-slate-400">載入中…</p>}
        {error && !loading && <p className="text-sm text-rose-300">{error}</p>}
        {!loading && !error && (summary?.items.length ?? 0) === 0 && (
          <p className="text-sm text-slate-400">目前還沒有可追蹤的訊號紀錄。</p>
        )}
        {!loading && !error && summary && summary.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className={`text-slate-300 ${STICKY_FIRST_COL_HEAD}`}>股票</TableHead>
                <TableHead className="text-slate-300">首次抓到</TableHead>
                <TableHead className="text-slate-300">最近抓到</TableHead>
                <TableHead className="text-slate-300">追蹤第幾天</TableHead>
                <TableHead className="text-slate-300">命中次數</TableHead>
                <TableHead className="text-slate-300">最新類型</TableHead>
                <TableHead className="text-slate-300">版本</TableHead>
                <TableHead className="text-slate-300">報酬率</TableHead>
                <TableHead className="text-slate-300">預測價</TableHead>
                <TableHead className="text-slate-300">最大正報酬</TableHead>
                <TableHead className="text-slate-300">最大負報酬</TableHead>
                <TableHead className="text-slate-300">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredActiveItems.length === 0 && activeSearch.trim() !== "" && (
                <TableRow className="border-slate-800">
                  <TableCell colSpan={12} className="text-center text-sm text-slate-400">
                    找不到符合「{activeSearch}」的股票
                  </TableCell>
                </TableRow>
              )}
              {filteredActiveItems.map((item) => {
                const active = item.stock_id === selectedStockId
                const hitPeak =
                  (item.max_positive_return_pct ?? -Infinity) >= PEAK_MILESTONE_PCT
                const hitStopLoss =
                  (item.return_pct ?? Infinity) <= EARLY_EXIT_THRESHOLD_PCT ||
                  (item.max_negative_return_pct ?? Infinity) <= EARLY_EXIT_THRESHOLD_PCT
                return (
                  <Fragment key={item.stock_id}>
                    <TableRow
                      className={`border-slate-800 ${active ? "bg-slate-800/40" : ""}`}
                    >
                      <TableCell className={`align-top ${STICKY_FIRST_COL_CELL}`}>
                        <div className="flex flex-col">
                          <button
                            type="button"
                            onClick={() => toggleExpand(item.stock_id)}
                            className="w-fit text-left text-sm font-semibold text-slate-100 hover:text-sky-300"
                          >
                            {item.stock_id} {item.stock_name}
                          </button>
                          <span className="text-xs text-slate-500">
                            {item.industry_name ?? "—"}
                            {item.sub_industry ? ` · ${item.sub_industry}` : ""}
                          </span>
                          {(hitPeak || hitStopLoss) && (
                            <div className="mt-1 flex flex-wrap gap-1">
                              {hitPeak && <PeakMilestoneChip />}
                              {hitStopLoss && <StopLossWarnChip />}
                            </div>
                          )}
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
                        <VersionChip version={item.prompt_version} />
                      </TableCell>
                      <TableCell>
                        <ReturnCell value={item.return_pct} />
                      </TableCell>
                      <TableCell>
                        <PredictionCell
                          conservative={item.conservative_price}
                          dream={item.dream_price}
                        />
                      </TableCell>
                      <TableCell>
                        <ExtremeReturnCell
                          value={item.max_positive_return_pct}
                          tradeDate={item.max_positive_return_trade_date}
                        />
                      </TableCell>
                      <TableCell>
                        <ExtremeReturnCell
                          value={item.max_negative_return_pct}
                          tradeDate={item.max_negative_return_trade_date}
                        />
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
                            onClick={() => toggleExpand(item.stock_id)}
                            className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
                          >
                            {active ? "收合報告" : "點我看更多分析結果"}
                          </button>
                        </div>
                      </TableCell>
                    </TableRow>
                    {active && (
                      <TableRow className="border-slate-800 bg-slate-900/30 hover:bg-slate-900/30">
                        <TableCell colSpan={12} className="p-0">
                          <div className="border-t border-slate-700 px-4 py-4">
                            {detailLoading && (
                              <p className="text-sm text-slate-400">載入報告中…</p>
                            )}
                            {detailError && !detailLoading && (
                              <p className="text-sm text-rose-300">{detailError}</p>
                            )}
                            {!detailLoading && !detailError && detail && detail.stock_id === item.stock_id && (
                              <div className="flex flex-col gap-4">
                                <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 pb-3">
                                  <div>
                                    <h3 className="text-base font-semibold text-slate-100">
                                      {detail.stock_id} {detail.stock_name}
                                    </h3>
                                    <p className="mt-1 text-xs text-slate-400">
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
                                    <span>最大正報酬</span>
                                    <span className="text-slate-200">
                                      <ExtremeReturnCell
                                        value={detail.max_positive_return_pct}
                                        tradeDate={detail.max_positive_return_trade_date}
                                      />
                                    </span>
                                    <span>最大負報酬</span>
                                    <span className="text-slate-200">
                                      <ExtremeReturnCell
                                        value={detail.max_negative_return_pct}
                                        tradeDate={detail.max_negative_return_trade_date}
                                      />
                                    </span>
                                  </div>
                                </header>

                                <div className="flex flex-col gap-3">
                                  <h4 className="text-sm font-medium text-slate-200">報告時間軸</h4>
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
                            {!detailLoading && !detailError && (!detail || detail.stock_id !== item.stock_id) && (
                              <p className="text-sm text-slate-400">
                                找不到 {item.stock_id} 的報告內容。
                              </p>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
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
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className={`text-slate-300 ${STICKY_FIRST_COL_HEAD}`}>股票 / 產業</TableHead>
                <TableHead className="text-slate-300">首次抓到</TableHead>
                <TableHead className="text-slate-300">抓到次數</TableHead>
                <TableHead className="text-slate-300">類型</TableHead>
                <TableHead className="text-slate-300">版本</TableHead>
                <TableHead className="text-slate-300">預測價</TableHead>
                <TableHead className="text-slate-300">最大正報酬</TableHead>
                <TableHead className="text-slate-300">最大負報酬</TableHead>
                <TableHead className="text-slate-300">移出原因</TableHead>
                <TableHead className="text-slate-300">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredCompletedItems.length === 0 && completedSearch.trim() !== "" && (
                <TableRow className="border-slate-800">
                  <TableCell colSpan={10} className="text-center text-sm text-slate-400">
                    找不到符合「{completedSearch}」的股票
                  </TableCell>
                </TableRow>
              )}
              {filteredCompletedItems.map((item) => {
                const hitPeak =
                  (item.max_positive_return_pct ?? -Infinity) >= PEAK_MILESTONE_PCT
                return (
                  <TableRow
                    key={`${item.stock_id}-${item.first_seen_date}`}
                    className="border-slate-800"
                  >
                    <TableCell className={`align-top ${STICKY_FIRST_COL_CELL}`}>
                      <div className="flex flex-col">
                        <span className="text-sm font-semibold text-slate-100">
                          {item.stock_id} {item.stock_name}
                        </span>
                        <span className="text-xs text-slate-500">
                          {item.industry_name ?? "—"}
                          {item.sub_industry ? ` · ${item.sub_industry}` : ""}
                        </span>
                        {hitPeak && (
                          <div className="mt-1">
                            <PeakMilestoneChip />
                          </div>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-slate-300">
                      {item.first_seen_date}
                    </TableCell>
                    <TableCell className="text-sm text-slate-200">
                      {item.hit_count} 次
                    </TableCell>
                    <TableCell>
                      <SignalTypeChip type={item.latest_signal_type} />
                    </TableCell>
                    <TableCell>
                      <VersionChip version={item.prompt_version} />
                    </TableCell>
                    <TableCell>
                      <PredictionCell
                        conservative={item.conservative_price}
                        dream={item.dream_price}
                      />
                    </TableCell>
                    <TableCell>
                      <ExtremeReturnCell
                        value={item.max_positive_return_pct}
                        tradeDate={item.max_positive_return_trade_date}
                      />
                    </TableCell>
                    <TableCell>
                      <ExtremeReturnCell
                        value={item.max_negative_return_pct}
                        tradeDate={item.max_negative_return_trade_date}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <ClosureReasonChip reason={item.closure_reason} />
                        <span className="font-mono text-[10px] text-slate-500">
                          {item.completed_trade_date}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/stocks/${encodeURIComponent(item.stock_id)}`}
                        className="rounded border border-sky-500/50 bg-sky-500/10 px-2 py-1 text-xs text-sky-200 hover:bg-sky-500/20"
                      >
                        K線圖
                      </Link>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
        </>
        )}
      </section>

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
