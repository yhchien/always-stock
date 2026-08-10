"use client"

import { useEffect, useState } from "react"
import Link from "next/link"

import ObservationStatusBadge, {
  ObservationLifecycleNotice,
  observationDecisionLabel,
  observationStatusLabel,
} from "@/components/ObservationStatusBadge"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import {
  fetchSignalObservationDetail,
  fetchSignalObservations,
  fetchSignalTrackingSummary,
  type SignalObservationDetail,
  type SignalObservationItem,
  type SignalObservationStatus,
  type SignalTrackingSummary,
} from "@/lib/api"
import { useSignalsViewMode } from "@/lib/signalsViewMode"

type StatusFilter = "ALL" | SignalObservationStatus

const FILTERS: Array<{ value: StatusFilter; label: string }> = [
  { value: "ALL", label: "全部" },
  { value: "OBSERVING", label: "觀察中" },
  { value: "CAUTION", label: "警戒" },
  { value: "STOPPED", label: "已停止觀察" },
]

const DIMENSION_LABELS: Record<string, string> = {
  MOMENTUM_STRUCTURE: "動能結構",
  PARTICIPATION: "市場參與",
  CATALYST_THESIS: "催化／投資論點",
  MARKET_CONTEXT: "市場環境",
  PERSISTENCE_WARNING: "持續性警示",
  DATA_QUALITY: "資料品質",
}

function dateText(value: string | null | undefined): string {
  return value || "—"
}

function EvidenceBlock({
  title,
  value,
}: {
  title: string
  value: Record<string, unknown> | null | undefined
}) {
  return (
    <section className="rounded-lg border border-slate-700/60 bg-slate-950/40 p-3">
      <h3 className="mb-2 text-xs font-semibold text-slate-300">{title}</h3>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">
        {JSON.stringify(value ?? {}, null, 2)}
      </pre>
    </section>
  )
}

