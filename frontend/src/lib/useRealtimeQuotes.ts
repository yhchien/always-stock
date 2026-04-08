"use client"

import { useEffect, useRef, useState } from "react"
import { fetchRealtimeQuotes, type RealtimeQuote } from "@/lib/api"

/** Returns true if current time is within TWSE trading hours (09:00–13:30, Mon–Fri, UTC+8). */
function isTradingHours(): boolean {
  const now = new Date()
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60 * 1000
  const tw = new Date(utcMs + 8 * 60 * 60 * 1000)
  const day = tw.getDay() // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false
  const mins = tw.getHours() * 60 + tw.getMinutes()
  return mins >= 9 * 60 && mins <= 13 * 60 + 30
}

/**
 * Poll real-time quotes for a list of stock IDs.
 * Returns a map of stock_id → RealtimeQuote, refreshed every `intervalMs`.
 * Polling only starts during TWSE trading hours (09:00-13:30 weekdays, UTC+8).
 * One initial fetch always runs on mount to show the latest available data.
 */
export function useRealtimeQuotes(
  stockIds: string[],
  intervalMs = 15000,
): Map<string, RealtimeQuote> {
  const [quotes, setQuotes] = useState<Map<string, RealtimeQuote>>(new Map())
  const idsRef = useRef(stockIds)
  idsRef.current = stockIds

  useEffect(() => {
    if (stockIds.length === 0) return

    let active = true

    async function poll() {
      if (!active || idsRef.current.length === 0) return
      try {
        const data = await fetchRealtimeQuotes(idsRef.current)
        if (active && data.length > 0) {
          setQuotes(new Map(data.map((q) => [q.stock_id, q])))
        }
      } catch {
        // Silently ignore — market may be closed
      }
    }

    // Always do one initial fetch (shows last known price even outside trading hours)
    poll()

    // Continuous polling only during trading hours
    let timer: ReturnType<typeof setInterval> | null = null
    if (isTradingHours()) {
      timer = setInterval(poll, intervalMs)
    }

    return () => {
      active = false
      if (timer) clearInterval(timer)
    }
  }, [stockIds.join(","), intervalMs])

  return quotes
}
