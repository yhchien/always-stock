"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import Link from "next/link"

import {
  fetchSignalRegenerateQuota,
  fetchLatestSignalSnapshot,
  type RealtimeQuote,
  type SignalJobResponse,
  regenerateSignals,
  type SignalDecisionType,
  type SignalRegenerateQuotaResponse,
  type SignalMarginAnalysis,
  type SignalSnapshotResponse,
  type SignalWatchlistItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useRealtimeQuotes } from "@/lib/useRealtimeQuotes"
import { useSignalJobPolling } from "@/lib/useSignalJobPolling"
import {
  signalValueLabel,
  signalValueTone,
  toneChipClass,
} from "@/lib/signalPresentation"
import { Dialog } from "@base-ui/react/dialog"

import { CanonicalSectorTag } from "@/components/CanonicalSectorTag"
import SignalAssetBadge from "@/components/SignalAssetBadge"
import SignalEmotionCard, { type EmotionTone } from "@/components/SignalEmotionCard"

import {
  isSignalProcessingIncomplete,
  SignalIncompleteWarning,
} from "@/components/SignalProcessingSummary"
import TradingPlanPanel, {
  PanelBulletList,
  type TradingPlanAccent,
} from "@/components/TradingPlanPanel"
import WatchlistAddButton from "@/components/WatchlistAddButton"

const LAST_SEEN_KEY = "always-stock:signals:last_seen_snapshot_date"
const COLLAPSED_KEY = "always-stock:signals:collapsed"

// 2026-08-13：即時報價輪詢間隔，原本沒帶參數走 useRealtimeQuotes 的預設值
// （15 秒），比 archive 頁等其他頁面已採用的 60 秒明顯更頻繁。使用者反映首頁
// 報價「很慢很慢，而且常常出不來」；查證 /api/realtime/quotes 是同步阻塞呼叫
// TWSE（無快取），多位使用者同時瀏覽首頁時同一批股票的報價會被重複打好幾次
// TWSE，放大延遲與失敗機率。後端已加上短 TTL cache，這裡同步把首頁輪詢頻率
// 降到跟其他頁面一致，從源頭減少請求量。
const REALTIME_INTERVAL_MS = 60_000

// 2026-05-27：暫時隱藏 SignalDetailDialog 內的融資融券分析紅色框框
// 改回顯示時把這個常數改成 true 即可（保留 MarginAnalysisPanel 函式與後端資料）
// 明確標型別 boolean（不能用 const false literal，否則 TS 會把三元 truthy branch narrow 成 unreachable）
const SHOW_MARGIN_ANALYSIS: boolean = false

// 2026-08-28：首頁隱藏「重新產生」整包每日訊號的按鈕（保留 handleRegenerate／
// regenerateSignals 呼叫與 quota 顯示邏輯，改回顯示時把這個常數改成 true 即可）。
const SHOW_REGENERATE_BUTTON: boolean = false

function formatTpeDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  try {
    const dt = new Date(iso)
    if (Number.isNaN(dt.getTime())) return ""
    return new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(dt)
  } catch {
    return ""
  }
}

function InlinePrice({
  quote,
}: {
  quote: RealtimeQuote | undefined
}) {
  if (!quote || quote.price == null) {
    return (
      <span className="shrink-0 whitespace-nowrap text-xs text-slate-500">報價載入中</span>
    )
  }
  const change = quote.change_pct
  const hasChange = change != null && !Number.isNaN(change)
  const color = !hasChange
    ? "text-slate-300"
    : (change as number) > 0
      ? "text-red-400"
      : (change as number) < 0
        ? "text-green-400"
        : "text-slate-300"
  const arrow = !hasChange
    ? ""
    : (change as number) > 0
      ? "▲"
      : (change as number) < 0
        ? "▼"
        : ""
  return (
    <span className="inline-flex shrink-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="font-mono text-sm text-slate-100">{quote.price.toFixed(2)}</span>
      {hasChange ? (
        <span className={`font-mono text-xs ${color}`}>
          {arrow} {(change as number) >= 0 ? "+" : ""}
          {(change as number).toFixed(2)}%
        </span>
      ) : null}
    </span>
  )
}

// M27：大盤 regime badge（panel header）。market_regime 已攤平為字串。
function RegimeBadge({
  regime,
  label,
  reason,
}: {
  regime?: string | null
  label?: string | null
  reason?: string | null
}) {
  if (!regime) return null
  const cls =
    regime === "BULL_TREND"
      ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-200"
      : regime === "RISK_OFF"
        ? "border-rose-500/60 bg-rose-500/15 text-rose-200"
        : "border-amber-500/60 bg-amber-500/15 text-amber-200"
  const fallback =
    regime === "BULL_TREND" ? "大多頭" : regime === "RISK_OFF" ? "風險退潮" : "震盪盤"
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}
      title={reason || ""}
    >
      大盤：{label || fallback}
    </span>
  )
}

