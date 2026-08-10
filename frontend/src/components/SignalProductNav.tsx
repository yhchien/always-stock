"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { useSignalsViewMode } from "@/lib/signalsViewMode"

const LINKS = [
  ["/signals", "總覽"],
  ["/signals/recommendations", "正式推薦"],
  ["/signals/archive", "追蹤紀錄"],
  ["/signals/observations", "觀察生命週期"],
  ["/signals/outcomes", "結果分析"],
  ["/signals/debug", "Debug"],
] as const

// Debug／結果分析／觀察生命週期是純工程診斷頁，正式版時不顯示連結（直接輸入網址仍可
// 進入，只是不曝光入口）。
// - 結果分析的 Day10 達標率／Winner Recall 是在稽核「選股演算法好不好」，跟「這檔股票
//   現在賺不賠」是兩件事——後者已經在正式推薦卡片直接顯示報酬率，不需要使用者自己來
//   這頁換算。
// - 觀察生命週期（P4）跟追蹤紀錄（archive／魚尾）是兩套獨立計算的追蹤系統（2026-08-10
//   合併）；正式版只留 archive 一個入口當「追蹤紀錄」，P4 的觀察中／警戒／已停止觀察
//   狀態已經以徽章形式嵌進 archive 卡片詳情，原始 Review Timeline／後端證據 JSON 這類
//   工程診斷資訊才留在這頁，給想深入看的人用。
const ENGINEERING_ONLY_HREFS: ReadonlySet<string> = new Set([
  "/signals/debug",
  "/signals/outcomes",
  "/signals/observations",
])

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