export default function SignalObservationsPage() {
  const { isEngineering } = useSignalsViewMode()
  const [filter, setFilter] = useState<StatusFilter>("ALL")
  const [search, setSearch] = useState("")
  const [assetType, setAssetType] = useState("")
  const [episodeSearch, setEpisodeSearch] = useState("")
  const [items, setItems] = useState<SignalObservationItem[]>([])
  const [summary, setSummary] = useState<SignalTrackingSummary | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<SignalObservationDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const visibleItems = items.filter((item) => {
    const normalizedSearch = search.trim().toLowerCase()
    return (
      (!normalizedSearch ||
        item.stock.toLowerCase().includes(normalizedSearch) ||
        item.name.toLowerCase().includes(normalizedSearch)) &&
      (!assetType || item.asset_type === assetType) &&
      (!episodeSearch ||
        item.episode_id.toLowerCase().includes(episodeSearch.trim().toLowerCase()))
    )
  })

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchSignalObservations(
        {
          status: filter === "ALL" ? undefined : filter,
          limit: 1000,
        },
        { signal: controller.signal },
      ),
      fetchSignalTrackingSummary(undefined, { signal: controller.signal }),
    ])
      .then(([list, tracking]) => {
        setItems(list.observations)
        setSummary(tracking.tracking_summary)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "觀察清單載入失敗")
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [filter])

  useEffect(() => {
    if (selectedId == null) return
    const controller = new AbortController()
    fetchSignalObservationDetail(selectedId, { signal: controller.signal })
      .then((payload) => setDetail(payload))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "觀察詳情載入失敗")
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false)
      })
    return () => controller.abort()
  }, [selectedId])

  const summaryCards = [
    ["檢查日", summary?.review_date ?? "—"],
    ["繼續", summary?.continue_count],
    ["警戒", summary?.caution_count],
    ["停止觀察", summary?.stopped_count],
    ...(isEngineering
      ? [
          ["檢查失敗", summary?.review_failed_count],
          ["政策衝突", summary?.conflict_count],
        ]
      : []),
  ]

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 py-6 text-slate-100">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          {isEngineering && (
            <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">
              P4 Daily Observation Lifecycle
            </p>
          )}
          <h1 className="mt-1 text-xl font-semibold">每日觀察檢查</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">
            {isEngineering
              ? "這裡追蹤既有推薦 thesis 是否仍值得觀察，與「今日正式推薦」是兩個獨立狀態。"
              : "這裡追蹤已推薦股票目前的觀察狀態，跟「今日正式推薦」是分開看的。"}
          </p>
        </div>
        <Link
          href="/signals/recommendations"
          className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-slate-500"
        >
          正式推薦
        </Link>
      </header>

      {summary && (
        <section className="mb-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {summaryCards.map(([label, value]) => (
            <div
              key={label}
              className="rounded-lg border border-slate-700/60 bg-slate-900/60 p-3"
            >
              <div className="text-[11px] text-slate-500">{label}</div>
              <div className="mt-1 font-mono text-sm text-slate-200">{value}</div>
            </div>
          ))}
        </section>
      )}

      {isEngineering && summary && !summary.review_complete && (
        <p className="mb-4 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          本次追蹤檢查未完整完成；失敗股票維持上一個有效狀態。
        </p>
      )}
      {isEngineering && summary && summary.conflict_count > 0 && (
        <p className="mb-4 rounded border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-sm text-violet-100">
          今日有 {summary.conflict_count} 筆 TRACKING_SELECTION_CONFLICT：P3
          正式推薦與 P4 停止觀察證據同日並存，兩筆紀錄皆保留，需人工檢查。
        </p>
      )}
      {error && (
        <p className="mb-4 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          {error}
        </p>
      )}

      <div className="mb-4 flex flex-wrap gap-2" aria-label="觀察狀態篩選">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => {
              if (option.value === filter) return
              setLoading(true)
              setError(null)
              setFilter(option.value)
              setSelectedId(null)
              setDetail(null)
            }}
            className={`rounded border px-3 py-1.5 text-xs ${
              filter === option.value
                ? "border-sky-500/50 bg-sky-500/10 text-sky-100"
                : "border-slate-700 text-slate-400 hover:border-slate-500"
            }`}
          >
            {option.label}
          </button>
        ))}
        <input
          aria-label="搜尋股票"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="股票代碼／名稱"
          className="ml-auto rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
        />
        {isEngineering && (
          <>
            <select
              aria-label="Asset Type"
              value={assetType}
              onChange={(event) => setAssetType(event.target.value)}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
            >
              <option value="">全部商品類型</option>
              <option value="COMMON_STOCK">普通股</option>
              <option value="FINANCIAL">金融股</option>
              <option value="ETF">ETF</option>
            </select>
            <input
              aria-label="Episode ID"
              value={episodeSearch}
              onChange={(event) => setEpisodeSearch(event.target.value)}
              placeholder="Episode ID"
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
            />
          </>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <section className="space-y-2">
          {loading && <p className="text-sm text-slate-500">載入觀察清單…</p>}
          {!loading && visibleItems.length === 0 && (
            <p className="rounded border border-slate-800 p-4 text-sm text-slate-500">
              目前沒有符合此狀態的 observation。
            </p>
          )}
          {visibleItems.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                if (item.id === selectedId) return
                setDetailLoading(true)
                setError(null)
                setSelectedId(item.id)
              }}
              className={`w-full rounded-lg border p-3 text-left transition-colors ${
                selectedId === item.id
                  ? "border-sky-500/50 bg-sky-500/5"
                  : "border-slate-700/60 bg-slate-900/50 hover:border-slate-600"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-slate-300">{item.stock}</span>
                <span className="text-sm font-medium">{item.name}</span>
                <SignalAssetBadge assetType={item.asset_type} />
                <ObservationStatusBadge status={item.status} />
                {item.recommended_today && (
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">
                    今日推薦：RECOMMEND
                  </span>
                )}
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-500">
                <div>首次推薦：{dateText(item.started_signal_date)}</div>
                <div>最新檢查：{dateText(item.last_review_date)}</div>
                <div>連續警戒：{item.consecutive_caution_count}</div>
                <div>停止日期：{dateText(item.stopped_at?.slice(0, 10))}</div>
              </dl>
              <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">
                {item.latest_reason ?? "尚無 Review"}
              </p>
              {item.latest_review_technical_status && (
                <p className="mt-2 text-xs text-amber-200">
                  本次追蹤檢查未完成，維持上一個有效狀態。
                </p>
              )}
            </button>
          ))}
        </section>

        <section className="min-h-60 rounded-xl border border-slate-700/60 bg-slate-900/40 p-4">
          {detailLoading && <p className="text-sm text-slate-500">載入詳情…</p>}
          {!detailLoading && !detail && (
            <p className="text-sm text-slate-500">選擇一檔股票查看 Review timeline。</p>
          )}
          {!detailLoading && detail && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-semibold">
                  {detail.stock} {detail.name}
                </h2>
                <ObservationStatusBadge status={detail.status} />
                {detail.recommended_today && (
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-200">
                    今日推薦：RECOMMEND
                  </span>
                )}
              </div>

              <ObservationLifecycleNotice
                status={detail.status}
                technicalStatus={detail.latest_review_technical_status}
              />

              {detail.status === "STOPPED" && (
                <Link
                  href={`/signals/archive?q=${encodeURIComponent(detail.stock)}`}
                  className="inline-block text-xs text-sky-300 hover:text-sky-200"
                >
                  查看完整追蹤紀錄（報酬率／最大正負報酬）→
                </Link>
              )}

              <section>
                <h3 className="text-xs font-semibold text-slate-300">當初推薦理由</h3>
                <p className="mt-1 text-sm leading-6 text-slate-400">
                  {String(
                    detail.initial_observation.recommendation_thesis ??
                      "歷史 observation 未保存完整推薦 thesis",
                  )}
                </p>
              </section>

              {detail.stop_reason && (
                <section>
                  <h3 className="text-xs font-semibold text-slate-300">停止原因</h3>
                  <p className="mt-1 text-sm text-slate-400">
                    {isEngineering && `${detail.stop_reason_code} · `}
                    {detail.stop_reason}
                  </p>
                </section>
              )}

              {isEngineering && (
                <div className="grid gap-3 md:grid-cols-2">
                  <EvidenceBlock
                    title="最新 Backend 證據"
                    value={
                      (detail.latest_snapshot.backend_evidence as Record<
                        string,
                        unknown
                      >) ?? {}
                    }
                  />
                  <EvidenceBlock
                    title="最新外部 Thesis 判斷"
                    value={
                      (detail.latest_snapshot.external_assessment as Record<
                        string,
                        unknown
                      >) ?? {}
                    }
                  />
                </div>
              )}

              <section>
                <h3 className="mb-2 text-xs font-semibold text-slate-300">
                  歷史 Review Timeline
                </h3>
                <ol className="space-y-3 border-l border-slate-700 pl-4">
                  {detail.review_timeline.map((review) => (
                    <li key={review.review_date} className="relative">
                      <span className="absolute -left-[1.18rem] top-1.5 h-2 w-2 rounded-full bg-slate-500" />
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-mono text-slate-400">
                          {review.review_date}
                        </span>
                        <span className="text-slate-200">
                          {observationDecisionLabel(review.decision)}
                        </span>
                        {review.previous_status && (
                          <span className="text-slate-600">
                            前態 {review.previous_status}
                          </span>
                        )}
                      </div>
                      {review.technical_status ? (
                        <p className="mt-1 text-xs text-amber-200">
                          本次追蹤檢查未完成，維持上一個有效狀態。
                        </p>
                      ) : (
                        <>
                          <p className="mt-1 text-xs leading-5 text-slate-400">
                            {review.reason}
                          </p>
                          {review.caution_dimensions.length > 0 && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              警戒維度：
                              {review.caution_dimensions
                                .map((value) => DIMENSION_LABELS[value] ?? value)
                                .join("、")}
                            </p>
                          )}
                          {review.failed_dimensions.length > 0 && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              失效維度：
                              {review.failed_dimensions
                                .map((value) => DIMENSION_LABELS[value] ?? value)
                                .join("、")}
                            </p>
                          )}
                          {isEngineering && (
                            <p className="mt-1 text-[10px] text-slate-600">
                              {review.tracking_prompt_version}・
                              {review.tracking_state_machine_version}
                            </p>
                          )}
                        </>
                      )}
                    </li>
                  ))}
                </ol>
              </section>
              {(detail.episode_history?.length ?? 0) > 0 && (
                <section>
                  <h3 className="mb-2 text-xs font-semibold text-slate-300">
                    追蹤紀錄
                  </h3>
                  <div className="space-y-2">
                    {detail.episode_history?.map((episode, index) => (
                      <article
                        key={episode.episode_id}
                        className={`rounded-lg border p-3 text-xs ${
                          episode.is_current
                            ? "border-sky-500/40 bg-sky-500/5"
                            : "border-slate-800"
                        }`}
                      >
                        <p className="text-slate-300">
                          第 {index + 1} 次・{observationStatusLabel(episode.status)}・
                          {episode.started_signal_date} →{" "}
                          {episode.stopped_at?.slice(0, 10) ?? "進行中"}
                        </p>
                        <p className="mt-1 text-slate-500">
                          {episode.initial_thesis ?? "歷史 episode 未保存完整 thesis"}
                        </p>
                        {episode.stop_reason && (
                          <p className="mt-1 text-slate-600">
                            {isEngineering && `${episode.stop_reason_code}・`}
                            {episode.stop_reason}
                          </p>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
