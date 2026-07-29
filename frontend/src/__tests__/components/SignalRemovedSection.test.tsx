import { render, screen } from "@testing-library/react"

import SignalRemovedSection from "@/components/SignalRemovedSection"

describe("SignalRemovedSection", () => {
  it("keeps true REMOVE separate from NOT_SELECTED", () => {
    render(
      <SignalRemovedSection
        items={[
          {
            stock: "9999",
            name: "不符標的",
            asset_type: "COMMON_STOCK",
            decision: "REMOVE",
            veto_reason: "BUSINESS_MISMATCH",
            short_reason: "公司實際業務與候選題材矛盾。",
          },
        ]}
      />,
    )

    expect(screen.getByText("已排除（1）")).toBeInTheDocument()
    expect(
      screen.getByText("BUSINESS_MISMATCH：公司實際業務與候選題材矛盾。"),
    ).toBeInTheDocument()
    expect(screen.queryByText(/NOT_SELECTED/)).not.toBeInTheDocument()
  })
})
