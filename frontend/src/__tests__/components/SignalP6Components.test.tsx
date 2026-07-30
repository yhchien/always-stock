import { render, screen } from "@testing-library/react"

import OutcomeMetricCard from "@/components/OutcomeMetricCard"
import SelectionReasonBadge from "@/components/SelectionReasonBadge"
import SignalFunnel from "@/components/SignalFunnel"
import {
  OUTCOME_LABELS,
  formatPercent,
  formatRate,
  selectionCompleteness,
} from "@/lib/signalP6Presentation"

describe("P6 shared presentation", () => {
  it("renders the complete funnel counts without imposing a cutoff", () => {
    render(
      <SignalFunnel
        steps={[
          { key: "raw", label: "Raw Union", value: 100, help: "原始聯集" },
          { key: "phase2", label: "Phase 2 Eligible", value: 35, help: "P2" },
          { key: "research", label: "Research", value: 34, help: "研究" },
          { key: "assessment", label: "Assessment Eligible", value: 29, help: "assessment" },
          { key: "removed", label: "True Removed", value: 5, help: "true veto" },
          { key: "recommended", label: "Recommended", value: 9, help: "推薦" },
          { key: "not-selected", label: "Not Selected", value: 20, help: "未入選" },
          { key: "technical", label: "Technical", value: 1, help: "技術" },
        ]}
      />,
    )
    for (const value of ["100", "35", "34", "29", "5", "9", "20", "1"]) {
      expect(screen.getByText(value)).toBeInTheDocument()
    }
  })

  it("centralizes neutral labels and completeness semantics", () => {
    render(
      <>
        <SelectionReasonBadge code="NO_DISTINCT_DAILY_EDGE" />
        <OutcomeMetricCard label="Acceptable Rate" value="80.0%" status="met" />
      </>,
    )
    expect(screen.getByText("今日缺少明確相對優勢")).toBeInTheDocument()
    expect(screen.getByText("80.0%")).toBeInTheDocument()
    expect(OUTCOME_LABELS.NEUTRAL).toBe("中性結果")
    expect(formatPercent(-10)).toBe("-10.0%")
    expect(formatRate(0.8)).toBe("80.0%")
    expect(selectionCompleteness("FAILED", false)).toBe("GLOBAL_SELECTION_FAILED")
    expect(selectionCompleteness("COMPLETED", true)).toBe("COMPLETE")
  })
})