// M27：個股 deterministic 觀察積極度 chip（一眼判斷今天要不要積極看）
function ConvictionChip({
  conviction,
  intensity,
}: {
  conviction?: string | null
  intensity?: string | null
}) {
  // 優先用 watch_intensity（積極/正常/保留）；舊快照無 intensity 時 fallback 用 conviction
  const intensityMap: Record<string, { label: string; cls: string }> = {
    aggressive: { label: "積極", cls: "border-emerald-500/60 bg-emerald-500/15 text-emerald-200" },
    normal: { label: "正常", cls: "border-slate-500/60 bg-slate-600/30 text-slate-200" },
    cautious: { label: "保留", cls: "border-amber-500/55 bg-amber-500/10 text-amber-200" },
  }
  const convLabel: Record<string, string> = { high: "高", medium: "中", low: "低" }
  const m = intensity ? intensityMap[intensity] : undefined
  if (!m) {
    if (!conviction) return null
    const cls =
      conviction === "high"
        ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-200"
        : conviction === "low"
          ? "border-rose-500/50 bg-rose-500/10 text-rose-200"
          : "border-slate-500/60 bg-slate-600/30 text-slate-200"
    return (
      <span
        className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}
        title="backend deterministic 信心度"
      >
        信心{convLabel[conviction] ?? conviction}
      </span>
    )
  }
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${m.cls}`}
      title={`backend deterministic 觀察積極度（信心度 ${convLabel[conviction ?? ""] ?? conviction ?? "—"}）`}
    >
      {m.label}
    </span>
  )
}

// ============================================================================
// v2.2（2026-07-16）：動能資料顯示（momentum_score / grade / phase / RS）
// 資料來源優先序：item.signal_metrics（backend deterministic，pipeline 蓋回）
// > item.momentum（v5 LLM 原樣回填）；v5 之前的舊快照兩者皆無 → 不顯示。
// ============================================================================

const MOMENTUM_PHASE_LABELS: Record<string, string> = {
  emerging: "啟動",
  accelerating: "加速",
  trending: "趨勢延續",
  extended: "過熱",
  weakening: "轉弱",
}

const MOMENTUM_PHASE_CLS: Record<string, string> = {
  emerging: "border-sky-500/60 bg-sky-500/15 text-sky-200",
  accelerating: "border-emerald-500/60 bg-emerald-500/15 text-emerald-200",
  trending: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200",
  extended: "border-amber-500/60 bg-amber-500/15 text-amber-200",
  weakening: "border-rose-500/60 bg-rose-500/15 text-rose-200",
}

const MOMENTUM_GRADE_CLS: Record<string, string> = {
  A: "border-emerald-500/60 bg-emerald-500/15 text-emerald-200",
  B: "border-sky-500/60 bg-sky-500/15 text-sky-200",
  C: "border-amber-500/60 bg-amber-500/15 text-amber-200",
  D: "border-rose-500/60 bg-rose-500/15 text-rose-200",
}

type ResolvedMomentum = {
  score: number | null
  grade: string | null
  phase: string | null
  rsMarketPct: number | null
  rsIndustryPct: number | null
  rsRankChange5d: number | null
  trendEfficiency: number | null
  distanceToHigh20d: number | null
  atrPct14d: number | null
  return20d: number | null
  return60d: number | null
  fundamentalApplicability: string | null
  fundamentalScore: number | null
  reasons: string[]
}

function resolveMomentum(item: SignalWatchlistItem): ResolvedMomentum | null {
  const det = item.signal_metrics
  const llm = item.momentum
  if (!det && !llm) return null
  const pick = <T,>(a: T | null | undefined, b: T | null | undefined): T | null =>
    a != null ? a : b != null ? b : null
  const resolved: ResolvedMomentum = {
    score: pick(det?.momentum_score, llm?.momentum_score),
    grade: pick(det?.momentum_grade, llm?.momentum_grade),
    phase: pick(det?.momentum_phase, llm?.momentum_phase),
    rsMarketPct: pick(det?.rs_market_percentile_20d, llm?.rs_market_percentile_20d),
    rsIndustryPct: pick(det?.rs_industry_percentile_20d, llm?.rs_industry_percentile_20d),
    rsRankChange5d: pick(det?.rs_rank_improvement_5d, llm?.rs_rank_change_5d),
    trendEfficiency: pick(det?.trend_efficiency_20d, llm?.trend_efficiency_20d),
    distanceToHigh20d: pick(det?.distance_to_high_20d, llm?.distance_to_high_20d_pct),
    atrPct14d: llm?.atr_pct_14d ?? null,
    return20d: pick(det?.return_20d, llm?.return_20d),
    return60d: pick(det?.return_60d, llm?.return_60d),
    fundamentalApplicability:
      det?.fundamental_applicability ??
      det?.momentum_score_detail?.fundamental_applicability ??
      null,
    fundamentalScore: det?.momentum_score_detail?.fundamental ?? null,
    reasons: Array.isArray(llm?.momentum_reason) ? llm.momentum_reason : [],
  }
  if (resolved.score == null && resolved.rsMarketPct == null) return null
  return resolved
}

/** 卡片用小 chip：「動能 A・82」，title 帶 phase 與 RS 摘要。 */
function MomentumChip({ item }: { item: SignalWatchlistItem }) {
  const m = resolveMomentum(item)
  if (!m || m.score == null) return null
  const grade = m.grade ?? ""
  const cls = MOMENTUM_GRADE_CLS[grade] ?? "border-slate-600 bg-slate-700/40 text-slate-300"
  const phaseLabel = m.phase ? MOMENTUM_PHASE_LABELS[m.phase] ?? m.phase : null
  const title = [
    phaseLabel ? `階段：${phaseLabel}` : null,
    m.rsMarketPct != null ? `RS 全市場 ${m.rsMarketPct.toFixed(0)} 百分位` : null,
  ]
    .filter(Boolean)
    .join("；")
  return (
    <span
      className={`inline-flex whitespace-nowrap items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}
      title={title || "backend deterministic 動能分數"}
    >
      動能 {grade ? `${grade}・` : ""}
      {m.score.toFixed(0)}
    </span>
  )
}

function MomentumMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-800/40 px-2.5 py-1.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="text-sm font-semibold text-slate-100">{value}</div>
    </div>
  )
}

