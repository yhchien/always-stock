"use client"

import { useEffect, useState, type ReactNode } from "react"

interface CollapsibleSectionProps {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  defaultCollapsed?: boolean
  storageKey?: string
  className?: string
  headerClassName?: string
  contentClassName?: string
}

function readStoredState(storageKey: string | undefined, defaultCollapsed: boolean): boolean {
  if (!storageKey || typeof window === "undefined") return defaultCollapsed
  try {
    const saved = window.localStorage.getItem(storageKey)
    if (saved === null) return defaultCollapsed
    return saved === "true"
  } catch {
    return defaultCollapsed
  }
}

export default function CollapsibleSection({
  title,
  subtitle,
  actions,
  children,
  defaultCollapsed = false,
  storageKey,
  className = "rounded-lg border border-zinc-700 bg-zinc-700/50",
  headerClassName = "flex flex-wrap items-center justify-between gap-3 px-4 py-3",
  contentClassName = "border-t border-zinc-700 px-4 py-4",
}: CollapsibleSectionProps) {
  const [collapsed, setCollapsed] = useState(() => readStoredState(storageKey, defaultCollapsed))

  useEffect(() => {
    if (!storageKey) return
    try {
      window.localStorage.setItem(storageKey, String(collapsed))
    } catch {
      // ignore persistence failures
    }
  }, [collapsed, storageKey])

  return (
    <section className={className}>
      <header className={headerClassName}>
        <button
          type="button"
          onClick={() => setCollapsed((prev) => !prev)}
          className="flex min-w-0 flex-wrap items-baseline gap-2 text-left text-base font-semibold text-slate-100 hover:text-sky-300"
          aria-expanded={!collapsed}
        >
          <span aria-hidden className="text-slate-400">
            {collapsed ? "▸" : "▾"}
          </span>
          <span>{title}</span>
          {subtitle ? (
            <span className="text-xs font-normal text-slate-500">{subtitle}</span>
          ) : null}
        </button>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>

      {!collapsed ? (
        <div className={contentClassName}>
          {children}
        </div>
      ) : null}
    </section>
  )
}
