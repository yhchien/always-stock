const LABELS: Record<string, string> = {
  COMMON_STOCK: "一般股",
  FINANCIAL: "金融股",
  ETF: "ETF",
}

export function signalAssetTypeLabel(assetType: string | null | undefined): string | null {
  if (!assetType) return null
  return LABELS[assetType] ?? assetType
}

/**
 * P2 neutral instrument badge. Every asset type deliberately shares the same
 * visual weight because this is context, not a ranking or risk signal.
 */
export default function SignalAssetBadge({
  assetType,
}: {
  assetType?: string | null
}) {
  const label = signalAssetTypeLabel(assetType)
  if (!label) return null
  return (
    <span
      className="inline-flex whitespace-nowrap rounded border border-slate-600/70 bg-slate-700/35 px-1.5 py-0.5 text-[10px] font-medium text-slate-300"
      title="商品類型只決定適用的研究證據，不影響選股資格或排序"
    >
      {label}
    </span>
  )
}
