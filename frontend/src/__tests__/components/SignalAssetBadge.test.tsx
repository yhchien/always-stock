import { render, screen } from "@testing-library/react"

import SignalAssetBadge, {
  signalAssetTypeLabel,
} from "@/components/SignalAssetBadge"

const ASSET_CASES: ReadonlyArray<readonly [string, string]> = [
  ["COMMON_STOCK", "一般股"],
  ["FINANCIAL", "金融股"],
  ["ETF", "ETF"],
]

describe("SignalAssetBadge", () => {
  it.each(ASSET_CASES)("renders %s with neutral parity styling", (assetType, label) => {
    render(<SignalAssetBadge assetType={assetType} />)
    const badge = screen.getByText(label)
    expect(badge.className).toContain("border-slate")
    expect(badge.className).not.toMatch(/rose|emerald|amber/)
  })

  it("is safe for historical snapshots without asset_type", () => {
    const { container } = render(<SignalAssetBadge />)
    expect(container).toBeEmptyDOMElement()
    expect(signalAssetTypeLabel(null)).toBeNull()
  })
})
