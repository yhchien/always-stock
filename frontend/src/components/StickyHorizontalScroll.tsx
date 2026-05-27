"use client"

import { useEffect, useRef, useState, type ReactNode } from "react"
import { createPortal } from "react-dom"

/**
 * 把一個水平捲動容器（overflow-x: auto）包起來，並在「視口底部」portal 一條 fake scrollbar
 * 與內部 wrapper 雙向同步 scrollLeft；表格很長時不必捲到表格底端才能水平捲。
 *
 * 顯示條件：wrapper 部分在視口內 + wrapper 底部尚未進入視口（亦即原生 scrollbar 還沒露面）
 * → 捲到表格底端時 fake bar 自動隱藏，避免與原生 scrollbar 重複。
 *
 * 多個 wrapper 共存時各自獨立 fake bar；實務上同時部分可見的機率低
 *（捲到 A 表中段時 B 表通常已在視口外）。極端情境疊兩條也仍可用。
 */
export default function StickyHorizontalScroll({
  children,
  className = "",
}: {
  children: ReactNode
  className?: string
}) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const fakeBarRef = useRef<HTMLDivElement>(null)
  const syncingRef = useRef<"wrapper" | "bar" | null>(null)
  const [contentWidth, setContentWidth] = useState(0)
  const [wrapperWidth, setWrapperWidth] = useState(0)
  const [showFakeBar, setShowFakeBar] = useState(false)
  const [mounted, setMounted] = useState(false)
  // 手機（< 768px）觸控直接滑表格本身即可，fake bar 反而視覺干擾且無法用滑鼠拖；
  // 桌機 / 平板（≥ 768px）才 portal 視口底部那條 sticky bar。
  const [isDesktop, setIsDesktop] = useState(false)

  // 必須等 client mount 後才能 createPortal 到 document.body
  useEffect(() => {
    setMounted(true)
    const mq = window.matchMedia("(min-width: 768px)")
    const update = () => setIsDesktop(mq.matches)
    update()
    mq.addEventListener("change", update)
    return () => mq.removeEventListener("change", update)
  }, [])

  // 雙向同步 scrollLeft（用 ref guard 避免無限 loop）
  useEffect(() => {
    const wrapper = wrapperRef.current
    const bar = fakeBarRef.current
    if (!wrapper || !bar) return
    const onWrapperScroll = () => {
      if (syncingRef.current === "bar") return
      syncingRef.current = "wrapper"
      bar.scrollLeft = wrapper.scrollLeft
      requestAnimationFrame(() => {
        syncingRef.current = null
      })
    }
    const onBarScroll = () => {
      if (syncingRef.current === "wrapper") return
      syncingRef.current = "bar"
      wrapper.scrollLeft = bar.scrollLeft
      requestAnimationFrame(() => {
        syncingRef.current = null
      })
    }
    wrapper.addEventListener("scroll", onWrapperScroll, { passive: true })
    bar.addEventListener("scroll", onBarScroll, { passive: true })
    return () => {
      wrapper.removeEventListener("scroll", onWrapperScroll)
      bar.removeEventListener("scroll", onBarScroll)
    }
  }, [mounted])

  // 監控 wrapper 與內部內容寬度（filter / inline expand row 增減時要重算）
  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const update = () => {
      setContentWidth(wrapper.scrollWidth)
      setWrapperWidth(wrapper.clientWidth)
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(wrapper)
    const inner = wrapper.firstElementChild
    if (inner) ro.observe(inner)
    const mo = new MutationObserver(update)
    mo.observe(wrapper, { childList: true, subtree: true })
    return () => {
      ro.disconnect()
      mo.disconnect()
    }
  }, [])

  // 決定何時顯示 fake bar：wrapper 在視口內 AND 底部仍在視口外
  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const update = () => {
      const rect = wrapper.getBoundingClientRect()
      const viewportH = window.innerHeight
      const intersecting = rect.bottom > 0 && rect.top < viewportH
      const bottomBelowViewport = rect.bottom > viewportH
      setShowFakeBar(intersecting && bottomBelowViewport)
    }
    update()
    window.addEventListener("scroll", update, { passive: true })
    window.addEventListener("resize", update, { passive: true })
    return () => {
      window.removeEventListener("scroll", update)
      window.removeEventListener("resize", update)
    }
  }, [])

  // +1 buffer 避免 rounding 邊界誤判
  const hasOverflow = contentWidth > wrapperWidth + 1
  const fakeBarVisible = showFakeBar && hasOverflow

  return (
    <>
      <div
        ref={wrapperRef}
        data-sticky-scroll-wrapper
        className={`relative w-full overflow-x-auto ${className}`}
      >
        {children}
      </div>
      {mounted && isDesktop &&
        createPortal(
          <>
            {/* 視口底部 sticky fake scrollbar；20px 高、深色背景 + 陰影讓使用者明顯感受到 */}
            <div
              ref={fakeBarRef}
              aria-hidden
              className="pointer-events-auto fixed bottom-0 left-0 right-0 z-50 overflow-x-auto border-t-2 border-sky-500/50 bg-slate-800/95 shadow-[0_-6px_16px_rgba(0,0,0,0.45)] backdrop-blur-sm"
              style={{
                display: fakeBarVisible ? "block" : "none",
                height: 20,
              }}
            >
              {/* spacer 撐出對應 contentWidth 讓 browser 渲染原生水平 scrollbar */}
              <div
                style={{
                  width: contentWidth,
                  height: 1,
                }}
              />
            </div>
            {/* 提示文字浮在 sticky bar 上方，告知使用者此處可水平拖動 */}
            <div
              aria-hidden
              className="pointer-events-none fixed left-1/2 z-50 -translate-x-1/2 select-none text-[10px] font-medium tracking-wider text-sky-300"
              style={{
                display: fakeBarVisible ? "block" : "none",
                bottom: 24,
              }}
            >
              ◀ 此處可水平捲動表格 ▶
            </div>
          </>,
          document.body,
        )}
    </>
  )
}
