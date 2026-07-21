/**
 * Phase 1 Canonical Market Classification 中文顯示字典（2026-07-21）。
 * 未命中字典的值直接 fallback 顯示原始 code，不會壞（向後相容，也涵蓋未來新增分類）。
 */

export const ETF_REGION_LABELS: Record<string, string> = {
  TAIWAN: "台灣",
  US: "美國",
  JAPAN: "日本",
  CHINA: "中國",
  HONG_KONG: "香港",
  KOREA: "韓國",
  INDIA: "印度",
  VIETNAM: "越南",
  EUROPE: "歐洲",
  GLOBAL: "全球",
  ASIA: "亞太",
  EMERGING_MARKETS: "新興市場",
  OTHER: "其他",
}

export const ETF_STRATEGY_LABELS: Record<string, string> = {
  MARKET_CAP: "市值型",
  HIGH_DIVIDEND: "高股息",
  ESG: "ESG／永續",
  GROWTH: "成長／動能",
  VALUE: "價值",
  LOW_VOLATILITY: "低波動",
  SECTOR: "產業型",
  THEMATIC: "主題型",
  LEVERAGED: "槓桿",
  INVERSE: "反向",
  ACTIVE: "主動式",
  BOND_DURATION: "債券",
  MULTI_ASSET_BALANCED: "多重資產／平衡",
  OTHER: "其他",
}

export const ETF_ASSET_CLASS_LABELS: Record<string, string> = {
  EQUITY: "股票",
  BOND: "債券",
  COMMODITY: "商品",
  MULTI_ASSET: "多重資產",
  CURRENCY: "匯率",
  OTHER: "其他",
}

export function etfRegionLabel(region?: string | null): string {
  if (!region) return "—"
  return ETF_REGION_LABELS[region] ?? region
}

export function etfStrategyLabel(strategy?: string | null): string {
  if (!strategy) return "—"
  return ETF_STRATEGY_LABELS[strategy] ?? strategy
}

export function etfAssetClassLabel(assetClass?: string | null): string {
  if (!assetClass) return "—"
  return ETF_ASSET_CLASS_LABELS[assetClass] ?? assetClass
}
