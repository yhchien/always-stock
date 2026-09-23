"use client"

import { useEffect, useMemo, useState } from "react"

import {
  fetchShadowRepairLab,
  type ShadowRepairDiff,
  type ShadowRepairLabResponse,
} from "@/lib/api"

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 0 })
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function action(value: string | null): string {
  if (!value) return "—"
  return value
}

function stateItemLabel(item: Record<string, unknown>): string {
  const stock = `${String(item.stock_id ?? "")} ${String(item.stock_name ?? "")}`.trim()
  const actionValue = item.action ? `・${String(item.action)}` : ""
  const units = item.units !== undefined ? `・${String(item.units)} 單位` : ""
  return `${stock || "—"}${actionValue}${units}`
}

function Stat({ label, value, tone = "text-slate-100" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${tone}`}>{value}</p>
    </div>
  )
}

function DiffRow({ row }: { row: ShadowRepairDiff }) {
  const actionChanged = row.baseline_action !== row.candidate_action
  return (
    <tr className="border-t border-slate-800/80 align-top">
      <td className="px-3 py-2 font-mono text-xs text-slate-300">{row.trade_date}</td>
      <td className="px-3 py-2 text-xs text-slate-200"><span className="font-mono">{row.stock_id}</span> {row.stock_name}</td>
      <td className={`px-3 py-2 text-xs ${actionChanged ? "text-amber-200" : "text-slate-400"}`}>
        {action(row.baseline_action)} → {action(row.candidate_action)}
      </td>
      <td className="px-3 py-2 text-xs text-slate-400">
        {row.baseline_reason || row.candidate_reason || "—"}
        {row.candidate_reason && row.candidate_reason !== row.baseline_reason && (
          <><br /><span className="text-sky-300">修正版：{row.candidate_reason}</span></>
        )}
      </td>
      <td className="px-3 py-2 text-right text-xs text-slate-400">
        {row.candidate_topup_required && row.candidate_topup_required > 0
          ? `補款 ${money(row.candidate_topup_required)}`
          : "—"}
      </td>
      <td className="px-3 py-2 text-xs text-slate-500">{row.difference_type}</td>
    </tr>
  )
}

export default function ShadowRepairLabPage() {
  const [data, setData] = useState<ShadowRepairLabResponse | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    fetchShadowRepairLab({ onlyDifferences: !showAll, limit: 1000 }, { signal: controller.signal })
      .then(setData)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "策略修正實驗載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [showAll])

  const selected = data?.selected_run ?? null
  const latestStates = useMemo(() => {
    if (!data) return { baseline: null, candidate: null }
    const byTrack = new Map<string, ShadowRepairLabResponse["daily_states"][number]>()
    for (const state of data.daily_states) {
      if (!byTrack.has(state.track)) byTrack.set(state.track, state)
    }
    return { baseline: byTrack.get("BASELINE") ?? null, candidate: byTrack.get("CANDIDATE") ?? null }
  }, [data])

  return (
    <main className="mx-auto max-w-7xl px-4 pb-12">
      <header className="mb-6">
        <p className="text-xs uppercase tracking-[0.2em] text-sky-400">Live Strategy Repair</p>
        <h1 className="mt-2 text-2xl font-semibold text-slate-100">策略修正實驗</h1>
        <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-400">
          以問題發生當天的決策前狀態為基準，同時保存 Baseline（原策略）與 Candidate（修正版）。
          6933 只是其中一個事件；未來任何股票或規則修正都會在同一個實驗時間軸留下紀錄。
        </p>
      </header>

      {loading && <p className="text-sm text-slate-500">正在載入策略修正實驗…</p>}
      {error && <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>}

      {!loading && !selected && (
        <section className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm leading-6 text-amber-100">
          <p className="font-semibold">尚未建立 repair run</p>
          <p className="mt-1 text-amber-200/80">
            頁面與資料結構已就緒。建立第一個 run 時，anchor date 必須填「策略第一次出現問題的決策日」，
            不是今天；從該日開始保存整套策略的新舊分支。
          </p>
        </section>
      )}

      {selected && data && (
        <>
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="實驗狀態" value={selected.status} />
            <Stat label="基準日" value={selected.anchor_trade_date} />
            <Stat label="修正次數" value={String(data.revisions.length)} />
            <Stat label="差異筆數" value={String(data.diffs.length)} />
            <Stat label="Baseline 最新權益" value={money(latestStates.baseline?.total_equity)} />
            <Stat label="Candidate 最新權益" value={money(latestStates.candidate?.total_equity)} />
            <Stat label="Baseline 報酬" value={pct(latestStates.baseline?.total_return_pct)} />
            <Stat label="Candidate 報酬" value={pct(latestStates.candidate?.total_return_pct)} />
          </section>

          <section className="mt-6 rounded-xl border border-slate-800 bg-slate-950/40 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="font-semibold text-slate-100">{selected.title}</h2>
                <p className="mt-1 text-xs text-slate-500">
                  {selected.baseline_strategy_version} → {selected.candidate_strategy_version} ・ anchor {selected.anchor_trade_date}
                </p>
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-400">
                <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />
                顯示沒有差異的股票
              </label>
            </div>
            {selected.notes && <p className="mt-3 text-sm text-slate-400">{selected.notes}</p>}
          </section>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-slate-200">最新每日狀態</h2>
            <div className="grid gap-3 lg:grid-cols-2">
              {[latestStates.baseline, latestStates.candidate].map((state) => (
                <article key={state?.track ?? "empty"} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-medium text-slate-100">{state?.track === "BASELINE" ? "Baseline（原策略）" : "Candidate（修正版）"}</h3>
                    <span className="font-mono text-xs text-slate-500">{state?.trade_date ?? "—"}</span>
                  </div>
                  {!state && <p className="mt-3 text-sm text-slate-500">尚未產生每日狀態。</p>}
                  {state && (
                    <>
                      <p className="mt-2 text-xs text-slate-400">現金 {money(state.cash)} ・持倉 {state.position_count} 檔 ・報酬 {pct(state.total_return_pct)} ・補款 {money(state.cash_topup_required)}</p>
                      <div className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
                        <div>
                          <p className="text-slate-500">持倉</p>
                          <p className="mt-1 leading-5 text-slate-300">{state.positions?.length ? state.positions.map(stateItemLabel).join("、") : "—"}</p>
                        </div>
                        <div>
                          <p className="text-slate-500">當日成交／待成交</p>
                          <p className="mt-1 leading-5 text-slate-300">
                            {state.executed_orders?.length ? `${state.executed_orders.length} 筆成交` : "0 筆成交"} ・ {state.pending_orders?.length ? `${state.pending_orders.length} 筆待成交` : "0 筆待成交"}
                          </p>
                        </div>
                      </div>
                    </>
                  )}
                </article>
              ))}
            </div>
          </section>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-slate-200">策略修改時間軸</h2>
            <div className="space-y-2">
              {data.revisions.length === 0 && <p className="text-sm text-slate-500">尚未記錄策略修改。</p>}
              {data.revisions.map((revision) => (
                <article key={revision.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded bg-sky-500/10 px-2 py-1 text-sky-200">Revision {revision.revision_no}</span>
                    <span className="font-mono text-slate-300">{revision.effective_trade_date}</span>
                    {revision.trigger_stock_id && <span className="text-slate-400">觸發：{revision.trigger_stock_id} {revision.trigger_stock_name}</span>}
                  </div>
                  <h3 className="mt-2 font-medium text-slate-100">{revision.title}</h3>
                  <p className="mt-1 text-sm leading-6 text-slate-400">{revision.reason}</p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <div className="rounded border border-rose-500/20 bg-rose-500/5 p-2 text-xs text-rose-200">修改前：{revision.before_strategy_label}</div>
                    <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2 text-xs text-emerald-200">修改後：{revision.after_strategy_label}</div>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="mt-6 overflow-hidden rounded-xl border border-slate-800">
            <div className="flex items-center justify-between bg-slate-900 px-3 py-3">
              <h2 className="text-sm font-semibold text-slate-200">整套策略的新舊決策差異</h2>
              <span className="text-xs text-slate-500">不是只顯示觸發問題的股票</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px]">
                <thead className="bg-slate-950 text-left text-[10px] text-slate-500">
                  <tr><th className="px-3 py-2">日期</th><th className="px-3 py-2">股票</th><th className="px-3 py-2">原策略 → 修正版</th><th className="px-3 py-2">原因</th><th className="px-3 py-2 text-right">補款</th><th className="px-3 py-2">差異類型</th></tr>
                </thead>
                <tbody>{data.diffs.length === 0 ? <tr><td colSpan={6} className="px-3 py-6 text-center text-sm text-slate-500">尚未產生差異紀錄。</td></tr> : data.diffs.map((row) => <DiffRow key={row.id} row={row} />)}</tbody>
              </table>
            </div>
          </section>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-slate-200">循環結束封存</h2>
            <div className="grid gap-2 sm:grid-cols-2">
              {data.archives.length === 0 ? <p className="text-sm text-slate-500">目前循環尚未結束。</p> : data.archives.map((archive) => (
                <article key={archive.cycle_number} className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 text-sm">
                  <p className="font-medium text-slate-200">第 {archive.cycle_number} 循環・{archive.start_trade_date} ～ {archive.end_trade_date}</p>
                  <p className="mt-2 text-xs text-slate-400">Baseline {pct(archive.baseline_return_pct)} ・ Candidate {pct(archive.candidate_return_pct)} ・ 差異 {pct(archive.return_delta_pct)}</p>
                  <p className="mt-1 text-xs text-slate-500">保留 {archive.revision_count} 次策略修改的完整紀錄</p>
                </article>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  )
}
