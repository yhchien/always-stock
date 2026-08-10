"use client"

import { useCallback, useEffect, useState } from "react"

import OutcomeMetricCard from "@/components/OutcomeMetricCard"
import { OutcomeDistributionChart, OutcomeTimeseriesChart } from "@/components/OutcomeCharts"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import {
  fetchSignalObservationAnalytics,
  fetchSignalOutcomeItems,
  fetchSignalOutcomeReviewQueue,
  fetchSignalOutcomeSummary,
  fetchSignalOutcomeTimeseries,
  signalOutcomeCsvUrl,
  updateSignalOutcomeReview,
  type SignalObservationAnalyticsResponse,
  type SignalOutcomeFilters,
  type SignalOutcomeItemsResponse,
  type SignalOutcomeReviewQueueResponse,
  type SignalOutcomeSummary,
  type SignalOutcomeTimeseriesResponse,
} from "@/lib/api"
import {
  OUTCOME_LABELS,
  P3_DECISION_LABELS,
  REVIEW_CATEGORY_LABELS,
  formatPercent,
  formatRate,
} from "@/lib/signalP6Presentation"
import { useSignalsViewMode } from "@/lib/signalsViewMode"

type VersionFilterKey =
  | "selection_version"
  | "momentum_score_version"
  | "research_prompt_version"
  | "assessment_prompt_version"
  | "global_selector_version"
  | "reason_prompt_version"
  | "tracking_prompt_version"
  | "tracking_state_machine_version"

