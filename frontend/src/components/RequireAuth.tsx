"use client"

import { usePathname } from "next/navigation"
import type { ReactNode } from "react"

import { useAuth } from "@/lib/auth"
import { isAuthDisabled } from "@/lib/feature_flags"

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, status } = useAuth()
  const pathname = usePathname()

  // DISABLE_AUTH=true 模式：直接放行；不等 useAuth 結算，避免冷啟動時閃載入畫面。
  if (isAuthDisabled()) {
    return <>{children}</>
  }

  if (status === "loading") {
    return (
      <main className="mx-auto flex w-full max-w-5xl items-center justify-center px-4 py-20">
        <p className="text-sm text-slate-500">載入中…</p>
      </main>
    )
  }

  if (!user) {
    const next = encodeURIComponent(pathname || "/")
    return (
      <main className="mx-auto flex w-full max-w-md flex-col items-center gap-4 px-4 py-20 text-center">
        <h1 className="text-lg font-semibold text-slate-100">需要登入</h1>
        <p className="text-sm text-slate-400">
          此頁面僅開放給已登入使用者。
          <br />
          首頁的「AI 交易分析」不需登入即可使用。
        </p>
        <a
          href={`/login?next=${next}`}
          className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-slate-50 transition-colors hover:bg-sky-500"
        >
          前往登入
        </a>
      </main>
    )
  }

  return <>{children}</>
}
