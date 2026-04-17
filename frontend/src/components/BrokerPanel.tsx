"use client"

import { useEffect, useState } from "react"
import {
  BrokerTradeItem,
  fetchBrokerRanked,
  fmtLots,
  toDisplayError,
} from "@/lib/api"

interface Props {
  stockId: string
  date?: string
  days?: number
  onSelectBroker?: (broker: BrokerTradeItem | null) => void
  selectedBrokerId?: string | null
}

const CATEGORY_LABELS: Record<string, string> = {
  day_trade: "當沖",
  next_day: "隔日沖",
  short_term: "短線",
  swing: "波段",
}

const CATEGORY_COLORS: Record<string, string> = {
  day_trade: "bg-orange-500/20 text-orange-300",
  next_day: "bg-yellow-500/20 text-yellow-300",
  short_term: "bg-sky-500/20 text-sky-300",
  swing: "bg-violet-500/20 text-violet-300",
}

const AUTO_REFRESH_MS = 4000

type TabKey = "buy" | "sell"

function BrokerRow({
  broker,
  isSelected,
  onClick,
}: {
  broker: BrokerTradeItem
  isSelected: boolean
  onClick: () => void
}) {
  return (
    <tr
      onClick={onClick}
      className={`border-b border-slate-700/50 cursor-pointer transition-colors ${
        isSelected ? "bg-slate-600/40" : "hover:bg-slate-700/30"
      }`}
    >
      <td className="py-1.5 pr-1">
        <div className="flex flex-col gap-0.5">
          <span className={`text-xs ${isSelected ? "text-slate-50 font-medium" : "text-slate-200"}`}>
            {broker.display_name}
          </span>
          {broker.categories.length > 0 && (
            <div className="flex flex-wrap gap-0.5">
              {broker.categories.map((cat) => (
                <span
                  key={cat}
                  className={`text-[9px] px-1 py-px rounded ${CATEGORY_COLORS[cat] ?? "bg-slate-600/30 text-slate-400"}`}
                >
                  {CATEGORY_LABELS[cat] ?? cat}
                </span>
              ))}
            </div>
          )}
        </div>
      </td>
      <td className="py-1.5 text-right text-red-400 text-xs font-mono">{fmtLots(broker.buy_shares)}</td>
      <td className="py-1.5 text-right text-green-400 text-xs font-mono">{fmtLots(broker.sell_shares)}</td>
      <td
        className={`py-1.5 text-right text-xs font-mono font-medium ${
          broker.net_shares > 0 ? "text-red-400" : broker.net_shares < 0 ? "text-green-400" : "text-slate-500"
        }`}
      >
        {fmtLots(broker.net_shares)}
      </td>
    </tr>
  )
}

