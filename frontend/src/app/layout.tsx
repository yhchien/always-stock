import type { Metadata } from "next"
import { Suspense } from "react"
import { Geist, Geist_Mono } from "next/font/google"
import "./globals.css"
import AppProviders from "@/components/AppProviders"
import Navbar from "@/components/Navbar"

// Navbar 內用 useSearchParams() 讀 ?date= 帶到個股頁；Next 16 prerender
// (含 /_not-found) 會 bail out 除非包在 Suspense 裡。fallback 用同高 header
// 骨架避免 CLS。
function NavbarFallback() {
  return (
    <header className="sticky top-0 z-50 h-[44px] border-b border-slate-700/40 bg-slate-900/90 backdrop-blur-sm" />
  )
}

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
})

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "台股法人流向儀表板",
  description: "TWSE 產業別三大法人資金流向分析",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-TW" className={`${geistSans.variable} ${geistMono.variable} dark h-full antialiased`}>
      <body className="min-h-full bg-slate-900 text-slate-100 flex flex-col">
        <AppProviders>
          <Suspense fallback={<NavbarFallback />}>
            <Navbar />
          </Suspense>
          <div className="flex-1">{children}</div>
        </AppProviders>
      </body>
    </html>
  )
}
