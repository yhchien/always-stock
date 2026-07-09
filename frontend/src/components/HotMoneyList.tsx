"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import CollapsibleSection from "@/components/CollapsibleSection"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import WatchlistAddButton from "@/components/WatchlistAddButton"
import {
  fetchIndustryHotMoney,
  fetchMarketHotMoney,
  fmtAmount,
  toDisplayError,
  type HotMoneyResponse,
} from "@/lib/api"

interface HotMoneyListProps {
  industryName?: string
  subIndustry?: string | null
  date: string
  days?: number
  limit?: number
  title?: string
  initialData?: HotMoneyResponse | null
  initialDataDate?: string | null
  collapsible?: boolean
  defaultCollapsed?: boolean
  storageKey?: string
}

function AmountCell({ value }: { value: number }) {
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  return <span className={`font-mono text-xs ${color}`}>{fmtAmount(value)}</span>
}

function PctCell({ value }: { value: number | null }) {
  if (value == null) return <span className="text-slate-500 text-xs">—</span>
  const color = value > 0 ? "text-red-400" : value < 0 ? "text-green-400" : "text-slate-400"
  const arrow = value > 0 ? "\u25B2" : value < 0 ? "\u25BC" : ""
  return (
    <span className={`font-mono text-xs ${color}`}>
      {arrow} {value >= 0 ? "+" : ""}{value.toFixed(2)}%
    </span>
  )
}

// 手機（< lg）時法人明細欄位隱藏，改在「個股」格下方以小字列呈現，資料不遺失也不需水平捲動
function InstMiniLine({
  foreignValue,
  trustValue,
  dealerValue,
}: {
  foreignValue: number
  trustValue: number
  dealerValue: number
}) {
  return (
    <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 lg:hidden">
      {([
        ["外", foreignValue],
        ["投", trustValue],
        ["自", dealerValue],
      ] as const).map(([label, value]) => (
        <span key={label} className="inline-flex items-center gap-0.5 whitespace-nowrap">
          <span className="text-[11px] text-slate-500">{label}</span>
          <AmountCell value={value} />
        </span>
      ))}
    </div>
  )
}

