"use client"

import { useCallback, useEffect, useState } from "react"

import {
  fetchShadowHistory,
  fetchShadowPendingActions,
  fetchShadowPortfolio,
  SHADOW_STRATEGY_VERSIONS,
  type ShadowCompletedTrade,
  type ShadowHistoryDay,
  type ShadowOrderAction,
  type ShadowPendingAction,
  type ShadowPortfolio,
  type ShadowStrategyVersion,
} from "@/lib/api"

const STRATEGY_VERSION_STORAGE_KEY = "always-stock:shadow-portfolio:strategy-version"

const STRATEGY_META: Record<
  ShadowStrategyVersion,
  { label: string; badge: string; description: string }
> = {
  v1_frozen: {
    label: "v1（Dual-Engine）",
    badge: "ACTIVE",
    description:
      "2026-09-07 起的新週期：把資金分成 Continuation、Pullback、Opportunity 三個桶，" +
      "用報告證據找強勢延續股，也保留拉回回穩與換股機會。",
  },
  FORWARD_V1_202609: {
    label: "FORWARD_V1_202609",
    badge: "FORWARD TEST",
    description:
      "2026-09-07 起正式 Forward Test：用較寬鬆的原始進場規則追蹤候選股，讓贏家續抱與加碼；" +
      "動能超過 80 仍可進場；不固定停利、不攤平，單檔成本最多占總權益 50%。",
  },
}

function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 0 })
}

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

// 台股慣例：紅漲綠跌（跟美股相反）
function returnTone(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-slate-400"
  if (value > 0) return "text-red-400"
  if (value < 0) return "text-green-400"
  return "text-slate-300"
}

const ACTION_META: Record<ShadowOrderAction, { emoji: string; label: string; tone: string }> = {
  BUY: { emoji: "🟢", label: "BUY", tone: "border-emerald-500/50 bg-emerald-500/10" },
  ADD: { emoji: "🔵", label: "ADD", tone: "border-sky-500/50 bg-sky-500/10" },
  SELL: { emoji: "🔴", label: "SELL", tone: "border-rose-500/50 bg-rose-500/10" },
}

const EXIT_REASON_LABELS: Record<string, string> = {
  P4_STOP: "P4 判定失效",
  OFFICIAL_EXIT: "追蹤期滿",
  TAKE_PROFIT: "固定停利 +10%",
  REAL_POSITION_STOP_LOSS: "真實停損 -8%",
  FORWARD_HIGH_MOMENTUM_ROTATION: "Forward 高動能換股",
  CYCLE_RESET: "循環期滿強制平倉",
  // v1_frozen Dual-Engine（2026-09-07 起）專屬出場原因
  CONTINUATION_FAST_STOP: "Continuation 快速停損 -5%",
  CONTINUATION_NO_FOLLOW_THROUGH: "Continuation 未證明延續（Prove-it 失敗）",
  CONTINUATION_TRAILING_EXIT: "Continuation 從高點回吐出場",
  PULLBACK_REAL_STOP: "Pullback 真實停損 -8%",
  PERIOD_END_SETTLEMENT: "回測期末結算",
}

function StatBox({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${tone ?? "text-slate-100"}`}>{value}</p>
    </div>
  )
}

function ActionCard({ action }: { action: ShadowPendingAction }) {
  const meta = ACTION_META[action.action]
  return (
    <article className={`rounded-lg border p-3 ${meta.tone}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-100">
          {meta.emoji} {meta.label} <span className="font-mono">{action.stock_id}</span> {action.stock_name}
        </span>
        <span className="shrink-0 text-[11px] text-slate-400">
          {action.units} 單位{action.planned_amount ? `・約 ${formatMoney(action.planned_amount)} 元` : ""}
        </span>
      </div>
      {action.entry_pattern && (
        <p className="mt-1 text-xs text-slate-400">進場型態：{action.entry_pattern}</p>
      )}
      {action.reason && <p className="mt-1 text-xs leading-5 text-slate-400">原因：{action.reason}</p>}
      <p className="mt-2 text-[11px] text-slate-500">
        訊號日 {action.signal_date} → 預計執行 {action.scheduled_execution_date}
      </p>
    </article>
  )
}

