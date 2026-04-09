"use client"

import { useState } from "react"

interface Props {
  stockId: string
}

// Placeholder result type — will be filled in when M11 backend is ready
interface BacktestResult {
  winRate: number
  totalReturn: number
  maxDrawdown: number
  sharpe: number
  trades: number
}

export default function BacktestPanel({ stockId }: Props) {
  const [buyCondition, setBuyCondition] = useState("")
  const [sellCondition, setSellCondition] = useState("")
  const [startDate, setStartDate] = useState("2023-01-01")
  const [endDate, setEndDate] = useState("2024-12-31")
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [running, setRunning] = useState(false)

  const handleRun = () => {
    // TODO (M11): call POST /api/backtest with stockId + conditions + dates
    setRunning(true)
    setTimeout(() => {
      setResult({
        winRate: 58.3,
        totalReturn: 24.7,
        maxDrawdown: -12.1,
        sharpe: 1.42,
        trades: 24,
      })
      setRunning(false)
    }, 800)
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-zinc-700 bg-zinc-900 p-4 h-full">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">回測策略</h2>
        <span className="text-xs text-zinc-600 border border-zinc-700 rounded px-1.5 py-0.5">M11 — 開發中</span>
      </div>

      {/* Strategy inputs */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-zinc-500">買進條件</label>
          <input
            value={buyCondition}
            onChange={(e) => setBuyCondition(e.target.value)}
            placeholder="例：MA10 上穿 MA20"
            className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-zinc-500">賣出條件</label>
          <input
            value={sellCondition}
            onChange={(e) => setSellCondition(e.target.value)}
            placeholder="例：MA10 下穿 MA20"
            className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </div>
        <div className="flex gap-2">
          <div className="flex flex-col gap-1 flex-1">
            <label className="text-xs text-zinc-500">開始日期</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </div>
          <div className="flex flex-col gap-1 flex-1">
            <label className="text-xs text-zinc-500">結束日期</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </div>
        </div>
        <button
          onClick={handleRun}
          disabled={running}
          className="rounded-md bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 px-4 py-1.5 text-sm text-zinc-100 transition-colors"
        >
          {running ? "計算中..." : "執行回測"}
        </button>
      </div>

      {/* Results */}
      {result && (
        <div className="flex flex-col gap-3 border-t border-zinc-700 pt-3">
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: "勝率", value: `${result.winRate.toFixed(1)}%`, color: result.winRate >= 50 ? "text-red-400" : "text-green-400" },
              { label: "總報酬", value: `${result.totalReturn >= 0 ? "+" : ""}${result.totalReturn.toFixed(1)}%`, color: result.totalReturn >= 0 ? "text-red-400" : "text-green-400" },
              { label: "最大回撤", value: `${result.maxDrawdown.toFixed(1)}%`, color: "text-green-400" },
              { label: "夏普比率", value: result.sharpe.toFixed(2), color: result.sharpe >= 1 ? "text-red-400" : "text-zinc-400" },
              { label: "交易次數", value: `${result.trades} 次`, color: "text-zinc-300" },
            ].map(({ label, value, color }) => (
              <div key={label} className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2">
                <div className="text-[10px] text-zinc-500">{label}</div>
                <div className={`font-mono text-sm font-medium ${color}`}>{value}</div>
              </div>
            ))}
          </div>
          {/* Equity curve placeholder */}
          <div className="rounded-md border border-dashed border-zinc-700 h-24 flex items-center justify-center text-xs text-zinc-600">
            資金曲線圖（M11 接後端後顯示）
          </div>
        </div>
      )}

      {!result && (
        <div className="flex-1 flex items-center justify-center rounded-md border border-dashed border-zinc-700">
          <p className="text-xs text-zinc-600">輸入策略條件後執行回測</p>
        </div>
      )}
    </div>
  )
}
