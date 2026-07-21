"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import {
  fetchPhase2ShadowDates,
  fetchPhase2ShadowSnapshot,
  type Phase2ExplainTrace,
  type Phase2ShadowDateItem,
  type Phase2ShadowSnapshotDetail,
} from "@/lib/api"

// Phase 2 Comparison Debug View（2026-07-21）
//
// 純顯示層：這裡看到的一切都來自 signal_shadow_snapshots（shadow mode 資料），
// 不影響任何選股決策，也不是使用者一般會看到的頁面——給開發時比較 legacy vs
// Phase 2 candidate 存活差異、逐檔追蹤決策路徑用。

const ROLE_LABELS: Record<string, string> = {
  SECTOR_LEADER: "產業領漲",
  CO_LEADER: "共同領漲",
  INDEPENDENT_LEADER: "獨立強勢",
  SECTOR_FOLLOWER: "產業跟漲",
  ROTATION_LAGGARD: "輪動補漲",
  EMERGING_MOMENTUM: "新興動能",
  UNCLASSIFIED_MOMENTUM: "未分類動能",
  NONE: "（無角色）",
}

const STAGE_LABELS: Record<string, string> = {
  candidate_discovery: "候選發現",
  sector_context: "產業情境",
  momentum_eligibility: "動能資格",
  role_annotation: "角色標註",
  tracking_state: "追蹤狀態",
  entry_state: "進場狀態",
  hard_exclusion: "硬性剔除",
  regime_gate: "盤勢關卡",
  sent_to_llm: "送交 LLM",
}

