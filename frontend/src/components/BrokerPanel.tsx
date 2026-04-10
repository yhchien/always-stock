"use client"

import { useEffect, useState } from "react"
import {
  BrokerCategory,
  BrokerTradeItem,
  fetchBrokerTrades,
  fmtLots,
  toDisplayError,
} from "@/lib/api"

interface Props {
  stockId: string
  date?: string
}

const CATEGORIES: { key: BrokerCategory; label: string }[] = [
  { key: "day_trade", label: "當沖" },
  { key: "next_day", label: "隔日沖" },
  { key: "short_term", label: "短線" },
  { key: "swing", label: "波段" },
]
const AUTO_REFRESH_MS = 4000

export default function BrokerPanel({ stockId, date }: Props) {
  const [category, setCategory] = useState<BrokerCategory>("day_trade")
  const [brokers, setBrokers] = useState<BrokerTradeItem[]>([])
  const [tradeDate, setTradeDate] = useState<string>("")
  const [resolvedRequestKey, setResolvedRequestKey] = useState("")
  const [errorState, setErrorState] = useState<{ requestKey: string; message: string } | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const requestKey = `${stockId}:${category}:${date ?? ""}:${reloadKey}`
  const error = errorState?.requestKey === requestKey ? errorState.message : null
  const loading = !error && resolvedRequestKey !== requestKey

  useEffect(() => {
    const controller = new AbortController()

    fetchBrokerTrades(stockId, category, date, 1, { signal: controller.signal })
      .then((data) => {
        setBrokers(data.brokers)
        setTradeDate(data.trade_date)
        setIsRefreshing(Boolean(data.is_refreshing))
        setErrorState(null)
        setResolvedRequestKey(requestKey)
      })
      .catch((err) => {
        if (controller.signal.aborted) return
        if (err instanceof DOMException && err.name === "AbortError") return
        setIsRefreshing(false)
        setErrorState({ requestKey, message: toDisplayError(err) })
        setResolvedRequestKey(requestKey)
        console.error(err)
      })

    return () => { controller.abort() }
  }, [stockId, category, date, reloadKey, requestKey])

  useEffect(() => {
    if (!isRefreshing || loading || error) return

    const timer = window.setTimeout(() => {
      setReloadKey((value) => value + 1)
    }, AUTO_REFRESH_MS)

    return () => {
      window.clearTimeout(timer)
    }
  }, [isRefreshing, loading, error])

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-zinc-700 bg-zinc-900 p-4 h-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">關鍵券商買賣</h2>
        {tradeDate && (
          <div className="flex items-center gap-2">
            {isRefreshing && (
              <span className="text-[10px] text-yellow-500">背景更新中</span>
            )}
            <span className="text-[10px] text-zinc-500">{tradeDate}</span>
          </div>
        )}
      </div>

      {/* Category tabs */}
      <div className="flex gap-1">
        {CATEGORIES.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setCategory(key)}
            className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
              category === key
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-800/50 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto min-h-[200px]">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
              <p className="text-xs text-zinc-500">正在從證交所取得資料...</p>
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <p className="text-xs text-red-400">{error}</p>
              <button
                type="button"
                onClick={() => setReloadKey((value) => value + 1)}
                className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:bg-zinc-800"
              >
                重新載入
              </button>
            </div>
          </div>
        ) : brokers.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-xs text-zinc-600">
              {isRefreshing ? "正在背景抓取券商資料，稍後可重新載入" : "此類別無券商交易紀錄"}
            </p>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-zinc-700/50 text-zinc-500">
                <th className="text-left py-1.5 font-medium">券商</th>
                <th className="text-right py-1.5 font-medium">買進</th>
                <th className="text-right py-1.5 font-medium">賣出</th>
                <th className="text-right py-1.5 font-medium">淨買超</th>
              </tr>
            </thead>
            <tbody>
              {brokers.map((b) => (
                <tr
                  key={b.broker_id}
                  className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition-colors"
                >
                  <td className="py-1.5 text-zinc-300">{b.display_name}</td>
                  <td className="py-1.5 text-right text-red-400">
                    {fmtLots(b.buy_shares)}
                  </td>
                  <td className="py-1.5 text-right text-green-400">
                    {fmtLots(b.sell_shares)}
                  </td>
                  <td
                    className={`py-1.5 text-right font-medium ${
                      b.net_shares > 0
                        ? "text-red-400"
                        : b.net_shares < 0
                          ? "text-green-400"
                          : "text-zinc-500"
                    }`}
                  >
                    {fmtLots(b.net_shares)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