function TradeCard({ trade }: { trade: ShadowCompletedTrade }) {
  return (
    <article className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-100">
          <span className="font-mono">{trade.stock_id}</span> {trade.stock_name}
        </span>
        <span className={`text-sm font-semibold ${returnTone(trade.realized_return_pct)}`}>
          {formatPct(trade.realized_return_pct)}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
        <span>買進 {trade.entry_execution_date} @ {trade.entry_price.toFixed(2)}</span>
        <span>賣出 {trade.exit_execution_date} @ {trade.exit_price.toFixed(2)}</span>
        <span>持有 {trade.holding_days} 天</span>
        <span>損益 {formatMoney(trade.realized_pnl)} 元</span>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        進場型態 {trade.entry_type}・進場時動能 {trade.entry_momentum?.toFixed(1) ?? "—"}・
        進場時報酬 {formatPct(trade.entry_mark_to_market_return)}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-300">
          出場原因：{EXIT_REASON_LABELS[trade.exit_reason] ?? trade.exit_reason}
        </span>
        {trade.followed_by_rotation && (
          <span className="rounded border border-amber-500/50 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
            🔄 賣出後同天換股
          </span>
        )}
        <span className="rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-400">
          第 {trade.cycle_number} 循環
        </span>
      </div>
    </article>
  )
}

