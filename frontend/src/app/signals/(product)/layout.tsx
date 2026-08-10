import type { ReactNode } from "react"

import SignalProductNav from "@/components/SignalProductNav"
import { SignalsViewModeProvider } from "@/lib/signalsViewMode"

/**
 * `/signals` 產品頁（總覽／正式推薦／觀察生命週期／結果分析／Debug）共用 layout：
 * 統一提供正式版／工程版 toggle 的狀態，並集中 render 一次 SignalProductNav
 * （子頁不再各自重複 render）。
 *
 * 用 `(product)` route group 隔離：Next.js 的 layout 會套用到整個子樹，
 * archive / phase2 兩頁是獨立風格頁面（不套 toggle、不用 SignalProductNav），
 * 放在 route group 外面才不會被這層 layout 影響（route group 資料夾名不影響 URL，
 * `/signals`、`/signals/recommendations` 等路徑完全不變）。
 */
export default function SignalsLayout({ children }: { children: ReactNode }) {
  return (
    <SignalsViewModeProvider>
      <SignalProductNav />
      {children}
    </SignalsViewModeProvider>
  )
}
