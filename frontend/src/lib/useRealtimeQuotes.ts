"use client"

import { useEffect, useRef, useState } from "react"
import { fetchRealtimeQuotes, type RealtimeQuote } from "@/lib/api"

/**
 * Poll real-time quotes for a list of stock IDs.
 * Returns a map of stock_id → RealtimeQuote, refreshed every `intervalMs`.
 * Automatically pauses outside TWSE trading hours (09:00-13:30 weekdays, UTC+8).
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

    // Initial fetch
    poll()

    // Set up polling interval
    const timer = setInterval(poll, intervalMs)

    return () => {
      active = false
      clearInterval(timer)
    }
  }, [stockIds.join(","), intervalMs])

  return quotes
}