/** popup 用完整動能分析區塊。 */
function MomentumPanel({ item }: { item: SignalWatchlistItem }) {
  const m = resolveMomentum(item)
  if (!m) return null
  const phaseLabel = m.phase ? MOMENTUM_PHASE_LABELS[m.phase] ?? m.phase : null
  const phaseCls = m.phase
    ? MOMENTUM_PHASE_CLS[m.phase] ?? "border-slate-600 bg-slate-700/40 text-slate-300"
    : ""
  const gradeCls = MOMENTUM_GRADE_CLS[m.grade ?? ""] ?? "border-slate-600 bg-slate-700/40 text-slate-300"
  const fmt = (v: number | null, digits = 1, suffix = "") =>
    v == null ? "—" : `${v.toFixed(digits)}${suffix}`
  return (
    <section className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-bold text-violet-200">動能分析</h4>
        {m.score != null ? (
          <span className={`inline-flex items-center rounded border px-2 py-0.5 text-sm font-bold ${gradeCls}`}>
            {m.grade ? `${m.grade} 級・` : ""}
            {m.score.toFixed(1)} 分
          </span>
        ) : null}
        {phaseLabel ? (
          <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${phaseCls}`}>
            {phaseLabel}
          </span>
        ) : null}
        <span className="ml-auto text-[10px] text-slate-500">backend deterministic 計算</span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <MomentumMetric label="RS 全市場百分位" value={fmt(m.rsMarketPct, 0)} />
        <MomentumMetric label="RS 產業內百分位" value={fmt(m.rsIndustryPct, 0)} />
        <MomentumMetric
          label="排名 5 日變化"
          value={
            m.rsRankChange5d == null
              ? "—"
              : `${m.rsRankChange5d > 0 ? "+" : ""}${m.rsRankChange5d.toFixed(0)} 名`
          }
        />
        <MomentumMetric label="20 日報酬" value={fmt(m.return20d, 1, "%")} />
        <MomentumMetric label="距 20 日高點" value={fmt(m.distanceToHigh20d, 1, "%")} />
        <MomentumMetric
          label="趨勢效率 / ATR"
          value={`${fmt(m.trendEfficiency, 2)} / ${fmt(m.atrPct14d, 1, "%")}`}
        />
        {m.fundamentalApplicability ? (
          <MomentumMetric
            label="公司基本面證據"
            value={
              m.fundamentalApplicability === "NOT_APPLICABLE"
                ? "不適用（N/A）"
                : m.fundamentalApplicability === "MISSING"
                  ? "資料缺漏（Missing）"
                  : `可用${m.fundamentalScore != null ? `・${m.fundamentalScore.toFixed(1)} 分` : ""}`
            }
          />
        ) : null}
      </div>

      {m.reasons.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {m.reasons.map((r, i) => (
            <li key={i} className="flex gap-2 text-xs leading-relaxed text-slate-300">
              <span aria-hidden className="mt-0.5 shrink-0 text-violet-400">▸</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function decisionToTone(type: SignalDecisionType | null | undefined): EmotionTone {
  if (type === "FOLLOWER") return "follower"
  if (type === "LAGGARD") return "laggard"
  return "leader"
}

type ReasonSection = {
  key: keyof Pick<
    SignalWatchlistItem,
    "theme_reason" | "capital_reason" | "chip_reason" | "margin_reason" | "technical_reason"
  >
  number: number
  title: string
  accent: TradingPlanAccent
}

// 5 段 panel 順序 / 配色（對應 M2 backend 5 段 bullet）
const REASON_PANELS: ReasonSection[] = [
  { key: "theme_reason", number: 1, title: "題材", accent: "amber" },
  { key: "capital_reason", number: 2, title: "資金", accent: "cyan" },
  { key: "chip_reason", number: 3, title: "籌碼", accent: "emerald" },
  { key: "margin_reason", number: 4, title: "融券", accent: "rose" },
  { key: "technical_reason", number: 5, title: "技術", accent: "slate" },
]

function SignalCard({
  item,
  quote,
}: {
  item: SignalWatchlistItem
  quote: RealtimeQuote | undefined
}) {
  const [detailOpen, setDetailOpen] = useState(false)
  const themeFit = item.theme_fit
  // 首頁卡片只留產業名稱；資產類型徽章、子產業、canonical 分類 tag 移到詳情 popup 顯示
  const subtitle = item.industry != null ? <span>{item.industry}</span> : null

  // 偵測是否有任一段 bullet array 有內容（舊快照可能全 null/empty → 不顯示「看細節」按鈕）
  const hasReasonSections = REASON_PANELS.some((p) => {
    const bullets = item[p.key]
    return Array.isArray(bullets) && bullets.length > 0
  })

  return (
    <>
      <SignalEmotionCard
        tone={decisionToTone(item.type)}
        stockId={item.stock}
        stockName={item.name ?? null}
        subtitle={subtitle}
        // 改為點整張卡開 modal（仿對方 screen-card → result 流程）
        onCardClick={() => setDetailOpen(true)}
        headerRight={<WatchlistAddButton stockId={item.stock} variant="compact" />}
      >
        <div className="space-y-2">
          {/* 第一行：只留股價與漲跌幅 */}
          <InlinePrice quote={quote} />

          {/* 第二行：其他狀態 label */}
          <div className="flex flex-wrap items-center gap-1.5">
            <MomentumChip item={item} />
            <ConvictionChip conviction={item.conviction} intensity={item.watch_intensity} />
            {themeFit ? (
              <span
                className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone("theme_fit", themeFit))}`}
              >
                題材 {signalValueLabel(themeFit, "theme_fit")}
              </span>
            ) : null}
          </div>

          {/* 觀察維度：只留判定為「強」的綠色 label，弱/中性/未知一律不顯示 */}
          <div className="flex flex-wrap items-center gap-1.5">
            <GreenOnlyChip label="資金" kind="capital_flow" value={item.signals?.capital_flow} />
            <GreenOnlyChip label="籌碼" kind="chip_trend" value={item.signals?.chip_trend} />
            <GreenOnlyChip
              label="融券"
              kind="margin_short_signal"
              value={item.signals?.margin_short_signal}
            />
            <GreenOnlyChip label="技術" kind="technical_status" value={item.signals?.technical_status} />
            {!hasReasonSections ? (
              <span className="ml-auto text-[11px] text-slate-500">細節資料待更新</span>
            ) : null}
          </div>

        </div>
      </SignalEmotionCard>

      <SignalDetailDialog
        item={item}
        quote={quote}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </>
  )
}

