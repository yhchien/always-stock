"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { useSignalsViewMode } from "@/lib/signalsViewMode"

const LINKS = [
  ["/signals", "總覽"],
  ["/signals/recommendations", "正式推薦"],
  ["/signals/observations", "觀察生命週期"],
  ["/signals/outcomes", "結果分析"],
  ["/signals/debug", "Debug"],
] as const

// Debug 是純工程診斷頁，正式版時不顯示連結（直接輸入網址仍可進入，只是不曝光入口）。
const ENGINEERING_ONLY_HREFS: ReadonlySet<string> = new Set(["/signals/debug"])

export default function SignalProductNav() {
  const pathname = usePathname()
  const { isEngineering, toggle } = useSignalsViewMode()
  const links = LINKS.filter(([href]) => isEngineering || !ENGINEERING_ONLY_HREFS.has(href))

  return (
    <nav
      aria-label="魚尾選股產品導覽"
      className="mb-5 flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/60 p-1"
    >
      <div className="flex flex-1 flex-wrap gap-1 overflow-x-auto">
        {links.map(([href, label]) => {
          const active =
            pathname === href ||
            (href !== "/signals" && pathname.startsWith(`${href}/`))
          return (
            <Link
              key={href}
              href={href}
              className={`shrink-0 rounded-lg px-3 py-2 text-xs transition-colors ${
                active
                  ? "bg-sky-500/15 text-sky-100"
                  : "text-slate-400 hover:bg-slate-800/70 hover:text-slate-200"
              }`}
            >
              {label}
            </Link>
          )
        })}
      </div>
      <button
        type="button"
        onClick={toggle}
        aria-pressed={isEngineering}
        title={isEngineering ? "切換為正式版（精簡內容）" : "切換為工程版（完整診斷資訊）"}
        className={`mr-1 shrink-0 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
          isEngineering
            ? "border-amber-500/40 bg-amber-500/10 text-amber-200 hover:border-amber-400/60"
            : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
        }`}
      >
        {isEngineering ? "工程版" : "正式版"}
      </button>
    </nav>
  )
}

