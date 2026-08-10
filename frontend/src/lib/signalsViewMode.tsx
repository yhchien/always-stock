"use client"

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react"

export type SignalsViewMode = "production" | "engineering"

const STORAGE_KEY = "always-stock:signals:view-mode"

function isValidMode(value: string | null): value is SignalsViewMode {
  return value === "production" || value === "engineering"
}

// 極簡 pub-sub：useSyncExternalStore 需要能感知「同一分頁內」的變化——
// 原生 storage 事件只在其他分頁才會觸發，同分頁呼叫 setItem 不會自動通知自己。
const listeners = new Set<() => void>()

function subscribe(callback: () => void): () => void {
  listeners.add(callback)
  window.addEventListener("storage", callback)
  return () => {
    listeners.delete(callback)
    window.removeEventListener("storage", callback)
  }
}

function getSnapshot(): SignalsViewMode {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isValidMode(stored) ? stored : "production"
  } catch {
    return "production"
  }
}

// SSR / 第一次 client render 前一律回預設值，避免 hydration mismatch；
// mount 後 useSyncExternalStore 會自動改讀 getSnapshot() 並在不同步時觸發重新渲染。
function getServerSnapshot(): SignalsViewMode {
  return "production"
}

function writeMode(next: SignalsViewMode): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // 隱私模式 / SSR 皆可能噴錯，忽略即可
  }
  listeners.forEach((listener) => listener())
}

interface SignalsViewModeContextValue {
  mode: SignalsViewMode
  isEngineering: boolean
  setMode: (mode: SignalsViewMode) => void
  toggle: () => void
}

const SignalsViewModeContext = createContext<SignalsViewModeContextValue | undefined>(undefined)

export function SignalsViewModeProvider({ children }: { children: ReactNode }) {
  const mode = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)

  const setMode = useCallback((next: SignalsViewMode) => {
    writeMode(next)
  }, [])

  const toggle = useCallback(() => {
    writeMode(mode === "production" ? "engineering" : "production")
  }, [mode])

  const value = useMemo<SignalsViewModeContextValue>(
    () => ({ mode, isEngineering: mode === "engineering", setMode, toggle }),
    [mode, setMode, toggle],
  )

  return (
    <SignalsViewModeContext.Provider value={value}>{children}</SignalsViewModeContext.Provider>
  )
}

export function useSignalsViewMode(): SignalsViewModeContextValue {
  const ctx = useContext(SignalsViewModeContext)
  if (!ctx) throw new Error("useSignalsViewMode must be used within <SignalsViewModeProvider>")
  return ctx
}
