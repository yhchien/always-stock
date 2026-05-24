import Link from "next/link"
import { type ReactNode } from "react"

export type EmotionTone =
  | "leader"
  | "follower"
  | "laggard"
  | "strong-buy"
  | "buy"
  | "neutral"
  | "watch"
  | "run"

const TONE_CLASSES: Record<
  EmotionTone,
  {
    base: string
    title: string
    label: string
  }
> = {
  // M23 三類魚尾，全部都看漲，色階遞減；不用綠（綠 = 跌台股慣例衝突）
  leader: {
    base: "bg-rose-900/40 border-rose-600/60 hover:bg-rose-900/55",
    title: "text-rose-100",
    label: "領漲",
  },
  follower: {
    base: "bg-rose-900/20 border-rose-700/40 hover:bg-rose-900/35",
    title: "text-rose-100",
    label: "跟漲",
  },
  laggard: {
    base: "bg-amber-900/30 border-amber-600/50 hover:bg-amber-900/45",
    title: "text-amber-100",
    label: "補漲",
  },
  // M17 5 階評級
  "strong-buy": {
    base: "bg-emerald-900/40 border-emerald-500/60 hover:bg-emerald-900/55",
    title: "text-emerald-100",
    label: "強烈推薦",
  },
  buy: {
    base: "bg-emerald-900/25 border-emerald-600/40 hover:bg-emerald-900/40",
    title: "text-emerald-100",
    label: "推薦",
  },
  neutral: {
    base: "bg-amber-900/25 border-amber-600/40 hover:bg-amber-900/40",
    title: "text-amber-100",
    label: "中立",
  },
  watch: {
    base: "bg-orange-900/30 border-orange-600/50 hover:bg-orange-900/45",
    title: "text-orange-100",
    label: "再看看",
  },
  run: {
    base: "bg-rose-900/40 border-rose-600/60 hover:bg-rose-900/55",
    title: "text-rose-100",
    label: "快跑",
  },
}

export interface SignalEmotionCardProps {
  /** 情緒色階：M23 三類 (leader/follower/laggard) 或 M17 五階評級 */
  tone: EmotionTone
  /** 股票代號;用於預設導向 `/stocks/{stockId}` */
  stockId: string
  /** 股票名稱顯示在 header */
  stockName?: string | null
  /** 自訂導向;預設 `/stocks/{stockId}`。`onCardClick` 提供時忽略此欄位。 */
  href?: string
  /**
   * 點擊整張卡片觸發的 handler;提供時整張卡會 render 成 `<button>` 而非 `<Link>`,
   * 適用於「點卡片開 modal 而非跳頁」的場景（例如 M23 魚尾改 popup 模式）。
   */
  onCardClick?: () => void
  /** 卡片右上角徽章（例 DecisionBadge / RatingPill），會自動 stopPropagation */
  headerRight?: ReactNode
  /** Header 下方副標題（例:產業、子產業） */
  subtitle?: ReactNode
  /** 主內容 */
  children?: ReactNode
  className?: string
}

/**
 * 仿 jianxuanchiustock 「本日適合進場」screen-card：
 *   <button class="bg-rose-50 hover:bg-rose-100 border border-rose-200 rounded-xl p-3 transition">
 *
 * 深色主題下用 `bg-{tone}-900/40` 階梯,hover 浮起 0.5。
 * 預設整張卡片是 `<Link>` 跳 L2;caller 傳 `onCardClick` 時改 render `<button>`。
 * nested 互動元素需自己 `e.stopPropagation()`。
 */
export default function SignalEmotionCard({
  tone,
  stockId,
  stockName,
  href,
  onCardClick,
  headerRight,
  subtitle,
  children,
  className,
}: SignalEmotionCardProps) {
  const styles = TONE_CLASSES[tone]
  const sharedClassName = `group block rounded-xl border p-3 text-left transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/30 ${styles.base} ${className ?? ""}`

  const cardBody = (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <span className={`text-base font-black ${styles.title}`}>
              {stockId}
            </span>
            {stockName ? (
              <span className="truncate text-sm text-slate-100/90">
                {stockName}
              </span>
            ) : null}
          </div>
          {subtitle ? (
            <div className="mt-0.5 text-xs text-slate-300/80">{subtitle}</div>
          ) : null}
        </div>
        {headerRight ? (
          <div
            className="shrink-0"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {headerRight}
          </div>
        ) : null}
      </div>
      {children ? <div className="mt-2">{children}</div> : null}
    </>
  )

  if (onCardClick) {
    return (
      <button
        type="button"
        onClick={onCardClick}
        className={`${sharedClassName} w-full cursor-pointer`}
      >
        {cardBody}
      </button>
    )
  }

  const finalHref = href ?? `/stocks/${encodeURIComponent(stockId)}`
  return (
    <Link href={finalHref} className={sharedClassName}>
      {cardBody}
    </Link>
  )
}

/** 取得情緒色階的「中文標籤」;caller 想自訂顯示時可用 */
export function emotionLabel(tone: EmotionTone): string {
  return TONE_CLASSES[tone].label
}
