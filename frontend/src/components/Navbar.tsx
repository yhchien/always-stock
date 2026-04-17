"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

export default function Navbar() {
  const pathname = usePathname()
  const isHome = pathname === "/"

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

        {/* Right: 回首頁（非首頁才顯示）*/}
        {!isHome && (
          <Link
            href="/"
            className="text-xs text-slate-500 hover:text-slate-200 transition-colors border border-slate-700/40 hover:border-slate-500 rounded-md px-3 py-1"
          >
            回首頁
          </Link>
        )}
      </div>
    </header>
  )
}
