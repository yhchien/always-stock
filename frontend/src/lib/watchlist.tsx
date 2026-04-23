"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"

import {
  addWatchlistEntry,
  clearWatchlistEntries,
  fetchWatchlist,
  removeWatchlistEntry,
  type WatchlistCreateRequest,
  type WatchlistItem,
} from "@/lib/api"
import { useAuth } from "@/lib/auth"

export const WATCHLIST_CAPACITY = 20

interface WatchlistContextValue {
  items: WatchlistItem[]
  total: number
  capacity: number
  isLoading: boolean
  isReady: boolean
  error: string | null
  has: (stockId: string) => boolean
  entryIdOf: (stockId: string) => number | null
  add: (payload: WatchlistCreateRequest) => Promise<WatchlistItem>
  remove: (entryId: number) => Promise<void>
  clear: () => Promise<void>
  refresh: () => Promise<void>
}

const WatchlistContext = createContext<WatchlistContextValue | undefined>(undefined)

export function WatchlistProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [capacity, setCapacity] = useState<number>(WATCHLIST_CAPACITY)
  const [isLoading, setIsLoading] = useState<boolean>(false)
  const [isReady, setIsReady] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (status !== "authenticated") {
      setItems([])
      setIsReady(true)
      return
    }
    setIsLoading(true)
    setError(null)
    try {
      const data = await fetchWatchlist()
      setItems(data.items)
      setCapacity(data.capacity ?? WATCHLIST_CAPACITY)
      setIsReady(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "清單載入失敗")
    } finally {
      setIsLoading(false)
    }
  }, [status])

  useEffect(() => {
    if (status === "loading") return
    refresh()
  }, [status, refresh])

  const has = useCallback(
    (stockId: string) => items.some((i) => i.stock_id === stockId),
    [items],
  )
  const entryIdOf = useCallback(
    (stockId: string) => items.find((i) => i.stock_id === stockId)?.id ?? null,
    [items],
  )

  const add = useCallback(async (payload: WatchlistCreateRequest) => {
    const created = await addWatchlistEntry(payload)
    setItems((prev) => [...prev, created])
    return created
  }, [])

  const remove = useCallback(async (entryId: number) => {
    await removeWatchlistEntry(entryId)
    setItems((prev) => prev.filter((i) => i.id !== entryId))
  }, [])

  const clear = useCallback(async () => {
    await clearWatchlistEntries()
    setItems([])
  }, [])

  const value = useMemo<WatchlistContextValue>(
    () => ({
      items,
      total: items.length,
      capacity,
      isLoading,
      isReady,
      error,
      has,
      entryIdOf,
      add,
      remove,
      clear,
      refresh,
    }),
    [items, capacity, isLoading, isReady, error, has, entryIdOf, add, remove, clear, refresh],
  )

  return <WatchlistContext.Provider value={value}>{children}</WatchlistContext.Provider>
}

export function useWatchlist(): WatchlistContextValue {
  const ctx = useContext(WatchlistContext)
  if (!ctx) throw new Error("useWatchlist must be used within <WatchlistProvider>")
  return ctx
}
