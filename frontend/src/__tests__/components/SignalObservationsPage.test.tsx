import { fireEvent, render, screen } from "@testing-library/react"

import SignalObservationsPage from "@/app/signals/(product)/observations/page"
import {
  fetchSignalObservationDetail,
  fetchSignalObservations,
  fetchSignalTrackingSummary,
} from "@/lib/api"

jest.mock("next/navigation", () => ({ usePathname: () => "/signals/observations" }))
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api")
  return {
    ...actual,
    fetchSignalObservationDetail: jest.fn(),
    fetchSignalObservations: jest.fn(),
    fetchSignalTrackingSummary: jest.fn(),
  }
})
// 這份測試在驗證工程稽核內容（TRACKING_SELECTION_CONFLICT 提示、Episode 歷史等）；
// 這些內容在正式版／工程版 toggle 上線後只在工程版顯示，直接 mock 成工程版繞過
// Provider／localStorage，不測 toggle 機制本身（toggle 機制另有測試涵蓋）。
jest.mock("@/lib/signalsViewMode", () => ({
  useSignalsViewMode: () => ({
    mode: "engineering",
    isEngineering: true,
    setMode: jest.fn(),
    toggle: jest.fn(),
  }),
}))

const listMock = fetchSignalObservations as jest.MockedFunction<typeof fetchSignalObservations>
const detailMock = fetchSignalObservationDetail as jest.MockedFunction<typeof fetchSignalObservationDetail>
const summaryMock = fetchSignalTrackingSummary as jest.MockedFunction<typeof fetchSignalTrackingSummary>

const item = {
  id: 1,
  stock: "2330",
  name: "台積電",
  asset_type: "COMMON_STOCK",
  episode_id: "episode-2",
  status: "STOPPED" as const,
  started_at: "2026-07-20T00:00:00",
  started_signal_date: "2026-07-20",
  last_review_date: "2026-07-29",
  latest_decision: "REVIEW_FAILED" as const,
  consecutive_caution_count: 1,
  latest_reason_codes: [],
  latest_reason: "技術檢查失敗",
  latest_review_technical_status: "API_FAILED",
  stopped_at: "2026-07-29T00:00:00",
  stop_reason_code: "STRUCTURE_DAMAGED",
  stop_reason: "結構破壞",
  baseline_quality: "P3_COMPLETE",
  selection_version: "v7_global_selector",
  recommended_today: true,
}

describe("SignalObservationsPage", () => {
  it("shows conflict, technical failure, non-sell notice, timeline and multiple episodes", async () => {
    listMock.mockResolvedValue({ as_of_date: "2026-07-29", observations: [item] })
    summaryMock.mockResolvedValue({
      tracking_summary: {
        review_date: "2026-07-29",
        active_before_review: 1,
        continue_count: 0,
        caution_count: 0,
        stopped_count: 1,
        review_failed_count: 1,
        conflict_count: 1,
        review_complete: false,
        tracking_prompt_version: "v7_tracking",
        tracking_state_machine_version: "p4_state_v1",
      },
    })
    detailMock.mockResolvedValue({
      ...item,
      as_of_date: "2026-07-29",
      initial_observation: { recommendation_thesis: "AI thesis" },
      latest_snapshot: {},
      review_timeline: [
        {
          review_date: "2026-07-29",
          previous_status: "STOPPED",
          decision: "REVIEW_FAILED",
          reason_codes: [],
          reason: "API failed",
          caution_dimensions: [],
          failed_dimensions: [],
          backend_evidence: {},
          external_assessment: null,
          market_context: {},
          persistence_warning: {},
          technical_status: "API_FAILED",
          tracking_prompt_version: "v7_tracking",
          tracking_state_machine_version: "p4_state_v1",
        },
      ],
      recommendation_history: [],
      episode_history: [
        {
          id: 0,
          episode_id: "episode-1",
          status: "STOPPED",
          started_signal_date: "2026-07-01",
          stopped_at: "2026-07-10T00:00:00",
          initial_thesis: "舊 thesis",
          stop_reason_code: "STRUCTURE_DAMAGED",
          stop_reason: "舊 episode 停止",
          is_current: false,
        },
        {
          id: 1,
          episode_id: "episode-2",
          status: "STOPPED",
          started_signal_date: "2026-07-20",
          stopped_at: "2026-07-29T00:00:00",
          initial_thesis: "AI thesis",
          stop_reason_code: "STRUCTURE_DAMAGED",
          stop_reason: "結構破壞",
          is_current: true,
        },
      ],
    })

    render(<SignalObservationsPage />)
    expect(await screen.findByText(/TRACKING_SELECTION_CONFLICT/)).toBeInTheDocument()
    expect(screen.getByText("本次追蹤檢查未完成，維持上一個有效狀態。")).toBeInTheDocument()
    fireEvent.click(screen.getByText("台積電"))
    expect(await screen.findByText("追蹤紀錄")).toBeInTheDocument()
    expect(screen.getByText(/第 1 次/)).toBeInTheDocument()
    expect(screen.getByText(/停止觀察僅代表/)).toBeInTheDocument()
    expect(screen.getAllByText("今日推薦：RECOMMEND").length).toBeGreaterThan(0)
  })
})
