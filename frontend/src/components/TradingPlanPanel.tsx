import { type ReactNode } from "react"

export type TradingPlanAccent = "amber" | "cyan" | "emerald" | "slate" | "rose"

const ACCENT_CLASSES: Record<
  TradingPlanAccent,
  { title: string; chip: string; bullet: string }
> = {
  amber: {
    title: "text-amber-300",
    chip: "bg-amber-400/15 text-amber-200 border border-amber-400/30",
    bullet: "text-amber-300",
  },
  cyan: {
    title: "text-cyan-300",
    chip: "bg-cyan-400/15 text-cyan-200 border border-cyan-400/30",
    bullet: "text-cyan-300",
  },
  emerald: {
    title: "text-emerald-300",
    chip: "bg-emerald-400/15 text-emerald-200 border border-emerald-400/30",
    bullet: "text-emerald-300",
  },
  slate: {
    title: "text-slate-200",
    chip: "bg-slate-500/20 text-slate-200 border border-slate-500/40",
    bullet: "text-slate-400",
  },
  rose: {
    title: "text-rose-300",
    chip: "bg-rose-400/15 text-rose-200 border border-rose-400/30",
    bullet: "text-rose-300",
  },
}

export interface TradingPlanPanelProps {
  /** 卡片左上角編號 chip；可傳數字或文字（例 1、"A"）。不傳則隱藏 chip。 */
  number?: string | number
  /** 卡片標題 */
  title: string
  /** Accent 配色：amber 題材 / cyan 資金 / emerald 籌碼 / rose 融券 / slate 技術或一般 */
  accent?: TradingPlanAccent
  /** 卡片右上角額外資訊（例：「2 個訊號」「強烈」等 chip） */
  badge?: ReactNode
  /** 主內容；建議搭配 PanelBulletList / PanelChecklist / 自定 ReactNode */
  children: ReactNode
  className?: string
}

/**
 * 仿 jianxuanchiustock 交易計畫的編號 panel。
 *
 * 對應原 HTML（`panel()` helper）：
 *   <div class="bg-slate-800/60 border border-slate-700/50 rounded-xl p-3 sm:p-4">
 *     <h4 class="text-{accent}-400 font-black ...">
 *       <span class="bg-{accent}-400/20 ...">N</span> Title
 *     </h4>
 *     <div class="text-xs sm:text-sm text-slate-200 ...">{children}</div>
 *   </div>
 *
 * 配色從 slate → zinc 對齊既有專案 palette。
 */
export default function TradingPlanPanel({
  number,
  title,
  accent = "slate",
  badge,
  children,
  className,
}: TradingPlanPanelProps) {
  const styles = ACCENT_CLASSES[accent]
  return (
    <div
      className={`rounded-xl border border-zinc-700/50 bg-zinc-800/60 p-3 sm:p-4 ${className ?? ""}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h4
          className={`flex items-center gap-1.5 text-sm font-black ${styles.title}`}
        >
          {number !== undefined ? (
            <span
              className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-xs font-bold ${styles.chip}`}
            >
              {number}
            </span>
          ) : null}
          <span>{title}</span>
        </h4>
        {badge ? <div className="shrink-0">{badge}</div> : null}
      </div>
      <div className="space-y-1.5 text-xs leading-relaxed text-slate-200 sm:text-sm">
        {children}
      </div>
    </div>
  )
}

export interface PanelBulletListProps {
  items: ReactNode[]
  bulletAccent?: TradingPlanAccent
  /** 為空時顯示的提示文字（例「資料待更新」） */
  emptyText?: string
}

/** 對齊對方 `<ul class="space-y-1">` + 彩色項目符號。空陣列時顯示 emptyText 而非塌成 0 高度。 */
export function PanelBulletList({
  items,
  bulletAccent = "cyan",
  emptyText = "—",
}: PanelBulletListProps) {
  if (items.length === 0) {
    return <p className="text-xs italic text-slate-500">{emptyText}</p>
  }
  const bulletClass = ACCENT_CLASSES[bulletAccent].bullet
  return (
    <ul className="space-y-1">
      {items.map((item, idx) => (
        <li key={idx} className="flex items-start gap-1.5">
          <span className={`mt-0.5 leading-none ${bulletClass}`}>•</span>
          <span className="flex-1">{item}</span>
        </li>
      ))}
    </ul>
  )
}

export interface PanelChecklistProps {
  items: ReactNode[]
  emptyText?: string
}

/** 對齊對方 `<ul>` + emerald `☑` checklist。常用於「執行紀律」「觀察重點」。 */
export function PanelChecklist({ items, emptyText = "—" }: PanelChecklistProps) {
  if (items.length === 0) {
    return <p className="text-xs italic text-slate-500">{emptyText}</p>
  }
  return (
    <ul className="space-y-1">
      {items.map((item, idx) => (
        <li key={idx} className="flex items-start gap-1.5">
          <span className="leading-none text-emerald-400">☑</span>
          <span className="flex-1">{item}</span>
        </li>
      ))}
    </ul>
  )
}
