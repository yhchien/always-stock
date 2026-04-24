"use client"

import { FormEvent, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

import { searchStocks, type StockSearchItem } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useWatchlist } from "@/lib/watchlist"

const SEARCH_DEBOUNCE_MS = 200
const NOT_FOUND_MESSAGE = "沒有該股票，請確定股票代號跟確定為上市股"

export default function Navbar() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const isHome = pathname === "/"
  const isLoginPage = pathname === "/login"
  const isWatchlistPage = pathname === "/watchlist"
  const { user, status, logout } = useAuth()
  const { total, capacity } = useWatchlist()
  const [query, setQuery] = useState("")
  const [suggestions, setSuggestions] = useState<StockSearchItem[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selected, setSelected] = useState<StockSearchItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const searchRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const keyword = query.trim()
    if (!keyword || selected?.stock_id === keyword || `${selected?.stock_id} ${selected?.stock_name}` === keyword) {
      setSuggestions([])
      return
    }

    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      searchStocks(keyword, { signal: controller.signal })
        .then((items) => {
          setSuggestions(items)
          setShowSuggestions(items.length > 0)
        })
        .catch(() => {
          setSuggestions([])
        })
    }, SEARCH_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [query, selected])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setShowSuggestions(false)
      }
    }

    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const navigateToStock = (item: StockSearchItem) => {
    const params = new URLSearchParams()
    const date = searchParams.get("date")
    if (date) params.set("date", date)
    const url = params.toString() ? `/stocks/${item.stock_id}?${params}` : `/stocks/${item.stock_id}`

    setSelected(item)
    setQuery(`${item.stock_id} ${item.stock_name}`)
    setSuggestions([])
    setShowSuggestions(false)
    setError(null)
    router.push(url)
  }

  const handleSelect = (item: StockSearchItem) => {
    navigateToStock(item)
  }

  const resolveMatch = async (): Promise<StockSearchItem | "multiple" | null> => {
    const keyword = query.trim()
    if (!keyword) return null

    const exactFromSuggestions = suggestions.find((item) =>
      item.stock_id === keyword ||
      item.stock_name === keyword ||
      `${item.stock_id} ${item.stock_name}` === keyword
    )
    if (exactFromSuggestions) return exactFromSuggestions

    const items = await searchStocks(keyword)
    const exact = items.find((item) =>
      item.stock_id === keyword ||
      item.stock_name === keyword ||
      `${item.stock_id} ${item.stock_name}` === keyword
    )

    if (exact) return exact
    if (items.length === 1) return items[0]

    setSuggestions(items)
    setShowSuggestions(items.length > 0)
    return items.length > 0 ? "multiple" : null
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const keyword = query.trim()
    if (!keyword) return

    setLoading(true)
    setError(null)
    try {
      if (selected && (`${selected.stock_id} ${selected.stock_name}` === keyword || selected.stock_id === keyword || selected.stock_name === keyword)) {
        navigateToStock(selected)
        return
      }

      const match = await resolveMatch()
      if (match === "multiple") {
        return
      }
      if (!match) {
        setError(NOT_FOUND_MESSAGE)
        return
      }
      navigateToStock(match)
    } catch {
      setError("股票搜尋失敗")
    } finally {
      setLoading(false)
    }
  }

  return (
    <header className="sticky top-0 z-50 border-b border-slate-700/40 bg-slate-900/90 backdrop-blur-sm">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-2">
        {/* Logo */}
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold tracking-tight text-slate-100 hover:text-white transition-colors"
        >
          <span className="text-red-400 font-mono text-base">▲</span>
          always-stock
        </Link>

        {!isLoginPage && (
          <div ref={searchRef} className="order-last relative w-full sm:order-none sm:flex-1 sm:max-w-md">
            <form onSubmit={handleSubmit} className="flex items-center gap-2">
              <input
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setSelected(null)
                  setError(null)
                }}
                onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
                placeholder="搜尋股票代號或名稱"
                className="w-full rounded-md border border-slate-700/60 bg-slate-800/80 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
                aria-label="搜尋股票代號或名稱"
              />
              <button
                type="submit"
                disabled={loading || !query.trim()}
                className="shrink-0 rounded-md border border-slate-700/60 px-3 py-1.5 text-xs text-slate-200 transition-colors hover:border-slate-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading ? "搜尋中…" : "搜尋"}
              </button>
            </form>

            {showSuggestions && suggestions.length > 0 && (
              <div className="absolute left-0 right-0 top-[calc(100%+0.375rem)] z-50 overflow-hidden rounded-md border border-slate-700 bg-slate-900 shadow-xl">
                {suggestions.map((item) => (
                  <button
                    key={item.stock_id}
                    type="button"
                    onClick={() => handleSelect(item)}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-slate-800"
                  >
                    <span className="flex items-center gap-2 truncate">
                      <span className="font-mono text-slate-400">{item.stock_id}</span>
                      <span className="truncate">{item.stock_name}</span>
                    </span>
                    <span className="truncate text-xs text-slate-500">{item.industry_name}</span>
                  </button>
                ))}
              </div>
            )}

            {error && (
              <p className="absolute left-0 top-[calc(100%+0.375rem)] z-40 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                {error}
              </p>
            )}
          </div>
        )}

        <div className="ml-auto flex items-center gap-3">
          {/* 非首頁才顯示「回首頁」*/}
          {!isHome && (
            <Link
              href="/"
              className="text-xs text-slate-500 hover:text-slate-200 transition-colors border border-slate-700/40 hover:border-slate-500 rounded-md px-3 py-1"
            >
              回首頁
            </Link>
          )}

          {/* Auth 狀態：loading 期間也顯示登入按鈕（半透明 + pulse dot），
              避免冷啟動時 /api/auth/me 慢回導致按鈕長時間卡在「…」看不見 */}
          {user ? (
            <div className="flex items-center gap-2">
              {!isWatchlistPage && (
                <Link
                  href="/watchlist"
                  className="text-xs text-slate-300 hover:text-white transition-colors border border-slate-700/40 hover:border-slate-500 rounded-md px-3 py-1"
                >
                  我的清單
                  <span className="ml-1 font-mono text-slate-500">
                    {total}/{capacity}
                  </span>
                </Link>
              )}
              <span className="text-xs text-slate-400">
                {user.name ?? user.email}
                {user.is_admin ? (
                  <span className="ml-1 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
                    admin
                  </span>
                ) : null}
              </span>
              <button
                onClick={logout}
                className="text-xs text-slate-500 hover:text-slate-200 transition-colors border border-slate-700/40 hover:border-slate-500 rounded-md px-3 py-1"
              >
                登出
              </button>
            </div>
          ) : isLoginPage ? null : (
            <Link
              href="/login"
              className={`text-xs text-slate-200 bg-sky-600 hover:bg-sky-500 transition-colors rounded-md px-3 py-1 inline-flex items-center gap-1.5 ${
                status === "loading" ? "opacity-70" : ""
              }`}
              aria-busy={status === "loading"}
            >
              {status === "loading" && (
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-100/80" />
              )}
              登入 / 註冊
            </Link>
          )}
        </div>
      </div>
    </header>
  )
}
