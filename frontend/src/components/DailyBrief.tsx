"use client"

import { useCallback, useEffect, useState } from "react"
import { fetchDailyBrief, toDisplayError } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"

interface Props {
  date: string
}

export default function DailyBrief({ date }: Props) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [tradeDate, setTradeDate] = useState("")

  const load = useCallback(async () => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    setContent(null)
    try {
      const data = await fetchDailyBrief(date, { signal: controller.signal })
      if (!controller.signal.aborted) {
        setContent(data.content)
        setTradeDate(data.trade_date)
        setExpanded(true)
      }
    } catch (e) {
      if (controller.signal.aborted) return
      setError(toDisplayError(e))
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
    return () => controller.abort()
  }, [date])

  // Do NOT auto-load — user triggers manually to avoid unnecessary API calls
  const handleLoad = () => {
    if (loading || content) return
    void load()
  }

  const handleRefresh = () => {
    setContent(null)
    void load()
  }

  return (
    <div className="rounded-xl border border-zinc-600 bg-zinc-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-600">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-zinc-100">今日觀察重點</span>
          {tradeDate && (
            <span className="text-xs text-zinc-400">{tradeDate}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {content && (
            <>
              <button
                onClick={handleRefresh}
                className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors px-2 py-0.5 rounded border border-zinc-600 hover:border-zinc-400"
              >
                重新分析
              </button>
              <button
                onClick={() => setExpanded((v) => !v)}
                className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                {expanded ? "收起" : "展開"}
              </button>
            </>
          )}
          {!content && !loading && (
            <button
              onClick={handleLoad}
              className="text-xs bg-zinc-600 hover:bg-zinc-500 text-zinc-100 px-3 py-1 rounded-md transition-colors"
            >
              產生 AI 盤前摘要
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      {loading && (
        <div className="p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 mb-1">
            <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-500 border-t-zinc-200" />
            <span className="text-xs text-zinc-400">AI 正在分析市場數據...</span>
          </div>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" style={{ width: `${70 + (i % 3) * 10}%` }} />
          ))}
        </div>
      )}

      {error && (
        <div className="p-4">
          <p className="text-xs text-red-400">{error}</p>
          <button
            onClick={handleRefresh}
            className="mt-2 text-xs text-zinc-400 hover:text-zinc-200 underline"
          >
            重試
          </button>
        </div>
      )}

      {content && expanded && (
        <div className="p-4">
          <pre className="text-xs text-zinc-200 leading-relaxed whitespace-pre-wrap font-sans">{content}</pre>
        </div>
      )}

      {content && !expanded && (
        <div
          className="px-4 py-2 cursor-pointer hover:bg-zinc-600/30 transition-colors"
          onClick={() => setExpanded(true)}
        >
          <p className="text-xs text-zinc-400 truncate">{content.slice(0, 80)}...</p>
        </div>
      )}

      {!loading && !error && !content && (
        <div className="px-4 py-3">
          <p className="text-xs text-zinc-500">點擊「產生 AI 盤前摘要」以獲得今日市場觀察。</p>
        </div>
      )}
    </div>
  )
}
