"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
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

export default function HotMoneyList({
  industryName,
  subIndustry,
  date,
  days = 3,
  limit,
  title,
}: HotMoneyListProps) {
  const router = useRouter()
  const [data, setData] = useState<HotMoneyResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const effectiveLimit = limit ?? (industryName ? 10 : 20)
  const heading = title ?? `近 ${days} 日三大法人累計買超 Top ${effectiveLimit}`

  useEffect(() => {
    if (!date) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    const request = industryName
      ? fetchIndustryHotMoney(industryName, date, {
          days,
          limit: effectiveLimit,
          subIndustry: subIndustry ?? null,
        }, { signal: controller.signal })
      : fetchMarketHotMoney(date, days, effectiveLimit, { signal: controller.signal })

    request
      .then((res) => {
        if (!controller.signal.aborted) setData(res)
      })
      .catch((err) => {
        if (controller.signal.aborted) return
        setError(toDisplayError(err))
        setData(null)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [industryName, subIndustry, date, days, effectiveLimit])

  const items = data?.items ?? []
  const windowLabel =
    data?.start_date && data?.end_date
      ? data.start_date === data.end_date
        ? data.end_date
        : `${data.start_date} ~ ${data.end_date}`
      : null

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <h2 className="text-base font-semibold text-slate-100">{heading}</h2>
        {windowLabel && (
          <span className="text-xs text-slate-500">窗口：{windowLabel}</span>
        )}
      </div>

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
        <div className="rounded-lg border border-slate-700 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700 hover:bg-transparent">
                <TableHead className="text-slate-300 w-10 text-center">#</TableHead>
                <TableHead className="text-slate-300">個股</TableHead>
                {!industryName && (
                  <TableHead className="text-slate-300 hidden md:table-cell">產業</TableHead>
                )}
                <TableHead className="text-slate-300 hidden sm:table-cell">子產業</TableHead>
                <TableHead className="text-slate-300 text-right">期間漲跌</TableHead>
                <TableHead className="text-slate-300 text-right hidden md:table-cell">外資</TableHead>
                <TableHead className="text-slate-300 text-right hidden md:table-cell">投信</TableHead>
                <TableHead className="text-slate-300 text-right hidden lg:table-cell">自營</TableHead>
                <TableHead className="text-slate-300 text-right">合計</TableHead>
                <TableHead className="text-slate-300 w-28 text-right">清單</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow
                  key={item.stock_id}
                  className="border-slate-700 cursor-pointer hover:bg-slate-800/40"
                  onClick={() => router.push(`/stocks/${item.stock_id}?date=${date}`)}
                >
                  <TableCell className="text-center font-mono text-xs text-slate-500">{item.rank}</TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="text-sm font-medium text-slate-100">{item.stock_name}</span>
                      <span className="font-mono text-xs text-slate-500">{item.stock_id}</span>
                    </div>
                  </TableCell>
                  {!industryName && (
                    <TableCell className="hidden md:table-cell text-xs text-slate-400">
                      {item.industry_name || "—"}
                    </TableCell>
                  )}
                  <TableCell className="hidden sm:table-cell text-xs text-slate-400">
                    {item.sub_industry ?? "—"}
                  </TableCell>
                  <TableCell className="text-right"><PctCell value={item.price_change_pct} /></TableCell>
                  <TableCell className="text-right hidden md:table-cell"><AmountCell value={item.foreign_net_amount} /></TableCell>
                  <TableCell className="text-right hidden md:table-cell"><AmountCell value={item.trust_net_amount} /></TableCell>
                  <TableCell className="text-right hidden lg:table-cell"><AmountCell value={item.dealer_net_amount} /></TableCell>
                  <TableCell className="text-right"><AmountCell value={item.total_net_amount} /></TableCell>
                  {/* 整格 stopPropagation：避免點到 cell 邊緣 / padding 觸發 row 導航到個股頁 */}
                  <TableCell
                    className="text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <WatchlistAddButton
                      stockId={item.stock_id}
                      stockName={item.stock_name}
                      defaultDate={date}
                      defaultAvgPrice={item.end_close_price}
                      variant="compact"
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}
