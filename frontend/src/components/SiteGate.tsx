"use client"

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react"

import { apiFetch } from "@/lib/api"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

const STORAGE_UNLOCKED_UNTIL = "always-stock:gate:unlocked_until"
const STORAGE_LOCKED_UNTIL = "always-stock:gate:locked_until"
const STORAGE_ATTEMPTS = "always-stock:gate:attempts"

const UNLOCK_DURATION_MS = 7 * 24 * 60 * 60 * 1000

const DEFAULT_MAX_ATTEMPTS = 3
const DEFAULT_LOCKOUT_MS = 5 * 60 * 1000

type Phase = "boot" | "locked" | "unlocked" | "prompt"

function readNumber(key: string): number | null {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return null
    const value = Number(raw)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

function writeNumber(key: string, value: number) {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // 隱私模式 / iframe 等情境會 throw；忽略
  }
}

function clearKey(key: string) {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // ignore
  }
}

function formatRemaining(ms: number): string {
  if (ms <= 0) return "0 秒"
  const totalSec = Math.ceil(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (min <= 0) return `${sec} 秒`
  return `${min} 分 ${sec.toString().padStart(2, "0")} 秒`
}

function LockedScreen({ remainingMs }: { remainingMs: number }) {
  return (
    <main className="flex min-h-[100dvh] flex-col items-center justify-center gap-6 bg-slate-950 px-6 text-center text-slate-200">
      <div className="text-6xl">🔒</div>
      <h1 className="text-xl font-semibold text-slate-100">已暫時鎖定</h1>
      <p className="max-w-sm text-sm text-slate-400">
        密碼錯誤次數過多，請於 <span className="font-mono text-slate-200">{formatRemaining(remainingMs)}</span> 後再試。
      </p>
      <p className="text-xs text-slate-600">如果仍需協助請聯絡管理員。</p>
    </main>
  )
}

function PromptScreen({
  onSubmit,
  submitting,
  error,
  remainingAttempts,
}: {
  onSubmit: (password: string) => Promise<void>
  submitting: boolean
  error: string | null
  remainingAttempts: number | null
}) {
  const [password, setPassword] = useState("")

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!password.trim() || submitting) return
    void onSubmit(password)
  }

  return (
    <main className="flex min-h-[100dvh] flex-col items-center justify-center gap-6 bg-slate-950 px-6">
      <div className="w-full max-w-sm rounded-xl border border-slate-700/60 bg-slate-900/80 p-6 shadow-xl">
        <h1 className="mb-1 text-lg font-semibold text-slate-100">請輸入訪問密碼</h1>
        <p className="mb-5 text-xs text-slate-500">需要密碼才能進入儀表板。</p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            密碼
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
              className="rounded-md border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none"
            />
          </label>

          {error && <p className="text-xs text-rose-400">{error}</p>}
          {remainingAttempts !== null && remainingAttempts > 0 && error && (
            <p className="text-[11px] text-slate-500">剩餘嘗試次數：{remainingAttempts}</p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-slate-50 transition-colors hover:bg-sky-500 disabled:opacity-50"
          >
            {submitting ? "驗證中…" : "進入"}
          </button>
        </form>
      </div>
    </main>
  )
}

export default function SiteGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("boot")
  const [now, setNow] = useState(() => Date.now())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [maxAttempts, setMaxAttempts] = useState(DEFAULT_MAX_ATTEMPTS)
  const [lockoutMs, setLockoutMs] = useState(DEFAULT_LOCKOUT_MS)
  const [attempts, setAttempts] = useState(0)
  const [lockedUntil, setLockedUntil] = useState<number | null>(null)

  // 從 localStorage 讀初始狀態（mount 後跑）
  useEffect(() => {
    const unlockedUntil = readNumber(STORAGE_UNLOCKED_UNTIL)
    const locked = readNumber(STORAGE_LOCKED_UNTIL)
    const storedAttempts = readNumber(STORAGE_ATTEMPTS) ?? 0
    const current = Date.now()

    if (locked && current < locked) {
      setLockedUntil(locked)
      setAttempts(storedAttempts)
      setPhase("locked")
      return
    }

    // 鎖定到期 → 清狀態
    if (locked && current >= locked) {
      clearKey(STORAGE_LOCKED_UNTIL)
      clearKey(STORAGE_ATTEMPTS)
      setAttempts(0)
    } else {
      setAttempts(storedAttempts)
    }

    if (unlockedUntil && current < unlockedUntil) {
      setPhase("unlocked")
      return
    }

    // 解鎖到期 → 清掉
    if (unlockedUntil && current >= unlockedUntil) {
      clearKey(STORAGE_UNLOCKED_UNTIL)
    }

    setPhase("prompt")
  }, [])

  // 拉 backend config（max_attempts / lockout_seconds）
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/gate/config`, { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (cancelled || !body) return
        if (typeof body.max_attempts === "number") setMaxAttempts(body.max_attempts)
        if (typeof body.lockout_seconds === "number") setLockoutMs(body.lockout_seconds * 1000)
      })
      .catch(() => {
        // 拉不到就用預設值
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 鎖定中 tick：每秒更新 now，到時間自動切回 prompt
  useEffect(() => {
    if (phase !== "locked" || lockedUntil === null) return
    const id = window.setInterval(() => {
      const current = Date.now()
      setNow(current)
      if (current >= lockedUntil) {
        clearKey(STORAGE_LOCKED_UNTIL)
        clearKey(STORAGE_ATTEMPTS)
        setLockedUntil(null)
        setAttempts(0)
        setError(null)
        setPhase("prompt")
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [phase, lockedUntil])

  const handleSubmit = useCallback(
    async (password: string) => {
      setSubmitting(true)
      setError(null)
      try {
        const res = await apiFetch(`${API_BASE}/api/gate/verify`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password }),
        })

        if (res.ok) {
          const expires = Date.now() + UNLOCK_DURATION_MS
          writeNumber(STORAGE_UNLOCKED_UNTIL, expires)
          clearKey(STORAGE_ATTEMPTS)
          clearKey(STORAGE_LOCKED_UNTIL)
          setAttempts(0)
          setLockedUntil(null)
          setPhase("unlocked")
          return
        }

        if (res.status === 503) {
          setError("伺服器尚未設定訪問密碼，請聯絡管理員。")
          return
        }

        // 其餘視為密碼錯誤（含 403 / 422）
        const nextAttempts = attempts + 1
        setAttempts(nextAttempts)
        writeNumber(STORAGE_ATTEMPTS, nextAttempts)

        if (nextAttempts >= maxAttempts) {
          const lockUntil = Date.now() + lockoutMs
          writeNumber(STORAGE_LOCKED_UNTIL, lockUntil)
          setLockedUntil(lockUntil)
          setPhase("locked")
          setError(null)
          return
        }

        setError("密碼錯誤")
      } catch (err) {
        setError(err instanceof Error ? err.message : "驗證失敗")
      } finally {
        setSubmitting(false)
      }
    },
    [attempts, lockoutMs, maxAttempts]
  )

  const remainingAttempts = useMemo(() => {
    return Math.max(0, maxAttempts - attempts)
  }, [maxAttempts, attempts])

  if (phase === "boot") {
    // SSR / 初次 mount 的中性畫面，避免 hydration mismatch；不洩漏主頁面內容
    return (
      <main className="flex min-h-[100dvh] items-center justify-center bg-slate-950 text-slate-500">
        <p className="text-xs">載入中…</p>
      </main>
    )
  }

  if (phase === "locked" && lockedUntil !== null) {
    return <LockedScreen remainingMs={lockedUntil - now} />
  }

  if (phase === "prompt") {
    return (
      <PromptScreen
        onSubmit={handleSubmit}
        submitting={submitting}
        error={error}
        remainingAttempts={remainingAttempts}
      />
    )
  }

  // unlocked → 渲染主頁面
  return <>{children}</>
}
