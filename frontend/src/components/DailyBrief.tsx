"use client"

import { useCallback, useEffect, useState } from "react"
import { fetchDailyBrief, toDisplayError } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"

interface Props {
  date: string
}

const DAILY_BRIEF_CACHE_PREFIX = "always-stock:daily-brief:"

interface DailyBriefCache {
  date: string
  tradeDate: string
  content: string
  expiresAt: string
}

function nextExpiryAtTaipei(now = new Date()): string {
  const taipeiNow = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Taipei" }))
  const expiry = new Date(taipeiNow)

  if (
    taipeiNow.getHours() > 8 ||
    (taipeiNow.getHours() === 8 && taipeiNow.getMinutes() >= 30)
  ) {
    expiry.setDate(expiry.getDate() + 1)
  }

  expiry.setHours(8, 30, 0, 0)
  return expiry.toISOString()
}

function getCacheKey(date: string): string {
  return `${DAILY_BRIEF_CACHE_PREFIX}${date}`
}

function readCachedDailyBrief(date: string): DailyBriefCache | null {
  if (typeof window === "undefined") return null

  try {
    const raw = window.localStorage.getItem(getCacheKey(date))
    if (!raw) return null

    const parsed = JSON.parse(raw) as DailyBriefCache
    if (!parsed.content || !parsed.tradeDate || !parsed.expiresAt) return null
    if (new Date(parsed.expiresAt).getTime() <= Date.now()) {
      window.localStorage.removeItem(getCacheKey(date))
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writeCachedDailyBrief(date: string, tradeDate: string, content: string): void {
  if (typeof window === "undefined") return

  const payload: DailyBriefCache = {
    date,
    tradeDate,
    content,
    expiresAt: nextExpiryAtTaipei(),
  }
  window.localStorage.setItem(getCacheKey(date), JSON.stringify(payload))
}

export default function DailyBrief({ date }: Props) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [visible, setVisible] = useState(false)
  const [tradeDate, setTradeDate] = useState("")

  const load = useCallback(async (forceRefresh = false) => {
    const controller = new AbortController()
    if (!forceRefresh) {
      const cached = readCachedDailyBrief(date)
      if (cached) {
        setContent(cached.content)
        setTradeDate(cached.tradeDate)
        setError(null)
        return
      }
    }

    setLoading(true)
    setError(null)
    try {
      const data = await fetchDailyBrief(date, { signal: controller.signal })
      if (!controller.signal.aborted) {
        setContent(data.content)
        setTradeDate(data.trade_date)
        writeCachedDailyBrief(date, data.trade_date, data.content)
      }
    } catch (e) {
      if (controller.signal.aborted) return
      setError(toDisplayError(e))
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
    return () => controller.abort()
  }, [date])

  useEffect(() => {
    void load()
  }, [load])

  const handleRefresh = () => {
    setContent(null)
    setTradeDate("")
    setError(null)
    void load(true)
  }

  return (
    <div className="rounded-xl border border-slate-600 bg-slate-800/40 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/40">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-100">今日觀察重點</span>
          {tradeDate && (
            <span className="text-xs text-slate-400">{tradeDate}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-100 px-3 py-1 rounded-md transition-colors"
          >
            {visible ? "關閉" : "打開"}
          </button>
          {visible && content && (
            <>
              <button
                onClick={handleRefresh}
                className="text-xs text-slate-400 hover:text-slate-200 transition-colors px-2 py-0.5 rounded border border-slate-600 hover:border-slate-400"
              >
                重新分析
              </button>
            </>
          )}
        </div>
      </div>

      {/* Body */}
      {visible && loading && !content && (
        <div className="p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 mb-1">
            <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-500 border-t-slate-200" />
            <span className="text-xs text-slate-400">AI 正在分析市場數據...</span>
          </div>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" style={{ width: `${70 + (i % 3) * 10}%` }} />
          ))}
        </div>
      )}

      {visible && error && (
        <div className="p-4">
          <p className="text-xs text-red-400">{error}</p>
          <button
            onClick={handleRefresh}
            className="mt-2 text-xs text-slate-400 hover:text-slate-200 underline"
          >
            重試
          </button>
        </div>
      )}

      {visible && content && (
        <div className="p-4">
          <pre className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap font-sans">{content}</pre>
        </div>
      )}

      {visible && !loading && !error && !content && (
        <div className="px-4 py-3">
          <p className="text-xs text-slate-500">背景摘要尚未完成，請稍後再打開查看。</p>
        </div>
      )}
    </div>
  )
}