export default function SignalOutcomesPage() {
  const { isEngineering } = useSignalsViewMode()
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [promptFamily, setPromptFamily] = useState("")
  const [outcomeLabel, setOutcomeLabel] = useState("")
  const [decision, setDecision] = useState("")
  const [versionKey, setVersionKey] = useState<VersionFilterKey>("selection_version")
  const [versionValue, setVersionValue] = useState("")
  const [page, setPage] = useState(1)
  const [summary, setSummary] = useState<SignalOutcomeSummary | null>(null)
  const [timeseries, setTimeseries] = useState<SignalOutcomeTimeseriesResponse | null>(null)
  const [items, setItems] = useState<SignalOutcomeItemsResponse | null>(null)
  const [observations, setObservations] = useState<SignalObservationAnalyticsResponse | null>(null)
  const [queue, setQueue] = useState<SignalOutcomeReviewQueueResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const filters: SignalOutcomeFilters = {
    start_date: startDate || undefined,
    end_date: endDate || undefined,
    prompt_family: promptFamily || undefined,
    outcome_label: (outcomeLabel || undefined) as SignalOutcomeFilters["outcome_label"],
    p3_decision: (decision || undefined) as SignalOutcomeFilters["p3_decision"],
    ...(versionValue ? { [versionKey]: versionValue } : {}),
  }

  const load = useCallback(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    Promise.all([
      fetchSignalOutcomeSummary(filters, { signal: controller.signal }),
      fetchSignalOutcomeTimeseries(filters, { signal: controller.signal }),
      fetchSignalOutcomeItems({ ...filters, page, page_size: 25 }, { signal: controller.signal }),
      fetchSignalObservationAnalytics({ signal: controller.signal }),
      fetchSignalOutcomeReviewQueue({ page: 1, page_size: 20 }, { signal: controller.signal }),
    ])
      .then(([summaryPayload, seriesPayload, itemPayload, observationPayload, queuePayload]) => {
        setSummary(summaryPayload)
        setTimeseries(seriesPayload)
        setItems(itemPayload)
        setObservations(observationPayload)
        setQueue(queuePayload)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "結果分析載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
    // The primitive values are intentionally the dependencies; recreating the
    // filter object must not trigger an otherwise identical request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startDate, endDate, promptFamily, outcomeLabel, decision, versionKey, versionValue, page])

  useEffect(() => load(), [load])

  async function toggleReviewed(id: number, reviewed: boolean) {
    try {
      await updateSignalOutcomeReview(id, {
        review_status: reviewed ? "REVIEWED" : "UNREVIEWED",
      })
      const refreshed = await fetchSignalOutcomeReviewQueue({ page: 1, page_size: 20 })
      setQueue(refreshed)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "人工檢查註記更新失敗")
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 py-6 text-slate-100">
      <header className="mb-5">
        {isEngineering && (
          <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">Post-decision Outcome Analytics</p>
        )}
        <h1 className="mt-1 text-2xl font-semibold">結果分析</h1>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
          {isEngineering
            ? "本頁是事後品質追蹤。主要指標只統計已滿 10 個後續交易日且價格完整的正式推薦；Outcome 不會回饋候選資格、排序、Global Selector 或 P4 state machine。"
            : "這裡看的是過去推薦的股票，10 個交易日後表現如何。這些結果不會影響今天的推薦名單。"}
        </p>
      </header>

      <section className="mb-5 flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/35 p-3">
        <input aria-label="開始日期" type="date" value={startDate} onChange={(event) => { setPage(1); setStartDate(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
        <input aria-label="結束日期" type="date" value={endDate} onChange={(event) => { setPage(1); setEndDate(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
        {isEngineering && (
          <>
            <select aria-label="Prompt Family" value={promptFamily} onChange={(event) => { setPage(1); setPromptFamily(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
              <option value="">全部 Prompt Family</option>
              {(summary?.versions.prompt_family ?? []).map((version) => <option key={version}>{version}</option>)}
              {!summary?.versions.prompt_family?.includes("v7") && <option value="v7">v7</option>}
              {!summary?.versions.prompt_family?.includes("legacy_split") && <option value="legacy_split">legacy_split</option>}
            </select>
            <select aria-label="Outcome Label" value={outcomeLabel} onChange={(event) => { setPage(1); setOutcomeLabel(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
              <option value="">全部 Outcome</option>
              {Object.entries(OUTCOME_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <select aria-label="P3 Decision" value={decision} onChange={(event) => { setPage(1); setDecision(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
              <option value="">全部 P3 決策</option>
              <option value="RECOMMEND">正式推薦</option>
              <option value="NOT_SELECTED">未列入今日推薦</option>
            </select>
            <select
              aria-label="版本維度"
              value={versionKey}
              onChange={(event) => {
                setPage(1)
                setVersionValue("")
                setVersionKey(event.target.value as VersionFilterKey)
              }}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
            >
              <option value="selection_version">Selection Version</option>
              <option value="momentum_score_version">Momentum Score</option>
              <option value="research_prompt_version">Research Prompt</option>
              <option value="assessment_prompt_version">Assessment Prompt</option>
              <option value="global_selector_version">Global Selector</option>
              <option value="reason_prompt_version">Reason Prompt</option>
              <option value="tracking_prompt_version">Tracking Prompt</option>
              <option value="tracking_state_machine_version">Tracking State Machine</option>
            </select>
            <select
              aria-label="版本值"
              value={versionValue}
              onChange={(event) => {
                setPage(1)
                setVersionValue(event.target.value)
              }}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
            >
              <option value="">全部版本</option>
              {(summary?.versions[versionKey] ?? []).map((version) => (
                <option key={version}>{version}</option>
              ))}
            </select>
            <a href={signalOutcomeCsvUrl(filters)} className="ml-auto rounded border border-sky-500/40 px-3 py-1.5 text-xs text-sky-200">匯出 CSV</a>
          </>
        )}
      </section>

      {loading && <p className="text-sm text-slate-500">正在載入結果分析…</p>}
      {error && <p className="mb-4 rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>}

      {summary && (
        <>
          {summary.sample.missing > 0 && <p className="mb-4 rounded border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100">部分結果資料不完整，本頁比例只使用價格資料完整的成熟樣本。</p>}
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {isEngineering && (
              <OutcomeMetricCard label="Phase2 / Global / 推薦（平均每日）" value={`${summary.selection.average_phase2_eligible_count.toFixed(1)} / ${summary.selection.average_global_eligible_count.toFixed(1)} / ${summary.selection.average_recommended_count.toFixed(1)}`} detail={`正式推薦總數 ${summary.sample.total}・成熟 ${summary.sample.matured}・未成熟 ${summary.sample.immature}・缺資料 ${summary.sample.missing}`} />
            )}
            <OutcomeMetricCard label="10 日後達標率" value={formatRate(summary.recommendation.acceptable_rate)} detail={`Winner ${summary.recommendation.winner_count} + 中性 ${summary.recommendation.neutral_count}／成熟 ${summary.sample.matured}・目標 80%`} status={summary.sample.matured ? summary.recommendation.acceptable_target_met ? "met" : "not-met" : "neutral"} />
            <OutcomeMetricCard label="成熟樣本數" value={`${summary.sample.matured} / ${summary.sample.total}`} detail={`未成熟 ${summary.sample.immature}・缺資料 ${summary.sample.missing}`} />
            {isEngineering && (
              <>
                <OutcomeMetricCard label="Winner > Neutral" value={summary.recommendation.winner_greater_than_neutral ? "符合" : "未符合"} detail={`Winner ${summary.recommendation.winner_count}（${formatRate(summary.recommendation.winner_rate)}）・中性 ${summary.recommendation.neutral_count}（${formatRate(summary.recommendation.neutral_rate)}）`} status={summary.sample.matured ? summary.recommendation.winner_greater_than_neutral ? "met" : "not-met" : "neutral"} />
                <OutcomeMetricCard label="Big Loser / Winner Recall" value={`${formatRate(summary.recommendation.big_loser_rate)} / ${formatRate(summary.selection.winner_recall)}`} detail={`漏選 Winner ${summary.selection.not_selected_winner_count}・壓縮率 ${formatRate(summary.selection.average_compression_rate)}`} />
              </>
            )}
          </section>
          <div className="mt-5 grid gap-4 xl:grid-cols-2">
            <OutcomeDistributionChart summary={summary} />
            <OutcomeTimeseriesChart items={timeseries?.items ?? []} />
          </div>
          {isEngineering && (
          <>
          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold">Backend Rank 分布與 Rank Override</h2>
            <p className="mt-1 text-xs text-slate-500">
              用於人工檢查 Global Selector 推翻 backend rank 的頻率；Dashboard
              不會據此建立 cutoff。
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-[34rem] text-left text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="px-2 py-1">樣本</th>
                    {["1-10", "11-25", "26-50", "51+"].map((bucket) => (
                      <th key={bucket} className="px-2 py-1">{bucket}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {[
                    ["recommend", "正式推薦"],
                    ["not_selected", "未列入今日推薦"],
                    ["winner", "Winner"],
                  ].map(([key, label]) => (
                    <tr key={key} className="border-t border-slate-800">
                      <td className="px-2 py-2">{label}</td>
                      {["1-10", "11-25", "26-50", "51+"].map((bucket) => (
                        <td key={bucket} className="px-2 py-2 font-mono">
                          {summary.selection.backend_rank_distribution[key]?.[bucket] ?? 0}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Rank Override {summary.selection.rank_override_count} 筆；其中大幅負報酬結果{" "}
              {summary.selection.rank_override_big_loser_count} 筆。
            </p>
          </section>
          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">NOT_SELECTED 後成為 Winner</h2>
                <p className="mt-1 text-xs text-slate-500">
                  {summary.selection.not_selected_winner_count} 筆・成熟未入選 Winner Rate{" "}
                  {formatRate(summary.selection.not_selected_winner_rate)}。只供人工檢查漏選類型。
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setPage(1)
                  setDecision("NOT_SELECTED")
                  setOutcomeLabel("WINNER")
                }}
                className="rounded border border-sky-500/40 px-3 py-1.5 text-xs text-sky-200"
              >
                篩出股票明細
              </button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(summary.selection.not_selected_winner_by_reason).map(
                ([reason, count]) => (
                  <span key={reason} className="rounded-full border border-slate-700 px-2 py-1 text-xs text-slate-400">
                    {reason}・{count}
                  </span>
                ),
              )}
              {!Object.keys(summary.selection.not_selected_winner_by_reason).length && (
                <span className="text-xs text-slate-600">目前沒有漏選 Winner。</span>
              )}
            </div>
          </section>

          <section className="mt-5">
            <h2 className="mb-3 text-base font-semibold">Observation Outcome</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <OutcomeMetricCard label="Caution Recovery" value={formatRate(observations?.summary.caution_recovery_rate)} detail="CAUTION 後在 STOP 前恢復 CONTINUE" />
              <OutcomeMetricCard label="過早停止候選" value={observations?.summary.premature_stop_candidate_count ?? 0} detail="停止後 10 交易日內相對停止日收盤 +10%" />
              <OutcomeMetricCard label="Stop Before Big Loss" value={formatRate(observations?.summary.stop_before_big_loss_rate)} detail="初始推薦價觸及 -10% 前已 STOP" />
              <OutcomeMetricCard label="平均停止交易日" value={(observations?.summary.average_trading_days_to_stop ?? 0).toFixed(1)} detail="使用交易日，不是日曆日" />
              <OutcomeMetricCard label="停止後新 Episode" value={observations?.summary.rerecommended_episode_count ?? 0} detail="不代表原停止決策錯誤" />
            </div>
            {(observations?.summary.premature_stop_candidate_count ?? 0) > 0 && <p className="mt-3 text-xs text-slate-500">此標記只表示停止後重新走強，需人工檢查；不代表當時停止決策一定錯誤。</p>}
          </section>
          </>
          )}

          {isEngineering && (
          <>
          <section className="mt-5 overflow-hidden rounded-xl border border-slate-800">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-900/50 p-3">
              <div>
                <h2 className="text-sm font-semibold">Outcome 明細</h2>
                <p className="text-[11px] text-slate-500">共 {items?.total ?? 0} 筆・第 {items?.page ?? 1}/{Math.max(items?.pages ?? 1, 1)} 頁</p>
              </div>
              <div className="flex gap-2">
                <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">上一頁</button>
                <button type="button" disabled={page >= (items?.pages ?? 1)} onClick={() => setPage((value) => value + 1)} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">下一頁</button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-xs">
                <thead className="bg-slate-950/70 text-slate-500"><tr>{["日期", "股票", "資產", "P3", "Ranks", "理由／Theme", "P4", "Day10", "Outcome", "Versions"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2">{label}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-800">
                  {(items?.items ?? []).map((item) => (
                    <tr key={item.id} className="text-slate-400">
                      <td className="whitespace-nowrap px-3 py-2 font-mono">{item.signal_date}</td>
                      <td className="whitespace-nowrap px-3 py-2"><span className="font-mono text-slate-200">{item.stock}</span> {item.name}</td>
                      <td className="px-3 py-2"><SignalAssetBadge assetType={item.asset_type} /></td>
                      <td className="whitespace-nowrap px-3 py-2">{P3_DECISION_LABELS[item.p3_decision]}</td>
                      <td className="whitespace-nowrap px-3 py-2">B {item.backend_priority_rank ?? "—"} / R {item.recommendation_rank ?? "—"}</td>
                      <td className="max-w-56 px-3 py-2">{item.selection_reason_code ?? item.theme_cluster ?? "—"}</td>
                      <td className="whitespace-nowrap px-3 py-2">{item.observation_status ?? "—"}</td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono">{formatPercent(item.day10_return)}</td>
                      <td className="whitespace-nowrap px-3 py-2">{OUTCOME_LABELS[item.outcome_label]}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-[10px]">{item.prompt_family_version ?? "—"} / {item.selection_version ?? "—"} / {item.outcome_definition_version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!items?.items.length && <p className="p-4 text-sm text-slate-500">此篩選區間沒有 Outcome 明細。</p>}
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold">人工檢查清單</h2>
            <p className="mt-1 text-xs text-slate-500">人工註記只保存 review status/note，不會修改 Outcome Label 或原始決策。</p>
            <div className="mt-3 space-y-2">
              {(queue?.items ?? []).map((item) => (
                <article key={item.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 p-3 text-xs">
                  <span className="font-mono text-slate-300">{item.stock}</span>
                  <span className="text-slate-400">{REVIEW_CATEGORY_LABELS[item.category] ?? item.category}</span>
                  <span className="text-slate-600">{item.signal_date ?? `Observation ${item.observation_id}`}</span>
                  <button type="button" onClick={() => toggleReviewed(item.id, item.review_status !== "REVIEWED")} className="ml-auto rounded border border-slate-700 px-2 py-1 text-slate-300">
                    {item.review_status === "REVIEWED" ? "標記未檢查" : "標記已檢查"}
                  </button>
                </article>
              ))}
              {!queue?.items.length && <p className="text-sm text-slate-500">目前沒有需要人工檢查的 Outcome。</p>}
            </div>
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 p-4 text-xs text-slate-500">
            <h2 className="font-semibold text-slate-300">版本與定義</h2>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap">{JSON.stringify({ versions: summary.versions, definitions: summary.definitions, observation_definitions: observations?.definitions }, null, 2)}</pre>
          </section>
          </>
          )}
        </>
      )}
    </main>
  )
}