function SignalDetailDialog({
  item,
  quote,
  open,
  onOpenChange,
}: {
  item: SignalWatchlistItem
  quote: RealtimeQuote | undefined
  open: boolean
  onOpenChange: (next: boolean) => void
}) {
  const stockHref = `/stocks/${encodeURIComponent(item.stock)}`
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[min(96vw,56rem)] max-h-[88vh] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl sm:p-6">
          {/* Header */}
          <div className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="flex items-baseline gap-2 text-xl font-black text-slate-100">
                <span>{item.stock}</span>
                {item.name ? <span className="text-base">{item.name}</span> : null}
                {item.type ? (
                  <span className="rounded-full border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-xs font-medium text-rose-200">
                    {item.type === "LEADER" ? "領漲" : item.type === "FOLLOWER" ? "跟漲" : "補漲"}
                  </span>
                ) : null}
                <SignalAssetBadge assetType={item.asset_type} />
              </Dialog.Title>
              <Dialog.Description className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                {item.industry ? (
                  <span>
                    {item.industry}
                    {item.sub_industry ? <span className="text-slate-500"> · {item.sub_industry}</span> : null}
                  </span>
                ) : null}
                <CanonicalSectorTag canonical={item.canonical} />
                <InlinePrice quote={quote} />
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="shrink-0 rounded-lg border border-slate-600 bg-slate-800/60 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700"
              aria-label="關閉"
            >
              ✕
            </Dialog.Close>
          </div>

          {/* 訊號 chip 列 */}
          <div className="mb-4 flex flex-wrap gap-1.5">
            {item.theme_fit ? (
              <span
                className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone("theme_fit", item.theme_fit))}`}
              >
                題材 {signalValueLabel(item.theme_fit, "theme_fit")}
              </span>
            ) : null}
            <ChipWithLabel label="資金" kind="capital_flow" value={item.signals?.capital_flow} />
            <ChipWithLabel label="籌碼" kind="chip_trend" value={item.signals?.chip_trend} />
            <ChipWithLabel label="融券" kind="margin_short_signal" value={item.signals?.margin_short_signal} />
            <ChipWithLabel label="技術" kind="technical_status" value={item.signals?.technical_status} />
          </div>

          {/* 5 panel grid（modal 寬度 56rem，明顯比卡片寬，每行字數舒服） */}
          <div className="grid gap-3 sm:grid-cols-2">
            {REASON_PANELS.map((p) => {
              const bullets = (item[p.key] ?? []) as string[]
              if (bullets.length === 0) return null
              return (
                <TradingPlanPanel
                  key={p.key}
                  number={p.number}
                  title={p.title}
                  accent={p.accent}
                >
                  <PanelBulletList items={bullets} bulletAccent={p.accent} />
                </TradingPlanPanel>
              )
            })}
          </div>

          {/* v2.2（2026-07-16）：動能分析（deterministic 分數 / RS / 階段 + v5 LLM 解讀 bullet） */}
          <div className="mt-4">
            <MomentumPanel item={item} />
          </div>


          {/* 2026-05-25：融資融券專屬結構化分析卡（比重 大盤 30% / 個股 70%） */}
          {/* 2026-05-27：暫時隱藏紅色框框（改回顯示請把 SHOW_MARGIN_ANALYSIS 改 true） */}
          {SHOW_MARGIN_ANALYSIS && item.margin_analysis ? (
            <div className="mt-4">
              <MarginAnalysisPanel analysis={item.margin_analysis} stockId={item.stock} />
            </div>
          ) : null}

          {/* Footer：兩個對等大按鈕（回上一頁 + 前往個股研究頁） */}
          <div className="mt-6 grid grid-cols-1 gap-3 border-t border-zinc-700 pt-5 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="inline-flex items-center justify-center rounded-lg border border-slate-600 bg-slate-800/60 px-5 py-3 text-base font-semibold text-slate-200 transition hover:bg-slate-700"
            >
              <span aria-hidden className="mr-1.5">←</span>
              回上一頁
            </button>
            <Link
              href={stockHref}
              className="inline-flex items-center justify-center rounded-lg border border-sky-500/60 bg-sky-500/20 px-5 py-3 text-base font-semibold text-sky-100 shadow-sm transition hover:bg-sky-500/30"
            >
              前往個股研究頁
              <span aria-hidden className="ml-1.5">→</span>
            </Link>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ChipWithLabel({
  label,
  kind,
  value,
}: {
  label: string
  kind: string
  value: string | null | undefined
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${toneChipClass(signalValueTone(kind, value))}`}
    >
      <span className="text-slate-400">{label}</span>
      <span>{signalValueLabel(value, kind)}</span>
    </span>
  )
}

