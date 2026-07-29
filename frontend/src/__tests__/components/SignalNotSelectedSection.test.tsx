import { render, screen } from "@testing-library/react"

import SignalNotSelectedSection from "@/components/SignalNotSelectedSection"

describe("SignalNotSelectedSection", () => {
  it("is absent for historical snapshots without the additive bucket", () => {
    const { container } = render(<SignalNotSelectedSection items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("shows neutral P2 asset badges, backend rank, cluster, code and reason", () => {
    render(
      <SignalNotSelectedSection
        items={[
          {
            stock: "0050",
            name: "元大台灣50",
            asset_type: "ETF",
            backend_priority_rank: 12,
            theme_cluster: "大型權值",
            selection_reason_code: "NO_DISTINCT_DAILY_EDGE",
            selection_reason: "候選有效，但今日沒有獨立的相對優勢。",
          },
          {
            stock: "2881",
            name: "富邦金",
            asset_type: "FINANCIAL",
            backend_priority_rank: 15,
            selection_reason_code: "SETUP_NEEDS_CONFIRMATION",
            selection_reason: "型態仍需確認。",
          },
        ]}
      />,
    )

    expect(screen.getByText("未列入今日推薦（2）")).toBeInTheDocument()
    expect(screen.getByText("ETF")).toBeInTheDocument()
    expect(screen.getByText("金融股")).toBeInTheDocument()
    expect(screen.getByText("Backend #12 · 大型權值")).toBeInTheDocument()
    expect(
      screen.getByText(
        "NO_DISTINCT_DAILY_EDGE：候選有效，但今日沒有獨立的相對優勢。",
      ),
    ).toBeInTheDocument()
  })
})
