"use client"

import { useEffect, useMemo, useState } from "react"
import { Dialog } from "@base-ui/react/dialog"

import Link from "next/link"

import ObservationStatusBadge from "@/components/ObservationStatusBadge"
import SelectionReasonBadge from "@/components/SelectionReasonBadge"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import SignalFunnel from "@/components/SignalFunnel"
import {
  fetchSignalArchive,
  fetchSignalObservations,
  fetchSignalRecommendations,
  type SignalArchiveSummaryItem,
  type SignalDecisionType,
  type SignalObservationItem,
  type SignalObservationStatus,
  type SignalRecommendationResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import { selectionCompleteness } from "@/lib/signalP6Presentation"
import { useSignalsViewMode } from "@/lib/signalsViewMode"

const REASON_SECTIONS: Array<[keyof SignalWatchlistItem, string]> = [
  ["theme_reason", "題材"],
  ["capital_reason", "資金"],
  ["chip_reason", "籌碼"],
  ["margin_reason", "融券"],
  ["technical_reason", "技術"],
]

// 沿用 /signals/archive 頁的格式與紅漲綠跌配色慣例，讓兩頁看到的報酬率語意一致。
function formatPct(value: number | null | undefined): string {
  if (value == null) return "--"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function formatPrice(value: number | null | undefined): string {
  if (value == null) return "--"
  return value.toFixed(2)
}

function PctText({ value, size = "sm" }: { value: number | null | undefined; size?: "sm" | "xs" }) {
  const textSize = size === "sm" ? "text-sm" : "text-xs"
  if (value == null) return <span className={`font-mono ${textSize} text-slate-500`}>--</span>
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-300"
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : ""
  return (
    <span className={`font-mono ${textSize} font-semibold ${color}`}>
      {arrow ? `${arrow} ` : ""}
      {formatPct(value)}
    </span>
  )
}

// 領漲／跟漲／補漲 色階，對齊首頁 DailySignalsPanel 的 SignalEmotionCard tone（紅/金黃/藍，刻意不用綠）
const TYPE_CHIP_CLASSES: Record<SignalDecisionType, string> = {
  LEADER: "border-rose-500/50 bg-rose-500/15 text-rose-200",
  FOLLOWER: "border-amber-500/50 bg-amber-500/15 text-amber-200",
  LAGGARD: "border-sky-500/50 bg-sky-500/15 text-sky-200",
}
const TYPE_LABELS: Record<SignalDecisionType, string> = {
  LEADER: "領漲",
  FOLLOWER: "跟漲",
  LAGGARD: "補漲",
}

function TypeChip({ type }: { type: SignalDecisionType | null | undefined }) {
  if (!type || !(type in TYPE_CHIP_CLASSES)) return null
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${TYPE_CHIP_CLASSES[type]}`}
    >
      {TYPE_LABELS[type]}
    </span>
  )
}

// 2026-08-11：觀察/警戒小徽章不明顯，改對整張卡片上色（比照首頁卡片配色語言）；
// 停止觀察只在正式推薦頁面是極端邊界情況（P4 已改成 P3 推薦當天就立即重開觀察，見
// sync_recommendations），保留較低調的樣式即可，不需要跟警戒一樣搶眼。
function observationCardTone(status: SignalObservationStatus | undefined): string {
  if (status === "CAUTION") return "border-amber-500/60 bg-amber-950/30"
  if (status === "STOPPED") return "border-slate-600 bg-slate-800/40"
  return "border-slate-700/60 bg-slate-900/55"
}

/** 比照 /signals/archive 卡片語意的追蹤資訊（首次抓到日期／收盤價／報酬率／預期價格）；正式版與工程版都顯示。 */
function TrackingSummary({ archive }: { archive: SignalArchiveSummaryItem | undefined }) {
  if (!archive) {
    return <p className="mt-2 text-xs text-slate-500">今日新入選，尚無追蹤數據（明天起顯示抓到日期與報酬率）。</p>
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs">
      <span className="flex items-baseline gap-1.5">
        <span className="text-slate-500">首次抓到</span>
        <span className="font-mono text-slate-200">{archive.first_seen_date}</span>
        <span className="text-slate-600">（第 {archive.tracking_day_index} 個交易日）</span>
      </span>
      <span className="flex items-baseline gap-1.5">
        <span className="text-slate-500">收盤</span>
        <span className="font-mono text-slate-200">{formatPrice(archive.latest_close_price)}</span>
        <PctText value={archive.daily_change_pct} size="xs" />
      </span>
      <span className="flex items-baseline gap-1.5">
        <span className="text-slate-500">報酬率</span>
        <PctText value={archive.return_pct} size="xs" />
      </span>
      {(archive.conservative_price != null || archive.dream_price != null) && (
        <span className="flex items-baseline gap-1.5">
          <span className="text-slate-500">預期價</span>
          <span className="font-mono text-slate-300">
            保守 {formatPrice(archive.conservative_price)}／夢想 {formatPrice(archive.dream_price)}
          </span>
        </span>
      )}
    </div>
  )
}

/** 工程版專用：完整推薦依據（含 thesis/relative_advantage 重複顯示）＋版本 footer，維持原樣不動。 */
function RecommendationDetail({ item }: { item: SignalWatchlistItem }) {
  return (
    <details className="mt-3 border-t border-slate-800 pt-3">
      <summary className="cursor-pointer text-xs text-sky-300">查看完整推薦依據與版本</summary>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <section className="rounded-lg border border-slate-800 p-3 md:col-span-2">
          <h4 className="text-xs font-semibold text-slate-300">推薦論點／同日相對優勢</h4>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            {item.recommendation_thesis ?? "歷史快照未保存推薦論點"}
          </p>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            {item.relative_advantage ?? "歷史快照未保存同日相對優勢"}
          </p>
        </section>
        {REASON_SECTIONS.map(([key, label]) => {
          const bullets = item[key] as string[] | null | undefined
          return (
            <section key={String(key)} className="rounded-lg border border-slate-800 p-3">
              <h4 className="text-xs font-semibold text-slate-300">{label}</h4>
              {bullets?.length ? (
                <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                  {bullets.map((bullet, index) => <li key={index}>• {bullet}</li>)}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-slate-600">此快照無結構化段落。</p>
              )}
            </section>
          )
        })}
        <p className="text-[11px] text-slate-600 md:col-span-2">
          Selection {item.selection_version ?? "—"}・Prompt {item.prompt_version ?? "—"}・
          Score {item.signal_metrics?.momentum_score_version ?? "—"}
        </p>
      </div>
    </details>
  )
}

/** 正式版專用：完整分析 popup，只留同日相對優勢＋5 段中文理由，不含版本字串。 */
function RecommendationDialog({
  item,
  onClose,
}: {
  item: SignalWatchlistItem | null
  onClose: () => void
}) {
  return (
    <Dialog.Root open={item !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Popup className="fixed inset-0 z-50 w-full overflow-y-auto bg-slate-900 p-4 shadow-2xl sm:inset-auto sm:left-1/2 sm:top-1/2 sm:max-h-[85vh] sm:w-[min(92vw,42rem)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:border sm:border-slate-700 sm:p-5">
          {item && (
            <div className="space-y-4">
              <header className="flex items-start justify-between gap-3">
                <div>
                  <Dialog.Title className="text-lg font-semibold text-slate-100">
                    {item.stock} {item.name}
                  </Dialog.Title>
                  <Dialog.Description className="mt-1 text-xs text-slate-500">
                    完整推薦分析
                  </Dialog.Description>
                </div>
                <Dialog.Close className="rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700">
                  關閉 ✕
                </Dialog.Close>
              </header>

              <section className="rounded-lg border border-slate-800 p-3">
                <h4 className="text-xs font-semibold text-slate-300">同日相對優勢</h4>
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  {item.relative_advantage ?? "此快照未保存同日相對優勢"}
                </p>
              </section>

              <div className="grid gap-3 sm:grid-cols-2">
                {REASON_SECTIONS.map(([key, label]) => {
                  const bullets = item[key] as string[] | null | undefined
                  return (
                    <section key={String(key)} className="rounded-lg border border-slate-800 p-3">
                      <h4 className="text-xs font-semibold text-slate-300">{label}</h4>
                      {bullets?.length ? (
                        <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">
                          {bullets.map((bullet, index) => <li key={index}>• {bullet}</li>)}
                        </ul>
                      ) : (
                        <p className="mt-2 text-xs text-slate-600">此項目暫無資料。</p>
                      )}
                    </section>
                  )
                })}
              </div>

              <Link
                href={`/signals/archive?q=${encodeURIComponent(item.stock)}`}
                className="inline-block text-xs text-sky-300 hover:text-sky-200"
              >
                查看完整追蹤紀錄（報酬率／最大正負報酬／歷史紀錄）→
              </Link>
            </div>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function TechnicalFailures({ items }: { items: SignalRecommendationResponse["data"]["technical_failures"] }) {
  if (!items?.length) return null
  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
      <h2 className="text-sm font-semibold text-amber-100">技術失敗</h2>
      <p className="mt-1 text-xs text-amber-200/70">
        這是研究或處理階段未完成，不是市場風險判斷，也不是 REMOVE。
      </p>
      <ul className="mt-3 space-y-2">
        {items.map((item, index) => (
          <li key={`${item.stock ?? item.stock_id}-${index}`} className="text-xs text-slate-400">
            <span className="font-mono text-slate-300">{item.stock ?? item.stock_id ?? "—"}</span>
            {" · "}{item.stage ?? item.processing_status ?? item.status ?? "UNKNOWN_STAGE"}
            {" · "}{item.error_code ?? item.error_summary ?? "可於資料修復後重跑"}
          </li>
        ))}
      </ul>
    </section>
  )
}

const SORT_OPTIONS = [
  { value: "rank", label: "推薦排序" },
  { value: "date_desc", label: "抓到日期（近到遠）" },
  { value: "return_desc", label: "報酬率（高到低）" },
] as const
type SortBy = (typeof SORT_OPTIONS)[number]["value"]

export default function SignalRecommendationsPage() {
  const { isEngineering } = useSignalsViewMode()
  const [snapshot, setSnapshot] = useState<SignalRecommendationResponse | null>(null)
  const [observations, setObservations] = useState<SignalObservationItem[]>([])
  const [archiveItems, setArchiveItems] = useState<SignalArchiveSummaryItem[]>([])
  const [date, setDate] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogItem, setDialogItem] = useState<SignalWatchlistItem | null>(null)
  const [sortBy, setSortBy] = useState<SortBy>("rank")

  useEffect(() => {
    const queryDate =
      typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("date") ?? ""
    const requested = date || queryDate || undefined
    const controller = new AbortController()
    fetchSignalRecommendations(requested, { signal: controller.signal })
      .then(async (payload) => {
        setSnapshot(payload)
        if (payload) {
          const [observationList, archiveList] = await Promise.all([
            fetchSignalObservations(
              { asOfDate: payload.snapshot_date, limit: 2000 },
              { signal: controller.signal },
            ),
            // 比照 /signals/archive 頁的 return_pct／預期價格語意，同一批 pipeline 寫入
            // signal_watch_hits，這裡直接借用來取代「追蹤中：自 X 起」那種不直覺的文字。
            fetchSignalArchive(undefined, { signal: controller.signal }),
          ])
          setObservations(observationList.observations)
          setArchiveItems(archiveList.items)
          setDate(payload.snapshot_date)
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "正式推薦快照載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [date])

  const observationByStock = useMemo(
    () => {
      const latest = new Map<string, SignalObservationItem>()
      observations.forEach((item) => {
        if (!latest.has(item.stock)) latest.set(item.stock, item)
      })
      return latest
    },
    [observations],
  )
  const archiveByStock = useMemo(
    () => new Map(archiveItems.map((item) => [item.stock_id, item])),
    [archiveItems],
  )
  const processing = snapshot?.data.summary.processing_summary
  const selection = snapshot?.data.summary.selection_summary
  const completeness = selectionCompleteness(
    processing?.global_selection_status,
    selection?.selection_complete,
  )
  const recommendations = [...(snapshot?.data.watchlist ?? [])]
    .filter(
      (item) =>
        item.selection_status === "RECOMMEND" ||
        item.decision === "RECOMMEND",
    )
    .sort((a, b) => {
      if (sortBy === "date_desc") {
        const dateA = archiveByStock.get(a.stock)?.first_seen_date ?? ""
        const dateB = archiveByStock.get(b.stock)?.first_seen_date ?? ""
        if (dateA !== dateB) return dateA < dateB ? 1 : -1
      } else if (sortBy === "return_desc") {
        const returnA = archiveByStock.get(a.stock)?.return_pct
        const returnB = archiveByStock.get(b.stock)?.return_pct
        if (returnA == null && returnB == null) {
          // 都沒有報酬率資料時繼續往下比排序，不要 fall through 到相等判斷卡住
        } else if (returnA == null) {
          return 1
        } else if (returnB == null) {
          return -1
        } else if (returnA !== returnB) {
          return returnB - returnA
        }
      }
      return (
        (a.recommendation_rank ?? Number.MAX_SAFE_INTEGER) -
        (b.recommendation_rank ?? Number.MAX_SAFE_INTEGER)
      )
    })
  const notSelected = snapshot?.data.not_selected ?? []
  const removed = snapshot?.data.removed ?? []
  const funnel = [
    ["raw", "Raw Union", processing?.raw_union_count ?? snapshot?.data.candidate_pool_size ?? 0, "A/B/C/D 原始聯集"],
    ["p2", "Phase 2 Eligible", processing?.llm_eligible_count ?? selection?.phase2_eligible_count ?? 0, "通過 P2 eligibility"],
    ["research", "Research", processing?.research_completed_count ?? selection?.research_completed_count ?? 0, "研究成功"],
    ["assessment", "Assessment Eligible", processing?.global_selection_eligible_count ?? selection?.global_eligible_count ?? 0, "assessment 後可比較"],
    ["removed", "True Removed", selection?.veto_removed_count ?? removed.length, "具真實 veto 的明確移除"],
    ["global", "Global Eligible", selection?.global_eligible_count ?? notSelected.length + recommendations.length, "進入一次完整全體比較"],
    ["recommended", "Recommended", recommendations.length, "今日正式推薦"],
    ["not-selected", "Not Selected", notSelected.length, "候選有效但未列入今日推薦"],
    ["technical", "Technical", snapshot?.data.technical_failures?.length ?? 0, "技術處理失敗"],
    ["unprocessed", "Unprocessed", processing?.unprocessed_count ?? 0, "尚未完成處理"],
  ].map(([key, label, value, help]) => ({ key: String(key), label: String(label), value: Number(value), help: String(help) }))

  function changeDate(next: string | null) {
    if (!next || next === date) return
    window.history.replaceState(null, "", `/signals/recommendations?date=${next}`)
    setLoading(true)
    setError(null)
    setDate(next)
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 py-6 text-slate-100">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          {isEngineering && (
            <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">P3 Formal Recommendations</p>
          )}
          <h1 className="mt-1 text-2xl font-semibold">正式推薦與候選比較</h1>
          <p className="mt-1 text-sm text-slate-400">
            {isEngineering
              ? "主清單只顯示 RECOMMEND；P3 今日決策與 P4 既有觀察狀態分開。"
              : "系統每天重新比較全部候選股票；同一檔可能連續多天勝出、持續留在名單上，不是每天都會整批換新。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => changeDate(snapshot?.navigation.previous_date ?? null)} disabled={!snapshot?.navigation.previous_date} className="rounded border border-slate-700 px-3 py-1.5 text-xs disabled:opacity-30">前一交易日</button>
          <input aria-label="訊號日期" type="date" value={date} onChange={(event) => changeDate(event.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
          <button type="button" onClick={() => changeDate(snapshot?.navigation.next_date ?? null)} disabled={!snapshot?.navigation.next_date} className="rounded border border-slate-700 px-3 py-1.5 text-xs disabled:opacity-30">下一交易日</button>
        </div>
      </header>

      {loading && <p className="text-sm text-slate-500">正在載入正式推薦…</p>}
      {error && <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>}
      {!loading && !error && !snapshot && <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">此日期沒有訊號快照。</p>}

      {snapshot && (
        <div className="space-y-5">
          {isEngineering && (
            <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">處理 Funnel</h2>
                <span className="rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400">{completeness}</span>
                <span className="text-[11px] text-slate-600">快照 {snapshot.snapshot_date}</span>
              </div>
              <SignalFunnel steps={funnel} />
            </section>
          )}

          {completeness === "GLOBAL_SELECTION_FAILED" && (
            <p className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
              本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單。
            </p>
          )}

          <section>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-semibold">目前正式推薦（{recommendations.length}）</h2>
              <div className="flex flex-wrap gap-1" aria-label="排序方式">
                {SORT_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setSortBy(option.value)}
                    className={`rounded border px-2.5 py-1 text-xs ${
                      sortBy === option.value
                        ? "border-sky-500/50 bg-sky-500/10 text-sky-100"
                        : "border-slate-700 text-slate-400 hover:border-slate-500"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <p className="mb-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs leading-5 text-slate-400">
              <span className="text-slate-300">觀察中</span>／<span className="text-amber-300">警戒</span>是系統每天覆核推薦論點的結果：
              <span className="text-slate-300">觀察中</span> = 動能結構、資金參與等關鍵條件目前仍然成立；
              <span className="text-amber-300">警戒</span> = 部分關鍵條件今天檢查後開始不成立，但還沒到判定「論點失效」的程度，值得留意但不是賣出訊號。
            </p>
            {!recommendations.length && completeness === "COMPLETE" && <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">此日期沒有正式推薦；這是完整比較後的合法 0 推薦結果。</p>}
            <div className="grid gap-3 lg:grid-cols-2">
              {recommendations.map((item) => {
                const observation = observationByStock.get(item.stock)
                const archive = archiveByStock.get(item.stock)
                return (
                  <article
                    key={item.stock}
                    className={`rounded-xl border p-4 ${observationCardTone(observation?.status)}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      {sortBy === "rank" && (
                        <span className="font-mono text-lg text-sky-200">#{item.recommendation_rank ?? "—"}</span>
                      )}
                      <span className="font-mono text-sm text-slate-300">{item.stock}</span>
                      <strong className="text-sm">{item.name}</strong>
                      <TypeChip type={item.type} />
                      {item.asset_type !== "COMMON_STOCK" && (
                        <SignalAssetBadge assetType={item.asset_type} />
                      )}
                      {observation && <ObservationStatusBadge status={observation.status} />}
                    </div>
                    {isEngineering ? (
                      <p className="mt-2 text-xs text-slate-500">
                        {item.industry ?? "—"}・{item.sub_industry ?? "—"}・Theme {item.theme_cluster ?? "—"}・Backend Rank {item.backend_priority_rank ?? "—"}
                      </p>
                    ) : (
                      <p className="mt-2 text-xs text-slate-500">
                        {item.industry ?? "—"}・{item.sub_industry ?? "—"}
                      </p>
                    )}
                    <p className="mt-3 text-sm leading-6 text-slate-300">{item.recommendation_thesis ?? item.reason ?? "歷史快照未保存推薦論點"}</p>
                    {isEngineering && (
                      <p className="mt-2 text-xs leading-5 text-slate-500">同日相對優勢：{item.relative_advantage ?? "—"}</p>
                    )}
                    <TrackingSummary archive={archive} />
                    {isEngineering && observation && (
                      <p className="mt-2 text-xs text-slate-500">P4 Episode {observation.episode_id}・首次推薦 {observation.started_signal_date}</p>
                    )}
                    {isEngineering ? (
                      <RecommendationDetail item={item} />
                    ) : (
                      <button
                        type="button"
                        onClick={() => setDialogItem(item)}
                        className="mt-3 text-xs text-sky-300 hover:text-sky-200"
                      >
                        查看完整分析 →
                      </button>
                    )}
                  </article>
                )
              })}
            </div>
          </section>

          {isEngineering && (
            <>
              <section className="rounded-xl border border-slate-700/60 bg-slate-900/35 p-4">
                <h2 className="text-sm font-semibold text-slate-200">未列入今日推薦（{notSelected.length}）</h2>
                <p className="mt-1 text-xs text-slate-500">候選仍有效；這是中性的同日相對選擇，不代表永久負面或停止追蹤。</p>
                <div className="mt-3 space-y-2">
                  {notSelected.map((item) => {
                    const observation = observationByStock.get(item.stock)
                    return (
                      <article key={item.stock} className="rounded-lg border border-slate-800 bg-slate-950/35 p-3">
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="font-mono text-slate-300">{item.stock}</span>
                          <span>{item.name}</span>
                          <SignalAssetBadge assetType={item.asset_type} />
                          <SelectionReasonBadge code={item.selection_reason_code} />
                          <span className="text-xs text-slate-600">Backend Rank {item.backend_priority_rank ?? "—"}</span>
                        </div>
                        <p className="mt-2 text-xs leading-5 text-slate-400">{item.selection_reason ?? "歷史快照未保存未入選原因"}</p>
                        {item.overlap_with?.length ? <p className="mt-1 text-[11px] text-slate-600">論點重疊：{item.overlap_with.join("、")}・{item.overlap_reason}</p> : null}
                        {observation && <p className="mt-2 text-xs text-sky-200">今日未列入推薦，但既有觀察仍繼續（P4：{observation.status}）。</p>}
                      </article>
                    )
                  })}
                </div>
              </section>

              <section className="rounded-xl border border-slate-700/60 bg-slate-900/35 p-4">
                <h2 className="text-sm font-semibold text-slate-200">明確移除（{removed.length}）</h2>
                <p className="mt-1 text-xs text-slate-500">此區只顯示 backend 驗證成立的 true veto。</p>
                <div className="mt-3 space-y-2">
                  {removed.map((item) => {
                    const observation = observationByStock.get(item.stock)
                    return (
                      <article key={item.stock} className="rounded-lg border border-slate-800 p-3 text-xs text-slate-400">
                        <span className="font-mono text-slate-300">{item.stock}</span> {item.name}・{item.veto_reason ?? "VALIDATED_VETO"}・{item.short_reason ?? item.reason}
                        {observation && <p className="mt-2 text-sky-200">今日候選評估為 REMOVE；既有觀察是否停止，仍以 P4 Review 為準。</p>}
                      </article>
                    )
                  })}
                </div>
              </section>
              <TechnicalFailures items={snapshot.data.technical_failures} />
            </>
          )}
        </div>
      )}

      <RecommendationDialog item={dialogItem} onClose={() => setDialogItem(null)} />
    </main>
  )
}
