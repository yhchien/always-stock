"use client"

import type { ReactNode } from "react"

import SiteGate from "@/components/SiteGate"
import { AuthProvider } from "@/lib/auth"
import { WatchlistProvider } from "@/lib/watchlist"

/**
 * 所有 client-side provider 集中在這裡，避免把整個 layout.tsx
 * 加上 "use client"（layout 是 Server Component）。
 *
 * SiteGate 包在最外層：未通過密碼閘門前，子層（Navbar / 主內容）完全不渲染。
 */
export default function AppProviders({ children }: { children: ReactNode }) {
  return (
    <SiteGate>
      <AuthProvider>
        <WatchlistProvider>{children}</WatchlistProvider>
      </AuthProvider>
    </SiteGate>
  )
}