function roleLabel(role: string | null): string {
  if (!role) return "（無）"
  return ROLE_LABELS[role] ?? role
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

function StatCard({ label, value, tone = "default" }: { label: string; value: string | number; tone?: "default" | "warn" | "good" }) {
  const toneClass =
    tone === "warn"
      ? "border-amber-700/60 bg-amber-900/20 text-amber-200"
      : tone === "good"
        ? "border-emerald-700/60 bg-emerald-900/20 text-emerald-200"
        : "border-zinc-700 bg-zinc-800/50 text-zinc-200"
  return (
    <div className={`rounded-lg border px-4 py-3 ${toneClass}`}>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  )
}

function TraceRow({
  stockId,
  trace,
  isLegacySurvivor,
  isPhase2Survivor,
  expanded,
  onToggle,
}: {
  stockId: string
  trace: Phase2ExplainTrace
  isLegacySurvivor: boolean
  isPhase2Survivor: boolean
  expanded: boolean
  onToggle: () => void
}) {
  // 注意：<a>（Link）不可巢狀在 <button> 裡面（無效 HTML，瀏覽器 parser 行為
  // 不可預期，實測點擊會被導去股票頁而不是觸發展開）。改成整列是 <div>，
  // Link 與「展開/收合」button 是平行的兩個子元素，不互相包裹。
  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800/40">
      <div className="flex w-full items-center justify-between gap-3 px-4 py-3">
        <button
          type="button"
          onClick={onToggle}
          className="flex flex-1 flex-wrap items-center gap-2 text-left"
        >
          <span className="font-mono text-sm text-zinc-200">{stockId}</span>
          <span
            className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${
              isPhase2Survivor
                ? "border-emerald-600 bg-emerald-900/30 text-emerald-300"
                : "border-zinc-600 bg-zinc-700/40 text-zinc-400"
            }`}
          >
            Phase2 {isPhase2Survivor ? "存活" : "剔除"}
          </span>
          <span
            className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${
              isLegacySurvivor
                ? "border-emerald-600 bg-emerald-900/30 text-emerald-300"
                : "border-zinc-600 bg-zinc-700/40 text-zinc-400"
            }`}
          >
            Legacy {isLegacySurvivor ? "存活" : "剔除"}
          </span>
          <span className="text-xs text-zinc-400">
            {stageLabel(trace.final_stage)}
            {trace.role.type ? ` · ${roleLabel(trace.role.type)}` : ""}
          </span>
        </button>
        <Link href={`/stocks/${stockId}`} className="text-xs text-zinc-500 hover:text-sky-300 hover:underline">
          個股頁 →
        </Link>
        <button
          type="button"
          onClick={onToggle}
          className="shrink-0 text-xs text-zinc-500"
        >
          {expanded ? "收合 ▲" : "展開 ▼"}
        </button>
      </div>

      {expanded && (
        <div className="border-t border-zinc-700 px-4 py-3">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="text-zinc-500">Candidate Channels</dt>
              <dd className="text-zinc-200">
                {trace.candidate_channels.length ? trace.candidate_channels.join(", ") : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">最終階段</dt>
              <dd className="text-zinc-200">
                {stageLabel(trace.final_stage)}
                {trace.first_exclusion_reason ? `（${trace.first_exclusion_reason}）` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">產業情境</dt>
              <dd className="text-zinc-200">
                {trace.sector_context ? (
                  <>
                    {trace.sector_context.primary_sector ?? "—"} / {trace.sector_context.sub_sector ?? "—"}
                    <br />
                    peer_scope={trace.sector_context.peer_scope_used}（quality=
                    {trace.sector_context.sector_context_quality}）
                    <br />
                    sector_strength={trace.sector_context.sector_strength_percentile_20d ?? "—"} / peer_rs=
                    {trace.sector_context.peer_rs_percentile_20d ?? "—"}
                  </>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">角色 / 追蹤 / 進場狀態</dt>
              <dd className="text-zinc-200">
                role={roleLabel(trace.role.type)} · tracking={trace.tracking_state ?? "—"} · entry=
                {trace.entry_state ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">硬性剔除</dt>
              <dd className="text-zinc-200">
                {trace.hard_exclusion_result.pass ? "通過" : `剔除（${trace.hard_exclusion_result.reason}）`}
              </dd>
            </div>
            <div>
              <dt className="text-zinc-500">盤勢關卡</dt>
              <dd className="text-zinc-200">
                {trace.regime_gate_result.pass === null
                  ? "未跑到（更早階段已排除）"
                  : trace.regime_gate_result.pass
                    ? `通過（信心度：${trace.regime_gate_result.conviction ?? "—"}）`
                    : `剔除（${trace.regime_gate_result.regime}）`}
              </dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  )
}

export default function Phase2DebugPage() {
  const [dates, setDates] = useState<Phase2ShadowDateItem[]>([])
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [detail, setDetail] = useState<Phase2ShadowSnapshotDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [onlyDifferences, setOnlyDifferences] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchPhase2ShadowDates()
      .then((rows) => {
        if (cancelled) return
        setDates(rows)
        if (rows.length > 0) setSelectedDate(rows[0].snapshot_date)
        else setLoading(false)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!selectedDate) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPhase2ShadowSnapshot(selectedDate)
      .then((d) => {
        if (!cancelled) {
          setDetail(d)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedDate])

  const legacyIds = useMemo(
    () => new Set(detail?.comparison_summary?.legacy_survivor_ids ?? []),
    [detail],
  )
  const phase2Ids = useMemo(
    () => new Set(detail?.comparison_summary?.phase2_survivor_ids ?? []),
    [detail],
  )

  const traceEntries = useMemo(() => {
    if (!detail) return []
    let entries = Object.entries(detail.explain_traces)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      entries = entries.filter(([sid]) => sid.toLowerCase().includes(q))
    }
    if (onlyDifferences) {
      entries = entries.filter(([sid]) => legacyIds.has(sid) !== phase2Ids.has(sid))
    }
    // 存活優先排序（Phase2 存活的排前面），方便快速看結果
    entries.sort(([a], [b]) => {
      const aSurvive = phase2Ids.has(a) ? 0 : 1
      const bSurvive = phase2Ids.has(b) ? 0 : 1
      if (aSurvive !== bSurvive) return aSurvive - bSurvive
      return a.localeCompare(b)
    })
    return entries
  }, [detail, search, onlyDifferences, legacyIds, phase2Ids])

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">Phase 2 Comparison Debug View</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Shadow mode 專用——這裡看到的一切不影響任何選股決策，只是拿 legacy 與 Phase 2
            deterministic 決策層做並排比較。資料來自{" "}
            <code className="rounded bg-zinc-800 px-1 py-0.5 text-xs">signal_shadow_snapshots</code>
            （由 <code className="rounded bg-zinc-800 px-1 py-0.5 text-xs">run_phase2_replay.py</code> 或
            shadow cron 寫入）。
          </p>
        </div>
        <Link href="/signals/archive" className="text-sm text-sky-300 hover:underline">
          ← 回 30 日追蹤
        </Link>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <label className="text-sm text-zinc-400">
          日期：
          <select
            value={selectedDate ?? ""}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="ml-2 rounded border border-zinc-600 bg-zinc-800 px-2 py-1 text-sm text-zinc-100"
          >
            {dates.map((d) => (
              <option key={d.snapshot_date} value={d.snapshot_date}>
                {d.snapshot_date}
              </option>
            ))}
          </select>
        </label>
        {dates.length === 0 && !loading && (
          <span className="text-sm text-zinc-500">
            尚無 shadow 資料——先在 backend 跑{" "}
            <code className="rounded bg-zinc-800 px-1 py-0.5 text-xs">
              python run_phase2_replay.py YYYY-MM-DD --persist
            </code>
          </span>
        )}
      </div>

      {error && (
        <div className="mb-6 rounded border border-rose-700 bg-rose-900/20 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {loading && <div className="text-sm text-zinc-500">載入中…</div>}

      {!loading && detail && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="候選池（Candidate Discovery）" value={detail.candidate_pool_size ?? "—"} />
            <StatCard
              label="Legacy 存活"
              value={detail.comparison_summary?.legacy_survivor_count ?? "—"}
              tone={
                (detail.comparison_summary?.legacy_survivor_count ?? 0) === 0 ? "warn" : "default"
              }
            />
            <StatCard
              label="Phase 2 存活"
              value={detail.comparison_summary?.phase2_survivor_count ?? "—"}
              tone="good"
            />
            <StatCard
              label="動能資格通過率"
              value={`${Math.round(detail.funnel_metrics.classification_survival_rate * 100)}%`}
            />
          </div>

          {detail.funnel_metrics.anomaly_flags.length > 0 && (
            <div className="mb-6 rounded border border-amber-700 bg-amber-900/20 px-4 py-3 text-sm text-amber-200">
              異常偵測：{detail.funnel_metrics.anomaly_flags.join("、")}
            </div>
          )}

          <div className="mb-6 rounded-lg border border-zinc-700 bg-zinc-800/30 px-4 py-3">
            <div className="mb-2 text-sm font-medium text-zinc-300">角色分布</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(detail.funnel_metrics.role_counts).map(([role, count]) => (
                <span
                  key={role}
                  className="rounded border border-zinc-600 bg-zinc-700/40 px-2 py-1 text-xs text-zinc-200"
                >
                  {roleLabel(role)}：{count}
                </span>
              ))}
            </div>
          </div>

          <div className="mb-4 flex flex-wrap items-center gap-3">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜尋股票代號…"
              className="rounded border border-zinc-600 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-500"
            />
            <label className="flex items-center gap-1.5 text-sm text-zinc-400">
              <input
                type="checkbox"
                checked={onlyDifferences}
                onChange={(e) => setOnlyDifferences(e.target.checked)}
              />
              只看 Legacy / Phase 2 判斷不同的股票
            </label>
            <span className="text-xs text-zinc-500">共 {traceEntries.length} 檔</span>
          </div>

          <div className="space-y-2">
            {traceEntries.map(([stockId, trace]) => (
              <TraceRow
                key={stockId}
                stockId={stockId}
                trace={trace}
                isLegacySurvivor={legacyIds.has(stockId)}
                isPhase2Survivor={phase2Ids.has(stockId)}
                expanded={expandedId === stockId}
                onToggle={() => setExpandedId((prev) => (prev === stockId ? null : stockId))}
              />
            ))}
            {traceEntries.length === 0 && (
              <div className="rounded border border-zinc-700 bg-zinc-800/30 px-4 py-6 text-center text-sm text-zinc-500">
                找不到符合條件的股票
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
