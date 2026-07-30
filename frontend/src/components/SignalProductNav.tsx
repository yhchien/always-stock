"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

const LINKS = [
  ["/signals", "總覽"],
  ["/signals/recommendations", "正式推薦"],
  ["/signals/observations", "觀察生命週期"],
  ["/signals/outcomes", "結果分析"],
  ["/signals/debug", "Debug"],
] as const

export default function SignalProductNav() {
  const pathname = usePathname()
  return (
    <nav
      aria-label="魚尾選股產品導覽"
      className="mb-5 flex gap-1 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-1"
    >
      {LINKS.map(([href, label]) => {
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
    </nav>
  )
}
