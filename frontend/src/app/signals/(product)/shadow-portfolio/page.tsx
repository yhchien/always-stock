"use client"

import { useEffect, useState } from "react"

import {
  fetchShadowCompletedTrades,
  fetchShadowPendingActions,
  fetchShadowPortfolio,
  fetchShadowTradesByStock,
  type ShadowCompletedTrade,
  type ShadowOrderAction,
  type ShadowPendingAction,
  type ShadowPortfolio,
  type ShadowStockStatSortBy,
  type ShadowStockTradeStat,
  type ShadowTradeSortBy,
} from "@/lib/api"

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
  CYCLE_RESET: "循環期滿強制平倉",
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

const TRADE_SORT_OPTIONS: Array<{ value: ShadowTradeSortBy; label: string }> = [
  { value: "entry_date_desc", label: "進場日期新到舊" },
  { value: "return_desc", label: "報酬率高到低" },
  { value: "return_asc", label: "報酬率低到高" },
]

const STOCK_SORT_OPTIONS: Array<{ value: ShadowStockStatSortBy; label: string }> = [
  { value: "trade_count_desc", label: "操作次數多到少" },
  { value: "avg_return_desc", label: "平均報酬高到低" },
  { value: "avg_return_asc", label: "平均報酬低到高" },
]

function SortChip<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<{ value: T; label: string }>
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
            value === opt.value
              ? "bg-sky-500/15 text-sky-100"
              : "border border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export default function ShadowPortfolioPage() {
  const [portfolio, setPortfolio] = useState<ShadowPortfolio | null>(null)
  const [actions, setActions] = useState<ShadowPendingAction[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [tradeView, setTradeView] = useState<"list" | "by-stock">("list")
  const [tradeSortBy, setTradeSortBy] = useState<ShadowTradeSortBy>("entry_date_desc")
  const [stockSortBy, setStockSortBy] = useState<ShadowStockStatSortBy>("trade_count_desc")
  const [trades, setTrades] = useState<ShadowCompletedTrade[]>([])
  const [stockStats, setStockStats] = useState<ShadowStockTradeStat[]>([])
  const [tradesLoading, setTradesLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchShadowPortfolio({ signal: controller.signal }),
      fetchShadowPendingActions({ signal: controller.signal }),
    ])
      .then(([p, a]) => {
        setPortfolio(p)
        setActions(a.actions)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "模擬交易資料載入失敗")
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    async function run() {
      setTradesLoading(true)
      try {
        if (tradeView === "list") {
          const res = await fetchShadowCompletedTrades({ sortBy: tradeSortBy }, { signal: controller.signal })
          setTrades(res.trades)
        } else {
          const res = await fetchShadowTradesByStock({ sortBy: stockSortBy }, { signal: controller.signal })
          setStockStats(res.stats)
        }
      } catch {
        // 靜默失敗——交易紀錄區塊不影響上面的 portfolio/actions 主要內容
      } finally {
        if (!controller.signal.aborted) setTradesLoading(false)
      }
    }
    void run()
    return () => controller.abort()
  }, [tradeView, tradeSortBy, stockSortBy])

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-6 text-slate-100">
      <header className="mb-5">
        <h1 className="text-2xl font-semibold">魚尾模擬交易</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
          用固定虛擬資金（Paper Trading）模擬已驗證的 v1 策略：不是真的下單，純粹追蹤「如果照
          這套規則交易，現在會是什麼結果」。以 35 個交易日為一個循環，循環結束時現金與持倉會
          全面強制清空重新開始——但每一筆已平倉交易都會永久保存，不會因為循環重置而消失。
        </p>
        <p className="mt-2 rounded-lg border border-sky-800/40 bg-sky-950/20 p-3 text-xs leading-5 text-sky-200">
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
          <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <StatBox label="總權益" value={formatMoney(portfolio.total_equity)} />
            <StatBox
              label="累積報酬"
              value={formatPct(portfolio.total_return_pct)}
              tone={returnTone(portfolio.total_return_pct)}
            />
            <StatBox label="現金" value={formatMoney(portfolio.cash)} />
            <StatBox label="持股數" value={`${portfolio.position_count} / ${portfolio.max_stocks}`} />
            <StatBox label="單位數" value={`${portfolio.total_units} / ${portfolio.max_total_units}`} />
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
            第 {portfolio.cycle_number} 個循環
            {portfolio.cycle_trading_days_elapsed !== null && (
              <>（第 {portfolio.cycle_trading_days_elapsed} / {portfolio.cycle_length_trading_days} 個交易日）</>
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

          <section className="mt-6">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-slate-200">交易紀錄（永久保存，不受循環重置影響）</h2>
              <div className="flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setTradeView("list")}
                  className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
                    tradeView === "list"
                      ? "bg-sky-500/15 text-sky-100"
                      : "border border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
                  }`}
                >
                  逐筆列表
                </button>
                <button
                  type="button"
                  onClick={() => setTradeView("by-stock")}
                  className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
                    tradeView === "by-stock"
                      ? "bg-sky-500/15 text-sky-100"
                      : "border border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
                  }`}
                >
                  依股票統計
                </button>
              </div>
            </div>

            {tradeView === "list" ? (
              <SortChip options={TRADE_SORT_OPTIONS} value={tradeSortBy} onChange={setTradeSortBy} />
            ) : (
              <SortChip options={STOCK_SORT_OPTIONS} value={stockSortBy} onChange={setStockSortBy} />
            )}

            <div className="mt-3">
              {tradesLoading && <p className="text-sm text-slate-500">正在載入交易紀錄…</p>}
              {!tradesLoading && tradeView === "list" && trades.length === 0 && (
                <p className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-500">
                  目前還沒有已平倉的交易。
                </p>
              )}
              {!tradesLoading && tradeView === "list" && trades.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {trades.map((t) => (
                    <TradeCard key={t.id} trade={t} />
                  ))}
                </div>
              )}
              {!tradesLoading && tradeView === "by-stock" && stockStats.length === 0 && (
                <p className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-500">
                  目前還沒有已平倉的交易。
                </p>
              )}
              {!tradesLoading && tradeView === "by-stock" && stockStats.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {stockStats.map((s) => (
                    <article
                      key={s.stock_id}
                      className="rounded-lg border border-slate-800 bg-slate-900/50 p-3"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-slate-100">
                          <span className="font-mono">{s.stock_id}</span> {s.stock_name}
                        </span>
                        <span className="text-xs text-slate-400">操作 {s.trade_count} 次</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                        <span className={returnTone(s.avg_return_pct)}>平均報酬 {formatPct(s.avg_return_pct)}</span>
                        <span>勝率 {s.win_rate_pct.toFixed(1)}%</span>
                        <span className={returnTone(s.total_realized_pnl)}>
                          累積損益 {formatMoney(s.total_realized_pnl)} 元
                        </span>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </main>
  )
}