export default function HotMoneyList({
  industryName,
  subIndustry,
  date,
  days = 3,
  limit,
  title,
  initialData,
  initialDataDate,
  collapsible = false,
  defaultCollapsed = false,
  storageKey,
}: HotMoneyListProps) {
  const router = useRouter()
  const hasInitialData = initialDataDate === date && initialData != null
  const [data, setData] = useState<HotMoneyResponse | null>(hasInitialData ? null : (initialData ?? null))
  const [loading, setLoading] = useState(hasInitialData ? false : true)
  const [error, setError] = useState<string | null>(null)

  const effectiveLimit = limit ?? (industryName ? 10 : 20)
  const heading = title ?? `近 ${days} 日三大法人累計買超 Top ${effectiveLimit}`

  useEffect(() => {
    if (!date) return
    if (hasInitialData) return
    const controller = new AbortController()
    async function run() {
      setLoading(true)
      setError(null)
      try {
        const res = industryName
          ? await fetchIndustryHotMoney(industryName, date, {
              days,
              limit: effectiveLimit,
              subIndustry: subIndustry ?? null,
            }, { signal: controller.signal })
          : await fetchMarketHotMoney(date, days, effectiveLimit, { signal: controller.signal })
        if (!controller.signal.aborted) setData(res)
      } catch (err) {
        if (controller.signal.aborted) return
        setError(toDisplayError(err))
        setData(null)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    void run()

    return () => controller.abort()
  }, [industryName, subIndustry, date, days, effectiveLimit, hasInitialData])

  const resolvedData = hasInitialData ? initialData : data
  const items = resolvedData?.items ?? []
  const windowLabel =
    resolvedData?.start_date && resolvedData?.end_date
      ? resolvedData.start_date === resolvedData.end_date
        ? resolvedData.end_date
        : `${resolvedData.start_date} ~ ${resolvedData.end_date}`
      : null

  const content = (
    <div className="flex flex-col gap-3">
      {!collapsible && (
        <div className="flex items-baseline gap-3">
          <h2 className="text-base font-semibold text-slate-100">{heading}</h2>
          {windowLabel && (
            <span className="text-xs text-slate-500">窗口：{windowLabel}</span>
          )}
        </div>
      )}

      {loading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && !error && items.length === 0 && (
        <p className="text-sm text-slate-500">此期間無資料</p>
      )}

      {!loading && !error && items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-700">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700 hover:bg-transparent">
                <TableHead className="text-slate-300 w-10 text-center hidden sm:table-cell">#</TableHead>
                <TableHead className="text-slate-300">個股</TableHead>
                {!industryName && (
                  <TableHead className="text-slate-300 hidden lg:table-cell">產業</TableHead>
                )}
                <TableHead className="text-slate-300 hidden lg:table-cell">子產業</TableHead>
                <TableHead className="text-slate-300 text-right">期間漲跌</TableHead>
                <TableHead className="text-slate-300 text-right hidden lg:table-cell">外資</TableHead>
                <TableHead className="text-slate-300 text-right hidden lg:table-cell">投信</TableHead>
                <TableHead className="text-slate-300 text-right hidden lg:table-cell">自營</TableHead>
                <TableHead className="text-slate-300 text-right">合計</TableHead>
                <TableHead className="text-slate-300 w-16 text-right lg:w-28">清單</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow
                  key={item.stock_id}
                  className="border-slate-700 cursor-pointer hover:bg-slate-800/40"
                  onClick={() => router.push(`/stocks/${item.stock_id}?date=${date}`)}
                >
                  <TableCell className="text-center font-mono text-xs text-slate-500 hidden sm:table-cell">{item.rank}</TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium text-slate-100">{item.stock_name}</span>
                      <span className="font-mono text-xs text-slate-500">{item.stock_id}</span>
                      {(() => {
                        const label = [!industryName ? item.industry_name : null, item.sub_industry]
                          .filter(Boolean)
                          .join(" · ")
                        return label ? (
                          <span className="text-[11px] text-slate-500 lg:hidden">{label}</span>
                        ) : null
                      })()}
                      <InstMiniLine
                        foreignValue={item.foreign_net_amount}
                        trustValue={item.trust_net_amount}
                        dealerValue={item.dealer_net_amount}
                      />
                    </div>
                  </TableCell>
                  {!industryName && (
                    <TableCell className="hidden lg:table-cell text-xs text-slate-400">
                      {item.industry_name || "—"}
                    </TableCell>
                  )}
                  <TableCell className="hidden lg:table-cell text-xs text-slate-400">
                    {item.sub_industry ?? "—"}
                  </TableCell>
                  <TableCell className="text-right"><PctCell value={item.price_change_pct} /></TableCell>
                  <TableCell className="text-right hidden lg:table-cell"><AmountCell value={item.foreign_net_amount} /></TableCell>
                  <TableCell className="text-right hidden lg:table-cell"><AmountCell value={item.trust_net_amount} /></TableCell>
                  <TableCell className="text-right hidden lg:table-cell"><AmountCell value={item.dealer_net_amount} /></TableCell>
                  <TableCell className="text-right"><AmountCell value={item.total_net_amount} /></TableCell>
                  <TableCell
                    className="text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <WatchlistAddButton stockId={item.stock_id} variant="compact" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )

  if (!collapsible) return <section>{content}</section>

  return (
    <CollapsibleSection
      title={heading}
      subtitle={windowLabel ? <>窗口：{windowLabel}</> : undefined}
      defaultCollapsed={defaultCollapsed}
      storageKey={storageKey}
    >
      {content}
    </CollapsibleSection>
  )
}
