"use client"

import type { ReactNode } from "react"

import { AuthProvider } from "@/lib/auth"

/**
 * 所有 client-side provider 集中在這裡，避免把整個 layout.tsx
 * 加上 "use client"（layout 是 Server Component）。
 */
export default function AppProviders({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}