/** 首頁卡片「觀察維度」用：只有判定為綠色（強）時才顯示，其餘一律不 render。 */
function GreenOnlyChip({
  label,
  kind,
  value,
}: {
  label: string
  kind: string
  value: string | null | undefined
}) {
  if (!value || signalValueTone(kind, value) !== "green") return null
  return <ChipWithLabel label={label} kind={kind} value={value} />
}

// 2026-05-25：融資融券專屬結構化分析卡（比重 大盤 30% / 個股 70%）
// 2026-08-11：改為匯出共用，/signals/archive 詳情 popup 也直接重用這個元件
// （60+ 行含格式化邏輯的完整元件，複製一份維護成本明顯高於匯出共用）
export function MarginAnalysisPanel({
  analysis,
  stockId,
}: {
  analysis: SignalMarginAnalysis
  stockId: string
}) {
  const t = analysis.stock_table
  const rows: { label: string; value: string; tone?: "green" | "red" | "neutral" }[] = [
    { label: "收盤價", value: formatPrice(t.close_price) },
    { label: "融資餘額", value: formatShares(t.margin_balance_shares) },
    {
      label: "融資增減",
      value: formatChangeShares(t.margin_change_shares),
      tone: chgTone(t.margin_change_shares),
    },
    { label: "融券餘額", value: formatShares(t.short_balance_shares) },
    {
      label: "融券增減",
      value: formatChangeShares(t.short_change_shares),
      tone: chgTone(t.short_change_shares),
    },
    { label: "券資比", value: formatPct(t.margin_short_ratio_pct) },
  ]

  return (
    <section className="rounded-xl border border-rose-500/30 bg-rose-500/[0.04] p-4 shadow-inner">
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-rose-200">
          融資融券分析
          <span className="ml-2 text-[11px] font-normal text-rose-300/70">
            {stockId} · 個股 70% / 大盤 30%
          </span>
        </h3>
        {analysis.weight_ratio ? (
          <span className="rounded-full border border-rose-400/40 bg-rose-500/15 px-2 py-0.5 text-[10px] font-medium text-rose-100">
            {analysis.weight_ratio}
          </span>
        ) : null}
      </header>

      {/* 表格 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-zinc-700 bg-zinc-900/40 p-3 text-sm sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3">
            <span className="text-slate-400">{row.label}</span>
            <span
              className={`font-mono font-semibold ${
                row.tone === "green"
                  ? "text-emerald-300"
                  : row.tone === "red"
                  ? "text-rose-300"
                  : "text-slate-100"
              }`}
            >
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {/* 個股解讀（70%） */}
      {analysis.stock_interpretation ? (
        <p className="mt-3 text-sm leading-relaxed text-slate-200">
          {analysis.stock_interpretation}
        </p>
      ) : null}

      {analysis.stock_conclusion ? (
        <p className="mt-2 text-base font-bold text-rose-200">
          → {analysis.stock_conclusion}
        </p>
      ) : null}

      {/* 大盤摘要（30%） */}
      {analysis.market_summary ? (
        <div className="mt-3 rounded-lg border border-zinc-700/60 bg-zinc-800/40 px-3 py-2 text-xs text-slate-400">
          <span className="font-semibold text-slate-300">大盤融資環境：</span>
          {analysis.market_summary}
        </div>
      ) : null}

      {/* 風險提示 */}
      {analysis.risk_note ? (
        <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-200">
          <span className="font-semibold">風險提示：</span>
          {analysis.risk_note}
        </div>
      ) : null}
    </section>
  )
}

function formatPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return v >= 1000 ? v.toFixed(0) : v.toFixed(2)
}

function formatShares(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return `${Math.round(v).toLocaleString()} 張`
}

function formatChangeShares(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  const sign = v > 0 ? "+" : v < 0 ? "" : ""
  return `${sign}${Math.round(v).toLocaleString()} 張`
}

function formatPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return `${v.toFixed(2)}%`
}

function chgTone(v: number | null | undefined): "green" | "red" | "neutral" {
  if (v == null || v === 0) return "neutral"
  // 台股慣例：融資/融券增加 = 散戶活躍 = 紅色；減少 = 退場 = 綠色
  return v > 0 ? "red" : "green"
}

function SignalCardGrid({
  items,
  realtimeQuotes,
  emptyText,
}: {
  items: SignalWatchlistItem[]
  realtimeQuotes: Map<string, RealtimeQuote>
  emptyText: string
}) {
  if (items.length === 0) {
    return <p className="text-sm text-slate-400">{emptyText}</p>
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item) => (
        <SignalCard
          key={item.stock}
          item={item}
          quote={realtimeQuotes.get(item.stock)}
        />
      ))}
    </div>
  )
}

function HelpDefinition({
  title,
  tone = "text-slate-200",
  children,
}: {
  title: string
  tone?: string
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-950/30 px-3 py-2">
      <div className={`font-medium ${tone}`}>{title}</div>
      <div className="mt-0.5 text-slate-400">{children}</div>
    </div>
  )
}

