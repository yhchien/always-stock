import type { SignalWatchlistItem } from "@/lib/api"

import SignalAssetBadge from "@/components/SignalAssetBadge"

export default function SignalNotSelectedSection({
  items,
}: {
  items: SignalWatchlistItem[]
}) {
  if (items.length === 0) return null

  return (
    <details className="rounded border border-slate-700 bg-slate-900/30">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-300">
        未列入今日推薦（{items.length}）
      </summary>
      <div className="divide-y divide-slate-800 border-t border-slate-700">
        {items.map((item) => (
          <div
            key={item.stock}
            className="grid gap-1 px-3 py-2 text-xs text-slate-300 sm:grid-cols-[minmax(8rem,1fr)_auto_minmax(12rem,2fr)] sm:items-center"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-100">
                {item.stock} {item.name}
              </span>
              <SignalAssetBadge assetType={item.asset_type} />
            </div>
            <span className="text-slate-500">
              Backend #{item.backend_priority_rank ?? "—"}
              {item.theme_cluster ? ` · ${item.theme_cluster}` : ""}
            </span>
            <span className="text-slate-400">
              {item.selection_reason_code
                ? `${item.selection_reason_code}：`
                : ""}
              {item.selection_reason ?? "本日相對優勢尚不足。"}
            </span>
          </div>
        ))}
      </div>
    </details>
  )
}
