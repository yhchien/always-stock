"use client"

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"

import OutcomeMetricCard from "@/components/OutcomeMetricCard"
import { OutcomeDistributionChart, OutcomeTimeseriesChart } from "@/components/OutcomeCharts"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import StickyHorizontalScroll from "@/components/StickyHorizontalScroll"
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
  type SignalOutcomeLabel,
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

type VersionFilterKey =
  | "selection_version"
  | "momentum_score_version"
  | "research_prompt_version"
  | "assessment_prompt_version"
  | "global_selector_version"
  | "reason_prompt_version"
  | "tracking_prompt_version"
  | "tracking_state_machine_version"

const VERSION_FILTER_LABELS: Record<VersionFilterKey, string> = {
  selection_version: "選股版本",
  momentum_score_version: "動能分數版本",
  research_prompt_version: "研究 Prompt 版本",
  assessment_prompt_version: "評估 Prompt 版本",
  global_selector_version: "全體比較版本",
  reason_prompt_version: "理由 Prompt 版本",
  tracking_prompt_version: "追蹤 Prompt 版本",
  tracking_state_machine_version: "追蹤狀態機版本",
}

/** 每個區塊底下的白話說明＋具體例子，統一樣式。 */
function SectionExplainer({ children }: { children: ReactNode }) {
  return (
    <div className="mt-2 rounded-lg border border-sky-900/40 bg-sky-950/20 p-3 text-xs leading-5 text-slate-300">
      {children}
    </div>
  )
}

