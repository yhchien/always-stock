import type { SignalWatchlistItem } from "@/lib/api"

import SignalAssetBadge from "@/components/SignalAssetBadge"

export default function SignalRemovedSection({
  items,
}: {
  items: SignalWatchlistItem[]
}) {
  if (items.length === 0) return null

  return (
    <details className="rounded border border-slate-700 bg-slate-950/30">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-400">
        已排除（{items.length}）
      </summary>
      <div className="divide-y divide-slate-800 border-t border-slate-700">
        {items.map((item) => (
          <div
            key={item.stock}
            className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs text-slate-400"
          >
            <span className="font-medium text-slate-200">
              {item.stock} {item.name}
            </span>
            <SignalAssetBadge assetType={item.asset_type} />
            <span>
              {item.veto_reason ? `${item.veto_reason}：` : ""}
              {item.short_reason ?? "Backend 或外部驗證已確認不符合。"}
            </span>
          </div>
        ))}
      </div>
    </details>
  )
}
