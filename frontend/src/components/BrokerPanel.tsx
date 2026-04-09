"use client"

import { useEffect, useState } from "react"
import {
  BrokerCategory,
  BrokerTradeItem,
  fetchBrokerTrades,
  fmtLots,
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

export default function BrokerPanel({ stockId, date }: Props) {
  const [category, setCategory] = useState<BrokerCategory>("day_trade")
  const [brokers, setBrokers] = useState<BrokerTradeItem[]>([])
  const [tradeDate, setTradeDate] = useState<string>("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetchBrokerTrades(stockId, category, date)
      .then((data) => {
        if (cancelled) return
        setBrokers(data.brokers)
        setTradeDate(data.trade_date)
      })
      .catch((err) => {
        if (cancelled) return
        setError("載入失敗")
        console.error(err)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [stockId, category, date])

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-zinc-700 bg-zinc-900 p-4 h-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">關鍵券商買賣</h2>
        {tradeDate && (
          <span className="text-[10px] text-zinc-500">{tradeDate}</span>
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
            <p className="text-xs text-red-400">{error}</p>
          </div>
        ) : brokers.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-xs text-zinc-600">此類別無券商交易紀錄</p>
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
