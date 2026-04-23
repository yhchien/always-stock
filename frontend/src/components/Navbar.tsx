"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { useAuth } from "@/lib/auth"
import { useWatchlist } from "@/lib/watchlist"

export default function Navbar() {
  const pathname = usePathname()
  const isHome = pathname === "/"
  const isLoginPage = pathname === "/login"
  const isWatchlistPage = pathname === "/watchlist"
  const { user, status, logout } = useAuth()
  const { total, capacity } = useWatchlist()

  return (
    <header className="sticky top-0 z-50 border-b border-slate-700/40 bg-slate-900/90 backdrop-blur-sm">
      <div className="mx-auto flex h-12 max-w-6xl items-center justify-between px-4">
        {/* Logo */}
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold tracking-tight text-slate-100 hover:text-white transition-colors"
        >
          <span className="text-red-400 font-mono text-base">▲</span>
          always-stock
        </Link>

        <div className="flex items-center gap-3">
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
