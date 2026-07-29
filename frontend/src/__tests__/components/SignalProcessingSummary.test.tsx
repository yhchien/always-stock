import { render, screen } from "@testing-library/react"

import {
  isSignalProcessingIncomplete,
  SignalIncompleteWarning,
  SignalProcessingCounts,
} from "@/components/SignalProcessingSummary"

describe("SignalProcessingSummary", () => {
  it("keeps historical snapshots without metadata complete and crash-free", () => {
    expect(isSignalProcessingIncomplete(undefined, "done")).toBe(false)
  })

  it("treats a partial job or unprocessed candidates as incomplete", () => {
    expect(isSignalProcessingIncomplete(undefined, "partial_failure")).toBe(true)
    expect(
      isSignalProcessingIncomplete({ unprocessed_count: 3 }, "done"),
    ).toBe(true)

    render(<SignalIncompleteWarning summary={{ unprocessed_count: 3 }} />)
    expect(screen.getByText("本次分析未完整完成：3 檔未完成")).toBeInTheDocument()
  })

  it("shows actual pipeline counts without slicing data", () => {
    render(
      <div>
        <SignalProcessingCounts
          incomplete={false}
          summary={{
            raw_union_count: 180,
            regime_survivor_count: 73,
            research_completed_count: 73,
            decision_completed_count: 73,
            global_selection_eligible_count: 68,
            global_selection_recommended_count: 10,
            global_selection_not_selected_count: 58,
            final_remove_count: 5,
            technical_failure_count: 0,
            unprocessed_count: 0,
          }}
        />
      </div>,
    )

    expect(screen.getByText("Raw 180")).toBeInTheDocument()
    expect(screen.getByText("Phase 2 73")).toBeInTheDocument()
    expect(screen.getByText("Research 73")).toBeInTheDocument()
    expect(screen.getByText("Assessment 73")).toBeInTheDocument()
    expect(screen.getByText("Global 68")).toBeInTheDocument()
    expect(screen.getByText("Recommend 10")).toBeInTheDocument()
    expect(screen.getByText("Not selected 58")).toBeInTheDocument()
    expect(screen.getByText("Removed 5")).toBeInTheDocument()
    expect(screen.getByText("Technical 0")).toBeInTheDocument()
    expect(screen.getByText("Unprocessed 0")).toBeInTheDocument()
  })

  it("shows the atomic global-selection failure warning", () => {
    render(
      <SignalIncompleteWarning
        summary={{
          global_selection_status: "FAILED",
          selection_complete: false,
          unprocessed_count: 37,
        }}
      />,
    )
    expect(
      screen.getByText(
        "本次研究已完成，但正式推薦選擇未完成；目前結果不可視為完整推薦名單。",
      ),
    ).toBeInTheDocument()
  })
})
