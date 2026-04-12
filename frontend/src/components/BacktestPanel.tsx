"use client"

import { useEffect, useMemo, useState } from "react"

import {
  fetchBacktestAdvice,
  fetchBacktestCapabilities,
  fetchBacktestTemplates,
  interpretBacktest,
  runBacktest,
  toDisplayError,
  type BacktestAdviceResponse,
  type BacktestCapabilityCatalog,
  type BacktestInterpretResponse,
  type BacktestRunResponse,
  type BacktestTemplate,
} from "@/lib/api"
import { todayInTaipei } from "@/lib/utils"
import BacktestEquityChart from "@/components/BacktestEquityChart"

interface Props {
  stockId: string
}

function oneYearAgoInTaipei(): string {
  const d = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000)
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" }).format(d)
}

function formatPct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "TWD",
    maximumFractionDigits: 0,
  }).format(value)
}

function formatRatio(value: number): string {
  return value.toFixed(2)
}

function formatTimes(value: number): string {
  return `${value} 次`
}

export default function BacktestPanel({ stockId }: Props) {
  const [startDate, setStartDate] = useState(oneYearAgoInTaipei)
  const [endDate, setEndDate] = useState(todayInTaipei)
  const [initialCapital, setInitialCapital] = useState("1000000")
  const [strategyText, setStrategyText] = useState("")
  const [templates, setTemplates] = useState<BacktestTemplate[]>([])
  const [capabilities, setCapabilities] = useState<BacktestCapabilityCatalog | null>(null)
  const [selectedTemplateId, setSelectedTemplateId] = useState("")
  const [interpretation, setInterpretation] = useState<BacktestInterpretResponse | null>(null)
  const [result, setResult] = useState<BacktestRunResponse | null>(null)
  const [advice, setAdvice] = useState<BacktestAdviceResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [interpreting, setInterpreting] = useState(false)
  const [loadingAdvice, setLoadingAdvice] = useState(false)
  const [loadingTemplates, setLoadingTemplates] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adviceError, setAdviceError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    void (async () => {
      try {
        const nextTemplates = await fetchBacktestTemplates({ signal: controller.signal })
        const nextCapabilities = await fetchBacktestCapabilities({ signal: controller.signal })
        setTemplates(nextTemplates)
        setCapabilities(nextCapabilities)
        if (nextTemplates[0]) {
          setSelectedTemplateId(nextTemplates[0].id)
          setStrategyText(nextTemplates[0].strategy_text)
        }
      } catch (fetchError) {
        setError(toDisplayError(fetchError, "讀取策略模板失敗"))
      } finally {
        setLoadingTemplates(false)
      }
    })()

    return () => controller.abort()
  }, [])

  const latestEquity = result?.equity_curve.at(-1)?.equity ?? null
  const equityPreview = useMemo(() => result?.equity_curve.slice(-8) ?? [], [result])
  const monthlyPreview = useMemo(() => result?.period_returns.monthly.slice(-6).reverse() ?? [], [result])
  const quarterlyPreview = useMemo(() => result?.period_returns.quarterly.slice(-4).reverse() ?? [], [result])
  const yearlyPreview = useMemo(() => result?.period_returns.yearly.slice(-4).reverse() ?? [], [result])
  const interpretedStrategy = interpretation?.strategy as {
    entry_rules?: Array<{ indicator?: string; params?: Record<string, unknown> }>
    exit_rules?: Array<{ indicator?: string; params?: Record<string, unknown> }>
    stop_loss_pct?: number | null
    take_profit_pct?: number | null
  } | null

  const capabilityMap = useMemo(() => {
    const items = [...(capabilities?.indicators ?? []), ...(capabilities?.risk_controls ?? [])]
    return new Map(items.map((item) => [item.id, item]))
  }, [capabilities])

  const describeRule = (rule: { indicator?: string; params?: Record<string, unknown> }) => {
    const item = rule.indicator ? capabilityMap.get(rule.indicator) : null
    const params = rule.params ?? {}
    const paramText = Object.entries(params)
      .map(([key, value]) => `${key}=${value}`)
      .join(", ")
    return item ? `${item.label}${paramText ? ` (${paramText})` : ""}` : `${rule.indicator ?? "unknown"}${paramText ? ` (${paramText})` : ""}`
  }

  const handleRun = async () => {
    setError(null)
    setAdviceError(null)
    setAdvice(null)
    setResult(null)
    setInterpretation(null)

    if (!strategyText.trim()) {
      setError("策略文字不能空白")
      return
    }
    if (startDate > endDate) {
      setError("開始日期不能晚於結束日期")
      return
    }

    try {
      setInterpreting(true)
      const interpreted = await interpretBacktest({
        stock_id: stockId,
        start_date: startDate,
        end_date: endDate,
        initial_capital: Number(initialCapital),
        strategy_text: strategyText,
      })
      setInterpretation(interpreted)
      setInterpreting(false)

      if (!interpreted.supported) {
        setError("這句策略目前只能部分判讀，請先調整不支援的條件。")
        return
      }

      setRunning(true)
      const backtestResult = await runBacktest({
        stock_id: stockId,
        start_date: startDate,
        end_date: endDate,
        initial_capital: Number(initialCapital),
        strategy_text: strategyText,
      })
      setResult(backtestResult)

      setLoadingAdvice(true)
      try {
        const nextAdvice = await fetchBacktestAdvice({
          stock_id: stockId,
          strategy_text: strategyText,
          normalized_text: backtestResult.normalized_text,
          metrics: backtestResult.metrics,
          trades: backtestResult.trades,
          latest_recommendation: backtestResult.latest_recommendation,
        })
        setAdvice(nextAdvice)
      } catch (nextAdviceError) {
        setAdviceError(toDisplayError(nextAdviceError, "讀取策略建議失敗"))
      } finally {
        setLoadingAdvice(false)
      }
    } catch (runError) {
      setResult(null)
      setError(toDisplayError(runError, "執行回測失敗"))
    } finally {
      setInterpreting(false)
      setRunning(false)
    }
  }

  return (
    <div className="flex h-full flex-col gap-4 rounded-lg border border-zinc-700 bg-zinc-900 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-zinc-200">L3 回測工作區</h2>
          <p className="text-xs text-zinc-500">先支援日線單檔 long-only 與標準績效指標。</p>
        </div>
        <span className="rounded border border-zinc-700 px-1.5 py-0.5 text-xs text-zinc-500">MVP</span>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-zinc-500">策略模板</label>
          <select
            value={selectedTemplateId}
            onChange={(e) => {
              const template = templates.find((item) => item.id === e.target.value)
              setSelectedTemplateId(e.target.value)
              if (template) setStrategyText(template.strategy_text)
            }}
            disabled={loadingTemplates}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          >
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
          {selectedTemplateId && (
            <p className="text-[11px] text-zinc-500">
              {templates.find((item) => item.id === selectedTemplateId)?.description}
            </p>
          )}
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="backtest-start-date" className="text-xs text-zinc-500">開始日期</label>
            <input
              id="backtest-start-date"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="backtest-end-date" className="text-xs text-zinc-500">結束日期</label>
            <input
              id="backtest-end-date"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="backtest-initial-capital" className="text-xs text-zinc-500">初始本金</label>
            <input
              id="backtest-initial-capital"
              type="number"
              min={1}
              value={initialCapital}
              onChange={(e) => setInitialCapital(e.target.value)}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="backtest-strategy-text" className="text-xs text-zinc-500">策略文字</label>
          <textarea
            id="backtest-strategy-text"
            value={strategyText}
            onChange={(e) => setStrategyText(e.target.value)}
            rows={4}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </div>

        <button
          onClick={handleRun}
          disabled={running || interpreting}
          className="rounded-md bg-zinc-700 px-4 py-1.5 text-sm text-zinc-100 transition-colors hover:bg-zinc-600 disabled:opacity-50"
        >
          {interpreting ? "解析策略中..." : running ? "計算中..." : "執行回測"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      {interpreting && (
        <div className="flex flex-col gap-3 rounded-md border border-zinc-700 bg-zinc-800/50 px-3 py-3">
          <div className="text-sm font-medium text-zinc-100">策略判讀預覽</div>
          <div className="rounded bg-zinc-900/60 px-3 py-3 text-sm text-zinc-400">
            正在解析策略文字，判斷這句話對應哪些指標...
          </div>
        </div>
      )}

      {interpretation && (
        <div className="flex flex-col gap-3 rounded-md border border-zinc-700 bg-zinc-800/50 px-3 py-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-zinc-100">策略判讀預覽</div>
            <div className={`text-xs ${interpretation.supported ? "text-emerald-300" : "text-amber-300"}`}>
              {interpretation.supported ? "可執行回測" : "部分支援"}
            </div>
          </div>
          <div className="rounded bg-zinc-900/60 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Normalized</div>
            <p className="mt-1 text-sm text-zinc-200">{interpretation.normalized_text}</p>
          </div>
          {interpretedStrategy && (
            <div className="grid gap-3 lg:grid-cols-3">
              <div className="rounded bg-zinc-900/60 px-3 py-3">
                <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Entry DSL</div>
                <div className="mt-2 space-y-1 text-sm text-zinc-200">
                  {(interpretedStrategy.entry_rules ?? []).length === 0 ? (
                    <p className="text-zinc-500">尚無可用進場條件</p>
                  ) : (
                    (interpretedStrategy.entry_rules ?? []).map((rule, index) => (
                      <p key={`entry-${index}`}>- {describeRule(rule)}</p>
                    ))
                  )}
                </div>
              </div>
              <div className="rounded bg-zinc-900/60 px-3 py-3">
                <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Exit DSL</div>
                <div className="mt-2 space-y-1 text-sm text-zinc-200">
                  {(interpretedStrategy.exit_rules ?? []).length === 0 ? (
                    <p className="text-zinc-500">尚無可用出場條件</p>
                  ) : (
                    (interpretedStrategy.exit_rules ?? []).map((rule, index) => (
                      <p key={`exit-${index}`}>- {describeRule(rule)}</p>
                    ))
                  )}
                </div>
              </div>
              <div className="rounded bg-zinc-900/60 px-3 py-3">
                <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Risk Controls</div>
                <div className="mt-2 space-y-1 text-sm text-zinc-200">
                  {interpretedStrategy.stop_loss_pct ? <p>- 固定停損 ({interpretedStrategy.stop_loss_pct}%)</p> : null}
                  {interpretedStrategy.take_profit_pct ? <p>- 固定停利 ({interpretedStrategy.take_profit_pct}%)</p> : null}
                  {!interpretedStrategy.stop_loss_pct && !interpretedStrategy.take_profit_pct ? (
                    <p className="text-zinc-500">未設定固定停損/停利</p>
                  ) : null}
                </div>
              </div>
            </div>
          )}
          {interpretation.ai_mapped_conditions?.length > 0 && (
            <div className="rounded border border-sky-500/30 bg-sky-500/10 px-3 py-2">
              <div className="text-xs font-medium text-sky-200">AI 補充解析的條件</div>
              <div className="mt-2 space-y-1 text-sm text-sky-100">
                {interpretation.ai_mapped_conditions.map((condition) => {
                  const item = capabilityMap.get(condition)
                  return <p key={condition}>- {item ? item.label : condition}</p>
                })}
              </div>
            </div>
          )}
          {interpretation.unsupported_conditions.length > 0 && (
            <div className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2">
              <div className="text-xs font-medium text-amber-200">目前不支援的條件</div>
              <div className="mt-2 space-y-1 text-sm text-amber-100">
                {interpretation.unsupported_conditions.map((condition) => (
                  <p key={condition}>- {condition}</p>
                ))}
              </div>
            </div>
          )}
          {interpretation.warnings.length > 0 && (
            <div className="rounded border border-amber-500/30 bg-amber-500/10 px-3 py-2">
              <div className="text-xs font-medium text-amber-200">解析提醒</div>
              <div className="mt-2 space-y-1 text-sm text-amber-100">
                {interpretation.warnings.map((warning) => (
                  <p key={warning}>- {warning}</p>
                ))}
              </div>
            </div>
          )}
          {capabilities && (
            <div className="rounded border border-zinc-700 bg-zinc-900/40 px-3 py-3">
              <div className="text-xs font-medium text-zinc-200">目前可判讀的條件 catalog</div>
              <div className="mt-2 grid gap-3 lg:grid-cols-2">
                <div>
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Indicators</div>
                  <div className="mt-2 space-y-1 text-xs text-zinc-300">
                    {capabilities.indicators.slice(0, 8).map((item) => (
                      <p key={item.id}>- {item.label}</p>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Risk Controls</div>
                  <div className="mt-2 space-y-1 text-xs text-zinc-300">
                    {capabilities.risk_controls.map((item) => (
                      <p key={item.id}>- {item.label}</p>
                    ))}
                    {capabilities.notes.slice(0, 2).map((note) => (
                      <p key={note} className="text-zinc-500">- {note}</p>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-4 border-t border-zinc-700 pt-3">
          {result.warnings.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-3">
              <div className="text-xs font-medium text-amber-200">回測提醒</div>
              <div className="mt-2 space-y-1 text-sm text-amber-100">
                {result.warnings.map((warning) => (
                  <p key={warning}>- {warning}</p>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Strategy</div>
            <p className="mt-1 text-sm text-zinc-200">{result.normalized_text}</p>
          </div>

          <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
            {[
              { label: "總報酬", value: formatPct(result.metrics.total_return_pct), color: result.metrics.total_return_pct >= 0 ? "text-red-400" : "text-green-400" },
              { label: "年化報酬", value: formatPct(result.metrics.annual_return_pct), color: result.metrics.annual_return_pct >= 0 ? "text-red-400" : "text-green-400" },
              { label: "勝率", value: formatPct(result.metrics.win_rate_pct), color: result.metrics.win_rate_pct >= 50 ? "text-red-400" : "text-zinc-300" },
              { label: "最大回撤", value: formatPct(result.metrics.max_drawdown_pct), color: "text-green-400" },
              { label: "夏普比率", value: result.metrics.sharpe_ratio.toFixed(2), color: result.metrics.sharpe_ratio >= 1 ? "text-red-400" : "text-zinc-300" },
              { label: "交易次數", value: `${result.metrics.trade_count} 次`, color: "text-zinc-300" },
            ].map(({ label, value, color }) => (
              <div key={label} className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2">
                <div className="text-[10px] text-zinc-500">{label}</div>
                <div className={`font-mono text-sm font-medium ${color}`}>{value}</div>
              </div>
            ))}
          </div>

          <div className="grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Equity</div>
                  <p className="text-sm text-zinc-200">期末資產 {latestEquity ? formatCurrency(latestEquity) : "-"}</p>
                </div>
                <a
                  href={`/stocks/${stockId}?date=${result.latest_recommendation.latest_signal_date}`}
                  className="text-xs text-emerald-300 underline-offset-2 hover:underline"
                >
                  回到研究頁
                </a>
              </div>
              <div className="mt-3">
                <BacktestEquityChart points={result.equity_curve} />
              </div>
              <div className="mt-3 grid gap-1">
                {equityPreview.map((point) => (
                  <div
                    key={point.trade_date}
                    className="flex items-center justify-between rounded bg-zinc-900/60 px-2 py-1 text-xs text-zinc-300"
                  >
                    <span>{point.trade_date}</span>
                    <span className="font-mono">{formatCurrency(point.equity)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
              <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Latest Signal</div>
              <p className="mt-1 text-sm font-medium text-zinc-100">{result.latest_recommendation.reason}</p>
              <p className="mt-2 text-xs text-zinc-400">
                {result.latest_recommendation.latest_signal_date} / {result.latest_recommendation.action}
              </p>
              <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                <div className="rounded bg-zinc-900/60 px-2 py-2">
                  <div className="text-zinc-500">Buy & Hold</div>
                  <div className="mt-1 font-mono text-zinc-200">{formatPct(result.metrics.benchmark_return_pct)}</div>
                </div>
                <div className="rounded bg-zinc-900/60 px-2 py-2">
                  <div className="text-zinc-500">Alpha</div>
                  <div className="mt-1 font-mono text-zinc-200">{formatPct(result.metrics.excess_return_pct)}</div>
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
              <div className="text-sm font-medium text-zinc-100">Summary</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {[
                  { label: "期末資產", value: formatCurrency(result.metrics.ending_equity) },
                  { label: "Buy & Hold", value: formatPct(result.metrics.benchmark_return_pct) },
                  { label: "Alpha", value: formatPct(result.metrics.excess_return_pct) },
                  { label: "平均每筆報酬", value: formatPct(result.metrics.avg_trade_return_pct) },
                  { label: "平均持有天數", value: `${result.metrics.avg_holding_days.toFixed(1)} 天` },
                  { label: "獲利因子", value: result.metrics.profit_factor != null ? formatRatio(result.metrics.profit_factor) : "—" },
                  { label: "平均獲利", value: result.metrics.avg_gain_pct != null ? formatPct(result.metrics.avg_gain_pct) : "—" },
                  { label: "平均虧損", value: result.metrics.avg_loss_pct != null ? formatPct(result.metrics.avg_loss_pct) : "—" },
                ].map((item) => (
                  <div key={item.label} className="rounded bg-zinc-900/60 px-3 py-2">
                    <div className="text-[10px] text-zinc-500">{item.label}</div>
                    <div className="mt-1 font-mono text-sm text-zinc-200">{item.value}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
              <div className="text-sm font-medium text-zinc-100">Performance Analysis</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <div className="rounded bg-zinc-900/60 px-3 py-2">
                  <div className="text-[10px] text-zinc-500">最大連續獲利</div>
                  <div className="mt-1 font-mono text-sm text-zinc-200">{formatTimes(result.metrics.max_consecutive_wins)}</div>
                </div>
                <div className="rounded bg-zinc-900/60 px-3 py-2">
                  <div className="text-[10px] text-zinc-500">最大連續虧損</div>
                  <div className="mt-1 font-mono text-sm text-zinc-200">{formatTimes(result.metrics.max_consecutive_losses)}</div>
                </div>
              </div>

              <div className="mt-3 grid gap-2 xl:grid-cols-3">
                {[
                  { title: "月度報酬", items: monthlyPreview },
                  { title: "季度報酬", items: quarterlyPreview },
                  { title: "年度報酬", items: yearlyPreview },
                ].map((section) => (
                  <div key={section.title} className="rounded bg-zinc-900/60 px-3 py-3">
                    <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">{section.title}</div>
                    <div className="mt-2 flex flex-col gap-1">
                      {section.items.length === 0 ? (
                        <p className="text-xs text-zinc-500">目前沒有可顯示資料</p>
                      ) : (
                        section.items.map((item) => (
                          <div key={item.period} className="flex items-center justify-between text-xs">
                            <span className="text-zinc-400">{item.period}</span>
                            <span className={`font-mono ${item.return_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                              {formatPct(item.return_pct)}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-zinc-100">策略建議</div>
              {advice && (
                <div className="text-xs text-zinc-500">
                  {advice.source === "openai" ? "OpenAI" : "Heuristic fallback"}
                </div>
              )}
            </div>

            {loadingAdvice && (
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="h-3 w-20 animate-pulse rounded bg-zinc-700" />
                  <div className="mt-3 h-4 w-full animate-pulse rounded bg-zinc-800" />
                  <div className="mt-2 h-4 w-4/5 animate-pulse rounded bg-zinc-800" />
                </div>
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="h-3 w-20 animate-pulse rounded bg-zinc-700" />
                  <div className="mt-3 h-4 w-full animate-pulse rounded bg-zinc-800" />
                  <div className="mt-2 h-4 w-5/6 animate-pulse rounded bg-zinc-800" />
                </div>
              </div>
            )}

            {adviceError && !loadingAdvice && (
              <div className="mt-3 rounded border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
                {adviceError}
              </div>
            )}

            {advice && !loadingAdvice && (
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Summary</div>
                  <p className="mt-2 text-sm text-zinc-200">{advice.summary}</p>
                </div>
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Rewrite</div>
                  <div className="mt-2 space-y-1 text-sm text-zinc-200">
                    {advice.rewrite_suggestions.map((item) => (
                      <p key={item}>- {item}</p>
                    ))}
                  </div>
                </div>
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Strengths</div>
                  <div className="mt-2 space-y-1 text-sm text-zinc-200">
                    {advice.strengths.map((item) => (
                      <p key={item}>- {item}</p>
                    ))}
                  </div>
                </div>
                <div className="rounded bg-zinc-900/60 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Weaknesses & Risks</div>
                  <div className="mt-2 space-y-1 text-sm text-zinc-200">
                    {[...advice.weaknesses, ...advice.risk_notes].map((item) => (
                      <p key={item}>- {item}</p>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="rounded-md border border-zinc-700 bg-zinc-800/60 px-3 py-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-zinc-100">交易紀錄</div>
              <div className="text-xs text-zinc-500">最近 {Math.min(result.trades.length, 5)} 筆</div>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {result.trades.length === 0 ? (
                <div className="rounded bg-zinc-900/60 px-3 py-2 text-xs text-zinc-500">這段期間沒有完成交易。</div>
              ) : (
                result.trades.slice(-5).reverse().map((trade) => (
                  <a
                    key={`${trade.entry_date}-${trade.exit_date}`}
                    href={`/stocks/${stockId}?date=${trade.exit_date}`}
                    className="rounded border border-zinc-700 bg-zinc-900/60 px-3 py-2 text-sm transition-colors hover:border-zinc-500"
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="text-zinc-200">
                        {trade.entry_date} -&gt; {trade.exit_date}
                      </div>
                      <div className={`font-mono ${trade.return_pct >= 0 ? "text-red-400" : "text-green-400"}`}>
                        {formatPct(trade.return_pct)}
                      </div>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-4 text-xs text-zinc-500">
                      <span>{trade.holding_days} 天 / {trade.exit_reason}</span>
                      <span>{formatCurrency(trade.pnl_amount)}</span>
                    </div>
                  </a>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {!result && (
        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-zinc-700">
          <p className="text-xs text-zinc-600">選模板或輸入策略後執行回測，先看夏普、勝率、回撤與交易清單。</p>
        </div>
      )}
    </div>
  )
}