export default function DailySignalsPanel({
  initialSnapshot,
  initialSnapshotLoaded = false,
  initialJob,
  initialJobLoaded = false,
}: {
  initialSnapshot?: SignalSnapshotResponse | null
  initialSnapshotLoaded?: boolean
  initialJob?: SignalJobResponse | null
  initialJobLoaded?: boolean
}) {
  const { status: authStatus } = useAuth()
  const [snapshot, setSnapshot] = useState<SignalSnapshotResponse | null>(initialSnapshot ?? null)
  const [snapshotLoading, setSnapshotLoading] = useState(!initialSnapshotLoaded)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(true)
  const [hasNewSignals, setHasNewSignals] = useState(false)
  const [bumpKey, setBumpKey] = useState(0)
  const [regenerating, setRegenerating] = useState(false)
  const [regenerateError, setRegenerateError] = useState<string | null>(null)
  const [regenerateQuota, setRegenerateQuota] = useState<SignalRegenerateQuotaResponse | null>(null)
  const [labelHelpOpen, setLabelHelpOpen] = useState(false)

  const { job } = useSignalJobPolling(bumpKey, initialJob ?? null, initialJobLoaded)
  const jobStatus = job?.status

  // 初始展開狀態：讀 localStorage（預設 collapse）
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(COLLAPSED_KEY)
      if (saved === "false") setCollapsed(false)
    } catch {
      // ignore
    }
  }, [])

  const persistCollapsed = useCallback((next: boolean) => {
    setCollapsed(next)
    try {
      window.localStorage.setItem(COLLAPSED_KEY, String(next))
    } catch {
      // ignore
    }
  }, [])

  // 載入最新 snapshot；當 job 進入完整或部分完成的 terminal state 時重新拉一次
  const loadSnapshot = useCallback(async () => {
    setSnapshotLoading(true)
    setSnapshotError(null)
    try {
      const data = await fetchLatestSignalSnapshot({ bypassCache: true })
      setSnapshot(data)
    } catch (err) {
      setSnapshotError(err instanceof Error ? err.message : "訊號清單載入失敗")
    } finally {
      setSnapshotLoading(false)
    }
  }, [])

  useEffect(() => {
    if (initialSnapshotLoaded) return
    void loadSnapshot()
  }, [initialSnapshotLoaded, loadSnapshot])

  const loadRegenerateQuota = useCallback(async () => {
    if (authStatus !== "authenticated") {
      setRegenerateQuota(null)
      return
    }
    try {
      const data = await fetchSignalRegenerateQuota()
      setRegenerateQuota(data)
    } catch {
      setRegenerateQuota(null)
    }
  }, [authStatus])

  useEffect(() => {
    void loadRegenerateQuota()
  }, [loadRegenerateQuota, bumpKey, jobStatus])

  // 偵測 job 完成 → 重新拉 snapshot（partial failure 仍有可用的部分 snapshot）
  useEffect(() => {
    if (jobStatus === "done" || jobStatus === "partial_failure") {
      void loadSnapshot()
    }
  }, [jobStatus, loadSnapshot])

  // 比對 last_seen → pulse badge
  useEffect(() => {
    if (!snapshot) {
      setHasNewSignals(false)
      return
    }
    try {
      const lastSeen = window.localStorage.getItem(LAST_SEEN_KEY)
      setHasNewSignals(!lastSeen || lastSeen < snapshot.snapshot_date)
    } catch {
      setHasNewSignals(false)
    }
  }, [snapshot])

  // 點擊任何 tab → 標記為已讀（清掉 pulse）
  const markSeen = useCallback(() => {
    if (!snapshot) return
    try {
      window.localStorage.setItem(LAST_SEEN_KEY, snapshot.snapshot_date)
    } catch {
      // ignore
    }
    setHasNewSignals(false)
  }, [snapshot])

  const handleToggleCollapse = useCallback(() => {
    const next = !collapsed
    persistCollapsed(next)
    if (!next) {
      // 展開時自動標已讀
      markSeen()
    }
  }, [collapsed, markSeen, persistCollapsed])

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true)
    setRegenerateError(null)
    try {
      await regenerateSignals()
      // 成功觸發後立刻清掉前一份 snapshot，避免使用者看到 stale 資料停留到新 job 完成；
      // 失敗（429 / 409）不清，保留前次清單可用。
      setSnapshot(null)
      setSnapshotError(null)
      setSnapshotLoading(false)
      setHasNewSignals(false)
      void loadRegenerateQuota()
      // 觸發 polling 重啟
      setBumpKey((k) => k + 1)
    } catch (err) {
      setRegenerateError(err instanceof Error ? err.message : "重新產生失敗")
      void loadRegenerateQuota()
    } finally {
      setRegenerating(false)
    }
  }, [loadRegenerateQuota])

  const watchlist = useMemo(() => snapshot?.data.watchlist ?? [], [snapshot])
  const summary = snapshot?.data.summary
  const processingSummary = summary?.processing_summary
  const leaderCount = summary?.leader_count ?? watchlist.filter((w) => w.type === "LEADER").length
  const followerCount =
    summary?.follower_count ?? watchlist.filter((w) => w.type === "FOLLOWER").length
  const laggardCount =
    summary?.laggard_count ?? watchlist.filter((w) => w.type === "LAGGARD").length

  const isAuthed = authStatus === "authenticated"
  const isJobActive = jobStatus === "pending" || jobStatus === "running"
  const isIncomplete = isSignalProcessingIncomplete(processingSummary, jobStatus)
  const quotaReached = isAuthed && !!regenerateQuota?.disabled

  // 「重新產生」按鈕狀態（spec §13.5）
  let regenerateLabel = "重新產生"
  let regenerateDisabled = false
  if (authStatus === "loading") {
    regenerateLabel = "重新產生"
    regenerateDisabled = true
  } else if (!isAuthed) {
    regenerateLabel = "重新產生（需登入）"
    regenerateDisabled = true
  } else if (isJobActive) {
    regenerateLabel = "產生中…"
    regenerateDisabled = true
  } else if (regenerating) {
    regenerateLabel = "送出中…"
    regenerateDisabled = true
  } else if (quotaReached) {
    regenerateLabel = `重新產生（今日已達 ${regenerateQuota?.daily_limit ?? 3} 次）`
    regenerateDisabled = true
  }

  // P3 snapshot 以 recommendation_rank 為正式順序；歷史 snapshot 沒 rank
  // 時維持既有領漲 → 跟漲 → 補漲順序。
  const allSignals = useMemo(() => {
    const order: Record<string, number> = { LEADER: 0, FOLLOWER: 1, LAGGARD: 2 }
    return [...watchlist].sort((a, b) => {
      const aRank = a.recommendation_rank
      const bRank = b.recommendation_rank
      if (typeof aRank === "number" && typeof bRank === "number") {
        return aRank - bRank
      }
      if (typeof aRank === "number") return -1
      if (typeof bRank === "number") return 1
      return (order[a.type ?? ""] ?? 99) - (order[b.type ?? ""] ?? 99)
    })
  }, [watchlist])

  // 收集所有 SignalCard 顯示的股票 ID，一次抓 batch realtime quote。
  // 折疊狀態下不抓（避免無謂打 API），展開後 hook 會自動觸發。
  const watchlistStockIds = useMemo(
    () => (collapsed ? [] : watchlist.map((w) => w.stock).filter(Boolean)),
    [collapsed, watchlist],
  )
  const realtimeQuotes = useRealtimeQuotes(watchlistStockIds, REALTIME_INTERVAL_MS)

  return (
    <section className="rounded-lg border border-zinc-700 bg-zinc-700/50">
      <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleToggleCollapse}
            className="flex items-center gap-2 text-base font-semibold text-slate-100 hover:text-sky-300"
            aria-expanded={!collapsed}
          >
            <span aria-hidden className="text-slate-400">
              {collapsed ? "▸" : "▾"}
            </span>
            <span>今日捕獲的大魚尾</span>
          </button>
          <button
            type="button"
            onClick={() => setLabelHelpOpen(true)}
            aria-label="查看魚尾標籤說明"
            className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-600 text-[11px] font-semibold text-slate-400 hover:border-sky-400 hover:text-sky-300"
          >
            ?
          </button>
          {hasNewSignals && (
            <span className="ml-1 inline-flex items-center gap-1">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <span className="text-xs text-emerald-400">新</span>
            </span>
          )}
          {snapshot && (
            <span className="text-xs text-slate-400">
              {snapshot.snapshot_date}
              {snapshot.generated_at ? ` · ${formatTpeDateTime(snapshot.generated_at)}` : ""}
            </span>
          )}
          <RegimeBadge
            regime={snapshot?.data.market_context?.market_regime}
            label={snapshot?.data.market_context?.market_regime_label}
            reason={snapshot?.data.market_context?.market_regime_reason}
          />
          <span className="text-[11px] text-slate-500">每日將於晚上 21:30 更新</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/signals/archive"
            className="inline-flex items-center rounded border border-slate-600 bg-slate-800/50 px-3 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            30日追蹤
          </Link>
          {SHOW_REGENERATE_BUTTON && (
            <>
              <button
                type="button"
                onClick={handleRegenerate}
                disabled={regenerateDisabled}
                className="inline-flex items-center rounded border border-sky-500/50 bg-sky-500/10 px-3 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {regenerateLabel}
              </button>
              {isAuthed && regenerateQuota && (
                <span className="text-[11px] text-slate-400">
                  今日剩餘 {regenerateQuota.remaining_count}/{regenerateQuota.daily_limit}
                </span>
              )}
            </>
          )}
        </div>
      </header>

      <Dialog.Root open={labelHelpOpen} onOpenChange={setLabelHelpOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
          <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(94vw,52rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl sm:p-6">
            <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-800 pb-3">
              <div>
                <Dialog.Title className="text-lg font-semibold text-slate-100">
                  魚尾標籤說明
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-xs leading-5 text-slate-400">
                  標籤混合了後端計算分類、動能分數與 AI 證據解讀；不是每個 label 都是買進訊號。
                </Dialog.Description>
              </div>
              <Dialog.Close className="shrink-0 rounded border border-slate-600 bg-slate-800/50 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700">
                關閉 ✕
              </Dialog.Close>
            </div>

            <div className="space-y-4 text-xs leading-6 text-slate-300 sm:text-sm">
              <section>
                <h3 className="mb-2 font-semibold text-slate-100">股票角色</h3>
                <div className="grid gap-2 sm:grid-cols-3">
                  <HelpDefinition title="領漲 LEADER" tone="text-rose-200">
                    產業或個股最強，通常有高動能、相對強度、法人或量能確認。
                  </HelpDefinition>
                  <HelpDefinition title="跟漲 FOLLOWER" tone="text-sky-200">
                    不是最強龍頭，但產業正在動，個股動能維持或改善。
                  </HelpDefinition>
                  <HelpDefinition title="補漲 LAGGARD" tone="text-amber-200">
                    產業很強但個股相對落後，最近開始出現補漲條件。
                  </HelpDefinition>
                </div>
              </section>

              <section className="rounded-lg border border-violet-500/25 bg-violet-500/5 p-3">
                <h3 className="mb-1 font-semibold text-violet-200">動能分數與階段</h3>
                <p>
                  動能分數是 0～100 的後端計算排名，不是報酬率預測，也不是勝率。它綜合價格動能 30、RS 25、法人資金 20、量價品質 15、基本面 10 分，並扣除過熱或轉弱風險。
                </p>
                <p className="mt-1 text-slate-200">
                  A ≥ 75　·　B = 60～74.9　·　C = 45～59.9　·　D &lt; 45
                </p>
                <p className="mt-1 text-slate-400">
                  階段：啟動、加速、趨勢延續、過熱、轉弱。轉弱與過熱會優先標示，所以分數高不代表一定適合追價。
                </p>
              </section>

              <section>
                <h3 className="mb-2 font-semibold text-slate-100">積極／正常／保留</h3>
                <div className="grid gap-2 sm:grid-cols-3">
                  <HelpDefinition title="積極" tone="text-emerald-200">
                    目前環境與個股證據較支持積極關注。
                  </HelpDefinition>
                  <HelpDefinition title="正常" tone="text-slate-200">
                    條件尚可，維持正常觀察。
                  </HelpDefinition>
                  <HelpDefinition title="保留" tone="text-amber-200">
                    不是剔除，而是提醒要更謹慎、嚴守紀律，不宜只靠這檔積極操作。
                  </HelpDefinition>
                </div>
              </section>

              <section>
                <h3 className="mb-2 font-semibold text-slate-100">觀察維度</h3>
                <div className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
                  <HelpDefinition title="題材 強／中／弱">
                    AI 查證公司業務是否符合市場題材，以及題材延續性。
                  </HelpDefinition>
                  <HelpDefinition title="資金 強／中／弱">
                    法人與市場資金是否提供支持，依證據綜合判斷。
                  </HelpDefinition>
                  <HelpDefinition title="籌碼">
                    集中＝法人連買且量價配合；轉弱／散戶過熱＝籌碼出現風險；軋空潛力＝融資下降、融券增加且股價未跌。
                  </HelpDefinition>
                  <HelpDefinition title="融券 正向／中性／負向">
                    融資融券結構對股價延續的整體解讀；正向不是保證上漲。
                  </HelpDefinition>
                  <HelpDefinition title="技術">
                    突破、上升、轉強代表價格結構較健康；盤整、偏弱、出貨代表確認度或風險較高。
                  </HelpDefinition>
                </div>
              </section>

              <section className="rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
                <h3 className="mb-1 font-semibold text-slate-100">為什麼有些卡片是 3 個、有些是 5 個？</h3>
                <p>
                  首頁卡片固定顯示動能、觀察積極度、題材；資金／籌碼／融券／技術只顯示判定為正向的綠色 label，其他狀態會隱藏。因此顯示 3 個通常代表沒有額外正向維度，顯示 5 個代表還有兩個維度被判定為正向。
                </p>
                <p className="mt-1 text-slate-400">
                  點開詳情後則是五個分析面向：題材、資金、籌碼、融券、技術。若某段沒有有效內容，該段會暫不顯示；每段通常有 3～5 個分析 bullet，融券段可能較少。
                </p>
              </section>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      {(isJobActive || isIncomplete || regenerateError) && (
        <div className="border-t border-zinc-700 px-4 py-2">
          {isJobActive && job && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-300">
                <span>{job.progress_label ?? job.current_stage ?? "進行中"}</span>
                <span>{Math.round(job.progress_pct ?? 0)}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700/60">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-[width]"
                  style={{ width: `${Math.min(100, Math.max(0, job.progress_pct ?? 0))}%` }}
                />
              </div>
            </div>
          )}
          {isIncomplete && (
            <SignalIncompleteWarning summary={processingSummary} />
          )}
          {regenerateError && (
            <p className="mt-1 text-xs text-rose-300">{regenerateError}</p>
          )}
        </div>
      )}

      {!collapsed && (
        <div className="border-t border-zinc-700 px-4 py-4">
          {snapshotLoading && (
            <p className="text-sm text-slate-400">載入中…</p>
          )}
          {snapshotError && !snapshotLoading && (
            <p className="text-sm text-rose-300">{snapshotError}</p>
          )}
          {!snapshotLoading && !snapshotError && !snapshot && isJobActive && (
            <p className="text-sm text-slate-300">
              正在重新產生訊號清單，請稍候…完成後會自動更新。
            </p>
          )}
          {!snapshotLoading && !snapshotError && !snapshot && !isJobActive && (
            <p className="text-sm text-slate-400">
              目前尚無訊號清單。{isAuthed ? "點擊上方「重新產生」即可建立第一份清單。" : "請等待排程產生或登入後手動觸發。"}
            </p>
          )}
          {!snapshotLoading && !snapshotError && snapshot && (
            <div className="flex flex-col gap-3">
              {/* chip 配色對齊 SignalEmotionCard：領漲 rose / 跟漲 amber / 補漲 sky（紅/金黃/藍，刻意不用綠） */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="inline-flex items-center rounded border border-rose-600/60 bg-rose-900/40 px-2 py-0.5 font-medium text-rose-100">
                  領漲 {leaderCount}
                </span>
                <span className="inline-flex items-center rounded border border-amber-500/50 bg-amber-900/30 px-2 py-0.5 font-medium text-amber-100">
                  跟漲 {followerCount}
                </span>
                <span className="inline-flex items-center rounded border border-sky-600/50 bg-sky-900/30 px-2 py-0.5 font-medium text-sky-100">
                  補漲 {laggardCount}
                </span>
              </div>
              <SignalCardGrid
                items={allSignals}
                realtimeQuotes={realtimeQuotes}
                emptyText="本日無訊號。"
              />
            </div>
          )}
        </div>
      )}
    </section>
  )
}