function HistoryDayRow({
  day,
  expanded,
  onToggle,
}: {
  day: ShadowHistoryDay
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div className="border-b border-slate-800/80 last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="grid w-full grid-cols-[auto_1fr_auto_auto_auto_auto] items-center gap-2 px-3 py-2.5 text-left text-xs hover:bg-slate-800/40 sm:gap-4"
      >
        <span aria-hidden className="text-slate-500">{expanded ? "▾" : "▸"}</span>
        <span className="font-mono text-slate-200">{day.trade_date}</span>
        <span className={returnTone(day.daily_return_pct)}>{formatPct(day.daily_return_pct)}</span>
        <span className={returnTone(day.total_return_pct)}>{formatPct(day.total_return_pct)}</span>
        <span className="text-slate-400">{formatMoney(day.total_equity)}</span>
        <span className="text-right text-slate-500">{day.executed_orders.length} 筆動作</span>
      </button>

      {expanded && (
        <div className="grid gap-3 border-t border-slate-800 bg-slate-950/40 px-4 py-3 sm:grid-cols-2">
          {day.settlement_reset && (
            <p className="sm:col-span-2 rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              期末結算：已按期末可用最低價全部賣出；結算後下一循環本金重設為 {formatMoney(day.settlement_cash)} 元。
            </p>
          )}
          <div>
            <p className="mb-2 text-[11px] font-semibold text-slate-300">當日成交動作</p>
            {day.executed_orders.length === 0 ? (
              <p className="text-xs text-slate-500">當日沒有成交。</p>
            ) : (
              <div className="space-y-2">
                {day.executed_orders.map((order) => {
                  const meta = ACTION_META[order.action]
                  return (
                    <div key={order.id} className={`rounded border p-2 ${meta.tone}`}>
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="font-semibold text-slate-100">
                          {meta.emoji} {meta.label} <span className="font-mono">{order.stock_id}</span> {order.stock_name}
                        </span>
                        <span className="text-slate-300">
                          {order.execution_price !== null ? `@ ${order.execution_price.toFixed(2)}` : "—"}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-500">
                        {order.reason ?? order.entry_pattern ?? "—"}・{order.units} 單位
                      </p>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <div>
            <p className="mb-2 text-[11px] font-semibold text-slate-300">當日完成交易</p>
            {day.completed_trades.length === 0 ? (
              <p className="text-xs text-slate-500">當日沒有平倉交易。</p>
            ) : (
              <div className="space-y-2">
                {day.completed_trades.map((trade) => <TradeCard key={trade.id} trade={trade} />)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function ShadowPortfolioPage() {
  const [strategyVersion, setStrategyVersion] = useState<ShadowStrategyVersion>("v1_frozen")

  // 讀 localStorage 記住上次選擇
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STRATEGY_VERSION_STORAGE_KEY)
      if (saved && (SHADOW_STRATEGY_VERSIONS as readonly string[]).includes(saved)) {
        setStrategyVersion(saved as ShadowStrategyVersion)
      }
    } catch {
      // ignore
    }
  }, [])

  const selectStrategyVersion = useCallback((next: ShadowStrategyVersion) => {
    setStrategyVersion(next)
    try {
      window.localStorage.setItem(STRATEGY_VERSION_STORAGE_KEY, next)
    } catch {
      // ignore
    }
  }, [])

  const [portfolio, setPortfolio] = useState<ShadowPortfolio | null>(null)
  const [actions, setActions] = useState<ShadowPendingAction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [history, setHistory] = useState<Awaited<ReturnType<typeof fetchShadowHistory>> | null>(null)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyCollapsed, setHistoryCollapsed] = useState(false)
  const [expandedHistoryDate, setExpandedHistoryDate] = useState<string | null>(null)
  const [historyStartInput, setHistoryStartInput] = useState("")
  const [historyEndInput, setHistoryEndInput] = useState("")
  const [historyRequestedStart, setHistoryRequestedStart] = useState("")
  const [historyRequestedEnd, setHistoryRequestedEnd] = useState("")
  const [strategyHelpCollapsed, setStrategyHelpCollapsed] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    Promise.all([
      fetchShadowPortfolio({ strategyVersion }, { signal: controller.signal }),
      fetchShadowPendingActions({ strategyVersion }, { signal: controller.signal }),
    ])
      .then(([p, a]) => {
        setPortfolio(p)
        setActions(a.actions)
        setError(null)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "模擬交易資料載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [strategyVersion])

  useEffect(() => {
    const controller = new AbortController()
    async function run() {
      setHistoryLoading(true)
      setHistoryError(null)
      try {
        const res = await fetchShadowHistory(
          {
            strategyVersion,
            startDate: historyRequestedStart || undefined,
            endDate: historyRequestedEnd || undefined,
          },
          { signal: controller.signal },
        )
        setHistory(res)
        setHistoryStartInput(res.start_date ?? "")
        setHistoryEndInput(res.end_date ?? "")
        setExpandedHistoryDate(null)
      } catch (reason: unknown) {
        if (!controller.signal.aborted) {
          setHistoryError(reason instanceof Error ? reason.message : "歷史回放資料載入失敗")
        }
      } finally {
        if (!controller.signal.aborted) setHistoryLoading(false)
      }
    }
    void run()
    return () => controller.abort()
  }, [strategyVersion, historyRequestedStart, historyRequestedEnd])

  const applyHistoryRange = () => {
    if (historyStartInput && historyEndInput && historyStartInput > historyEndInput) {
      setHistoryError("歷史回放的起始日不能晚於結束日")
      return
    }
    setHistoryError(null)
    setHistoryRequestedStart(historyStartInput)
    setHistoryRequestedEnd(historyEndInput)
  }

  const resetHistoryRange = () => {
    setHistoryError(null)
    setHistoryStartInput("")
    setHistoryEndInput("")
    setHistoryRequestedStart("")
    setHistoryRequestedEnd("")
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-6 text-slate-100">
      <header className="mb-5">
        <h1 className="text-2xl font-semibold">魚尾模擬交易</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
          用固定虛擬資金（Paper Trading）模擬策略：不是真的下單，純粹追蹤「如果照這套規則交易，
          現在會是什麼結果」。
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {SHADOW_STRATEGY_VERSIONS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => selectStrategyVersion(v)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                strategyVersion === v
                  ? "border-sky-500/60 bg-sky-500/15 text-sky-100"
                  : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
              }`}
            >
              <span
                className={`mr-1.5 rounded px-1.5 py-0.5 text-[9px] font-semibold ${
                  v === "FORWARD_V1_202609"
                    ? "bg-amber-500/20 text-amber-200"
                    : "bg-slate-700/60 text-slate-300"
                }`}
              >
                {STRATEGY_META[v].badge}
              </span>
              {STRATEGY_META[v].label}
            </button>
          ))}
        </div>
        <p className="mt-2 max-w-3xl text-xs leading-5 text-slate-500">{STRATEGY_META[strategyVersion].description}</p>

        <section className="mt-4 rounded-lg border border-slate-800 bg-slate-900/40">
          <button
            type="button"
            onClick={() => setStrategyHelpCollapsed((collapsed) => !collapsed)}
            aria-expanded={!strategyHelpCollapsed}
            className="flex w-full items-center justify-between gap-3 p-3 text-left"
          >
            <span className="flex items-center gap-2 text-sm font-semibold text-slate-200">
              <span aria-hidden className="text-slate-400">{strategyHelpCollapsed ? "▸" : "▾"}</span>
              兩個交易策略怎麼運作？
            </span>
            <span className="text-xs text-slate-500">{strategyHelpCollapsed ? "展開說明" : "收合說明"}</span>
          </button>

          {!strategyHelpCollapsed && (
            <div className="grid gap-3 border-t border-slate-800 p-3 text-xs leading-5 text-slate-400 lg:grid-cols-2">
              <div className="rounded-lg border border-sky-900/50 bg-sky-950/20 p-3">
                <p className="font-semibold text-sky-200">v1（Dual-Engine，2026-09-07 起）</p>
                <p className="mt-2 font-medium text-slate-300">這套策略在做什麼？</p>
                <p className="mt-1">它把 600,000 元拆成三個用途不同的資金桶，不是看到動能分數高就直接買：</p>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  <li><span className="text-slate-300">Continuation 核心桶：</span>300,000 元，最多 3 檔，每檔 100,000 元，找報告判定為 HIGH 主題、領導股，且技術／相對強度／法人流向等證據至少有足夠正向支持的「正在延續」股票。</li>
                  <li><span className="text-slate-300">Pullback 核心桶：</span>100,000 元，最多 1 檔，找先回落、但價格與動能重新回穩的股票；還在下跌或尚未確認時只觀察，不急著買。</li>
                  <li><span className="text-slate-300">Opportunity 機會桶：</span>最多 300,000 元、最多 3 檔。Continuation 核心桶滿了後，若出現同樣高品質但更強的候選，才用可用現金進場或替換較弱的機會部位；不會寫死特定股票。</li>
                </ul>
                <p className="mt-2 font-medium text-slate-300">每天如何決策？</p>
                <ol className="mt-1 list-decimal space-y-1 pl-4">
                  <li>收盤後讀取當天報告與魚尾追蹤資料，建立候選排序。</li>
                  <li>符合報告 profile 的強勢延續股可直接進場；一般 Starter 則要等下一個評估日通過 prove-it 才確認，否則退出。這一版的確認是狀態確認，目前不另外投入第二筆資金。</li>
                  <li>持倉先檢查停損與失效；資金桶已滿時，只接受更強、且符合即時證據條件的換股。</li>
                </ol>
                <p className="mt-2 text-slate-500">風控：Starter 快速失敗約 -5%；確認後約 -8% 停損；profile 部位最多 -12%，獲利達 +10% 後從最高收盤回吐 12% 出場。Pullback 以實際持倉 -8% 停損。</p>
              </div>
              <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3">
                <p className="font-semibold text-amber-200">FORWARD_V1_202609（Forward Test）</p>
                <p className="mt-2 font-medium text-slate-300">這套策略在做什麼？</p>
                <p className="mt-1">它是從 2026-09-07 的收盤訊號開始獨立觀察的新版本，不把 v1 Dual-Engine 的歷史績效混進來；買賣會在下一個交易日模擬成交。它沿用原本的兩種進場型態：</p>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  <li><span className="text-slate-300">Early healthy pullback：</span>小幅拉回、仍在健康區間的候選。</li>
                  <li><span className="text-slate-300">Deep pullback：</span>較深幅度回落、但仍符合動能與 P4 條件的候選。</li>
                  <li><span className="text-slate-300">BUY：</span>符合進場條件就用一個 100,000 元單位建立部位；<span className="text-slate-300">ADD：</span>同一檔再次符合條件時才考慮加碼，而且目前部位必須已經獲利，絕不攤平。動能超過 80 的候選不會被上限直接排除。</li>
                </ul>
                <p className="mt-2 font-medium text-slate-300">它和 Dual-Engine 最大的差異</p>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  <li>不設固定停利，讓已經上漲的股票繼續發展；但仍有實際部位 -8% 停損與 P4／官方結束訊號。</li>
                  <li>最多同時持有 5 檔不同股票；不設總單位上限，但單檔成本不得超過當下總權益 50%，也不做 35 交易日循環重置。</li>
                  <li>滿倉時不普遍換股；只有候選當日重新被 P3 選中、動能超過 80、專用 entry score 至少 6，且比持有至少 2 個交易日的虧損弱部位高至少 5 分時，才允許每天換掉 1 檔。已獲利 +10% 以上的 winner 不會被換掉。</li>
                </ul>
              </div>
              <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-3 lg:col-span-2">
                <p className="font-semibold text-slate-300">兩套策略共用的回測／模擬成交方式</p>
                <ul className="mt-1 grid gap-x-6 gap-y-1 sm:grid-cols-2">
                  <li>晚上收盤後才產生訊號，所以 BUY／ADD 使用下一個交易日的最高價模擬成交。</li>
                  <li>SELL 也是晚上產生，所以使用下一個交易日的最低價模擬成交。</li>
                  <li>每次 ADD 都會增加投入成本，持倉報酬以新的加權平均成本計算。</li>
                  <li>回測區間最後一天會強制結算；歷史回放可用日期選擇器查看任意已累積的交易日區間。</li>
                </ul>
                <p className="mt-2 text-slate-500">簡單說：Dual-Engine 比較像「分桶管理、主動挑更強的延續股」；Forward Test 比較像「保留原始候選、讓贏家續抱並在獲利後加碼」。</p>
              </div>
            </div>
          )}
        </section>

        <p className="mt-3 rounded-lg border border-sky-800/40 bg-sky-950/20 p-3 text-xs leading-5 text-sky-200">
          <strong>重要</strong>
          ：以下「下一交易日動作」是收盤後根據今天資料做出的決策，代表「預計執行」，不是「已經
          成交」——實際成交要等下一個交易日行情出來，用當天最高價（買）／最低價（賣）模擬。
        </p>
      </header>

      {loading && <p className="text-sm text-slate-500">正在載入模擬交易資料…</p>}
      {error && (
        <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p>
      )}

      {portfolio && (
        <>
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7">
            <StatBox label="總權益" value={formatMoney(portfolio.total_equity)} />
            <StatBox
              label="目前循環報酬"
              value={formatPct(portfolio.total_return_pct)}
              tone={returnTone(portfolio.total_return_pct)}
            />
            <StatBox
              label="歷史回放報酬"
              value={historyLoading ? "載入中" : formatPct(history?.period_return_pct)}
              tone={returnTone(history?.period_return_pct)}
            />
            <StatBox label="現金" value={formatMoney(portfolio.cash)} />
            <StatBox label="持股數" value={`${portfolio.position_count} / ${portfolio.max_stocks}`} />
            <StatBox
              label="單位數"
              value={
                portfolio.max_total_units === null
                  ? `${portfolio.total_units}（無上限）`
                  : `${portfolio.total_units} / ${portfolio.max_total_units}`
              }
            />
            <StatBox
              label="已實現損益"
              value={formatMoney(portfolio.realized_pnl_cumulative)}
              tone={returnTone(portfolio.realized_pnl_cumulative)}
            />
          </section>
          <p className="mt-2 text-[11px] text-slate-500">
            {portfolio.as_of_trade_date && (
              <>資料截至 {portfolio.as_of_trade_date}・起始本金 {formatMoney(portfolio.initial_capital)} 元・</>
            )}
            {portfolio.cycle_length_trading_days !== null ? (
              <>
                第 {portfolio.cycle_number} 個循環
                {portfolio.cycle_trading_days_elapsed !== null && (
                  <>（第 {portfolio.cycle_trading_days_elapsed} / {portfolio.cycle_length_trading_days} 個交易日）</>
                )}
              </>
            ) : (
              <>無強制循環重置</>
            )}
            {portfolio.max_position_exposure_pct !== null && (
              <>・單檔曝險上限 {(portfolio.max_position_exposure_pct * 100).toFixed(0)}%</>
            )}
          </p>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-slate-200">下一交易日動作</h2>
            {actions.length === 0 ? (
              <p className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-500">
                今日沒有需要調整的部位——目前策略建議：維持現有 Portfolio。
              </p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {actions.map((a) => (
                  <ActionCard key={a.id} action={a} />
                ))}
              </div>
            )}
          </section>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-slate-200">
              目前持倉（{portfolio.positions.length}）
            </h2>
            {portfolio.positions.length === 0 ? (
              <p className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-500">
                目前沒有持倉。
              </p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {portfolio.positions.map((pos) => (
                  <article
                    key={pos.stock_id}
                    className="rounded-lg border border-slate-800 bg-slate-900/50 p-3"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-slate-100">
                        <span className="font-mono">{pos.stock_id}</span> {pos.stock_name}
                      </span>
                      <span className={`text-sm font-semibold ${returnTone(pos.unrealized_return_pct)}`}>
                        {formatPct(pos.unrealized_return_pct)}
                      </span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                      <span>{pos.units} 單位</span>
                      <span>成本 {pos.average_entry_price.toFixed(2)}</span>
                      <span>現價 {pos.latest_close?.toFixed(2) ?? "—"}</span>
                      <span>未實現損益 {formatMoney(pos.unrealized_pnl)}</span>
                    </div>
                    <p className="mt-2 text-[11px] text-slate-500">首次進場 {pos.first_seen_date}</p>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="mt-6 rounded-lg border border-slate-800">
            <button
              type="button"
              onClick={() => setHistoryCollapsed((collapsed) => !collapsed)}
              aria-expanded={!historyCollapsed}
              className="flex w-full items-center justify-between gap-2 p-3 text-left"
            >
              <span className="flex items-center gap-2 text-sm font-semibold text-slate-200">
                <span aria-hidden className="shrink-0 text-slate-400">{historyCollapsed ? "▸" : "▾"}</span>
                {history
                  ? `歷史 ${history.trading_day_count} 個交易日回放（${history.start_date ?? "—"} ～ ${history.end_date ?? "—"}）`
                  : "歷史交易日回放"}
              </span>
              <span className="shrink-0 text-xs text-slate-500">
                {historyCollapsed ? "點擊展開" : history ? `${history.trading_day_count} 個實際交易日` : "收合"}
              </span>
            </button>

            {!historyCollapsed && (
              <div className="border-t border-slate-800 p-3">
                <p className="mb-3 text-xs leading-5 text-slate-500">
                  歷史資料會持續 append，不會因為新週期開始而覆蓋舊紀錄。未指定日期時優先顯示最近一個已完成結算區間，
                  尚未有結算時才顯示最新 25 個有回放資料的交易日；也可以自訂任意起訖日。
                  點擊任一交易日可查看當日權益、成交動作與完成交易。
                </p>
                <div className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border border-slate-800 bg-slate-950/30 p-3">
                  <label className="grid gap-1 text-[11px] text-slate-500">
                    起始日
                    <input
                      type="date"
                      value={historyStartInput}
                      onChange={(event) => setHistoryStartInput(event.target.value)}
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                    />
                  </label>
                  <label className="grid gap-1 text-[11px] text-slate-500">
                    結束日
                    <input
                      type="date"
                      value={historyEndInput}
                      onChange={(event) => setHistoryEndInput(event.target.value)}
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={applyHistoryRange}
                    className="rounded bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600"
                  >
                    查詢區間
                  </button>
                  <button
                    type="button"
                    onClick={resetHistoryRange}
                    className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                  >
                    最新 25 個交易日
                  </button>
                </div>
                {historyLoading && <p className="text-sm text-slate-500">正在載入歷史回放…</p>}
                {historyError && (
                  <p className="rounded border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-100">
                    {historyError}
                  </p>
                )}
                {!historyLoading && !historyError && history && history.trading_days.length === 0 && (
                  <p className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-500">
                    這個策略在指定期間沒有歷史回放資料。
                  </p>
                )}
                {!historyLoading && !historyError && history && history.trading_days.length > 0 && (
                  <>
                    <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
                      <StatBox label="期間起始權益" value={formatMoney(history.start_equity)} />
                      <StatBox label="期間結束權益" value={formatMoney(history.end_equity)} />
                      <StatBox label="期間報酬" value={formatPct(history.period_return_pct)} tone={returnTone(history.period_return_pct)} />
                      <StatBox
                        label="勝率（已平倉）"
                        value={history.win_rate_pct === null ? "—" : `${history.win_rate_pct.toFixed(2)}%`}
                        tone={history.win_rate_pct !== null && history.win_rate_pct >= 50 ? "text-red-300" : "text-slate-100"}
                      />
                      <StatBox label="成交動作" value={`${history.trading_days.reduce((sum, day) => sum + day.executed_orders.length, 0)} 筆`} />
                    </div>
                    {history.settlement_cash !== null && (
                      <p className="mb-3 text-[11px] text-amber-300/80">
                        {history.trading_days.filter((day) => day.settlement_reset).map((day) => day.trade_date).join("、")} 期末已按最低價全部賣出，下一循環資產重設為 {formatMoney(history.settlement_cash)} 元；期間報酬依實際結算成交價計算。
                      </p>
                    )}
                    <div className="overflow-hidden rounded-lg border border-slate-800">
                      <div className="grid grid-cols-[auto_1fr_auto_auto_auto_auto] gap-2 bg-slate-900 px-3 py-2 text-[10px] text-slate-500 sm:gap-4">
                        <span />
                        <span>交易日</span>
                        <span>日報酬</span>
                        <span>累積</span>
                        <span>總權益</span>
                        <span className="text-right">動作</span>
                      </div>
                      {history.trading_days.map((day) => (
                        <HistoryDayRow
                          key={day.trade_date}
                          day={day}
                          expanded={expandedHistoryDate === day.trade_date}
                          onToggle={() => setExpandedHistoryDate((current) => current === day.trade_date ? null : day.trade_date)}
                        />
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </main>
  )
}
