import { render, screen } from "@testing-library/react"

import ObservationStatusBadge, {
  ObservationLifecycleNotice,
  observationDecisionLabel,
  observationStatusLabel,
} from "@/components/ObservationStatusBadge"

describe("ObservationStatusBadge", () => {
  it.each([
    ["OBSERVING", "觀察中"],
    ["CAUTION", "警戒"],
    ["STOPPED", "已停止觀察"],
  ] as const)("renders %s without trading-action language", (status, label) => {
    render(<ObservationStatusBadge status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(observationStatusLabel(status)).toBe(label)
    expect(label).not.toMatch(/賣出|停損|看空/)
  })

  it("keeps technical failure separate from caution", () => {
    expect(observationDecisionLabel("REVIEW_FAILED")).toBe("檢查未完成")
    expect(observationDecisionLabel("STOP_OBSERVING")).toBe("停止觀察")
  })

  it("states that stop is not a sell action and failure preserves state", () => {
    render(
      <ObservationLifecycleNotice
        status="STOPPED"
        technicalStatus="TRACKING_RESEARCH_FAILED"
      />,
    )
    expect(
      screen.getByText("停止觀察僅代表不再列入魚尾追蹤名單，不構成賣出建議。"),
    ).toBeInTheDocument()
    expect(
      screen.getByText("本次追蹤檢查未完成，維持上一個有效狀態。"),
    ).toBeInTheDocument()
  })
})