export default function BrokerPanel({ stockId, date, days = 1, onSelectBroker, selectedBrokerId }: Props) {
  const [tab, setTab] = useState<TabKey>("buy")
  const [buyTop, setBuyTop] = useState<BrokerTradeItem[]>([])
  const [sellTop, setSellTop] = useState<BrokerTradeItem[]>([])
  const [tradeDate, setTradeDate] = useState<string>("")
  const [resolvedRequestKey, setResolvedRequestKey] = useState("")
  const [errorState, setErrorState] = useState<{ requestKey: string; message: string } | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [emptyRefreshCount, setEmptyRefreshCount] = useState(0)

  const MAX_EMPTY_RETRIES = 3

  const requestKey = `${stockId}:${date ?? ""}:${days}:${reloadKey}`
  const error = errorState?.requestKey === requestKey ? errorState.message : null
  const loading = !error && resolvedRequestKey !== requestKey

  // Reset retry counter when stock/date/days changes (but not on reloadKey bump)
  useEffect(() => {
    setEmptyRefreshCount(0)
    setReloadKey(0)
  }, [stockId, date, days])

  useEffect(() => {
    const controller = new AbortController()

    fetchBrokerRanked(stockId, date, days, { signal: controller.signal })
      .then((data) => {
        setBuyTop(data.buy_top)
        setSellTop(data.sell_top)
        setTradeDate(String(data.trade_date))
        const stillEmpty = data.buy_top.length === 0 && data.sell_top.length === 0
        if (data.is_refreshing && stillEmpty) {
          setEmptyRefreshCount((c) => c + 1)
        } else {
          setEmptyRefreshCount(0)
        }
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
  }, [stockId, date, days, reloadKey, requestKey])

  useEffect(() => {
    if (!isRefreshing || loading || error) return
    if (emptyRefreshCount >= MAX_EMPTY_RETRIES) return
    const timer = window.setTimeout(() => {
      setReloadKey((v) => v + 1)
    }, AUTO_REFRESH_MS)
    return () => { window.clearTimeout(timer) }
  }, [isRefreshing, loading, error, emptyRefreshCount])

  const brokers = tab === "buy" ? buyTop : sellTop
  const isEmpty = brokers.length === 0
  // Only show full loading skeleton on first load (no data yet)
  const isInitialLoad = loading && buyTop.length === 0 && sellTop.length === 0
  // Show subtle badge when refreshing in background while data is already shown
  // Stop treating as "fetching" once retries are exhausted
  const gaveUp = emptyRefreshCount >= MAX_EMPTY_RETRIES
  const isFetching = loading || (isRefreshing && !gaveUp)

  const handleRowClick = (broker: BrokerTradeItem) => {
    if (!onSelectBroker) return
    if (selectedBrokerId === broker.broker_id) {
      onSelectBroker(null)
    } else {
      onSelectBroker(broker)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-slate-700/40 bg-slate-800/40 p-4 h-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-100">關鍵券商買賣</h2>
        <div className="flex items-center gap-2">
          {isFetching && !isInitialLoad && (
            <span className="flex items-center gap-1 text-[10px] text-yellow-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-yellow-400" />
              更新中
            </span>
          )}
          {tradeDate && (
            <span className="text-[10px] text-slate-400">{tradeDate}</span>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1">
        {(["buy", "sell"] as TabKey[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition-colors ${
              tab === t
                ? t === "buy"
                  ? "bg-red-500/20 text-red-300 border border-red-500/30"
                  : "bg-green-500/20 text-green-300 border border-green-500/30"
                : "bg-slate-800/50 text-slate-400 border border-transparent hover:bg-slate-700 hover:text-slate-200"
            }`}
          >
            {t === "buy" ? "買進 Top10" : "賣出 Top10"}
          </button>
        ))}
      </div>

      {/* Help text */}
      {onSelectBroker && !isEmpty && (
        <p className="text-[10px] text-slate-500">
          {selectedBrokerId ? "點擊同一券商取消選擇" : "點擊券商查看買賣超走勢"}
        </p>
      )}

      {/* Content */}
      <div className="flex-1 overflow-auto min-h-[200px]">
        {isInitialLoad ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
              <p className="text-xs text-slate-400">正在從證交所取得資料...</p>
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <p className="text-xs text-red-400">{error}</p>
              <button
                type="button"
                onClick={() => setReloadKey((v) => v + 1)}
                className="rounded-md border border-slate-600 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-600"
              >
                重新載入
              </button>
            </div>
          </div>
        ) : isEmpty && !isFetching ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-xs text-slate-500">此日期無券商交易紀錄</p>
          </div>
        ) : isEmpty ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
              <p className="text-xs text-slate-400">正在背景抓取券商資料...</p>
            </div>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-600/50 text-slate-400">
                <th className="text-left py-1.5 font-medium">券商</th>
                <th className="text-right py-1.5 font-medium">買進</th>
                <th className="text-right py-1.5 font-medium">賣出</th>
                <th className="text-right py-1.5 font-medium">淨</th>
              </tr>
            </thead>
            <tbody>
              {brokers.map((b) => (
                <BrokerRow
                  key={b.broker_id}
                  broker={b}
                  isSelected={selectedBrokerId === b.broker_id}
                  onClick={() => handleRowClick(b)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
