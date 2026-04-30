"use client"

import { useEffect, useRef, useState } from "react"

import { fetchLatestSignalJob, type SignalJobResponse } from "@/lib/api"

const POLL_INTERVAL_MS = 3000

interface PollingState {
  job: SignalJobResponse | null
  loading: boolean
  error: string | null
}

/**
 * 多工進度條輪詢 hook（spec §13.4）。
 *
 * 行為：
 *   - mount 時打一次 GET /api/signals/jobs/latest
 *   - 若 status=pending|running → 每 3 秒繼續 poll；status=done|failed 或 null → 停止
 *   - `bumpKey` 變動時重新觸發 polling（讓使用者點「重新產生」後立刻接上新 job）
 *   - unmount 自動清 timer，無 long-lived connection
 */
export function useSignalJobPolling(
  bumpKey: number = 0,
  initialJob: SignalJobResponse | null = null,
  initialLoaded: boolean = false,
): PollingState {
  const [job, setJob] = useState<SignalJobResponse | null>(initialJob)
  const [loading, setLoading] = useState(!initialLoaded)
  const [error, setError] = useState<string | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const useInitialValue = initialLoaded && bumpKey === 0
    const initialJobIsActive =
      initialJob != null && (initialJob.status === "pending" || initialJob.status === "running")

    async function poll() {
      try {
        const next = await fetchLatestSignalJob()
        if (cancelledRef.current) return
        setJob(next)
        setError(null)
        if (next && (next.status === "pending" || next.status === "running")) {
          timer = setTimeout(poll, POLL_INTERVAL_MS)
        }
      } catch (err) {
        if (cancelledRef.current) return
        setError(err instanceof Error ? err.message : "Job 狀態載入失敗")
        // 失敗時 backoff：等 6 秒再重試，避免一直撞同一個錯誤
        timer = setTimeout(poll, POLL_INTERVAL_MS * 2)
      } finally {
        if (!cancelledRef.current) setLoading(false)
      }
    }

    if (useInitialValue) {
      setJob(initialJob)
      setLoading(false)
      setError(null)
      if (initialJobIsActive) {
        timer = setTimeout(poll, POLL_INTERVAL_MS)
      }
    } else {
      setLoading(true)
      poll()
    }

    return () => {
      cancelledRef.current = true
      if (timer) clearTimeout(timer)
    }
  }, [bumpKey, initialJob, initialLoaded])

  return { job, loading, error }
}