// 這頁是純工程稽核用（結果分析在做的是「選股演算法本身好不好」，不是「這檔股票現在賺不賠」；
// 後者已經在正式推薦卡片直接顯示報酬率），nav 只在工程版才會連過來，所以這裡不再分正式/工程
// 兩種內容——找到這頁的人本來就是想看完整資訊。全頁改中文＋每個區塊加白話說明與例子。
export default function SignalOutcomesPage() {
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
  const detailRef = useRef<HTMLDivElement | null>(null)

  /** 任何摘要數字／圖表被點擊時，把逐筆明細表篩到對應條件並捲到該處，讓「這是哪幾檔」有地方看。 */
  const goToDetail = useCallback((patch: { outcomeLabel?: string; decision?: string }) => {
    setPage(1)
    setOutcomeLabel(patch.outcomeLabel ?? "")
    setDecision(patch.decision ?? "")
    requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
    })
  }, [])

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
    // 篩選條件（含分頁）一變就先清空逐筆明細，避免使用者看到上一次篩選殘留的舊資料，
    // 誤以為新篩選已經跑完。
    setItems(null)
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
        <p className="text-xs uppercase tracking-[0.2em] text-sky-300/80">推薦成效稽核</p>
        <h1 className="mt-1 text-2xl font-semibold">結果分析</h1>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
          本頁是事後品質追蹤，用來檢查「選股邏輯本身準不準」，不是看「單一股票現在賺不賠」——
          後者請直接看正式推薦卡片上的報酬率。這頁的結果不會回頭影響任何一天的候選資格、排序
          或推薦決策。
        </p>
        <SectionExplainer>
          <strong className="text-slate-100">怎麼讀這頁最重要的一件事：</strong>
          「10 日報酬率」是抓「推薦後第 10 個交易日收盤」那一個時間點的價格，跟正式推薦卡片
          上「即時報酬率」不是同一個數字。舉例：某股票推薦後第 5 天漲到 +20%，但第 10 天拉回
          到只剩 +6%，這頁記錄的就是 +6%（判定為「持平」），不是曾經出現過的 +20% 最高點。反過
          來說，還沒滿 10 個交易日的推薦（本頁標「尚未滿10日」）也還沒有結果可看，不代表它表現
          不好，只是還沒到判定的那一天。
        </SectionExplainer>
      </header>

      <section className="mb-5 flex flex-wrap gap-2 rounded-xl border border-slate-800 bg-slate-900/35 p-3">
        <input aria-label="開始日期" type="date" value={startDate} onChange={(event) => { setPage(1); setStartDate(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
        <input aria-label="結束日期" type="date" value={endDate} onChange={(event) => { setPage(1); setEndDate(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs" />
        <select aria-label="Prompt 世代" value={promptFamily} onChange={(event) => { setPage(1); setPromptFamily(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
          <option value="">全部 Prompt 世代</option>
          {(summary?.versions.prompt_family ?? []).map((version) => <option key={version}>{version}</option>)}
          {!summary?.versions.prompt_family?.includes("v7") && <option value="v7">v7</option>}
          {!summary?.versions.prompt_family?.includes("legacy_split") && <option value="legacy_split">legacy_split（舊版）</option>}
        </select>
        <select aria-label="結果" value={outcomeLabel} onChange={(event) => { setPage(1); setOutcomeLabel(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
          <option value="">全部結果</option>
          {Object.entries(OUTCOME_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select aria-label="當日決策" value={decision} onChange={(event) => { setPage(1); setDecision(event.target.value) }} className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs">
          <option value="">全部決策</option>
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
          {Object.entries(VERSION_FILTER_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
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
      </section>

      {loading && <p className="text-sm text-slate-500">正在載入 10 日報酬指標、趨勢、明細與觀察分析…</p>}
      {error && <p className="mb-4 rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>}

      {summary && (
        <>
          <p className="mb-4 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs text-slate-300">
            目前顯示區間：
            <span className="ml-1 font-mono text-slate-100">
              {summary.date_range.actual_start ?? "—"} ～ {summary.date_range.actual_end ?? "—"}
            </span>
            （可用上方「開始日期」「結束日期」調整；沒填就是系統目前有的全部資料）
          </p>
          {summary.sample.missing > 0 && <p className="mb-4 rounded border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100">部分結果資料不完整，本頁比例只使用價格資料完整的成熟樣本。</p>}

          <section>
            <h2 className="text-sm font-semibold text-slate-200">整體概況</h2>
            <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <OutcomeMetricCard
                label="候選漏斗（每日平均）"
                value={`${summary.selection.average_phase2_eligible_count.toFixed(1)} / ${summary.selection.average_global_eligible_count.toFixed(1)} / ${summary.selection.average_recommended_count.toFixed(1)}`}
                detail={`初步入圍 → 進入最終比較 → 正式推薦；累計推薦 ${summary.sample.total} 檔・已判定 ${summary.sample.matured}・尚未滿10日 ${summary.sample.immature}・缺資料 ${summary.sample.missing}`}
              />
              <OutcomeMetricCard
                label="10 日後達標率"
                value={formatRate(summary.recommendation.acceptable_rate)}
                detail={`大漲達標 ${summary.recommendation.winner_count} + 持平 ${summary.recommendation.neutral_count}／已判定 ${summary.sample.matured}・目標 80%`}
                status={summary.sample.matured ? summary.recommendation.acceptable_target_met ? "met" : "not-met" : "neutral"}
              />
              <OutcomeMetricCard
                label="賺錢比輸家多嗎"
                value={summary.recommendation.winner_greater_than_neutral ? "符合" : "未符合"}
                detail={`大漲達標 ${summary.recommendation.winner_count}（${formatRate(summary.recommendation.winner_rate)}）・持平 ${summary.recommendation.neutral_count}（${formatRate(summary.recommendation.neutral_rate)}）`}
                status={summary.sample.matured ? summary.recommendation.winner_greater_than_neutral ? "met" : "not-met" : "neutral"}
              />
              <OutcomeMetricCard
                label="大跌比例／大漲抓取率"
                value={`${formatRate(summary.recommendation.big_loser_rate)} / ${formatRate(summary.selection.winner_recall)}`}
                detail={
                  <>
                    漏抓大漲股 {summary.selection.not_selected_winner_count} 檔・平均篩掉{" "}
                    {formatRate(summary.selection.average_compression_rate)} 的候選
                    <span className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                      <button
                        type="button"
                        onClick={() => goToDetail({ outcomeLabel: "BIG_LOSER", decision: "RECOMMEND" })}
                        className="text-sky-300 hover:text-sky-200"
                      >
                        查看大跌名單 →
                      </button>
                      <button
                        type="button"
                        onClick={() => goToDetail({ outcomeLabel: "WINNER" })}
                        className="text-sky-300 hover:text-sky-200"
                      >
                        查看大漲名單 →
                      </button>
                    </span>
                  </>
                }
              />
            </div>
            <SectionExplainer>
              <ul className="list-disc space-y-1.5 pl-4">
                <li>
                  <strong className="text-slate-100">候選漏斗</strong>：每天系統大約先篩出幾檔「初步入圍」，
                  再從中選出幾檔進入「最終一次性比較」，最後正式推薦幾檔。例：今天 55 檔入圍
                  → 41 檔進入最終比較 → 只推薦 13 檔，代表篩選相當嚴格。
                </li>
                <li>
                  <strong className="text-slate-100">10 日後達標率</strong>：已經滿 10 個交易日的推薦裡，「大漲達標＋持平」
                  合計占多少比例，目標是 80% 以上。例：16 檔已判定，0 檔大漲、8 檔持平、8 檔大跌
                  → 達標率 = (0+8)/16 = 50%，未達 80% 目標。
                </li>
                <li>
                  <strong className="text-slate-100">賺錢比輸家多嗎</strong>：單純比較「大漲達標」跟「持平」哪個
                  數量比較多，用來快速看盤面體感——理論上好的選股應該大漲檔數多於持平檔數。
                </li>
                <li>
                  <strong className="text-slate-100">大漲抓取率（Winner Recall）</strong>：在所有「當天有機會被
                  推薦」的候選股裡（不只推薦的，包含被排除的），最後真的大漲的股票裡，有幾成有
                  被系統抓到正式推薦。例：這段期間全市場候選裡有 3 檔最後大漲，系統推薦到其中
                  0 檔 → 抓取率 0%，代表這幾檔大漲股全部被漏掉了。
                </li>
              </ul>
            </SectionExplainer>
          </section>

          <section className="mt-5">
            <h2 className="text-sm font-semibold text-slate-200">趨勢圖表</h2>
            <div className="mt-2 grid gap-4 xl:grid-cols-2">
              <OutcomeDistributionChart
                summary={summary}
                onSelect={(label: SignalOutcomeLabel) => goToDetail({ outcomeLabel: label })}
              />
              <OutcomeTimeseriesChart items={timeseries?.items ?? []} />
            </div>
            <SectionExplainer>
              左圖是「已判定樣本」最後落在大漲／持平／大跌哪一類的比例圓餅圖；右圖是逐日的推薦
              檔數與其中變成大漲達標的檔數，用來看這套邏輯的表現是不是隨時間在變好或變差，而不
              是只看單一天的結果。
            </SectionExplainer>
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold">後端排序分布與越級推薦</h2>
            <p className="mt-1 text-xs text-slate-500">
              後端排序（Backend Rank）是純演算法（不含 AI）算出來的強弱順位，1 名最強；AI（Global
              Selector）看過這個排序後，仍會用質化判斷做最終推薦名單，兩者不一定完全一致。
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-[34rem] text-left text-xs">
                <thead className="text-slate-500">
                  <tr>
                    <th className="px-2 py-1">後端排序區間</th>
                    {["1-10", "11-25", "26-50", "51+"].map((bucket) => (
                      <th key={bucket} className="px-2 py-1">{bucket} 名</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {(
                    [
                      ["recommend", "被 AI 正式推薦", { decision: "RECOMMEND" }],
                      ["not_selected", "被 AI 排除", { decision: "NOT_SELECTED" }],
                      ["winner", "最後大漲達標", { outcomeLabel: "WINNER" }],
                    ] as const
                  ).map(([key, label, patch]) => (
                    <tr key={key} className="border-t border-slate-800">
                      <td className="px-2 py-2">
                        <button
                          type="button"
                          onClick={() => goToDetail(patch)}
                          className="text-sky-300 hover:text-sky-200 hover:underline"
                          title="點擊查看這個分類的股票明細（表格不分後端排序區間，只能整類看）"
                        >
                          {label}
                        </button>
                      </td>
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
              越級推薦 {summary.selection.rank_override_count} 次；其中最後大跌虧損{" "}
              {summary.selection.rank_override_big_loser_count} 次。
            </p>
            <SectionExplainer>
              <p>
                每一列是「這群股票的後端排序，落在哪個區間」的統計。例如「被 AI 排除」那列
                「51+ 名」欄是 63，代表：後端排序在 51 名之後（演算法認為比較弱）的股票，有 63
                檔最後也被 AI 判斷不值得推薦——排序跟 AI 判斷方向一致，這是健康的訊號。
              </p>
              <p className="mt-2">
                但「被 AI 正式推薦」那列「51+ 名」欄如果不是 0，例如是 2，代表：有 2 檔股票後端
                排序其實很後面（51 名之後），AI 卻還是選了它們正式推薦——這就是下面講的「越級
                推薦」。
              </p>
              <p className="mt-2">
                <strong className="text-slate-100">越級推薦（Rank Override）</strong>：當 AI 推薦了一檔
                股票，但同一天有另一檔排序「比它更前面」的股票卻被 AI 排除時，就算一次越級推薦。
                舉例：股票 A 後端排序第 38 名被正式推薦，但同一天排序第 12 名的股票 B 卻被排除
                掉了——A 的排序比 B 差卻雀屏中選，系統就記一次「越級推薦」。「其中最後大跌虧損」
                則是這些越級推薦的股票裡，有幾次事後證明選錯了（大跌超過 10%），用來檢查 AI 跳過
                演算法排序去選股，準不準。
              </p>
            </SectionExplainer>
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold">被排除卻後來大漲的股票</h2>
                <p className="mt-1 text-xs text-slate-500">
                  {summary.selection.not_selected_winner_count} 檔・被排除股票裡最後大漲的比例{" "}
                  {formatRate(summary.selection.not_selected_winner_rate)}。用來檢查有沒有漏掉的大魚。
                </p>
              </div>
              <button
                type="button"
                onClick={() => goToDetail({ decision: "NOT_SELECTED", outcomeLabel: "WINNER" })}
                className="rounded border border-sky-500/40 px-3 py-1.5 text-xs text-sky-200"
              >
                查看股票明細 →
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
                <span className="text-xs text-slate-600">目前沒有漏選大漲股。</span>
              )}
            </div>
            <SectionExplainer>
              當天 AI 判斷「不推薦」的股票，裡面有沒有後來（第 10 個交易日）漲超過 10% 的——也就是
              「漏掉的大魚」。例如目前有 1 檔，原因標「CATALYST_UNCONFIRMED」（催化尚未確認）：
              代表 AI 當時覺得這檔的上漲題材還沒有明確的新聞或事件可以佐證，所以先不推薦，結果它
              還是漲了。這不代表 AI 判斷錯誤（保守本來就會漏掉一些），但漏掉的原因分布可以看出
              「AI 是不是太保守」。
            </SectionExplainer>
          </section>

          <section className="mt-5">
            <h2 className="text-base font-semibold">既有觀察的停止品質</h2>
            <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <OutcomeMetricCard label="警戒後恢復比例" value={formatRate(observations?.summary.caution_recovery_rate)} detail="被標「警戒」後，在真的停止前又恢復正常追蹤" />
              <OutcomeMetricCard label="疑似過早停止" value={observations?.summary.premature_stop_candidate_count ?? 0} detail="停止追蹤後 10 個交易日內反而漲超過 10%" />
              <OutcomeMetricCard label="停損提前預警比例" value={formatRate(observations?.summary.stop_before_big_loss_rate)} detail="股票真的大跌超過 10% 前，系統有沒有提早停止追蹤" />
              <OutcomeMetricCard label="平均追蹤天數" value={(observations?.summary.average_trading_days_to_stop ?? 0).toFixed(1)} detail="從開始追蹤到停止觀察，平均經過幾個交易日" />
              <OutcomeMetricCard label="停止後重新入選次數" value={observations?.summary.rerecommended_episode_count ?? 0} detail="同一檔股票停止觀察後，又重新被推薦的次數" />
            </div>
            {(observations?.summary.premature_stop_candidate_count ?? 0) > 0 && <p className="mt-3 text-xs text-slate-500">此標記只表示停止後重新走強，需人工檢查；不代表當時停止決策一定錯誤。</p>}
            <SectionExplainer>
              這裡看的不是「選股準不準」，而是「已經在追蹤的股票，系統判斷『該停止觀察了』的時機
              好不好」。例：一檔股票被系統標記停止追蹤，結果接下來 10 個交易日又漲了 12%——這種
              情況會被算進「疑似過早停止」，提醒可能停太早了；反過來，「停損提前預警比例」高代表
              系統擅長在股票真正崩跌之前就先示警，是好事。
            </SectionExplainer>
          </section>

          <section ref={detailRef} className="mt-5 overflow-hidden rounded-xl border border-slate-800 scroll-mt-4">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-900/50 p-3">
              <div>
                <h2 className="text-sm font-semibold">逐筆明細</h2>
                <p className="text-[11px] text-slate-500">
                  共 {items?.total ?? 0} 筆・第 {items?.page ?? 1}/{Math.max(items?.pages ?? 1, 1)} 頁
                  {(outcomeLabel || decision) && (
                    <>
                      ・目前篩選：
                      {decision && <span className="text-sky-300">{P3_DECISION_LABELS[decision] ?? decision}</span>}
                      {decision && outcomeLabel && "・"}
                      {outcomeLabel && (
                        <span className="text-sky-300">{OUTCOME_LABELS[outcomeLabel as SignalOutcomeLabel] ?? outcomeLabel}</span>
                      )}
                      <button
                        type="button"
                        onClick={() => { setPage(1); setOutcomeLabel(""); setDecision("") }}
                        className="ml-2 text-slate-500 underline hover:text-slate-300"
                      >
                        清除篩選
                      </button>
                    </>
                  )}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label="逐筆明細：結果"
                  value={outcomeLabel}
                  onChange={(event) => { setPage(1); setOutcomeLabel(event.target.value) }}
                  className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
                >
                  <option value="">全部結果</option>
                  {Object.entries(OUTCOME_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <select
                  aria-label="逐筆明細：當日決策"
                  value={decision}
                  onChange={(event) => { setPage(1); setDecision(event.target.value) }}
                  className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
                >
                  <option value="">全部決策</option>
                  <option value="RECOMMEND">正式推薦</option>
                  <option value="NOT_SELECTED">未列入今日推薦</option>
                </select>
                <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">上一頁</button>
                <button type="button" disabled={page >= (items?.pages ?? 1)} onClick={() => setPage((value) => value + 1)} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">下一頁</button>
              </div>
            </div>
            <StickyHorizontalScroll>
              <table className="min-w-full text-left text-xs">
                <thead className="bg-slate-950/70 text-slate-500"><tr>{["日期", "股票", "資產", "當日決策", "後端排序／推薦排序", "原因／主題", "追蹤狀態", "10日報酬", "結果", "版本"].map((label) => <th key={label} className="whitespace-nowrap px-3 py-2">{label}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-800">
                  {loading ? (
                    <tr>
                      <td colSpan={10} className="px-3 py-6 text-center text-slate-500">正在載入符合篩選條件的明細…</td>
                    </tr>
                  ) : (
                    (items?.items ?? []).map((item) => (
                      <tr key={item.id} className="text-slate-400">
                        <td className="whitespace-nowrap px-3 py-2 font-mono">{item.signal_date}</td>
                        <td className="whitespace-nowrap px-3 py-2"><span className="font-mono text-slate-200">{item.stock}</span> {item.name}</td>
                        <td className="px-3 py-2"><SignalAssetBadge assetType={item.asset_type} /></td>
                        <td className="whitespace-nowrap px-3 py-2">{P3_DECISION_LABELS[item.p3_decision]}</td>
                        <td className="whitespace-nowrap px-3 py-2">後端 {item.backend_priority_rank ?? "—"} / 推薦 {item.recommendation_rank ?? "—"}</td>
                        <td className="max-w-56 px-3 py-2">{item.selection_reason_code ?? item.theme_cluster ?? "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2">{item.observation_status ?? "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2 font-mono">{formatPercent(item.day10_return)}</td>
                        <td className="whitespace-nowrap px-3 py-2">{OUTCOME_LABELS[item.outcome_label]}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-[10px]">{item.prompt_family_version ?? "—"} / {item.selection_version ?? "—"} / {item.outcome_definition_version}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </StickyHorizontalScroll>
            {!loading && !items?.items.length && <p className="p-4 text-sm text-slate-500">此篩選區間沒有明細。</p>}
            <div className="border-t border-slate-800 p-3">
              <SectionExplainer>
                這是最原始的逐筆資料，每一列是「某一天、某一檔股票」的完整紀錄，給想自己核對數字
                的人用。上面所有卡片與圖表的統計數字，都是從這張表彙總出來的——點上面任何一個
                數字、長條圖或表格列，都會自動篩到這裡並捲下來，不用自己重選篩選條件。
              </SectionExplainer>
            </div>
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
            <h2 className="text-sm font-semibold">需要人工檢查的異常案例</h2>
            <p className="mt-1 text-xs text-slate-500">人工標記只保存檢查狀態與備註，不會修改結果分類或原始推薦決策。</p>
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
              {!queue?.items.length && <p className="text-sm text-slate-500">目前沒有需要人工檢查的案例。</p>}
            </div>
            <SectionExplainer>
              系統自動把幾種「值得回頭看一眼」的情況收集在這裡，例如「推薦後大跌」「漏掉的大漲
              股」「疑似過早停止」——不是系統判斷有錯，只是提醒可能值得人工複核。點「標記已檢查」
              只是留一個「我看過了」的紀錄，不會改變任何統計數字或分類結果。
            </SectionExplainer>
          </section>

          <section className="mt-5 rounded-xl border border-slate-800 p-4 text-xs text-slate-500">
            <h2 className="font-semibold text-slate-300">版本與定義（純工程紀錄）</h2>
            <p className="mt-1">
              這段是這批統計數字用到的各種 prompt／演算法版本號原始資料，一般不需要看；只有在懷疑
              「是不是換了新版邏輯導致數字變化」時才需要對照。
            </p>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap">{JSON.stringify({ versions: summary.versions, definitions: summary.definitions, observation_definitions: observations?.definitions }, null, 2)}</pre>
          </section>
        </>
      )}
    </main>
  )
}
